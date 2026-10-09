"""
DNP3 (IEEE 1815-2012) - Outstation (RTU / IED de campo).

Servidor TCP na porta 20000 que mantem a base de dados estatica, gera eventos
por classe (1-3) e responde a Integrity Poll, leituras por classe, comandos
(SBO e Direct Operate) e sincronismo de relogio.

Multi-cliente: um mesmo outstation pode servir varios masters simultaneamente.
Cada conexao mantem seu proprio estado de sessao (buffers, sequencias app/
transport, fila de eventos, SBO e IIN), enquanto a base de dados (points) e as
estatisticas sao compartilhadas. Os eventos gerados pelo scan e pelos comandos
sao transmitidos (broadcast) para a fila de todas as conexoes ativas.
"""

import asyncio
import math
import random
import time
import logging
from typing import Dict, List, Optional, Set

from .frames import (
    DNP3Codec, DNP3Object, ObjectHeader,
    FunctionCode, ObjectGroup, LinkFunction,
    DEFAULT_PORT, CLASS_VARIATIONS,
    VAR_BI_FLAGS, VAR_BI_EVENT_TIME, VAR_BO_FLAGS, VAR_COUNTER_32,
    VAR_AI_SHORT_FLOAT, VAR_AI_EVENT_FLOAT,
    QUAL_8BIT_START_STOP, QUAL_8BIT_COUNT_INDEX,
    FLAG_ONLINE, IIN_CLASS1_EVENTS, IIN_CLASS2_EVENTS, IIN_CLASS3_EVENTS,
    IIN_DEVICE_RESTART, IIN_NEED_TIME,
    CROB_LATCH_ON, CROB_LATCH_OFF, CROB_PULSE_ON, CROB_CLOSE, CROB_TRIP,
)
from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.network_conditions import NetworkConditions
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.dnp3.outstation")


class DNP3Point:
    """Ponto da base de dados do outstation, com modelo fisico de variacao."""

    def __init__(self, index: int, group: int, value=None, event_class: int = 1,
                 vmin: float = 0.0, vmax: float = 100.0,
                 noise: float = 1.0, drift: float = 0.0,
                 change_prob: float = 0.02, deadband: float = 0.01):
        self.index = index
        self.group = group
        self.value = value
        self.event_class = event_class
        self.min = vmin
        self.max = vmax
        self.noise = noise
        self.drift = drift
        self.change_prob = change_prob
        self.deadband = deadband
        self.flags = FLAG_ONLINE
        self.last_reported = value
        self._phase = random.random() * math.tau
        self._t0 = time.time()
        # Binding ao modelo de processo (Passo 2).
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
        if self._model is not None and self._model_input:
            self._model.command(self._model_signal, v)
        else:
            self.value = v

    def update(self) -> bool:
        """Atualiza o valor. Devolve True se passou do deadband (gera evento)."""
        if self._model_signal is not None:
            return self._exceeds_deadband()   # amarrado ao modelo: nao randomiza
        if self.group == ObjectGroup.BINARY_INPUT:
            if random.random() < self.change_prob:
                self.value = not self.value
        elif self.group == ObjectGroup.COUNTER:
            if random.random() < self.change_prob:
                self.value = (int(self.value or 0) + random.randint(1, 5)) & 0xFFFFFFFF
        elif self.group == ObjectGroup.ANALOG_INPUT:
            t = time.time() - self._t0
            base = (self.min + self.max) / 2.0
            span = (self.max - self.min) / 2.0
            v = (base
                 + span * 0.6 * math.sin(t * 0.05 + self._phase)
                 + random.gauss(0, self.noise * 0.3)
                 + self.drift * math.sin(t * 0.001))
            self.value = max(self.min, min(self.max, v))

        return self._exceeds_deadband()

    def _exceeds_deadband(self) -> bool:
        if self.last_reported is None:
            return True
        if isinstance(self.value, bool):
            return self.value != self.last_reported
        try:
            limiar = abs(self.max - self.min) * self.deadband
            return abs(float(self.value) - float(self.last_reported)) > limiar
        except (TypeError, ValueError):
            return self.value != self.last_reported

    def mark_reported(self):
        self.last_reported = self.value


class _OutstationConn:
    """Estado de sessao de UMA conexao de master (independente das demais)."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 master_address: int, iin_init: int):
        self.reader = reader
        self.writer = writer
        self.peername = writer.get_extra_info("peername")
        self.master_addr = master_address     # atualizado com o src dos frames recebidos
        self.recv_buffer = b''
        self.rx_fragment = b''
        self.app_seq = 0
        self.transport_seq = 0
        self.iin = iin_init
        # fila de eventos por classe (1, 2 e 3), propria desta conexao
        self.events: Dict[int, List[tuple]] = {1: [], 2: [], 3: []}
        # pontos selecionados por SELECT aguardando OPERATE (SBO), proprios desta conexao
        self.selected: Dict[int, tuple] = {}
        self.closed = False
        self.tasks: List[asyncio.Task] = []


class DNP3Outstation(BaseProtocolGenerator):
    """Simulador de outstation DNP3 (RTU), multi-cliente."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        # Interface de bind (ver slave IEC 104): bind: 0.0.0.0 para multi-maquina.
        self.bind = config.get("bind", self.host)
        self.port = config.get("port", DEFAULT_PORT)
        self.address = config.get("address", 4)          # endereco do outstation
        self.master_address = config.get("master_address", 1)

        self.event_scan_interval = config.get("event_scan_interval", 2.0)
        self.unsolicited_enabled = config.get("unsolicited_enabled", True)
        self.unsolicited_interval = config.get("unsolicited_interval", 10.0)
        self.max_events_per_response = config.get("max_events_per_response", 20)

        self.server: Optional[asyncio.AbstractServer] = None
        self._conns: Set[_OutstationConn] = set()

        self.points: List[DNP3Point] = self._build_points(config.get("points"))
        logger.info(f"Outstation DNP3 com {len(self.points)} pontos")

        self._net = NetworkConditions.from_config(config.get("network_conditions", {}))
        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @staticmethod
    def _iin_init() -> int:
        return IIN_DEVICE_RESTART | IIN_NEED_TIME

    def _active_conns(self) -> List["_OutstationConn"]:
        return [c for c in self._conns if not c.closed]

    @property
    def protocol_name(self) -> str:
        return "DNP3-Outstation"

    # ------------------------------------------------------------------
    # Base de dados
    # ------------------------------------------------------------------
    def _build_points(self, cfg) -> List[DNP3Point]:
        if cfg:
            return [
                DNP3Point(
                    index=p["index"], group=p.get("group", ObjectGroup.ANALOG_INPUT),
                    value=p.get("value"), event_class=p.get("class", 1),
                    vmin=p.get("min", 0.0), vmax=p.get("max", 100.0),
                    noise=p.get("noise", 1.0), drift=p.get("drift", 0.0),
                    change_prob=p.get("change_prob", 0.02),
                    deadband=p.get("deadband", 0.01),
                )
                for p in cfg
            ]

        # Subestacao padrao, espelhando os datapoints do IEC 104
        G_BI, G_AI, G_CT, G_BO = (ObjectGroup.BINARY_INPUT, ObjectGroup.ANALOG_INPUT,
                                  ObjectGroup.COUNTER, ObjectGroup.BINARY_OUTPUT)
        return [
            DNP3Point(0, G_BI, False, event_class=1, change_prob=0.02),   # Disjuntor 1
            DNP3Point(1, G_BI, True,  event_class=1, change_prob=0.02),   # Disjuntor 2
            DNP3Point(2, G_BI, True,  event_class=1, change_prob=0.02),   # Disjuntor 3
            DNP3Point(3, G_BI, False, event_class=2, change_prob=0.01),   # Seccionadora
            DNP3Point(0, G_AI, 138.5, event_class=2, vmin=120.0, vmax=145.0, noise=2.0, drift=0.5),
            DNP3Point(1, G_AI, 69.0,  event_class=2, vmin=60.0,  vmax=75.0,  noise=1.5, drift=0.3),
            DNP3Point(2, G_AI, 420.0, event_class=2, vmin=0.0,   vmax=800.0, noise=25.0),
            DNP3Point(3, G_AI, 310.0, event_class=2, vmin=0.0,   vmax=600.0, noise=20.0),
            DNP3Point(4, G_AI, 55.0,  event_class=3, vmin=0.0,   vmax=100.0, noise=5.0),
            DNP3Point(0, G_CT, 10520, event_class=3, change_prob=0.30),   # Energia ativa
            DNP3Point(1, G_CT, 3140,  event_class=3, change_prob=0.20),   # Energia reativa
            DNP3Point(0, G_BO, False, event_class=1, change_prob=0.0),    # Rele 1
            DNP3Point(1, G_BO, False, event_class=1, change_prob=0.0),    # Rele 2
        ]

    def _by_group(self, group: int) -> List[DNP3Point]:
        return [p for p in self.points if p.group == group]

    def _find(self, group: int, index: int) -> Optional[DNP3Point]:
        return next((p for p in self.points
                     if p.group == group and p.index == index), None)

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self._conns = set()
        self.server = await asyncio.start_server(
            self._handle_connection, self.bind, self.port
        )
        addr = self.server.sockets[0].getsockname()
        self.state = SessionState.CONNECTED
        logger.info(f"Outstation DNP3 ouvindo em {addr[0]}:{addr[1]}")

        # O scan de eventos e compartilhado por todas as conexoes.
        self._tasks = [asyncio.create_task(self._event_scan_loop())]

        # Sem "async with self.server" (mesmo motivo do slave IEC 104): evitar
        # que a saida do bloco chame wait_closed() no caminho de cancelamento.
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
                await asyncio.wait_for(self.server.wait_closed(), timeout=2)
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
            try:
                conn.writer.transport.abort()
            except Exception:
                pass

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                 writer: asyncio.StreamWriter):
        conn = _OutstationConn(reader, writer, self.master_address, self._iin_init())
        self._conns.add(conn)
        logger.info(f"Master DNP3 conectado: {conn.peername}  "
                    f"({len(self._conns)} conexao(es))")

        conn.tasks = [asyncio.create_task(self._receive_loop(conn))]
        if self.unsolicited_enabled:
            conn.tasks.append(asyncio.create_task(self._unsolicited_loop(conn)))

        try:
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
            logger.info(f"Master DNP3 desconectado: {conn.peername}")
            try:
                writer.close()
                await asyncio.wait_for(writer.wait_closed(), timeout=2)
            except Exception:
                try:
                    writer.transport.abort()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # I/O (por conexao)
    # ------------------------------------------------------------------
    async def _send_link_frame(self, conn: _OutstationConn, payload: bytes, desc: str = ""):
        frame = DNP3Codec.encode_link_frame(
            payload, dest=conn.master_addr, src=self.address,
            func=LinkFunction.UNCONFIRMED_USER, prm=True,
        )
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

    async def _send_app(self, conn: _OutstationConn, fragment: bytes, desc: str = ""):
        for pdu in DNP3Codec.encode_transport(fragment, conn.transport_seq):
            await self._send_link_frame(conn, pdu, desc)
            conn.transport_seq = (conn.transport_seq + 1) % 64

    async def _receive_loop(self, conn: _OutstationConn):
        while not self._stop_event.is_set() and not conn.closed:
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
                frame, conn.recv_buffer = DNP3Codec.read_frame_from_buffer(
                    conn.recv_buffer
                )
                if frame is None:
                    break
                self.stats.record_received(len(frame))
                await self._process_frame(conn, frame)

    async def _process_frame(self, conn: _OutstationConn, frame: bytes):
        link = DNP3Codec.decode_link_frame(frame)
        if link is None:
            return
        if link.get("crc_error"):
            self.stats.record_error()
            logger.warning("  RX <- frame com erro de CRC, descartado")
            return

        conn.master_addr = link.get("src", conn.master_addr)   # responde ao master certo
        tp = DNP3Codec.decode_transport(link["payload"])
        if tp["fir"]:
            conn.rx_fragment = b''
        conn.rx_fragment += tp["data"]
        if not tp["fin"]:
            return                                   # aguarda os proximos segmentos

        app = DNP3Codec.decode_app(conn.rx_fragment)
        conn.rx_fragment = b''
        if app is None:
            return

        self.stats.record_asdu_type(app["func"])
        logger.info(f"  RX <- {app['func_name']} seq={app['seq']}")
        await self._dispatch(conn, app)

    # ------------------------------------------------------------------
    # Tratamento de requisicoes
    # ------------------------------------------------------------------
    async def _dispatch(self, conn: _OutstationConn, app: dict):
        func = app["func"]
        conn.app_seq = app["seq"]

        if func == FunctionCode.READ:
            await self._handle_read(conn, app)
        elif func in (FunctionCode.DIRECT_OPERATE, FunctionCode.DIRECT_OP_NR):
            await self._handle_operate(conn, app, direct=True)
        elif func == FunctionCode.SELECT:
            await self._handle_select(conn, app)
        elif func == FunctionCode.OPERATE:
            await self._handle_operate(conn, app, direct=False)
        elif func == FunctionCode.WRITE:
            await self._handle_write(conn, app)
        elif func == FunctionCode.DELAY_MEASURE:
            await self._send_response(conn, [], desc="DELAY MEASURE ACK")
        elif func == FunctionCode.CONFIRM:
            pass
        else:
            logger.debug(f"  Funcao nao tratada: {app['func_name']}")

    async def _handle_read(self, conn: _OutstationConn, app: dict):
        classes = []
        static = False
        for h in app["headers"]:
            if h.group == ObjectGroup.CLASS_OBJECTS:
                cls = next((c for c, v in CLASS_VARIATIONS.items()
                            if v == h.variation), None)
                if cls == 0:
                    static = True
                elif cls is not None:
                    classes.append(cls)
            else:
                static = True

        if static:
            logger.info("  >> Integrity Poll (Class 0): despejando base estatica")
            await self._send_static_response(conn)
        if classes:
            logger.info(f"  >> Leitura de eventos das classes {classes}")
            await self._send_event_response(conn, classes)

    async def _send_static_response(self, conn: _OutstationConn):
        headers = []
        for group, variation in (
            (ObjectGroup.BINARY_INPUT, VAR_BI_FLAGS),
            (ObjectGroup.ANALOG_INPUT, VAR_AI_SHORT_FLOAT),
            (ObjectGroup.COUNTER, VAR_COUNTER_32),
            (ObjectGroup.BINARY_OUTPUT, VAR_BO_FLAGS),
        ):
            pts = self._by_group(group)
            if not pts:
                continue
            headers.append(ObjectHeader(
                group, variation, QUAL_8BIT_START_STOP,
                [DNP3Object(index=p.index, value=p.value, flags=p.flags) for p in pts],
            ))
            for p in pts:
                p.mark_reported()
        await self._send_response(conn, headers, desc="Integrity Poll Response")

    async def _send_event_response(self, conn: _OutstationConn, classes: List[int]):
        headers, total = [], 0
        for cls in sorted(classes):
            buf = conn.events.get(cls, [])
            if not buf:
                continue
            take = buf[:self.max_events_per_response - total]
            del buf[:len(take)]
            total += len(take)
            for group, variation, obj in take:
                if headers and headers[-1].group == group and headers[-1].variation == variation:
                    headers[-1].objects.append(obj)
                else:
                    headers.append(ObjectHeader(group, variation,
                                                QUAL_8BIT_COUNT_INDEX, [obj]))
            if total >= self.max_events_per_response:
                break

        self._refresh_iin(conn)
        desc = f"Event Response x{total}" if total else "Null Response (sem eventos)"
        await self._send_response(conn, headers, desc=desc)

    async def _handle_select(self, conn: _OutstationConn, app: dict):
        for h in app["headers"]:
            for o in h.objects:
                conn.selected[o.index] = (h.group, o.value, time.time())
                logger.info(f"  >> SELECT ponto {o.index} codigo=0x{(o.value or 0):02X}")
        await self._send_response(conn, app["headers"], desc="SELECT Response (SBO)",
                                  echo=True)

    async def _handle_operate(self, conn: _OutstationConn, app: dict, direct: bool):
        for h in app["headers"]:
            for o in h.objects:
                if not direct:
                    sel = conn.selected.pop(o.index, None)
                    if sel is None or time.time() - sel[2] > 10:
                        logger.warning(f"  OPERATE sem SELECT valido no ponto {o.index}")
                        continue
                self._apply_command(o.index, o.value)
        await self._send_response(conn, app["headers"],
                                  desc="DIRECT OPERATE Response" if direct
                                  else "OPERATE Response (SBO)", echo=True)

    def _apply_command(self, index: int, code) -> None:
        pt = self._find(ObjectGroup.BINARY_OUTPUT, index)
        if pt is None:
            return
        if code in (CROB_LATCH_ON, CROB_CLOSE, CROB_PULSE_ON):
            pt.value = True
        elif code in (CROB_LATCH_OFF, CROB_TRIP):
            pt.value = False
        logger.info(f"  >> Comando aplicado: BO[{index}] = {pt.value}")
        # o efeito do comando e visivel a todos os masters
        self._broadcast_event(pt, ObjectGroup.BINARY_INPUT_EVENT, VAR_BI_EVENT_TIME)

    async def _handle_write(self, conn: _OutstationConn, app: dict):
        for h in app["headers"]:
            if h.group == ObjectGroup.TIME_AND_DATE:
                conn.iin &= ~IIN_NEED_TIME
                logger.info("  >> Relogio sincronizado pelo master")
        conn.iin &= ~IIN_DEVICE_RESTART
        await self._send_response(conn, [], desc="WRITE Response")

    async def _send_response(self, conn: _OutstationConn, headers,
                             desc: str = "", echo: bool = False):
        self._refresh_iin(conn)
        fragment = DNP3Codec.encode_app_response(
            FunctionCode.RESPONSE, conn.app_seq, conn.iin, headers,
        )
        await self._send_app(conn, fragment, desc)

    def _refresh_iin(self, conn: _OutstationConn):
        for bit, cls in ((IIN_CLASS1_EVENTS, 1), (IIN_CLASS2_EVENTS, 2),
                         (IIN_CLASS3_EVENTS, 3)):
            if conn.events[cls]:
                conn.iin |= bit
            else:
                conn.iin &= ~bit

    # ------------------------------------------------------------------
    # Geracao de eventos (broadcast para todas as conexoes ativas)
    # ------------------------------------------------------------------
    def _broadcast_event(self, pt: DNP3Point, group: int, variation: int):
        obj = DNP3Object(index=pt.index, value=pt.value, flags=pt.flags,
                         timestamp=int(time.time() * 1000))
        for conn in self._active_conns():
            buf = conn.events.setdefault(pt.event_class, [])
            buf.append((group, variation, obj))
            # limita o buffer para nao crescer sem limite se o master nao ler
            if len(buf) > 200:
                del buf[:-200]

    async def _event_scan_loop(self):
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.event_scan_interval)
            await asyncio.sleep(interval)
            if not self._active_conns():
                # o processo fisico evolui, mas sem master nao ha para quem enfileirar
                for pt in self.points:
                    if pt.group != ObjectGroup.BINARY_OUTPUT:
                        pt.update()
                continue

            novos = 0
            for pt in self.points:
                if pt.group == ObjectGroup.BINARY_OUTPUT:
                    continue
                if pt.update():
                    if pt.group == ObjectGroup.BINARY_INPUT:
                        self._broadcast_event(pt, ObjectGroup.BINARY_INPUT_EVENT,
                                              VAR_BI_EVENT_TIME)
                    elif pt.group == ObjectGroup.ANALOG_INPUT:
                        self._broadcast_event(pt, ObjectGroup.ANALOG_INPUT_EVENT,
                                              VAR_AI_EVENT_FLOAT)
                    elif pt.group == ObjectGroup.COUNTER:
                        self._broadcast_event(pt, ObjectGroup.COUNTER_EVENT,
                                              VAR_COUNTER_32)
                    pt.mark_reported()
                    novos += 1
            if novos:
                for conn in self._active_conns():
                    self._refresh_iin(conn)
                logger.debug(f"  {novos} evento(s) enfileirado(s) p/ {len(self._conns)} conexao(es)")

    async def _unsolicited_loop(self, conn: _OutstationConn):
        """Envia eventos espontaneamente para ESTA conexao, sem esperar o poll."""
        while not self._stop_event.is_set() and not conn.closed:
            await asyncio.sleep(self.unsolicited_interval)
            pendentes = sum(len(v) for v in conn.events.values())
            if not pendentes:
                continue

            headers, total = [], 0
            for cls in (1, 2, 3):
                buf = conn.events[cls]
                take = buf[:self.max_events_per_response - total]
                del buf[:len(take)]
                total += len(take)
                for group, variation, obj in take:
                    if headers and headers[-1].group == group:
                        headers[-1].objects.append(obj)
                    else:
                        headers.append(ObjectHeader(group, variation,
                                                    QUAL_8BIT_COUNT_INDEX, [obj]))
                if total >= self.max_events_per_response:
                    break

            self._refresh_iin(conn)
            fragment = DNP3Codec.encode_app_response(
                FunctionCode.UNSOLICITED_RESP, conn.app_seq, conn.iin,
                headers, con=True, uns=True,
            )
            logger.info(f"  << Unsolicited Response x{total}")
            await self._send_app(conn, fragment, f"Unsolicited x{total}")
