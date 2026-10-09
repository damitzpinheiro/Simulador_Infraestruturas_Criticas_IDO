"""
IEC 60870-5-104 - Slave (RTU)
Simula uma RTU aguardando conexao do master na porta 2404.

Multi-cliente: um mesmo RTU pode servir varios masters simultaneamente. Cada conexao
mantem seu proprio estado de sessao (SSN/RSN, buffers, timers), enquanto os datapoints
e as estatisticas sao compartilhados. Os eventos espontaneos e o scan periodico sao
transmitidos (broadcast) para todas as conexoes ativas.
"""

import asyncio
import time
import random
import math
import logging
from typing import Dict, List, Optional, Set

from .frames import (
    APDUCodec, ASDU, InformationObject, CP56Time2a,
    TypeID, CauseOfTransmission,
    QDS_INVALID, QDS_OVERFLOW,
    DEFAULT_PORT,
    STARTDT_ACT, STARTDT_CON, STOPDT_ACT, STOPDT_CON,
    TESTFR_ACT, TESTFR_CON,
)
from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.network_conditions import NetworkConditions
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.iec104.slave")


class DataPoint:
    """Ponto de dados simulado na RTU."""

    def __init__(self, ioa: int, type_id: int, initial_value=0,
                 min_val=None, max_val=None, noise: float = 0.0,
                 drift: float = 0.0, change_probability: float = 0.1):
        self.ioa = ioa
        self.type_id = type_id
        self.value = initial_value
        self.min_val = min_val
        self.max_val = max_val
        self.noise = noise
        self.drift = drift
        self.change_probability = change_probability
        self.quality = 0x00
        self.last_sent_value = initial_value
        self._phase = random.uniform(0, 2 * math.pi)
        # Contador simulado para M_IT_NA_1
        self._counter: int = 0
        # Binding ao modelo de processo (Passo 2). Quando amarrado, o ponto para de
        # randomizar sozinho e passa a refletir o sinal do modelo.
        self._model = None
        self._model_signal = None
        self._model_input = False

    def bind_model(self, model, signal, is_input=False):
        self._model = model
        self._model_signal = signal
        self._model_input = is_input

    def _set_modeled_value(self, v):
        if v is not None:
            self.value = bool(v) if isinstance(self.value, bool) else v

    def apply_write(self, v):
        """Escrita da HMI/master. Ponto amarrado a ENTRADA do modelo roteia ao modelo."""
        if self._model is not None and self._model_input:
            self._model.command(self._model_signal, v)
        else:
            self.value = v

    def update(self) -> bool:
        """Atualiza o valor. Retorna True se houve mudanca significativa."""
        old = self.value

        # Ponto amarrado ao modelo: valor ja empurrado pelo driver; nao randomiza.
        if self._model_signal is not None:
            pass
        elif self.type_id in (TypeID.M_SP_NA_1, TypeID.M_SP_TB_1):
            if random.random() < self.change_probability:
                self.value = not self.value
        elif self.type_id in (TypeID.M_DP_NA_1, TypeID.M_DP_TB_1):
            if random.random() < self.change_probability:
                self.value = 2 if self.value == 1 else 1
        elif self.type_id in (TypeID.M_ME_NA_1, TypeID.M_ME_TD_1,
                              TypeID.M_ME_NC_1, TypeID.M_ME_TF_1):
            t = time.time()
            sine = math.sin(t * 0.05 + self._phase) * self.noise * 0.5
            noise = random.gauss(0, self.noise * 0.3)
            drift = self.drift * math.sin(t * 0.001 + self._phase)
            self.value += sine + noise + drift
            if self.min_val is not None:
                self.value = max(self.min_val, self.value)
            if self.max_val is not None:
                self.value = min(self.max_val, self.value)
        elif self.type_id in (TypeID.M_ME_NB_1, TypeID.M_ME_TE_1):
            if random.random() < self.change_probability:
                self.value += random.randint(-2, 2)
                if self.min_val is not None:
                    self.value = max(self.min_val, self.value)
                if self.max_val is not None:
                    self.value = min(self.max_val, self.value)
        elif self.type_id == TypeID.M_ST_NA_1:
            if random.random() < self.change_probability:
                self.value = max(-64, min(63, self.value + random.randint(-1, 1)))

        # Incrementa contador a cada atualizacao
        self._counter += 1

        # --- Qualidade de bits ---
        self.quality = 0x00
        if isinstance(self.value, float):
            if self.max_val is not None and self.value >= self.max_val:
                self.quality |= QDS_OVERFLOW
            if self.min_val is not None and self.value <= self.min_val:
                self.quality |= QDS_INVALID

        # Deteccao de mudanca
        if self.type_id in (TypeID.M_SP_NA_1, TypeID.M_SP_TB_1,
                            TypeID.M_DP_NA_1, TypeID.M_DP_TB_1):
            changed = self.value != old
        elif self.type_id == TypeID.M_ST_NA_1:
            changed = self.value != old
        else:
            range_val = abs((self.max_val or 100) - (self.min_val or 0)) or 1
            deadband = range_val * 0.01
            changed = abs(self.value - self.last_sent_value) > deadband

        if changed:
            self.last_sent_value = self.value
        return changed


class _MasterConn:
    """Estado de sessao de UMA conexao de master (independente das demais)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peername = writer.get_extra_info("peername")

        self.ssn = 0
        self.rsn = 0
        self.peer_rsn = 0
        self.ack_pending = 0
        self.ack_since: Optional[float] = None   # instante do 1o I-frame nao confirmado (T2)
        self.t1_start: Optional[float] = None
        self.last_rx_time: Optional[float] = None
        self.data_transfer_active = False
        self.recv_buffer = b""
        self.closed = False
        self.tasks: List[asyncio.Task] = []


class IEC104Slave(BaseProtocolGenerator):
    """Simulador de estacao controlada IEC 104 (RTU/slave), multi-cliente."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        # Interface de bind do servidor. Por padrao segue o host (retrocompativel:
        # 127.0.0.1 = so local). Para operacao multi-maquina, use bind: 0.0.0.0
        # (aceita conexoes de outras maquinas na LAN).
        self.bind = config.get("bind", self.host)
        self.port = config.get("port", DEFAULT_PORT)
        self.common_address = config.get("common_address", 1)
        self.originator = config.get("originator", 0)

        self.t1 = config.get("t1", 15)
        self.t2 = config.get("t2", 10)
        self.t3 = config.get("t3", 20)
        self.spontaneous_interval = config.get("spontaneous_interval", 2.0)
        self.periodic_interval = config.get("periodic_interval", 10.0)
        self.gi_response_delay = config.get("gi_response_delay", 0.05)
        self._k = config.get("k", 12)
        self._w = config.get("w", 8)

        self.server: Optional[asyncio.Server] = None
        self._conns: Set[_MasterConn] = set()

        self.datapoints: Dict[int, DataPoint] = {}
        self._init_datapoints(config.get("datapoints", []))

        self._net = NetworkConditions.from_config(config.get("network_conditions", {}))
        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @property
    def protocol_name(self) -> str:
        return "IEC104-Slave"

    def _init_datapoints(self, dp_configs: list):
        if not dp_configs:
            dp_configs = [
                # disjuntores (single-point)
                {"ioa": 100, "type_id": 1,  "value": False, "change_prob": 0.02},
                {"ioa": 101, "type_id": 1,  "value": True,  "change_prob": 0.02},
                {"ioa": 102, "type_id": 1,  "value": False, "change_prob": 0.02},
                {"ioa": 103, "type_id": 1,  "value": True,  "change_prob": 0.01},
                # tensao kV (short float)
                {"ioa": 200, "type_id": 13, "value": 138.5, "min": 120.0, "max": 145.0,
                 "noise": 2.0, "drift": 0.5},
                {"ioa": 201, "type_id": 13, "value": 69.2,  "min": 60.0,  "max": 75.0,
                 "noise": 1.5, "drift": 0.3},
                # corrente A (short float)
                {"ioa": 300, "type_id": 13, "value": 450.0, "min": 0.0,   "max": 800.0,
                 "noise": 15.0, "drift": 2.0},
                {"ioa": 301, "type_id": 13, "value": 320.0, "min": 0.0,   "max": 600.0,
                 "noise": 10.0, "drift": 1.5},
                # potencia MW (short float)
                {"ioa": 400, "type_id": 13, "value": 62.5,  "min": 0.0,   "max": 100.0,
                 "noise": 3.0, "drift": 1.0},
                # temperatura C (scaled)
                {"ioa": 500, "type_id": 11, "value": 45, "min": 20, "max": 90,
                 "change_prob": 0.3},
                {"ioa": 501, "type_id": 11, "value": 38, "min": 20, "max": 90,
                 "change_prob": 0.3},
                # posicao tap changer (double-point)
                {"ioa": 600, "type_id": 3,  "value": 2, "change_prob": 0.01},
            ]

        for dp in dp_configs:
            ioa = dp["ioa"]
            self.datapoints[ioa] = DataPoint(
                ioa=ioa,
                type_id=dp.get("type_id", TypeID.M_SP_NA_1),
                initial_value=dp.get("value", 0),
                min_val=dp.get("min"),
                max_val=dp.get("max"),
                noise=dp.get("noise", 0.0),
                drift=dp.get("drift", 0.0),
                change_probability=dp.get("change_prob", 0.1),
            )
        logger.info(f"Slave inicializado com {len(self.datapoints)} datapoints")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self.server = await asyncio.start_server(
            self._handle_connection, self.bind, self.port
        )
        addr = self.server.sockets[0].getsockname()
        self.state = SessionState.CONNECTED
        logger.info(f"Slave IEC 104 ouvindo em {addr[0]}:{addr[1]}")

        # Loops de nivel do RTU (compartilhados por todas as conexoes).
        self._tasks = [
            asyncio.create_task(self._spontaneous_loop()),
            asyncio.create_task(self._periodic_scan_loop()),
        ]

        # Sem "async with self.server": a saida do bloco chamaria wait_closed()
        # durante o desenrolar do CancelledError e travaria se houvesse conexao
        # ativa. O fechamento do servidor fica so no stop(), com timeout.
        try:
            await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            self._cancel_all()

    async def stop(self):
        self._stop_event.set()
        self._cancel_all()
        if self.server:
            self.server.close()
            try:
                # Os transportes ja foram abortados em _cancel_all, entao
                # wait_closed() retorna rapido; o timeout curto e so uma salvaguarda.
                await asyncio.wait_for(self.server.wait_closed(), timeout=0.5)
            except (asyncio.TimeoutError, Exception):
                pass
        await super().stop()

    def _cancel_all(self):
        for task in self._tasks:
            if not task.done():
                task.cancel()
        for conn in list(self._conns):
            conn.closed = True
            for task in conn.tasks:
                if not task.done():
                    task.cancel()
            # Aborta o transporte na hora (sem handshake de fechamento gracioso):
            # na pausa nao ha por que esperar o wait_closed de cada conexao.
            try:
                conn.writer.transport.abort()
            except Exception:
                pass

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter):
        conn = _MasterConn(reader, writer)
        self._conns.add(conn)
        logger.info(f"Master conectado: {conn.peername}  ({len(self._conns)} conexao(es))")

        conn.tasks = [
            asyncio.create_task(self._receive_loop(conn)),
            asyncio.create_task(self._t2_ack_loop(conn)),
            asyncio.create_task(self._t1_monitor_loop(conn)),
            asyncio.create_task(self._t3_monitor_loop(conn)),
        ]
        try:
            # A conexao vive ate qualquer um dos loops terminar (receive retorna
            # na desconexao; t1 levanta ConnectionError no timeout).
            await asyncio.wait(conn.tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            pass
        finally:
            conn.closed = True
            for task in conn.tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*conn.tasks, return_exceptions=True)
            self._conns.discard(conn)
            logger.info(f"Master desconectado: {conn.peername}")
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
            except Exception:
                try:
                    writer.transport.abort()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Envio (por conexao)
    # ------------------------------------------------------------------
    async def _send_frame(self, conn: _MasterConn, frame: bytes, desc: str = ""):
        w = conn.writer
        if w and not w.is_closing():
            try:
                sent = await self._net.apply(w, frame)
                if sent:
                    self.stats.record_sent(len(frame))
                    if desc:
                        logger.debug(f"  TX -> {desc} ({len(frame)} B)")
                else:
                    self.stats.record_dropped()
            except (ConnectionError, OSError):
                self.stats.record_error()
                raise

    async def _send_i_frame(self, conn: _MasterConn, asdu: ASDU, desc: str = ""):
        if not conn.data_transfer_active:
            return
        # bloqueia envio se janela k estiver cheia (norma secao 5.1)
        blocked = False
        while (conn.ssn - conn.peer_rsn) % 32768 >= self._k:
            if not blocked:
                self.stats.record_window_block()
                blocked = True
            await asyncio.sleep(0.1)
            if self._stop_event.is_set() or conn.closed:
                return
        frame = APDUCodec.encode_i_frame(conn.ssn, conn.rsn, asdu)
        await self._send_frame(conn, frame, desc or f"I SSN={conn.ssn}")
        if (conn.ssn - conn.peer_rsn) % 32768 == 0:
            conn.t1_start = time.time()
        conn.ssn = (conn.ssn + 1) % 32768

    async def _send_s_frame(self, conn: _MasterConn):
        frame = APDUCodec.encode_s_frame(conn.rsn)
        await self._send_frame(conn, frame, f"S-frame RSN={conn.rsn}")
        conn.ack_pending = 0
        conn.ack_since = None

    def _update_peer_ack(self, conn: _MasterConn, nr: int):
        avancou = (nr != conn.peer_rsn)
        conn.peer_rsn = nr
        if (conn.ssn - conn.peer_rsn) % 32768 == 0:
            conn.t1_start = None            # tudo confirmado: para o T1
        elif avancou:
            # ACK confirmou parte, mas ainda ha frames pendentes: houve progresso
            # (o peer esta vivo), entao reinicia o T1 para os frames restantes.
            # Sem isso, com envio continuo o peer_rsn nunca alcanca o ssn e o
            # T1 estoura mesmo com a conexao saudavel.
            conn.t1_start = time.time()

    # ------------------------------------------------------------------
    # Recepcao (por conexao)
    # ------------------------------------------------------------------
    async def _receive_loop(self, conn: _MasterConn):
        while not self._stop_event.is_set():
            try:
                data = await asyncio.wait_for(conn.reader.read(4096), timeout=30)
            except asyncio.TimeoutError:
                continue
            except (ConnectionError, OSError):
                break
            if not data:
                break

            conn.recv_buffer += data
            while True:
                frame, conn.recv_buffer = APDUCodec.read_frame_from_buffer(
                    conn.recv_buffer
                )
                if frame is None:
                    break
                self.stats.record_received(len(frame))
                await self._process_frame(conn, frame)

    async def _process_frame(self, conn: _MasterConn, frame: bytes):
        decoded = APDUCodec.decode_frame(frame)
        ftype = decoded["type"]

        if ftype == "U":
            conn.last_rx_time = time.time()
            await self._handle_u_frame(conn, decoded)
        elif ftype == "S":
            conn.last_rx_time = time.time()
            logger.debug(f"  RX <- S-frame RSN={decoded['rsn']}")
            self._update_peer_ack(conn, decoded["rsn"])
        elif ftype == "I":
            await self._handle_i_frame(conn, decoded)

    async def _handle_u_frame(self, conn: _MasterConn, decoded: dict):
        func = decoded["function"]
        fname = decoded["function_name"]
        logger.info(f"  RX <- U-frame: {fname}")

        if func == STARTDT_ACT:
            await self._send_frame(conn, APDUCodec.encode_u_frame(STARTDT_CON),
                                   "STARTDT_CON")
            conn.data_transfer_active = True
            self.state = SessionState.ACTIVE
            logger.info("Transferencia de dados ATIVADA")
            await self._send_end_of_init(conn)

        elif func == STOPDT_ACT:
            await self._send_frame(conn, APDUCodec.encode_u_frame(STOPDT_CON),
                                   "STOPDT_CON")
            conn.data_transfer_active = False
            logger.info("Transferencia de dados DESATIVADA")

        elif func == TESTFR_ACT:
            await self._send_frame(conn, APDUCodec.encode_u_frame(TESTFR_CON),
                                   "TESTFR_CON")

    async def _send_end_of_init(self, conn: _MasterConn):
        asdu = ASDU(
            type_id=TypeID.M_EI_NA_1,
            cause=CauseOfTransmission.INITIALIZED,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=0)],
        )
        await self._send_i_frame(conn, asdu, "End of Initialization")

    async def _handle_i_frame(self, conn: _MasterConn, decoded: dict):
        ssn = decoded["ssn"]
        asdu_info = decoded.get("asdu")
        conn.rsn = (ssn + 1) % 32768
        if conn.ack_pending == 0:
            conn.ack_since = time.time()   # inicia o relogio T2 do 1o pendente
        conn.ack_pending += 1
        conn.last_rx_time = time.time()
        self._update_peer_ack(conn, decoded.get("rsn", 0))

        # Deteccao de frame fora de ordem
        expected_rsn = (conn.rsn - 1) % 32768
        if ssn != expected_rsn and conn.ack_pending > 1:
            logger.warning(f"  Frame fora de ordem: esperado SSN={expected_rsn}, recebido={ssn}")

        if asdu_info:
            type_name = asdu_info["type_name"]
            cause_name = asdu_info["cause_name"]
            logger.info(f"  RX <- I-frame SSN={ssn}: {type_name} COT={cause_name}")
            self.stats.record_asdu_type(asdu_info["type_id"])

            type_id = asdu_info["type_id"]
            if type_id == TypeID.C_IC_NA_1:
                await self._handle_general_interrogation(conn, decoded)
            elif type_id == TypeID.C_SC_NA_1:
                await self._handle_single_command(conn, decoded)
            elif type_id == TypeID.C_CS_NA_1:
                await self._handle_clock_sync(conn)
            elif type_id == TypeID.C_RD_NA_1:
                await self._handle_read_command(conn, decoded)
            elif type_id == TypeID.C_CI_NA_1:
                await self._handle_counter_interrogation(conn, decoded)

        if conn.ack_pending >= self._w:
            await self._send_s_frame(conn)

    async def _handle_general_interrogation(self, conn: _MasterConn, decoded: dict):
        # Verifica QOI para interrogacao por grupo (21-36)
        objects = decoded.get("objects") or []
        qoi = objects[0]["value"] if objects and objects[0]["value"] is not None else 20
        if isinstance(qoi, int) and 21 <= qoi <= 36:
            group = qoi - 20
            logger.info(f">> Interrogacao de Grupo {group} (QOI={qoi})")
            cot_resp = CauseOfTransmission(qoi)
        else:
            logger.info(">> Processando Interrogacao Geral...")
            cot_resp = CauseOfTransmission.INTERROGATED_STATION

        asdu_ack = ASDU(
            type_id=TypeID.C_IC_NA_1,
            cause=CauseOfTransmission.ACTIVATION_CON,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=qoi)],
        )
        await self._send_i_frame(conn, asdu_ack, "GI ACK")

        by_type: Dict[int, List[DataPoint]] = {}
        for dp in self.datapoints.values():
            by_type.setdefault(dp.type_id, []).append(dp)

        for type_id, dps in by_type.items():
            objects_list = [
                InformationObject(ioa=dp.ioa, value=dp.value, quality=dp.quality)
                for dp in dps
            ]
            asdu = ASDU(
                type_id=type_id,
                num_objects=len(objects_list),
                cause=int(cot_resp),
                common_address=self.common_address,
                originator=self.originator,
                objects=objects_list,
            )
            try:
                tname = TypeID(type_id).name
            except ValueError:
                tname = str(type_id)
            await self._send_i_frame(conn, asdu, f"GI: {tname} x{len(objects_list)}")
            await asyncio.sleep(self.gi_response_delay)

        asdu_term = ASDU(
            type_id=TypeID.C_IC_NA_1,
            cause=CauseOfTransmission.ACTIVATION_TERMINATION,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=qoi)],
        )
        await self._send_i_frame(conn, asdu_term, "GI Termination")
        logger.info(">> Interrogacao Geral concluida")

    async def _handle_single_command(self, conn: _MasterConn, decoded: dict):
        asdu_raw = decoded.get("asdu_raw", b'')
        objects = decoded.get("objects") or []
        if objects:
            ioa = objects[0]["ioa"]
            value = objects[0]["value"]
        elif len(asdu_raw) >= 10:
            ioa = asdu_raw[6] | (asdu_raw[7] << 8) | (asdu_raw[8] << 16)
            sco = asdu_raw[9]
            value = bool(sco & 0x01)
        else:
            return

        logger.info(f">> Comando recebido: IOA={ioa} valor={value}")
        if ioa in self.datapoints:
            self.datapoints[ioa].apply_write(value)

        for cause in (CauseOfTransmission.ACTIVATION_CON, CauseOfTransmission.ACTIVATION_TERMINATION):
            asdu_con = ASDU(
                type_id=TypeID.C_SC_NA_1,
                cause=cause,
                common_address=self.common_address,
                originator=self.originator,
                objects=[InformationObject(ioa=ioa, value=value)],
            )
            label = "ACK" if cause == CauseOfTransmission.ACTIVATION_CON else "Term"
            await self._send_i_frame(conn, asdu_con, f"Cmd {label} IOA={ioa}")

    async def _handle_clock_sync(self, conn: _MasterConn):
        logger.info(">> Clock sync recebido")
        asdu_con = ASDU(
            type_id=TypeID.C_CS_NA_1,
            cause=CauseOfTransmission.ACTIVATION_CON,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, timestamp=CP56Time2a.now())],
        )
        await self._send_i_frame(conn, asdu_con, "Clock Sync ACK")

    async def _handle_read_command(self, conn: _MasterConn, decoded: dict):
        """Responde a C_RD_NA_1 com o valor atual do datapoint (COT=REQUEST)."""
        asdu_raw = decoded.get("asdu_raw", b'')
        if len(asdu_raw) < 9:
            return
        ioa = asdu_raw[6] | (asdu_raw[7] << 8) | (asdu_raw[8] << 16)
        logger.info(f">> Read command: IOA={ioa}")

        dp = self.datapoints.get(ioa)
        if dp is None:
            logger.warning(f"   IOA={ioa} nao encontrado")
            return

        type_map = {
            TypeID.M_SP_NA_1: TypeID.M_SP_TB_1,
            TypeID.M_DP_NA_1: TypeID.M_DP_TB_1,
            TypeID.M_ME_NA_1: TypeID.M_ME_TD_1,
            TypeID.M_ME_NB_1: TypeID.M_ME_TE_1,
            TypeID.M_ME_NC_1: TypeID.M_ME_TF_1,
        }
        type_id = type_map.get(dp.type_id, dp.type_id)
        obj = InformationObject(
            ioa=dp.ioa, value=dp.value, quality=dp.quality,
            timestamp=CP56Time2a.now()
        )
        asdu = ASDU(
            type_id=type_id,
            cause=CauseOfTransmission.REQUEST,
            common_address=self.common_address,
            originator=self.originator,
            objects=[obj],
        )
        await self._send_i_frame(conn, asdu, f"Read Response IOA={ioa}")

    async def _handle_counter_interrogation(self, conn: _MasterConn, decoded: dict):
        """Responde a C_CI_NA_1 com contadores simulados (M_IT_NA_1)."""
        logger.info(">> Interrogacao de contadores recebida")

        asdu_ack = ASDU(
            type_id=TypeID.C_CI_NA_1,
            cause=CauseOfTransmission.ACTIVATION_CON,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=5)],
        )
        await self._send_i_frame(conn, asdu_ack, "CI ACK")

        counter_objects = [
            InformationObject(ioa=dp.ioa, value=dp._counter, quality=0x00)
            for dp in self.datapoints.values()
            if dp.type_id in (TypeID.M_ME_NC_1, TypeID.M_ME_NB_1, TypeID.M_ME_NA_1)
        ]
        if counter_objects:
            asdu = ASDU(
                type_id=TypeID.M_IT_NA_1,
                num_objects=len(counter_objects),
                cause=CauseOfTransmission.INTERROGATED_STATION,
                common_address=self.common_address,
                originator=self.originator,
                objects=counter_objects,
            )
            await self._send_i_frame(conn, asdu, f"Counter Response x{len(counter_objects)}")

        asdu_term = ASDU(
            type_id=TypeID.C_CI_NA_1,
            cause=CauseOfTransmission.ACTIVATION_TERMINATION,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=5)],
        )
        await self._send_i_frame(conn, asdu_term, "CI Termination")

    # ------------------------------------------------------------------
    # Timers (por conexao)
    # ------------------------------------------------------------------
    async def _t2_ack_loop(self, conn: _MasterConn):
        # T2: confirma os I-frames pendentes ate T2 segundos apos o 1o nao confirmado
        # (contado desde ack_since, nao desde o ultimo frame recebido - senao, com
        # trafego continuo, o T2 nunca dispararia e o master cairia por T1).
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
            if (conn.ack_pending > 0
                    and conn.ack_since is not None
                    and time.time() - conn.ack_since >= self.t2):
                await self._send_s_frame(conn)

    async def _t1_monitor_loop(self, conn: _MasterConn):
        # T1: encerra ESTA conexao se I-frames enviados ficarem sem ACK
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            if (conn.t1_start is not None
                    and time.time() - conn.t1_start >= self.t1):
                self.stats.record_t1_timeout()
                logger.warning("[Slave] T1 timeout - I-frames sem confirmacao")
                raise ConnectionError("T1 timeout")

    async def _t3_monitor_loop(self, conn: _MasterConn):
        # T3: envia TESTFR_ACT se nenhum frame for recebido por T3 segundos
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            if (conn.last_rx_time is not None
                    and time.time() - conn.last_rx_time >= self.t3):
                logger.debug("[Slave] T3: enviando TESTFR_ACT por inatividade")
                await self._send_frame(conn, APDUCodec.encode_u_frame(TESTFR_ACT),
                                       "TESTFR_ACT (T3)")

    # ------------------------------------------------------------------
    # Loops de nivel do RTU (broadcast para todas as conexoes ativas)
    # ------------------------------------------------------------------
    def _active_conns(self) -> List[_MasterConn]:
        return [c for c in self._conns if c.data_transfer_active and not c.closed]

    async def _broadcast_i_frame(self, asdu: ASDU, desc: str):
        for conn in self._active_conns():
            try:
                await self._send_i_frame(conn, asdu, desc)
            except (ConnectionError, OSError):
                pass  # a conexao morrera pelo seu proprio loop de recepcao/T1

    async def _periodic_scan_loop(self):
        """Envia todos os datapoints periodicamente (COT=PERIODIC) a cada master ativo."""
        while not self._stop_event.is_set():
            await asyncio.sleep(self.periodic_interval)
            if not self._active_conns():
                continue
            by_type: Dict[int, List[DataPoint]] = {}
            for dp in self.datapoints.values():
                by_type.setdefault(dp.type_id, []).append(dp)
            for type_id, dps in by_type.items():
                objects = [
                    InformationObject(ioa=dp.ioa, value=dp.value, quality=dp.quality)
                    for dp in dps
                ]
                asdu = ASDU(
                    type_id=type_id,
                    num_objects=len(objects),
                    cause=CauseOfTransmission.PERIODIC,
                    common_address=self.common_address,
                    originator=self.originator,
                    objects=objects,
                )
                await self._broadcast_i_frame(
                    asdu, f"Periodic: {TypeID(type_id).name} x{len(objects)}")

    async def _spontaneous_loop(self):
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.spontaneous_interval)
            await asyncio.sleep(interval)
            # o processo fisico evolui sempre; so transmitimos se ha master ativo
            changed = [dp for dp in self.datapoints.values() if dp.update()]
            if not changed or not self._active_conns():
                continue
            for dp in changed:
                await self._broadcast_spontaneous(dp)

    async def _broadcast_spontaneous(self, dp: DataPoint):
        # usa versao com timestamp quando disponivel para cada tipo base
        type_map = {
            TypeID.M_SP_NA_1: TypeID.M_SP_TB_1,
            TypeID.M_DP_NA_1: TypeID.M_DP_TB_1,
            TypeID.M_ME_NA_1: TypeID.M_ME_TD_1,
            TypeID.M_ME_NB_1: TypeID.M_ME_TE_1,
            TypeID.M_ME_NC_1: TypeID.M_ME_TF_1,
        }
        type_id = type_map.get(dp.type_id, dp.type_id)
        obj = InformationObject(
            ioa=dp.ioa, value=dp.value,
            quality=dp.quality, timestamp=CP56Time2a.now(),
        )
        asdu = ASDU(
            type_id=type_id,
            cause=CauseOfTransmission.SPONTANEOUS,
            common_address=self.common_address,
            originator=self.originator,
            objects=[obj],
        )
        try:
            tname = TypeID(type_id).name
        except ValueError:
            tname = str(type_id)
        val_str = f"{dp.value:.2f}" if isinstance(dp.value, float) else str(dp.value)
        await self._broadcast_i_frame(asdu, f"Spont: IOA={dp.ioa} {tname}={val_str}")
