"""
IEC 60870-5-104 - Frames e Codec

APDU: 0x68 | length (1B) | control field (4B) | ASDU (variavel)
  I-frame: dados          (bit 0 byte1 = 0)
  S-frame: supervisao/ACK (byte1 & 0x03 == 0x01)
  U-frame: controle       (byte1 & 0x03 == 0x03)
"""

import struct
import time
from enum import IntEnum
from dataclasses import dataclass, field
from typing import Optional, List, Tuple, Any

START_BYTE = 0x68
DEFAULT_PORT = 2404

# U-frame function codes
STARTDT_ACT = 0x07
STARTDT_CON = 0x0B
STOPDT_ACT  = 0x13
STOPDT_CON  = 0x23
TESTFR_ACT  = 0x43
TESTFR_CON  = 0x83

# Quality descriptor bits (IEC 60870-5-101, Secao 7.2.6)
QDS_OVERFLOW    = 0x01  # bit 0 — estouro de faixa (analogicos)
QDS_BLOCKED     = 0x10  # bit 4 — valor bloqueado
QDS_SUBSTITUTED = 0x20  # bit 5 — valor substituido por operador
QDS_NONTOPICAL  = 0x40  # bit 6 — valor antigo / nao atual
QDS_INVALID     = 0x80  # bit 7 — valor invalido


class TypeID(IntEnum):
    """Tipos de informacao IEC 104."""
    # --- Monitoramento (slave -> master) ---
    M_SP_NA_1 = 1    # Single-point
    M_SP_TA_1 = 2    # Single-point + timestamp curto
    M_DP_NA_1 = 3    # Double-point
    M_ST_NA_1 = 5    # Step position
    M_ME_NA_1 = 9    # Measured normalized
    M_ME_NB_1 = 11   # Measured scaled
    M_ME_NC_1 = 13   # Measured short float
    M_IT_NA_1 = 15   # Integrated totals
    M_SP_TB_1 = 30   # Single-point + CP56Time2a
    M_DP_TB_1 = 31   # Double-point + CP56Time2a
    M_ME_TD_1 = 34   # Normalized + CP56Time2a
    M_ME_TE_1 = 35   # Scaled + CP56Time2a
    M_ME_TF_1 = 36   # Short float + CP56Time2a
    M_EI_NA_1 = 70   # End of initialization

    # --- Comandos (master -> slave) ---
    C_SC_NA_1 = 45   # Single command
    C_DC_NA_1 = 46   # Double command
    C_RC_NA_1 = 47   # Regulating step command
    C_SE_NA_1 = 48   # Set-point normalized
    C_SE_NB_1 = 49   # Set-point scaled
    C_SE_NC_1 = 50   # Set-point short float

    # --- Sistema ---
    C_IC_NA_1 = 100  # Interrogation command
    C_CI_NA_1 = 101  # Counter interrogation
    C_RD_NA_1 = 102  # Read command
    C_CS_NA_1 = 103  # Clock synchronization


class CauseOfTransmission(IntEnum):
    """Causas de transmissao (COT)."""
    PERIODIC              = 1
    BACKGROUND            = 2
    SPONTANEOUS           = 3
    INITIALIZED           = 4
    REQUEST               = 5
    ACTIVATION            = 6
    ACTIVATION_CON        = 7
    DEACTIVATION          = 8
    DEACTIVATION_CON      = 9
    ACTIVATION_TERMINATION = 10
    INTERROGATED_STATION  = 20
    INTERROGATED_GROUP_1  = 21
    INTERROGATED_GROUP_2  = 22
    INTERROGATED_GROUP_3  = 23
    INTERROGATED_GROUP_4  = 24
    INTERROGATED_GROUP_5  = 25
    INTERROGATED_GROUP_6  = 26
    INTERROGATED_GROUP_7  = 27
    INTERROGATED_GROUP_8  = 28
    INTERROGATED_GROUP_9  = 29
    INTERROGATED_GROUP_10 = 30
    INTERROGATED_GROUP_11 = 31
    INTERROGATED_GROUP_12 = 32
    INTERROGATED_GROUP_13 = 33
    INTERROGATED_GROUP_14 = 34
    INTERROGATED_GROUP_15 = 35
    INTERROGATED_GROUP_16 = 36


@dataclass
class CP56Time2a:
    """Timestamp CP56Time2a (7 bytes)."""
    ms: int = 0
    minute: int = 0
    hour: int = 0
    day: int = 1
    month: int = 1
    year: int = 24

    @classmethod
    def now(cls) -> 'CP56Time2a':
        ts = time.time()
        t = time.localtime(ts)
        ms = t.tm_sec * 1000 + int((ts % 1) * 1000)
        return cls(
            ms=ms, minute=t.tm_min, hour=t.tm_hour,
            day=t.tm_mday, month=t.tm_mon, year=t.tm_year - 2000,
        )

    def encode(self) -> bytes:
        t = time.localtime()
        dow = t.tm_wday + 1  # 1=Monday .. 7=Sunday (ISO 8601)
        b = bytearray(7)
        b[0] = self.ms & 0xFF
        b[1] = (self.ms >> 8) & 0xFF
        b[2] = self.minute & 0x3F
        b[3] = self.hour & 0x1F
        b[4] = (self.day & 0x1F) | ((dow & 0x07) << 5)
        b[5] = self.month & 0x0F
        b[6] = self.year & 0x7F
        return bytes(b)

    @classmethod
    def decode(cls, data: bytes) -> 'CP56Time2a':
        ms = data[0] | (data[1] << 8)
        return cls(
            ms=ms, minute=data[2] & 0x3F, hour=data[3] & 0x1F,
            day=data[4] & 0x1F, month=data[5] & 0x0F, year=data[6] & 0x7F,
        )


@dataclass
class InformationObject:
    """Objeto de informacao generico."""
    ioa: int            # Information Object Address (3 bytes)
    value: Any = None
    quality: int = 0x00
    timestamp: Optional[CP56Time2a] = None


@dataclass
class ASDU:
    """Application Service Data Unit."""
    type_id: int
    num_objects: int = 1
    sq: int = 0
    cause: int = CauseOfTransmission.SPONTANEOUS
    negative: bool = False
    test: bool = False
    originator: int = 0
    common_address: int = 1
    objects: List[InformationObject] = field(default_factory=list)

    def encode(self) -> bytes:
        buf = bytearray()
        buf.append(self.type_id & 0xFF)

        vsq = (self.sq << 7) | (self.num_objects & 0x7F)
        buf.append(vsq)

        cot = self.cause & 0x3F
        if self.negative:
            cot |= 0x40
        if self.test:
            cot |= 0x80
        buf.append(cot)
        buf.append(self.originator & 0xFF)

        buf.extend(struct.pack('<H', self.common_address))

        for obj in self.objects:
            buf.append(obj.ioa & 0xFF)
            buf.append((obj.ioa >> 8) & 0xFF)
            buf.append((obj.ioa >> 16) & 0xFF)
            self._encode_value(buf, obj)

        return bytes(buf)

    def _encode_value(self, buf: bytearray, obj: InformationObject):
        tid = self.type_id

        if tid in (TypeID.M_SP_NA_1, TypeID.M_SP_TB_1):
            val = (1 if obj.value else 0) | (obj.quality & 0xF1)
            buf.append(val)
            if tid == TypeID.M_SP_TB_1 and obj.timestamp:
                buf.extend(obj.timestamp.encode())

        elif tid in (TypeID.M_DP_NA_1, TypeID.M_DP_TB_1):
            val = (int(obj.value) & 0x03) | (obj.quality & 0xF0)
            buf.append(val)
            if tid == TypeID.M_DP_TB_1 and obj.timestamp:
                buf.extend(obj.timestamp.encode())

        elif tid == TypeID.M_ST_NA_1:
            # VTI: bits 0-6 = posicao (-64..63), bit 7 = transient flag
            vti = (int(obj.value) & 0x7F)
            buf.append(vti)
            buf.append(obj.quality & 0xF1)

        elif tid in (TypeID.M_ME_NA_1, TypeID.M_ME_TD_1):
            nva = int(max(-1.0, min(1.0, obj.value)) * 32767)
            buf.extend(struct.pack('<h', nva))
            buf.append(obj.quality)
            if tid == TypeID.M_ME_TD_1 and obj.timestamp:
                buf.extend(obj.timestamp.encode())

        elif tid in (TypeID.M_ME_NB_1, TypeID.M_ME_TE_1):
            buf.extend(struct.pack('<h', max(-32768, min(32767, int(obj.value)))))
            buf.append(obj.quality)
            if tid == TypeID.M_ME_TE_1 and obj.timestamp:
                buf.extend(obj.timestamp.encode())

        elif tid in (TypeID.M_ME_NC_1, TypeID.M_ME_TF_1):
            buf.extend(struct.pack('<f', float(obj.value)))
            buf.append(obj.quality)
            if tid == TypeID.M_ME_TF_1 and obj.timestamp:
                buf.extend(obj.timestamp.encode())

        elif tid == TypeID.M_IT_NA_1:
            # BCR: 4 bytes contador (int32 LE) + 1 byte sequencia/qualidade
            counter = int(obj.value) if obj.value is not None else 0
            buf.extend(struct.pack('<i', counter))
            buf.append(obj.quality & 0x3F)  # bits 0-4: sequencia, bit 5: cy, bit 6: ca, bit 7: iv

        elif tid == TypeID.C_SC_NA_1:
            buf.append(1 if obj.value else 0)

        elif tid == TypeID.C_DC_NA_1:
            buf.append(int(obj.value) & 0x03)

        elif tid == TypeID.C_RC_NA_1:
            # RCS: bits 0-1 (0=nenhum, 1=decrement, 2=increment), bits 2-7 = qualificador
            rcs = (int(obj.value) & 0x03) if obj.value is not None else 0
            buf.append(rcs)

        elif tid == TypeID.C_SE_NA_1:
            # Set-point normalizado: int16 LE + 1 byte qualificador
            nva = int(max(-1.0, min(1.0, float(obj.value) if obj.value else 0.0)) * 32767)
            buf.extend(struct.pack('<h', nva))
            buf.append(0x00)  # qualificador

        elif tid == TypeID.C_SE_NB_1:
            # Set-point escalonado: int16 LE + 1 byte qualificador
            sva = max(-32768, min(32767, int(obj.value) if obj.value is not None else 0))
            buf.extend(struct.pack('<h', sva))
            buf.append(0x00)

        elif tid == TypeID.C_SE_NC_1:
            buf.extend(struct.pack('<f', float(obj.value)))
            buf.append(0x00)

        elif tid == TypeID.C_IC_NA_1:
            buf.append(obj.value if obj.value else 20)

        elif tid == TypeID.C_CS_NA_1:
            ts = obj.timestamp or CP56Time2a.now()
            buf.extend(ts.encode())

        elif tid == TypeID.M_EI_NA_1:
            buf.append(obj.value if obj.value else 0)

        elif tid == TypeID.C_CI_NA_1:
            buf.append(obj.value if obj.value else 5)

        elif tid == TypeID.C_RD_NA_1:
            pass  # C_RD_NA_1 nao tem valor alem do IOA

        else:
            if obj.value is not None:
                if isinstance(obj.value, bytes):
                    buf.extend(obj.value)
                elif isinstance(obj.value, int):
                    buf.append(obj.value & 0xFF)


def frame_label(decoded: dict) -> dict:
    """Rotulo legivel de um frame ja decodificado, para log e MITM.

    Devolve sempre 'name' e 'ftype'; para I-frames com ASDU acrescenta a causa
    de transmissao ('cot') e a quantidade de objetos ('n').
    """
    ftype = decoded.get("type", "?")

    if ftype == "U":
        return {"name": decoded.get("function_name", "U-frame"), "ftype": "U"}

    if ftype == "S":
        return {"name": f"S-frame RSN={decoded.get('rsn', 0)}", "ftype": "S"}

    asdu = decoded.get("asdu")
    if ftype == "I" and asdu:
        return {
            "name": asdu.get("type_name", "?"),
            "ftype": "I",
            "cot": asdu.get("cause_name"),
            "n": asdu.get("num_objects"),
        }

    if ftype == "I":
        return {"name": f"I-frame SSN={decoded.get('ssn', 0)}", "ftype": "I"}

    return {"name": ftype, "ftype": ftype}


class APDUCodec:
    """Codificador/decodificador de frames IEC 104 APDU."""

    @staticmethod
    def encode_i_frame(ssn: int, rsn: int, asdu: ASDU) -> bytes:
        asdu_bytes = asdu.encode()
        length = 4 + len(asdu_bytes)
        buf = bytearray()
        buf.append(START_BYTE)
        buf.append(length)
        buf.extend(struct.pack('<H', (ssn << 1) & 0xFFFE))
        buf.extend(struct.pack('<H', (rsn << 1) & 0xFFFE))
        buf.extend(asdu_bytes)
        return bytes(buf)

    @staticmethod
    def encode_s_frame(rsn: int) -> bytes:
        buf = bytearray()
        buf.append(START_BYTE)
        buf.append(4)
        buf.append(0x01)
        buf.append(0x00)
        buf.extend(struct.pack('<H', (rsn << 1) & 0xFFFE))
        return bytes(buf)

    @staticmethod
    def encode_u_frame(func: int) -> bytes:
        buf = bytearray()
        buf.append(START_BYTE)
        buf.append(4)
        buf.append(func)
        buf.append(0x00)
        buf.append(0x00)
        buf.append(0x00)
        return bytes(buf)

    @staticmethod
    def decode_frame(data: bytes) -> dict:
        if len(data) < 6 or data[0] != START_BYTE:
            return {"type": "invalid", "raw": data}

        length = data[1]
        ctrl = data[2:6]

        if ctrl[0] & 0x01 == 0:
            # I-frame
            ssn = struct.unpack('<H', ctrl[0:2])[0] >> 1
            rsn = struct.unpack('<H', ctrl[2:4])[0] >> 1
            asdu_data = data[6:2 + length] if len(data) >= 2 + length else b''
            asdu_header = APDUCodec._decode_asdu_header(asdu_data) if asdu_data else None
            objects = None
            if asdu_header and asdu_data:
                objects = APDUCodec._decode_asdu_objects(
                    asdu_data, asdu_header["type_id"], asdu_header["num_objects"]
                )
            return {
                "type": "I", "ssn": ssn, "rsn": rsn,
                "asdu_raw": asdu_data,
                "asdu": asdu_header,
                "objects": objects,
            }
        elif ctrl[0] & 0x03 == 0x01:
            # S-frame
            rsn = struct.unpack('<H', ctrl[2:4])[0] >> 1
            return {"type": "S", "rsn": rsn}
        elif ctrl[0] & 0x03 == 0x03:
            # U-frame
            func = ctrl[0]
            func_name = {
                STARTDT_ACT: "STARTDT_ACT", STARTDT_CON: "STARTDT_CON",
                STOPDT_ACT: "STOPDT_ACT",   STOPDT_CON: "STOPDT_CON",
                TESTFR_ACT: "TESTFR_ACT",   TESTFR_CON: "TESTFR_CON",
            }.get(func, f"UNKNOWN(0x{func:02X})")
            return {"type": "U", "function": func, "function_name": func_name}

        return {"type": "unknown", "raw": data}

    @staticmethod
    def _decode_asdu_header(data: bytes) -> Optional[dict]:
        if len(data) < 6:
            return None
        type_id = data[0]
        vsq = data[1]
        sq = (vsq >> 7) & 0x01
        num_objects = vsq & 0x7F
        cause = data[2] & 0x3F
        negative = bool(data[2] & 0x40)
        test = bool(data[2] & 0x80)
        originator = data[3]
        common_address = struct.unpack('<H', data[4:6])[0]

        try:
            type_name = TypeID(type_id).name
        except ValueError:
            type_name = f"TYPE_{type_id}"
        try:
            cause_name = CauseOfTransmission(cause).name
        except ValueError:
            cause_name = f"COT_{cause}"

        return {
            "type_id": type_id, "type_name": type_name,
            "sq": sq, "num_objects": num_objects,
            "cause": cause, "cause_name": cause_name,
            "negative": negative, "test": test,
            "originator": originator, "common_address": common_address,
        }

    @staticmethod
    def _decode_asdu_objects(
        data: bytes, type_id: int, num_objects: int
    ) -> Optional[List[dict]]:
        """Extrai os Information Objects do corpo do ASDU (apos os 6 bytes de cabecalho)."""
        if len(data) < 6:
            return None

        body = data[6:]
        # Tamanho do valor por tipo (alem dos 3 bytes de IOA)
        value_sizes = {
            TypeID.M_SP_NA_1: 1,   # SIQ
            TypeID.M_DP_NA_1: 1,   # DIQ
            TypeID.M_ST_NA_1: 2,   # VTI + QDS
            TypeID.M_ME_NA_1: 3,   # NVA(2) + QDS
            TypeID.M_ME_NB_1: 3,   # SVA(2) + QDS
            TypeID.M_ME_NC_1: 5,   # IEEE754(4) + QDS
            TypeID.M_IT_NA_1: 5,   # BCR(4) + SQ
            TypeID.M_SP_TB_1: 8,   # SIQ + CP56Time2a(7)
            TypeID.M_DP_TB_1: 8,   # DIQ + CP56Time2a(7)
            TypeID.M_ME_TD_1: 10,  # NVA(2) + QDS + CP56Time2a(7)
            TypeID.M_ME_TE_1: 10,  # SVA(2) + QDS + CP56Time2a(7)
            TypeID.M_ME_TF_1: 12,  # IEEE754(4) + QDS + CP56Time2a(7)
            TypeID.M_EI_NA_1: 1,   # COI
            TypeID.C_SC_NA_1: 1,   # SCO
            TypeID.C_DC_NA_1: 1,   # DCO
            TypeID.C_RC_NA_1: 1,   # RCS
            TypeID.C_SE_NA_1: 3,   # NVA(2) + QL
            TypeID.C_SE_NB_1: 3,   # SVA(2) + QL
            TypeID.C_SE_NC_1: 5,   # IEEE754(4) + QL
            TypeID.C_IC_NA_1: 1,   # QOI
            TypeID.C_CI_NA_1: 1,   # QCC
            TypeID.C_RD_NA_1: 0,   # sem valor
            TypeID.C_CS_NA_1: 7,   # CP56Time2a
        }

        try:
            val_size = value_sizes.get(type_id, -1)
        except Exception:
            return None

        if val_size < 0:
            return None

        obj_size = 3 + val_size
        results = []
        pos = 0

        for _ in range(num_objects):
            if pos + 3 > len(body):
                break
            ioa = body[pos] | (body[pos + 1] << 8) | (body[pos + 2] << 16)
            raw = body[pos + 3: pos + obj_size] if val_size > 0 else b''
            pos += obj_size

            obj: dict = {"ioa": ioa, "raw": raw, "value": None, "quality": 0}

            try:
                if type_id in (TypeID.M_SP_NA_1, TypeID.M_SP_TB_1):
                    obj["value"] = bool(raw[0] & 0x01)
                    obj["quality"] = raw[0] & 0xF0

                elif type_id in (TypeID.M_DP_NA_1, TypeID.M_DP_TB_1):
                    obj["value"] = raw[0] & 0x03
                    obj["quality"] = raw[0] & 0xF0

                elif type_id == TypeID.M_ST_NA_1:
                    obj["value"] = raw[0] & 0x7F
                    obj["quality"] = raw[1] if len(raw) > 1 else 0

                elif type_id in (TypeID.M_ME_NA_1, TypeID.M_ME_TD_1):
                    obj["value"] = struct.unpack('<h', raw[:2])[0] / 32767.0
                    obj["quality"] = raw[2] if len(raw) > 2 else 0

                elif type_id in (TypeID.M_ME_NB_1, TypeID.M_ME_TE_1):
                    obj["value"] = struct.unpack('<h', raw[:2])[0]
                    obj["quality"] = raw[2] if len(raw) > 2 else 0

                elif type_id in (TypeID.M_ME_NC_1, TypeID.M_ME_TF_1):
                    obj["value"] = struct.unpack('<f', raw[:4])[0]
                    obj["quality"] = raw[4] if len(raw) > 4 else 0

                elif type_id == TypeID.M_IT_NA_1:
                    obj["value"] = struct.unpack('<i', raw[:4])[0]
                    obj["quality"] = raw[4] if len(raw) > 4 else 0

                elif type_id == TypeID.C_SC_NA_1:
                    obj["value"] = bool(raw[0] & 0x01)

                elif type_id == TypeID.C_DC_NA_1:
                    obj["value"] = raw[0] & 0x03

                elif type_id == TypeID.C_RC_NA_1:
                    obj["value"] = raw[0] & 0x03

                elif type_id in (TypeID.C_SE_NA_1,):
                    obj["value"] = struct.unpack('<h', raw[:2])[0] / 32767.0

                elif type_id in (TypeID.C_SE_NB_1,):
                    obj["value"] = struct.unpack('<h', raw[:2])[0]

                elif type_id in (TypeID.C_SE_NC_1,):
                    obj["value"] = struct.unpack('<f', raw[:4])[0]

                elif type_id == TypeID.C_IC_NA_1:
                    obj["value"] = raw[0] if raw else 20

                elif type_id == TypeID.C_CS_NA_1:
                    if len(raw) >= 7:
                        obj["value"] = CP56Time2a.decode(raw[:7])

            except Exception:
                pass

            results.append(obj)

        return results if results else None

    @staticmethod
    def read_frame_from_buffer(buffer: bytes) -> Tuple[Optional[bytes], bytes]:
        """Extrai um frame completo do buffer."""
        if len(buffer) < 2:
            return None, buffer
        idx = buffer.find(bytes([START_BYTE]))
        if idx == -1:
            return None, b''
        if idx > 0:
            buffer = buffer[idx:]
        if len(buffer) < 2:
            return None, buffer
        length = buffer[1]
        total = 2 + length
        if len(buffer) < total:
            return None, buffer
        return buffer[:total], buffer[total:]
