"""Gateway Modbus-TCP: espelha os datapoints dos slaves do simulador em registradores Modbus.

Motivacao
---------
O SCADA-LTS (o SCADA de codigo aberto usado como HMI/centro de controle no PFC)
NAO possui driver IEC 60870-5-104 nativo; possui, entre outros, Modbus TCP. Para
que o SCADA-LTS observe a "planta" simulada e monte uma HMI real, este gateway
funciona como uma RTU Modbus: le, em tempo real, os valores que os slaves do
simulador (IEC104 slave, DNP3 outstation, OPC-UA server) mantem em memoria e os
publica como coils (booleanos) e holding registers (analogicos float32). O
SCADA-LTS conecta como mestre Modbus e faz polling.

Importante: o gateway NAO toca no trafego IEC104/DNP3/OPC-UA original - ele apenas
LE o estado dos pontos ja mantido pelos slaves. Assim, os tres protocolos seguem
intactos como objeto de estudo/ataque MITM, e o Modbus e so uma "janela" de leitura
para a HMI.

Implementacao
-------------
Servidor Modbus-TCP escrito do zero (sem dependencias externas), no mesmo estilo
dos codecs IEC104/DNP3 do projeto. Suporta as function codes usadas pelo SCADA-LTS:

  FC 01  Read Coils            (le booleanos)
  FC 02  Read Discrete Inputs  (espelho dos coils; conveniencia p/ o SCADA-LTS)
  FC 03  Read Holding Registers(le analogicos float32 - 2 registradores por ponto)
  FC 04  Read Input Registers  (espelho dos holding; conveniencia)
  FC 05  Write Single Coil     (comando da HMI -> volta ao ponto do slave)*
  FC 16  Write Multiple Regs   (setpoint float da HMI -> volta ao ponto do slave)*

  * Escrita DESABILITADA por padrao (read-only). So e aceita com allow_writes: true;
    caso contrario o gateway responde ILLEGAL_FUNCTION. Nos experimentos do PFC o
    Modbus foi usado apenas como janela de observacao (leitura).

Mapa de registradores
----------------------
Os pontos sao ordenados de forma estavel (por slave e por id do ponto) e recebem
enderecos contiguos, base 0:
  - cada ponto BOOLEANO  -> 1 coil               (endereco 0, 1, 2, ...)
  - cada ponto ANALOGICO -> 2 holding registers  (endereco 0, 2, 4, ...; float32 big-endian, palavra alta primeiro)

O mapa completo e impresso no log ao subir e exportado por `register_map_text()`.
"""

import asyncio
import struct
import logging
from typing import Callable, List, Optional

from src.core.engine import BaseProtocolGenerator, SessionState

logger = logging.getLogger("scada_trafgen.modbus")

# Function codes
FC_READ_COILS = 0x01
FC_READ_DISCRETE_INPUTS = 0x02
FC_READ_HOLDING_REGISTERS = 0x03
FC_READ_INPUT_REGISTERS = 0x04
FC_WRITE_SINGLE_COIL = 0x05
FC_WRITE_SINGLE_REGISTER = 0x06
FC_WRITE_MULTIPLE_REGISTERS = 0x10

# Exception codes
EXC_ILLEGAL_FUNCTION = 0x01
EXC_ILLEGAL_DATA_ADDRESS = 0x02
EXC_ILLEGAL_DATA_VALUE = 0x03


class Channel:
    """Um ponto do simulador exposto no espaco de enderecos Modbus.

    getter() -> valor atual do ponto (lido do slave a cada requisicao).
    setter(v) -> escreve de volta no ponto do slave (None se somente leitura).
    """

    def __init__(self, name: str, is_bool: bool, addr: int,
                 getter: Callable, setter: Optional[Callable] = None,
                 source: str = "", point_id: str = "", unit: str = ""):
        self.name = name
        self.is_bool = is_bool
        self.addr = addr           # coil addr (bool) ou primeiro holding reg (analog)
        self.getter = getter
        self.setter = setter
        self.source = source
        self.point_id = point_id
        self.unit = unit


class ModbusGateway(BaseProtocolGenerator):
    """Servidor Modbus-TCP que espelha os pontos dos slaves como registradores."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.bind = config.get("host", "0.0.0.0")
        self.port = int(config.get("port", 5020))
        self.unit_id = int(config.get("unit_id", 1))
        # Lista opcional de nomes de geradores a espelhar; vazio = auto (todos os slaves).
        self.source_names: List[str] = list(config.get("sources", []) or [])
        # Padrao READ-ONLY: o Modbus e apenas uma janela de observacao para a HMI.
        # allow_writes: true reabilita o comando da HMI (FC05/FC16) de volta ao ponto.
        self.allow_writes = bool(config.get("allow_writes", False))

        self._engine = None
        self.server: Optional[asyncio.AbstractServer] = None
        self._coil_channels: List[Channel] = []
        self._reg_channels: List[Channel] = []

    @property
    def protocol_name(self) -> str:
        return "Modbus-Gateway"

    def attach_engine(self, engine):
        """Recebe a Engine para poder ler os geradores-fonte pelo nome."""
        self._engine = engine

    # ------------------------------------------------------------------
    # Construcao do mapa de canais a partir dos slaves
    # ------------------------------------------------------------------
    def _iter_sources(self):
        """Gera (nome, gerador) dos slaves a espelhar.

        Sem `sources` explicito, pega todo gerador que EXPOE uma colecao de pontos
        (duck typing: IEC104 `.datapoints` dict, DNP3 `.points` list, OPC-UA
        `.variaveis` list). Isso funciona com nomes arbitrarios (multi-instancia por
        entidade, ex. `iec104-termica`), onde `role_of` -- baseado em substring do
        nome -- nao acha "slave/outstation/server". Masters/clientes so tem
        `last_values`, entao ficam de fora naturalmente.
        """
        if not self._engine:
            return
        for name, gen in self._engine.generators.items():
            if gen is self:
                continue
            if self.source_names:
                if name not in self.source_names:
                    continue
            else:
                has_points = (isinstance(getattr(gen, "datapoints", None), dict)
                              or isinstance(getattr(gen, "points", None), list)
                              or isinstance(getattr(gen, "variaveis", None), list))
                if not has_points:
                    continue
            yield name, gen

    @staticmethod
    def _extract_points(gen_name: str, gen) -> List[dict]:
        """Extrai pontos genericos de um slave, por duck typing do modelo de ponto.

        Retorna dicts {name, is_bool, getter, setter, point_id, unit}.
        """
        pts: List[dict] = []

        # IEC 104 slave: self.datapoints = {ioa: DataPoint(.value, .type_id)}
        if isinstance(getattr(gen, "datapoints", None), dict):
            for ioa, dp in sorted(gen.datapoints.items()):
                is_bool = isinstance(dp.value, bool)
                pts.append({
                    "name": f"{gen_name}:IOA{ioa}",
                    "is_bool": is_bool,
                    "getter": (lambda dp=dp: dp.value),
                    "setter": (lambda v, dp=dp: dp.apply_write(v)),
                    "point_id": f"IOA {ioa} (type {dp.type_id})",
                    "unit": "",
                })
            return pts

        # DNP3 outstation: self.points = [DNP3Point(.index, .group, .value)]
        if isinstance(getattr(gen, "points", None), list):
            for p in sorted(gen.points, key=lambda x: (getattr(x, "group", 0),
                                                       getattr(x, "index", 0))):
                is_bool = isinstance(p.value, bool)
                pts.append({
                    "name": f"{gen_name}:g{p.group}i{p.index}",
                    "is_bool": is_bool,
                    "getter": (lambda p=p: p.value),
                    "setter": (lambda v, p=p: p.apply_write(v)),
                    "point_id": f"group {p.group} index {p.index}",
                    "unit": "",
                })
            return pts

        # OPC-UA server: self.variaveis = [OPCUAVariable(.nome, .valor, .booleano)]
        if isinstance(getattr(gen, "variaveis", None), list):
            for v in gen.variaveis:
                is_bool = bool(getattr(v, "booleano", False))
                pts.append({
                    "name": f"{gen_name}:{v.nome}",
                    "is_bool": is_bool,
                    "getter": (lambda v=v: v.valor),
                    "setter": (lambda x, v=v: v.apply_write(x)),
                    "point_id": v.nome,
                    "unit": "",
                })
            return pts

        return pts

    def _build_channels(self):
        """Monta a lista ordenada de canais e atribui enderecos Modbus."""
        self._coil_channels = []
        self._reg_channels = []
        coil_addr = 0
        reg_addr = 0
        for gen_name, gen in self._iter_sources():
            for p in self._extract_points(gen_name, gen):
                if p["is_bool"]:
                    ch = Channel(p["name"], True, coil_addr, p["getter"],
                                 p["setter"] if self.allow_writes else None,
                                 source=gen_name, point_id=p["point_id"])
                    self._coil_channels.append(ch)
                    coil_addr += 1
                else:
                    ch = Channel(p["name"], False, reg_addr, p["getter"],
                                 p["setter"] if self.allow_writes else None,
                                 source=gen_name, point_id=p["point_id"])
                    self._reg_channels.append(ch)
                    reg_addr += 2      # float32 = 2 registradores
        logger.info(f"Gateway Modbus: {len(self._coil_channels)} coils + "
                    f"{len(self._reg_channels)} registradores analogicos (float32)")

    # ------------------------------------------------------------------
    # Snapshot dos valores atuais para os arrays Modbus
    # ------------------------------------------------------------------
    def _read_coils(self) -> List[bool]:
        out = []
        for ch in self._coil_channels:
            try:
                out.append(bool(ch.getter()))
            except Exception:
                out.append(False)
        return out

    def _read_registers(self) -> List[int]:
        """Retorna a lista de registradores 16-bit (2 por canal analogico, float32 BE)."""
        regs: List[int] = []
        for ch in self._reg_channels:
            try:
                val = ch.getter()
                f = float(val if val is not None else 0.0)
            except Exception:
                f = 0.0
            hi, lo = struct.unpack(">HH", struct.pack(">f", f))
            regs.append(hi)
            regs.append(lo)
        return regs

    # ------------------------------------------------------------------
    # Servidor Modbus-TCP
    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self._build_channels()
        for linha in self.register_map_text().splitlines():
            logger.info(linha)

        self.server = await asyncio.start_server(
            self._handle_client, self.bind, self.port)
        addr = self.server.sockets[0].getsockname()
        self.state = SessionState.ACTIVE
        logger.info(f"Gateway Modbus-TCP ouvindo em {addr[0]}:{addr[1]} "
                    f"(unit id {self.unit_id})")
        try:
            async with self.server:
                await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            self.state = SessionState.STOPPED

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        logger.info(f"Mestre Modbus conectado: {peer}")
        try:
            while not self._stop_event.is_set():
                # MBAP header: transaction(2) protocol(2) length(2) unit(1)
                header = await reader.readexactly(7)
                trans_id, proto_id, length, unit = struct.unpack(">HHHB", header)
                body = await reader.readexactly(length - 1)   # length inclui o unit id
                self.stats.record_received(7 + len(body))
                resp_pdu = self._process_pdu(body)
                # Remonta MBAP: length = unit(1) + pdu
                out = struct.pack(">HHHB", trans_id, 0, len(resp_pdu) + 1, unit) + resp_pdu
                writer.write(out)
                await writer.drain()
                self.stats.record_sent(len(out))
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass
        except Exception as e:
            self.stats.record_error()
            logger.error(f"Erro no cliente Modbus {peer}: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info(f"Mestre Modbus desconectado: {peer}")

    def _exc(self, fc: int, code: int) -> bytes:
        return struct.pack(">BB", fc | 0x80, code)

    def _process_pdu(self, pdu: bytes) -> bytes:
        if not pdu:
            return self._exc(0, EXC_ILLEGAL_FUNCTION)
        fc = pdu[0]
        try:
            if fc in (FC_READ_COILS, FC_READ_DISCRETE_INPUTS):
                return self._read_bits(fc, pdu)
            if fc in (FC_READ_HOLDING_REGISTERS, FC_READ_INPUT_REGISTERS):
                return self._read_words(fc, pdu)
            if fc in (FC_WRITE_SINGLE_COIL, FC_WRITE_MULTIPLE_REGISTERS,
                      FC_WRITE_SINGLE_REGISTER):
                # Read-only por padrao: recusa qualquer escrita se allow_writes: false.
                if not self.allow_writes:
                    return self._exc(fc, EXC_ILLEGAL_FUNCTION)
                if fc == FC_WRITE_SINGLE_COIL:
                    return self._write_single_coil(pdu)
                if fc == FC_WRITE_MULTIPLE_REGISTERS:
                    return self._write_multiple_registers(pdu)
                # FC_WRITE_SINGLE_REGISTER: aceito mas nao mapeado para float
                # (SCADA-LTS usa FC16 p/ float): eco do request.
                return pdu
            return self._exc(fc, EXC_ILLEGAL_FUNCTION)
        except struct.error:
            return self._exc(fc, EXC_ILLEGAL_DATA_VALUE)

    def _read_bits(self, fc: int, pdu: bytes) -> bytes:
        _, start, qty = struct.unpack(">BHH", pdu[:5])
        if qty < 1 or qty > 2000:
            return self._exc(fc, EXC_ILLEGAL_DATA_VALUE)
        coils = self._read_coils()
        if start + qty > max(len(coils), start):   # permite ler alem do fim como 0? nao: erro
            if start + qty > len(coils):
                return self._exc(fc, EXC_ILLEGAL_DATA_ADDRESS)
        nbytes = (qty + 7) // 8
        buf = bytearray(nbytes)
        for i in range(qty):
            if coils[start + i]:
                buf[i // 8] |= (1 << (i % 8))
        return struct.pack(">BB", fc, nbytes) + bytes(buf)

    def _read_words(self, fc: int, pdu: bytes) -> bytes:
        _, start, qty = struct.unpack(">BHH", pdu[:5])
        if qty < 1 or qty > 125:
            return self._exc(fc, EXC_ILLEGAL_DATA_VALUE)
        regs = self._read_registers()
        if start + qty > len(regs):
            return self._exc(fc, EXC_ILLEGAL_DATA_ADDRESS)
        payload = b"".join(struct.pack(">H", regs[start + i]) for i in range(qty))
        return struct.pack(">BB", fc, qty * 2) + payload

    def _write_single_coil(self, pdu: bytes) -> bytes:
        _, addr, value = struct.unpack(">BHH", pdu[:5])
        if addr >= len(self._coil_channels):
            return self._exc(FC_WRITE_SINGLE_COIL, EXC_ILLEGAL_DATA_ADDRESS)
        ch = self._coil_channels[addr]
        if ch.setter is not None:
            newval = (value == 0xFF00)
            try:
                ch.setter(newval)
                logger.info(f"HMI -> {ch.name} = {newval} (write coil)")
            except Exception as e:
                logger.error(f"Falha ao escrever coil {ch.name}: {e}")
        # eco do request (formato padrao da resposta FC5)
        return pdu[:5]

    def _write_multiple_registers(self, pdu: bytes) -> bytes:
        _, start, qty, bytecount = struct.unpack(">BHHB", pdu[:6])
        data = pdu[6:6 + bytecount]
        if len(data) != qty * 2:
            return self._exc(FC_WRITE_MULTIPLE_REGISTERS, EXC_ILLEGAL_DATA_VALUE)
        # Aplica em canais analogicos cujos 2 registradores caibam no bloco escrito.
        for ch in self._reg_channels:
            if ch.setter is None:
                continue
            if start <= ch.addr and (ch.addr + 1) < (start + qty):
                off = (ch.addr - start) * 2
                try:
                    f = struct.unpack(">f", data[off:off + 4])[0]
                    ch.setter(f)
                    logger.info(f"HMI -> {ch.name} = {f:.3f} (write regs)")
                except Exception as e:
                    logger.error(f"Falha ao escrever regs {ch.name}: {e}")
        return struct.pack(">BHH", FC_WRITE_MULTIPLE_REGISTERS, start, qty)

    # ------------------------------------------------------------------
    # Mapa de registradores (log + documentacao + config do SCADA-LTS)
    # ------------------------------------------------------------------
    def register_map_text(self) -> str:
        linhas = []
        linhas.append("=" * 70)
        linhas.append("  MAPA DE REGISTRADORES MODBUS (base 0)")
        linhas.append("-" * 70)
        linhas.append("  COILS (booleanos)  - FC01/FC05")
        if not self._coil_channels:
            linhas.append("    (nenhum)")
        for ch in self._coil_channels:
            linhas.append(f"    coil {ch.addr:<4} <- {ch.point_id:<24} [{ch.source}]")
        linhas.append("  HOLDING REGISTERS (analogicos float32, 2 regs cada) - FC03/FC16")
        if not self._reg_channels:
            linhas.append("    (nenhum)")
        for ch in self._reg_channels:
            linhas.append(f"    reg  {ch.addr:<4} <- {ch.point_id:<24} [{ch.source}]")
        linhas.append("=" * 70)
        return "\n".join(linhas)
