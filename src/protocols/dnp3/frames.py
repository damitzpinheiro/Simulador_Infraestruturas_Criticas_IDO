"""
DNP3 (IEEE 1815-2012) - Frames e Codec das tres camadas do modelo EPA.

Data Link:   0x05 0x64 | len | ctrl | dest (2B) | src (2B) | CRC (2B)
             + blocos de dados de ate 16 bytes, cada um com CRC proprio
Transport:   1 byte  FIR | FIN | sequence (6 bits)
Application: 1 byte  AC (FIR/FIN/CON/UNS/seq) + func code [+ IIN nas respostas]
             + object headers (group, variation, qualifier, range)

Implementacao propria em Python puro, sem bibliotecas de protocolo.
"""

import struct
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

START_BYTES = b'\x05\x64'
DEFAULT_PORT = 20000

MAX_BLOCK = 16           # bytes de dados por bloco CRC no data link
MAX_LINK_PAYLOAD = 250   # bytes de payload por frame de link
MAX_APP_FRAGMENT = 2048  # bytes por fragmento de aplicacao


# ---------------------------------------------------------------------------
# CRC-16/DNP
# ---------------------------------------------------------------------------
# Polinomio 0x3D65, refletido, init 0x0000, xorout 0xFFFF.
def _build_crc_table() -> List[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA6BC if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC_TABLE = _build_crc_table()


def crc_dnp(data: bytes) -> int:
    """CRC-16/DNP de um bloco. Devolve o valor ja com o xorout aplicado."""
    crc = 0x0000
    for b in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ b) & 0xFF]
    return (~crc) & 0xFFFF


def crc_bytes(data: bytes) -> bytes:
    """CRC de um bloco, em little-endian, pronto para concatenar."""
    return struct.pack('<H', crc_dnp(data))


# ---------------------------------------------------------------------------
# Data Link
# ---------------------------------------------------------------------------
class LinkFunction(IntEnum):
    # Primarias (PRM=1)
    RESET_LINK      = 0
    TEST_LINK       = 2
    CONFIRMED_USER  = 3
    UNCONFIRMED_USER = 4
    REQUEST_STATUS  = 9
    # Secundarias (PRM=0)
    ACK             = 0
    NACK            = 1
    LINK_STATUS     = 11
    NOT_SUPPORTED   = 15


# ---------------------------------------------------------------------------
# Application layer
# ---------------------------------------------------------------------------
class FunctionCode(IntEnum):
    CONFIRM          = 0
    READ             = 1
    WRITE            = 2
    SELECT           = 3
    OPERATE          = 4
    DIRECT_OPERATE   = 5
    DIRECT_OP_NR     = 6
    COLD_RESTART     = 13
    WARM_RESTART     = 14
    DELAY_MEASURE    = 23
    RECORD_CURRENT_TIME = 24
    RESPONSE         = 129
    UNSOLICITED_RESP = 130


FUNCTION_NAMES = {f.value: f.name for f in FunctionCode}


class ObjectGroup(IntEnum):
    BINARY_INPUT       = 1
    BINARY_INPUT_EVENT = 2
    BINARY_OUTPUT      = 10
    BINARY_OUTPUT_CMD  = 12
    COUNTER            = 20
    COUNTER_EVENT      = 22
    ANALOG_INPUT       = 30
    ANALOG_INPUT_EVENT = 32
    ANALOG_OUTPUT      = 40
    ANALOG_OUTPUT_CMD  = 41
    TIME_AND_DATE      = 50
    CLASS_OBJECTS      = 60
    INTERNAL_INDICATIONS = 80


GROUP_NAMES = {g.value: g.name for g in ObjectGroup}

# Variacoes usadas por grupo (as mais comuns em campo)
VAR_BI_FLAGS        = 2   # g1v2  binary input com flags
VAR_BI_EVENT_TIME   = 2   # g2v2  binary input event com timestamp
VAR_BO_FLAGS        = 2   # g10v2 binary output com flags
VAR_CROB            = 1   # g12v1 control relay output block
VAR_COUNTER_32      = 1   # g20v1 counter 32 bits com flag
VAR_AI_32           = 1   # g30v1 analog input 32 bits com flag
VAR_AI_SHORT_FLOAT  = 5   # g30v5 analog input float com flag
VAR_AI_EVENT_FLOAT  = 5   # g32v5 analog input event float
VAR_AO_SHORT_FLOAT  = 3   # g40v3 analog output status float
VAR_AO_BLOCK_FLOAT  = 3   # g41v3 analog output block float

# Variacao das classes: g60v1=Class0, v2=Class1, v3=Class2, v4=Class3
CLASS_VARIATIONS = {0: 1, 1: 2, 2: 3, 3: 4}

# Qualifiers
QUAL_ALL_POINTS   = 0x06  # sem range, todos os pontos
QUAL_8BIT_START_STOP = 0x00
QUAL_16BIT_START_STOP = 0x01
QUAL_8BIT_COUNT_INDEX = 0x17
QUAL_8BIT_COUNT   = 0x07

# Flags de qualidade (bit 0 = ONLINE em todos os grupos)
FLAG_ONLINE         = 0x01
FLAG_RESTART        = 0x02
FLAG_COMM_LOST      = 0x04
FLAG_REMOTE_FORCED  = 0x08
FLAG_LOCAL_FORCED   = 0x10
FLAG_OVER_RANGE     = 0x20
FLAG_REFERENCE_ERR  = 0x40

# Internal Indications (2 bytes nas respostas)
IIN_BROADCAST       = 0x0001
IIN_CLASS1_EVENTS   = 0x0002
IIN_CLASS2_EVENTS   = 0x0004
IIN_CLASS3_EVENTS   = 0x0008
IIN_NEED_TIME       = 0x0010
IIN_DEVICE_RESTART  = 0x0080

# Control codes do CROB (g12v1)
CROB_NUL          = 0x00
CROB_PULSE_ON     = 0x01
CROB_LATCH_ON     = 0x03
CROB_LATCH_OFF    = 0x04
CROB_TRIP         = 0x81
CROB_CLOSE        = 0x41

# Status do CROB na resposta de SELECT/OPERATE (IEEE 1815, g12v1)
CROB_STATUS_SUCCESS   = 0
CROB_STATUS_NO_SELECT = 2   # OPERATE sem SELECT valido (ou divergente do selecionado)


@dataclass
class DNP3Object:
    """Um ponto de dado dentro de um object header."""
    index: int
    value: Any = None
    flags: int = FLAG_ONLINE
    timestamp: Optional[int] = None   # ms desde a epoca (DNP3 usa 48 bits)
    status: int = 0                   # status do CROB na resposta (0 = sucesso)


@dataclass
class ObjectHeader:
    """Cabecalho de objeto + seus pontos."""
    group: int
    variation: int
    qualifier: int = QUAL_8BIT_START_STOP
    objects: List[DNP3Object] = field(default_factory=list)

    @property
    def name(self) -> str:
        g = GROUP_NAMES.get(self.group, f"G{self.group}")
        return f"{g}(g{self.group}v{self.variation})"


class DNP3Codec:
    """Codificador/decodificador das tres camadas do DNP3."""

    # ------------------------------------------------------------------
    # Data Link
    # ------------------------------------------------------------------
    @staticmethod
    def encode_link_frame(payload: bytes, dest: int, src: int,
                          func: int = LinkFunction.UNCONFIRMED_USER,
                          prm: bool = True, dir_bit: bool = False) -> bytes:
        """Monta um frame de link completo, com CRC no header e em cada bloco."""
        if len(payload) > MAX_LINK_PAYLOAD:
            raise ValueError(f"payload de link excede {MAX_LINK_PAYLOAD} bytes")

        ctrl = func & 0x0F
        if prm:
            ctrl |= 0x40
        if dir_bit:
            ctrl |= 0x80

        # LEN conta ctrl + dest + src + dados (nao conta start, len nem CRCs)
        length = 5 + len(payload)
        header = struct.pack('<BBBBHH', 0x05, 0x64, length, ctrl, dest, src)
        frame = bytearray(header + crc_bytes(header))

        for i in range(0, len(payload), MAX_BLOCK):
            block = payload[i:i + MAX_BLOCK]
            frame += block + crc_bytes(block)
        return bytes(frame)

    @staticmethod
    def decode_link_frame(data: bytes) -> Optional[dict]:
        """Decodifica um frame de link. Devolve None se o CRC nao bater."""
        if len(data) < 10 or data[:2] != START_BYTES:
            return None

        header = data[:8]
        length = data[2]
        ctrl = data[3]
        dest, src = struct.unpack('<HH', data[4:8])
        base = {
            "type": "link", "func": ctrl & 0x0F,
            "prm": bool(ctrl & 0x40), "dir": bool(ctrl & 0x80),
            "dest": dest, "src": src,
        }

        if struct.unpack('<H', data[8:10])[0] != crc_dnp(header):
            return {**base, "crc_error": True, "payload": b''}

        n_payload = length - 5

        payload = bytearray()
        pos = 10
        remaining = n_payload
        crc_error = False
        while remaining > 0:
            n = min(MAX_BLOCK, remaining)
            block = data[pos:pos + n]
            block_crc = data[pos + n:pos + n + 2]
            if len(block_crc) < 2 or struct.unpack('<H', block_crc)[0] != crc_dnp(block):
                crc_error = True
                break
            payload += block
            pos += n + 2
            remaining -= n

        return {**base, "crc_error": crc_error, "payload": bytes(payload)}

    @staticmethod
    def frame_length(data: bytes) -> Optional[int]:
        """Tamanho total em bytes do frame que comeca em data, ou None."""
        if len(data) < 3 or data[:2] != START_BYTES:
            return None
        n_payload = data[2] - 5
        if n_payload < 0:
            return None
        n_blocks = (n_payload + MAX_BLOCK - 1) // MAX_BLOCK
        return 10 + n_payload + 2 * n_blocks

    @staticmethod
    def read_frame_from_buffer(buf: bytes) -> Tuple[Optional[bytes], bytes]:
        """Extrai o primeiro frame completo do buffer TCP continuo."""
        start = buf.find(START_BYTES)
        if start < 0:
            # nada aproveitavel; guarda no maximo 1 byte para o caso de 0x05 solto
            return None, buf[-1:] if buf else b''
        buf = buf[start:]
        if len(buf) < 3:
            return None, buf                 # cabecalho incompleto: aguarda o byte de LEN
        total = DNP3Codec.frame_length(buf)
        if total is None:
            # START valido mas LEN invalido (<5): marcador corrompido. Descarta este
            # START e ressincroniza no proximo, em vez de ficar preso no mesmo prefixo.
            return None, buf[2:]
        if len(buf) < total:
            return None, buf                 # frame incompleto: aguarda mais bytes
        return buf[:total], buf[total:]

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    @staticmethod
    def encode_transport(fragment: bytes, seq: int) -> List[bytes]:
        """Segmenta um fragmento de aplicacao em PDUs de transporte."""
        chunk = MAX_LINK_PAYLOAD - 1     # 1 byte do header de transporte
        pieces = [fragment[i:i + chunk] for i in range(0, len(fragment), chunk)] or [b'']
        out = []
        for i, piece in enumerate(pieces):
            th = seq & 0x3F
            if i == 0:
                th |= 0x40                       # FIR
            if i == len(pieces) - 1:
                th |= 0x80                       # FIN
            out.append(bytes([th]) + piece)
            seq = (seq + 1) % 64
        return out

    @staticmethod
    def decode_transport(payload: bytes) -> dict:
        if not payload:
            return {"fir": False, "fin": False, "seq": 0, "data": b''}
        th = payload[0]
        return {
            "fir": bool(th & 0x40),
            "fin": bool(th & 0x80),
            "seq": th & 0x3F,
            "data": payload[1:],
        }

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    @staticmethod
    def encode_app_request(func: int, seq: int, headers: List[ObjectHeader],
                           con: bool = False, uns: bool = False) -> bytes:
        ac = (seq & 0x0F) | 0x40 | 0x80          # FIR + FIN
        if con:
            ac |= 0x20
        if uns:
            ac |= 0x10
        out = bytearray([ac, func & 0xFF])
        for h in headers:
            out += DNP3Codec._encode_object_header(h)
        return bytes(out)

    @staticmethod
    def encode_app_response(func: int, seq: int, iin: int,
                            headers: List[ObjectHeader],
                            con: bool = False, uns: bool = False) -> bytes:
        ac = (seq & 0x0F) | 0x40 | 0x80
        if con:
            ac |= 0x20
        if uns:
            ac |= 0x10
        out = bytearray([ac, func & 0xFF])
        out += struct.pack('<H', iin & 0xFFFF)
        for h in headers:
            out += DNP3Codec._encode_object_header(h)
        return bytes(out)

    @staticmethod
    def decode_app(data: bytes) -> Optional[dict]:
        if len(data) < 2:
            return None
        ac = data[0]
        func = data[1]
        pos = 2
        iin = None
        if func in (FunctionCode.RESPONSE, FunctionCode.UNSOLICITED_RESP):
            if len(data) < 4:
                return None
            iin = struct.unpack('<H', data[2:4])[0]
            pos = 4

        headers = DNP3Codec._decode_object_headers(data[pos:])
        return {
            "type": "app",
            "fir": bool(ac & 0x40),
            "fin": bool(ac & 0x80),
            "con": bool(ac & 0x20),
            "uns": bool(ac & 0x10),
            "seq": ac & 0x0F,
            "func": func,
            "func_name": FUNCTION_NAMES.get(func, f"FUNC_{func}"),
            "iin": iin,
            "headers": headers,
        }

    # ------------------------------------------------------------------
    # Object headers
    # ------------------------------------------------------------------
    @staticmethod
    def _encode_object_header(h: ObjectHeader) -> bytes:
        out = bytearray([h.group & 0xFF, h.variation & 0xFF, h.qualifier & 0xFF])

        if h.qualifier == QUAL_ALL_POINTS or not h.objects:
            return bytes(out)

        indexes = [o.index for o in h.objects]
        if h.qualifier == QUAL_8BIT_COUNT_INDEX:
            out.append(len(h.objects) & 0xFF)
            for o in h.objects:
                out.append(o.index & 0xFF)
                out += DNP3Codec._encode_value(h.group, h.variation, o)
            return bytes(out)

        # start/stop
        start, stop = min(indexes), max(indexes)
        if h.qualifier == QUAL_16BIT_START_STOP:
            out += struct.pack('<HH', start, stop)
        else:
            out += bytes([start & 0xFF, stop & 0xFF])
        for o in h.objects:
            out += DNP3Codec._encode_value(h.group, h.variation, o)
        return bytes(out)

    @staticmethod
    def _encode_value(group: int, variation: int, o: DNP3Object) -> bytes:
        g, v = group, variation

        if g in (ObjectGroup.BINARY_INPUT, ObjectGroup.BINARY_OUTPUT):
            flags = o.flags & ~0x80
            if o.value:
                flags |= 0x80                     # bit 7 = estado
            return bytes([flags])

        if g == ObjectGroup.BINARY_INPUT_EVENT:
            flags = o.flags & ~0x80
            if o.value:
                flags |= 0x80
            if v == 2 and o.timestamp is not None:
                return bytes([flags]) + o.timestamp.to_bytes(6, 'little')
            return bytes([flags])

        if g in (ObjectGroup.COUNTER, ObjectGroup.COUNTER_EVENT):
            return bytes([o.flags]) + struct.pack('<I', int(o.value) & 0xFFFFFFFF)

        if g in (ObjectGroup.ANALOG_INPUT, ObjectGroup.ANALOG_INPUT_EVENT,
                 ObjectGroup.ANALOG_OUTPUT):
            if v in (5, 6):                       # float
                data = bytes([o.flags]) + struct.pack('<f', float(o.value))
            else:                                 # inteiro 32 bits
                data = bytes([o.flags]) + struct.pack('<i', int(o.value))
            if g == ObjectGroup.ANALOG_INPUT_EVENT and v == 7 and o.timestamp:
                data += o.timestamp.to_bytes(6, 'little')
            return data

        if g == ObjectGroup.BINARY_OUTPUT_CMD:    # g12v1 CROB
            code = int(o.value) if o.value is not None else CROB_NUL
            # control code, count, on-time, off-time, status
            return bytes([code, 1]) + struct.pack('<IIB', 100, 100, o.status & 0xFF)

        if g == ObjectGroup.ANALOG_OUTPUT_CMD:    # g41v3 float
            return struct.pack('<f', float(o.value)) + bytes([0])

        if g == ObjectGroup.TIME_AND_DATE:
            ts = o.timestamp or 0
            return ts.to_bytes(6, 'little')

        return b''

    @staticmethod
    def _decode_object_headers(data: bytes) -> List[ObjectHeader]:
        headers: List[ObjectHeader] = []
        pos = 0
        while pos + 3 <= len(data):
            group, variation, qualifier = data[pos], data[pos + 1], data[pos + 2]
            pos += 3
            h = ObjectHeader(group=group, variation=variation, qualifier=qualifier)

            if qualifier == QUAL_ALL_POINTS:
                headers.append(h)
                continue

            if qualifier == QUAL_8BIT_COUNT_INDEX:
                if pos >= len(data):
                    break
                count = data[pos]
                pos += 1
                for _ in range(count):
                    if pos >= len(data):
                        break
                    idx = data[pos]
                    pos += 1
                    value, used = DNP3Codec._decode_value(group, variation, data[pos:])
                    pos += used
                    h.objects.append(DNP3Object(index=idx, value=value))
                headers.append(h)
                continue

            if qualifier == QUAL_16BIT_START_STOP:
                if pos + 4 > len(data):
                    break
                start, stop = struct.unpack('<HH', data[pos:pos + 4])
                pos += 4
            else:
                if pos + 2 > len(data):
                    break
                start, stop = data[pos], data[pos + 1]
                pos += 2

            for idx in range(start, stop + 1):
                value, used = DNP3Codec._decode_value(group, variation, data[pos:])
                if used == 0:
                    break
                pos += used
                h.objects.append(DNP3Object(index=idx, value=value))
            headers.append(h)

        return headers

    @staticmethod
    def _decode_value(group: int, variation: int, data: bytes) -> Tuple[Any, int]:
        """Devolve (valor, bytes consumidos)."""
        if not data:
            return None, 0
        g, v = group, variation

        if g in (ObjectGroup.BINARY_INPUT, ObjectGroup.BINARY_OUTPUT):
            return bool(data[0] & 0x80), 1

        if g == ObjectGroup.BINARY_INPUT_EVENT:
            n = 7 if (v == 2 and len(data) >= 7) else 1
            return bool(data[0] & 0x80), n

        if g in (ObjectGroup.COUNTER, ObjectGroup.COUNTER_EVENT):
            if len(data) < 5:
                return None, 0
            return struct.unpack('<I', data[1:5])[0], 5

        if g in (ObjectGroup.ANALOG_INPUT, ObjectGroup.ANALOG_INPUT_EVENT,
                 ObjectGroup.ANALOG_OUTPUT):
            if len(data) < 5:
                return None, 0
            if v in (5, 6):
                return struct.unpack('<f', data[1:5])[0], 5
            return struct.unpack('<i', data[1:5])[0], 5

        if g == ObjectGroup.BINARY_OUTPUT_CMD:
            if len(data) < 11:
                return None, 0
            return data[0], 11

        if g == ObjectGroup.ANALOG_OUTPUT_CMD:
            if len(data) < 5:
                return None, 0
            return struct.unpack('<f', data[0:4])[0], 5

        if g == ObjectGroup.TIME_AND_DATE:
            if len(data) < 6:
                return None, 0
            return int.from_bytes(data[0:6], 'little'), 6

        return None, 0


def frame_label(decoded_app: Optional[dict], link: Optional[dict] = None) -> dict:
    """Rotulo legivel de um frame DNP3, para log e MITM."""
    if decoded_app is None:
        if link and link.get("crc_error"):
            return {"name": "CRC ERROR", "ftype": "link"}
        func = link.get("func") if link else None
        return {"name": f"Link func={func}", "ftype": "link"}

    groups = ", ".join(
        f"g{h.group}v{h.variation}"
        + (f" x{len(h.objects)}" if h.objects else "")
        for h in decoded_app.get("headers", [])
    )
    return {
        "name": decoded_app.get("func_name", "?"),
        "ftype": "app",
        "cot": groups or None,
        "n": sum(len(h.objects) for h in decoded_app.get("headers", [])) or None,
    }
