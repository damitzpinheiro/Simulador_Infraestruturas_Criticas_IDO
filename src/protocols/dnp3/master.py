"""
DNP3 (IEEE 1815-2012) - Master (Estacao de controle SCADA).

Cliente TCP que faz Integrity Poll (Class 0), varreduras de eventos por classe
(1-3), comandos com Select-Before-Operate e sincronismo de relogio, alem de
receber Unsolicited Responses do outstation.
"""

import asyncio
import random
import time
import logging
from typing import Dict, Optional

from .frames import (
    DNP3Codec, DNP3Object, ObjectHeader,
    FunctionCode, ObjectGroup, LinkFunction,
    DEFAULT_PORT, CLASS_VARIATIONS,
    QUAL_ALL_POINTS, QUAL_8BIT_COUNT_INDEX,
    GROUP_NAMES, CROB_LATCH_ON, CROB_LATCH_OFF,
    IIN_CLASS1_EVENTS, IIN_CLASS2_EVENTS, IIN_CLASS3_EVENTS,
)
from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.network_conditions import NetworkConditions
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.dnp3.master")


class DNP3Master(BaseProtocolGenerator):
    """Simulador de master DNP3 (centro de controle)."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", DEFAULT_PORT)
        self.address = config.get("address", 1)
        self.outstation_address = config.get("outstation_address", 4)

        self.startup_delay = config.get("startup_delay", 2.0)
        self.integrity_interval = config.get("integrity_interval", 60.0)
        self.event_poll_interval = config.get("event_poll_interval", 5.0)
        self.command_interval = config.get("command_interval", 20.0)
        self.clock_sync_interval = config.get("clock_sync_interval", 120.0)
        self.command_indexes = config.get("command_indexes", [0, 1])
        self.use_sbo = config.get("use_sbo", True)

        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._recv_buffer = b''
        self._rx_fragment = b''

        self._app_seq = 0
        self._transport_seq = 0
        self._last_iin = 0
        # Rastreamento de RTT: seq da requisicao -> instante do envio
        self._pending: Dict[int, float] = {}

        self._net = NetworkConditions.from_config(config.get("network_conditions", {}))
        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @property
    def protocol_name(self) -> str:
        return "DNP3-Master"

    def _next_seq(self) -> int:
        self._app_seq = (self._app_seq + 1) % 16
        return self._app_seq

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self._recv_buffer = b''
        self._rx_fragment = b''
        self._pending.clear()

        await asyncio.sleep(self.startup_delay)

        logger.info(f"Conectando ao outstation em {self.host}:{self.port}...")
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.state = SessionState.CONNECTED
        logger.info("Conectado ao outstation DNP3")

        try:
            # Alinhamento inicial: varredura estatica completa
            await asyncio.sleep(0.3)
            await self._send_integrity_poll()
            await asyncio.sleep(0.3)
            await self._send_clock_sync()

            self.state = SessionState.ACTIVE
            tasks = [
                asyncio.create_task(self._receive_loop()),
                asyncio.create_task(self._integrity_loop()),
                asyncio.create_task(self._event_poll_loop()),
                asyncio.create_task(self._command_loop()),
                asyncio.create_task(self._clock_sync_loop()),
            ]
            self._tasks = tasks
            await asyncio.gather(*tasks)

        except (ConnectionError, OSError) as e:
            logger.error(f"Conexao DNP3 perdida: {e}")
            self.state = SessionState.ERROR
            raise
        except asyncio.CancelledError:
            pass
        finally:
            await self._close()

    async def _close(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            try:
                await asyncio.wait_for(self.writer.wait_closed(), timeout=5)
            except Exception:
                pass
        self.state = SessionState.STOPPED

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------
    async def _send_link_frame(self, payload: bytes, desc: str = ""):
        frame = DNP3Codec.encode_link_frame(
            payload, dest=self.outstation_address, src=self.address,
            func=LinkFunction.UNCONFIRMED_USER, prm=True,
        )
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

    async def _send_app(self, fragment: bytes, desc: str = "", seq: Optional[int] = None):
        if seq is not None:
            self._pending[seq] = time.time()
        for pdu in DNP3Codec.encode_transport(fragment, self._transport_seq):
            await self._send_link_frame(pdu, desc)
            self._transport_seq = (self._transport_seq + 1) % 64

    async def _receive_loop(self):
        while not self._stop_event.is_set():
            try:
                data = await asyncio.wait_for(self.reader.read(4096), timeout=30)
            except asyncio.TimeoutError:
                continue
            except (ConnectionError, OSError):
                break
            if not data:
                break

            self._recv_buffer += data
            while True:
                frame, self._recv_buffer = DNP3Codec.read_frame_from_buffer(
                    self._recv_buffer
                )
                if frame is None:
                    break
                self.stats.record_received(len(frame))
                await self._process_frame(frame)

    async def _process_frame(self, frame: bytes):
        link = DNP3Codec.decode_link_frame(frame)
        if link is None:
            return
        if link.get("crc_error"):
            self.stats.record_error()
            logger.warning("  RX <- frame com erro de CRC, descartado")
            return

        tp = DNP3Codec.decode_transport(link["payload"])
        if tp["fir"]:
            self._rx_fragment = b''
        self._rx_fragment += tp["data"]
        if not tp["fin"]:
            return

        app = DNP3Codec.decode_app(self._rx_fragment)
        self._rx_fragment = b''
        if app is None:
            return

        self.stats.record_asdu_type(app["func"])
        self._check_rtt(app)
        self._log_response(app)

        if app["func"] == FunctionCode.UNSOLICITED_RESP and app["con"]:
            await self._send_confirm(app["seq"], uns=True)

    def _check_rtt(self, app: dict):
        if app["func"] != FunctionCode.RESPONSE:
            return
        t0 = self._pending.pop(app["seq"], None)
        if t0 is not None:
            rtt_ms = (time.time() - t0) * 1000
            self.stats.record_rtt(rtt_ms)
            logger.debug(f"  RTT medido: {rtt_ms:.1f}ms (seq={app['seq']})")

    def _log_response(self, app: dict):
        iin = app.get("iin") or 0
        self._last_iin = iin
        total = sum(len(h.objects) for h in app["headers"])

        if app["func"] == FunctionCode.UNSOLICITED_RESP:
            logger.info(f"  << Unsolicited Response: {total} objeto(s)")
        elif total:
            resumo = ", ".join(
                f"{GROUP_NAMES.get(h.group, 'G' + str(h.group))} x{len(h.objects)}"
                for h in app["headers"] if h.objects
            )
            logger.info(f"  << Response: {resumo}")
        else:
            logger.debug("  << Null Response (sem dados)")

        pend = [n for bit, n in ((IIN_CLASS1_EVENTS, 1), (IIN_CLASS2_EVENTS, 2),
                                 (IIN_CLASS3_EVENTS, 3)) if iin & bit]
        if pend:
            logger.debug(f"     IIN sinaliza eventos nas classes {pend}")

    # ------------------------------------------------------------------
    # Requisicoes
    # ------------------------------------------------------------------
    async def _send_integrity_poll(self):
        seq = self._next_seq()
        h = ObjectHeader(ObjectGroup.CLASS_OBJECTS, CLASS_VARIATIONS[0],
                         QUAL_ALL_POINTS)
        frag = DNP3Codec.encode_app_request(FunctionCode.READ, seq, [h])
        logger.info(">> Integrity Poll (Class 0)")
        await self._send_app(frag, "Integrity Poll (Class 0)", seq=seq)

    async def _send_event_poll(self):
        seq = self._next_seq()
        headers = [
            ObjectHeader(ObjectGroup.CLASS_OBJECTS, CLASS_VARIATIONS[c],
                         QUAL_ALL_POINTS)
            for c in (1, 2, 3)
        ]
        frag = DNP3Codec.encode_app_request(FunctionCode.READ, seq, headers)
        logger.debug(">> Event Poll (Class 1/2/3)")
        await self._send_app(frag, "Event Poll (Class 1,2,3)", seq=seq)

    async def _send_command(self, index: int, ligar: bool):
        code = CROB_LATCH_ON if ligar else CROB_LATCH_OFF
        header = ObjectHeader(ObjectGroup.BINARY_OUTPUT_CMD, 1,
                              QUAL_8BIT_COUNT_INDEX,
                              [DNP3Object(index=index, value=code)])

        if self.use_sbo:
            seq = self._next_seq()
            frag = DNP3Codec.encode_app_request(FunctionCode.SELECT, seq, [header])
            logger.info(f">> SELECT BO[{index}] = {ligar}")
            await self._send_app(frag, f"SELECT BO[{index}]", seq=seq)

            await asyncio.sleep(0.2)
            seq = self._next_seq()
            frag = DNP3Codec.encode_app_request(FunctionCode.OPERATE, seq, [header])
            logger.info(f">> OPERATE BO[{index}]")
            await self._send_app(frag, f"OPERATE BO[{index}]", seq=seq)
        else:
            seq = self._next_seq()
            frag = DNP3Codec.encode_app_request(FunctionCode.DIRECT_OPERATE, seq,
                                                [header])
            logger.info(f">> DIRECT OPERATE BO[{index}] = {ligar}")
            await self._send_app(frag, f"DIRECT OPERATE BO[{index}]", seq=seq)

    async def _send_clock_sync(self):
        seq = self._next_seq()
        h = ObjectHeader(ObjectGroup.TIME_AND_DATE, 1, QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=0, value=0,
                                     timestamp=int(time.time() * 1000))])
        frag = DNP3Codec.encode_app_request(FunctionCode.WRITE, seq, [h])
        logger.info(">> Clock Sync (g50v1)")
        await self._send_app(frag, "Clock Sync", seq=seq)

    async def _send_confirm(self, seq: int, uns: bool = False):
        frag = DNP3Codec.encode_app_request(FunctionCode.CONFIRM, seq, [], uns=uns)
        await self._send_app(frag, "CONFIRM")

    # ------------------------------------------------------------------
    # Loops periodicos
    # ------------------------------------------------------------------
    async def _integrity_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.integrity_interval)
            await self._send_integrity_poll()

    async def _event_poll_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.event_poll_interval)
            await self._send_event_poll()

    async def _command_loop(self):
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.command_interval)
            await asyncio.sleep(interval)
            if not self.command_indexes:
                continue
            await self._send_command(random.choice(self.command_indexes),
                                     random.choice([True, False]))

    async def _clock_sync_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.clock_sync_interval)
            await self._send_clock_sync()
