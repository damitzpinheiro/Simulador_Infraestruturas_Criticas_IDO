"""
IEC 60870-5-104 - Master (Estacao de Controle / SCADA)
Simula uma estacao SCADA conectando ao slave na porta 2404.
"""

import asyncio
import random
import time
import logging
from typing import Optional, Dict

from .frames import (
    APDUCodec, ASDU, InformationObject, CP56Time2a,
    TypeID, CauseOfTransmission,
    DEFAULT_PORT,
    STARTDT_ACT, STARTDT_CON, STOPDT_ACT, STOPDT_CON,
    TESTFR_ACT, TESTFR_CON,
)
from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.network_conditions import NetworkConditions
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.iec104.master")


class IEC104Master(BaseProtocolGenerator):
    """Simulador de estacao de controle IEC 104 (master/SCADA)."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", DEFAULT_PORT)
        self.common_address = config.get("common_address", 1)
        self.originator = config.get("originator", 0)

        self.ssn = 0
        self.rsn = 0
        self.peer_ssn = 0

        self.t1 = config.get("t1", 15)
        self.t2 = config.get("t2", 10)
        self.t3 = config.get("t3", 20)
        self.startup_delay = config.get("startup_delay", 1.5)

        self.gi_interval = config.get("gi_interval", 60)
        self.command_interval = config.get("command_interval", 15)
        self.testfr_interval = config.get("testfr_interval", 30)
        self.clock_sync_interval = config.get("clock_sync_interval", 120)

        self.command_ioas = config.get("command_ioas", [100, 101, 102, 103])

        self._k = config.get("k", 12)
        self._w = config.get("w", 8)
        self._ack_pending = 0
        self._ack_since = None
        self.data_transfer_active = False
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._recv_buffer = b''

        self._peer_rsn: int = 0
        self._t1_start: Optional[float] = None
        self._last_rx_time: Optional[float] = None
        self._stopdt_con_event: asyncio.Event = asyncio.Event()

        # Rastreamento de RTT: ssn_enviado -> timestamp
        self._pending_cmds: Dict[int, float] = {}

        # Ultimo valor recebido por IOA (ioa -> {value, type_name, cause_name, ts}),
        # para inspecao via console sem precisar de 'observe on'.
        self.last_values: Dict[int, dict] = {}

        self._net = NetworkConditions.from_config(config.get("network_conditions", {}))
        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @property
    def protocol_name(self) -> str:
        return "IEC104-Master"

    def _reset(self):
        self.ssn = 0
        self.rsn = 0
        self.peer_ssn = 0
        self._peer_rsn = 0
        self._ack_pending = 0
        self._ack_since = None        # instante do 1o I-frame ainda nao confirmado (T2)
        self._t1_start = None
        self._last_rx_time = None
        self._stopdt_con_event.clear()
        self.data_transfer_active = False
        self._recv_buffer = b''
        self._pending_cmds.clear()

    def _update_peer_ack(self, nr: int):
        avancou = (nr != self._peer_rsn)
        self._peer_rsn = nr
        if (self.ssn - self._peer_rsn) % 32768 == 0:
            self._t1_start = None           # tudo confirmado: para o T1
        elif avancou:
            # ACK confirmou parte, mas ainda ha frames pendentes: houve progresso
            # (o peer esta vivo), entao reinicia o T1 para os frames restantes.
            self._t1_start = time.time()

    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self._reset()

        await asyncio.sleep(self.startup_delay)

        logger.info(f"Conectando ao slave em {self.host}:{self.port}...")
        # "Connection refused" so significa que o slave ainda nao esta escutando
        # (subindo/reiniciando). Em vez de estourar erro e esperar o retry_delay
        # inteiro da engine, tenta reconectar rapido e em silencio ate o slave
        # aparecer - isso evita a enxurrada de ERROR e reconecta em ~1s.
        tentativas = 0
        while not self._stop_event.is_set():
            try:
                self.reader, self.writer = await asyncio.open_connection(
                    self.host, self.port
                )
                break
            except ConnectionRefusedError:
                tentativas += 1
                if tentativas == 1:
                    logger.warning(
                        f"Slave ainda nao disponivel em {self.host}:{self.port}; "
                        f"tentando reconectar..."
                    )
                elif tentativas % 20 == 0:
                    logger.warning(
                        f"Slave ainda indisponivel apos {tentativas} tentativas "
                        f"(o iec104-slave esta rodando? veja 'status')"
                    )
                await asyncio.sleep(1.0)
        if self._stop_event.is_set():
            return
        if tentativas:
            logger.info(f"Slave respondeu apos {tentativas} tentativa(s)")
        self.state = SessionState.CONNECTED
        logger.info("Conectado ao slave")

        try:
            await self._activate_data_transfer()

            await asyncio.sleep(0.5)
            await self._send_general_interrogation()

            await asyncio.sleep(0.3)
            await self._send_clock_sync()

            tasks = [
                asyncio.create_task(self._receive_loop()),
                asyncio.create_task(self._testfr_loop()),
                asyncio.create_task(self._gi_loop()),
                asyncio.create_task(self._command_loop()),
                asyncio.create_task(self._clock_sync_loop()),
                asyncio.create_task(self._s_frame_timer_loop()),
                asyncio.create_task(self._t1_monitor_loop()),
                asyncio.create_task(self._t3_monitor_loop()),
            ]
            self._tasks = tasks
            await asyncio.gather(*tasks)

        except (ConnectionError, OSError) as e:
            logger.error(f"Conexao perdida: {e}")
            self.state = SessionState.ERROR
            raise
        except asyncio.CancelledError:
            pass
        finally:
            await self._close_connection()

    async def _close_connection(self):
        if self.data_transfer_active and self.writer and not self.writer.is_closing():
            try:
                # Cortesia: avisa o slave com STOPDT_ACT, mas sem esperar o CON -
                # na pausa o receive loop ja foi cancelado e o CON nunca seria lido,
                # entao qualquer espera aqui e tempo perdido no desligamento.
                await self._send_frame(
                    APDUCodec.encode_u_frame(STOPDT_ACT), "STOPDT_ACT"
                )
            except Exception:
                pass
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.close()
                await asyncio.wait_for(self.writer.wait_closed(), timeout=0.5)
            except Exception:
                # fecha na marra se o wait_closed nao retornar rapido
                try:
                    self.writer.transport.abort()
                except Exception:
                    pass
        self.state = SessionState.STOPPED

    async def _send_frame(self, frame: bytes, desc: str = ""):
        if self.writer and not self.writer.is_closing():
            try:
                sent = await self._net.apply(self.writer, frame)
                if sent:
                    self.stats.record_sent(len(frame))
                    if desc:
                        logger.debug(f"  TX -> {desc} ({len(frame)} B)")
                else:
                    self.stats.record_dropped()
            except (ConnectionError, OSError):
                self.stats.record_error()
                raise

    async def _send_i_frame(self, asdu: ASDU, desc: str = "", track_rtt: bool = False):
        # bloqueia envio se janela k estiver cheia (norma secao 5.1)
        blocked = False
        while (self.ssn - self._peer_rsn) % 32768 >= self._k:
            if not blocked:
                self.stats.record_window_block()
                blocked = True
            await asyncio.sleep(0.1)
            if self._stop_event.is_set():
                return
        frame = APDUCodec.encode_i_frame(self.ssn, self.rsn, asdu)
        if track_rtt:
            self._pending_cmds[self.ssn] = time.time()
        await self._send_frame(frame, desc or f"I SSN={self.ssn}")
        if (self.ssn - self._peer_rsn) % 32768 == 0:
            self._t1_start = time.time()
        self.ssn = (self.ssn + 1) % 32768

    async def _send_s_frame(self):
        frame = APDUCodec.encode_s_frame(self.rsn)
        await self._send_frame(frame, f"S-frame RSN={self.rsn}")
        self._ack_pending = 0
        self._ack_since = None

    async def _activate_data_transfer(self):
        logger.info("Enviando STARTDT_ACT...")
        await self._send_frame(
            APDUCodec.encode_u_frame(STARTDT_ACT), "STARTDT_ACT"
        )
        confirmed = False
        deadline = time.time() + self.t1

        while time.time() < deadline and not confirmed:
            try:
                data = await asyncio.wait_for(
                    self.reader.read(4096), timeout=2.0
                )
            except asyncio.TimeoutError:
                continue
            if not data:
                raise ConnectionError("Conexao fechada pelo slave")

            self._recv_buffer += data
            while True:
                frame, self._recv_buffer = APDUCodec.read_frame_from_buffer(
                    self._recv_buffer
                )
                if frame is None:
                    break
                self.stats.record_received(len(frame))
                decoded = APDUCodec.decode_frame(frame)
                if decoded["type"] == "U" and decoded["function"] == STARTDT_CON:
                    confirmed = True
                    self.data_transfer_active = True
                    self.state = SessionState.ACTIVE
                    self._last_rx_time = time.time()
                    logger.info("STARTDT confirmado - transferencia ATIVA")
                    break
                else:
                    await self._process_received_frame(decoded)

        if not confirmed:
            raise ConnectionError("Timeout aguardando STARTDT_CON")

    async def _receive_loop(self):
        while not self._stop_event.is_set():
            try:
                data = await asyncio.wait_for(
                    self.reader.read(4096), timeout=self.t1 + 5
                )
            except asyncio.TimeoutError:
                continue
            except (ConnectionError, OSError):
                break
            if not data:
                break

            self._recv_buffer += data
            while True:
                frame, self._recv_buffer = APDUCodec.read_frame_from_buffer(
                    self._recv_buffer
                )
                if frame is None:
                    break
                self.stats.record_received(len(frame))
                decoded = APDUCodec.decode_frame(frame)
                await self._process_received_frame(decoded)

    async def _process_received_frame(self, decoded: dict):
        ftype = decoded["type"]

        if ftype == "U":
            self._last_rx_time = time.time()
            func = decoded["function"]
            fname = decoded["function_name"]
            logger.debug(f"  RX <- U-frame: {fname}")
            if func == TESTFR_ACT:
                await self._send_frame(
                    APDUCodec.encode_u_frame(TESTFR_CON), "TESTFR_CON"
                )
            elif func == TESTFR_CON:
                pass  # heartbeat confirmado
            elif func == STOPDT_CON:
                self.data_transfer_active = False
                self._stopdt_con_event.set()
            elif func == STOPDT_ACT:
                await self._send_frame(
                    APDUCodec.encode_u_frame(STOPDT_CON), "STOPDT_CON"
                )
                self.data_transfer_active = False

        elif ftype == "S":
            rsn = decoded["rsn"]
            logger.debug(f"  RX <- S-frame RSN={rsn}")
            self._last_rx_time = time.time()
            self._update_peer_ack(rsn)

        elif ftype == "I":
            ssn = decoded["ssn"]
            rsn = decoded["rsn"]
            asdu_info = decoded.get("asdu")
            self.rsn = (ssn + 1) % 32768
            if self._ack_pending == 0:
                self._ack_since = time.time()   # inicia o relogio T2 do 1o pendente
            self._ack_pending += 1
            self._last_rx_time = time.time()
            self._update_peer_ack(rsn)

            if asdu_info:
                self.stats.record_asdu_type(asdu_info["type_id"])
                self._log_received_asdu(asdu_info, ssn)
                self._check_rtt(asdu_info)
                self._update_last_values(asdu_info, decoded.get("objects"))

            if self._ack_pending >= self._w:
                await self._send_s_frame()

    def _check_rtt(self, asdu_info: dict):
        """Calcula RTT quando recebe confirmacao de comando."""
        type_id = asdu_info.get("type_id", 0)
        cause = asdu_info.get("cause", 0)
        if (type_id in (TypeID.C_SC_NA_1, TypeID.C_IC_NA_1, TypeID.C_CS_NA_1)
                and cause == CauseOfTransmission.ACTIVATION_CON):
            if self._pending_cmds:
                # usa o comando mais antigo pendente como referencia
                oldest_ssn = min(self._pending_cmds)
                send_time = self._pending_cmds.pop(oldest_ssn)
                rtt_ms = (time.time() - send_time) * 1000
                self.stats.record_rtt(rtt_ms)
                logger.debug(f"  RTT medido: {rtt_ms:.1f}ms (cmd SSN={oldest_ssn})")

    def _update_last_values(self, asdu_info: dict, objects: Optional[list]):
        """Guarda o ultimo valor recebido por IOA, para inspecao via console (comando 'dp')."""
        if not objects:
            return
        for obj in objects:
            ioa = obj.get("ioa")
            if ioa is None:
                continue
            self.last_values[ioa] = {
                "value": obj.get("value"),
                "type_name": asdu_info.get("type_name", "?"),
                "cause_name": asdu_info.get("cause_name", "?"),
                "ts": time.time(),
            }

    def _log_received_asdu(self, asdu_info: dict, ssn: int):
        type_name = asdu_info.get("type_name", "?")
        cause = asdu_info.get("cause", 0)
        cause_name = asdu_info.get("cause_name", "?")
        n = asdu_info.get("num_objects", 0)
        type_id = asdu_info.get("type_id", 0)

        if cause == CauseOfTransmission.SPONTANEOUS:
            logger.info(f"  << Evento espontaneo: {type_name} x{n}")
        elif cause == CauseOfTransmission.PERIODIC:
            logger.debug(f"  << Scan periodico: {type_name} x{n}")
        elif type_id == TypeID.M_EI_NA_1:
            logger.info("  << Slave inicializado (End of Init)")
        elif cause == CauseOfTransmission.INTERROGATED_STATION:
            logger.info(f"  << GI Response: {type_name} x{n}")
        elif type_id == TypeID.C_IC_NA_1:
            if cause == CauseOfTransmission.ACTIVATION_CON:
                logger.info("  << GI - ACK")
            elif cause == CauseOfTransmission.ACTIVATION_TERMINATION:
                logger.info("  << GI - Completa")
        elif type_id == TypeID.C_CI_NA_1:
            if cause == CauseOfTransmission.ACTIVATION_CON:
                logger.info("  << CI - ACK")
            elif cause == CauseOfTransmission.ACTIVATION_TERMINATION:
                logger.info("  << CI - Completa")
        elif type_id == TypeID.M_IT_NA_1:
            logger.info(f"  << Contadores: {type_name} x{n}")
        elif type_id == TypeID.C_SC_NA_1:
            if cause == CauseOfTransmission.ACTIVATION_CON:
                logger.info("  << Comando confirmado")
            elif cause == CauseOfTransmission.ACTIVATION_TERMINATION:
                logger.info("  << Comando executado")
        elif type_id == TypeID.C_CS_NA_1:
            logger.info("  << Clock Sync ACK")
        elif cause == CauseOfTransmission.REQUEST:
            logger.info(f"  << Read Response: {type_name} x{n}")
        else:
            logger.debug(f"  RX <- I SSN={ssn}: {type_name} COT={cause_name} x{n}")

    async def _testfr_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.testfr_interval)
            if not self.data_transfer_active:
                continue
            logger.info("Heartbeat: TESTFR_ACT")
            await self._send_frame(
                APDUCodec.encode_u_frame(TESTFR_ACT), "TESTFR_ACT"
            )

    async def _gi_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.gi_interval)
            if not self.data_transfer_active:
                continue
            await self._send_general_interrogation()

    async def _command_loop(self):
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.command_interval)
            await asyncio.sleep(interval)
            if not self.data_transfer_active or not self.command_ioas:
                continue
            ioa = random.choice(self.command_ioas)
            value = random.choice([True, False])
            await self._send_single_command(ioa, value)

    async def _clock_sync_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.clock_sync_interval)
            if not self.data_transfer_active:
                continue
            await self._send_clock_sync()

    async def _s_frame_timer_loop(self):
        # T2: confirma os I-frames pendentes ate T2 segundos apos o 1o nao confirmado
        # (contado desde _ack_since, nao desde o ultimo frame recebido - senao, com
        # trafego continuo, o T2 nunca dispararia e o outro lado cairia por T1).
        while not self._stop_event.is_set():
            await asyncio.sleep(0.5)
            if (self._ack_pending > 0
                    and self._ack_since is not None
                    and time.time() - self._ack_since >= self.t2):
                await self._send_s_frame()

    async def _t1_monitor_loop(self):
        # T1: desconecta se I-frames enviados ficarem sem ACK por mais de T1 segundos
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            if (self._t1_start is not None
                    and time.time() - self._t1_start >= self.t1):
                self.stats.record_t1_timeout()
                logger.warning("T1 timeout - I-frames sem confirmacao, desconectando")
                raise ConnectionError("T1 timeout")

    async def _t3_monitor_loop(self):
        # T3: envia TESTFR_ACT se nenhum frame for recebido por T3 segundos
        while not self._stop_event.is_set():
            await asyncio.sleep(1)
            if (self._last_rx_time is not None
                    and time.time() - self._last_rx_time >= self.t3):
                logger.debug("[Master] T3: enviando TESTFR_ACT por inatividade")
                await self._send_frame(
                    APDUCodec.encode_u_frame(TESTFR_ACT), "TESTFR_ACT (T3)"
                )

    async def _send_general_interrogation(self):
        logger.info(">> Enviando Interrogacao Geral...")
        asdu = ASDU(
            type_id=TypeID.C_IC_NA_1,
            cause=CauseOfTransmission.ACTIVATION,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, value=20)],
        )
        await self._send_i_frame(asdu, "General Interrogation", track_rtt=True)

    async def _send_single_command(self, ioa: int, value: bool):
        logger.info(f">> Enviando comando: IOA={ioa} valor={value}")
        asdu = ASDU(
            type_id=TypeID.C_SC_NA_1,
            cause=CauseOfTransmission.ACTIVATION,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=ioa, value=value)],
        )
        await self._send_i_frame(asdu, f"Cmd IOA={ioa}={value}", track_rtt=True)

    async def _send_clock_sync(self):
        logger.info(">> Enviando Clock Sync...")
        asdu = ASDU(
            type_id=TypeID.C_CS_NA_1,
            cause=CauseOfTransmission.ACTIVATION,
            common_address=self.common_address,
            originator=self.originator,
            objects=[InformationObject(ioa=0, timestamp=CP56Time2a.now())],
        )
        await self._send_i_frame(asdu, "Clock Sync", track_rtt=True)
