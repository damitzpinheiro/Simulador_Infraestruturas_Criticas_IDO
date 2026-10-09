"""Sincronizacao planta<->RTU para a operacao DISTRIBUIDA (uma entidade por VM).

Na versao de host unico, o `CityGridModel` (a fisica) e os slaves vivem no mesmo
processo, e o modelo toca os pontos em memoria. Quando se coloca UMA entidade por
VM, isso deixa de ser possivel: cada VM e um processo separado. Mas a cascata
depende de uma grandeza GLOBAL (a frequencia), que precisa ser calculada num lugar
so -- porque, na vida real, quem "acopla" as subestacoes e a propria rede eletrica
(as linhas), nao uma rede de dados.

Este modulo implementa esse acoplamento como um COORDENADOR central (a planta
fisica simulada) e um canal de sync fino ("I/O fino") ate cada RTU:

  COORDENADOR (roda o CityGridModel)                 RTU (uma VM, so protocolo)
     |  a cada tick, para cada RTU:                     |  serve IEC104/DNP3/OPC-UA
     |    envia  {"set": {sinal: valor, ...}}  ---------> aplica nos pontos (display)
     |    recebe {"inputs": {sinal: valor}}   <--------- comandos que chegaram
     |    aplica model.command(...)                      pelo protocolo (ou MITM)

Importante (fidelidade): esse canal NAO e a rede SCADA. Ele representa a fiacao de
campo do RTU + o acoplamento eletrico pelas linhas -- coisas que na realidade nao
sao rede de dados. E andaime da simulacao, out-of-band: rode-o numa rede/porta
separada e NAO o inclua nas capturas/ataques (que ficam nos links RTU<->master).

Protocolo do canal: JSON por linha (newline-delimited), sobre TCP. O coordenador e
o CLIENTE (poll), o RTU e o SERVIDOR -- espelhando o padrao master/RTU.
"""

import asyncio
import json
import logging

from .engine import BaseProtocolGenerator, SessionState

logger = logging.getLogger("scada_trafgen.plant_sync")


# ---------------------------------------------------------------------------
# Lado do RTU
# ---------------------------------------------------------------------------
class RemoteModelClient:
    """Fica no lugar do modelo, do lado do RTU. Nao calcula fisica: guarda os
    valores empurrados pelo coordenador (para os pontos exibirem) e bufferiza os
    comandos locais (recebidos pelo protocolo) para devolver ao coordenador.

    Implementa a mesma interface que o `ProcessModelDriver` espera de um modelo:
    advance(dt) [no-op], get(sig), command(sig, val), signals().
    """

    def __init__(self):
        self._values = {}      # sinal -> valor (empurrado pelo coordenador)
        self._pending = {}     # sinal -> valor (comandos locais a enviar)

    def advance(self, dt):
        pass                   # a fisica e remota (no coordenador)

    def get(self, sig):
        return self._values.get(sig)

    def command(self, sig, val):
        # chamado por apply_write() do ponto quando o master/MITM comanda
        self._pending[sig] = val

    def signals(self):
        return set(self._values.keys())

    # usados pelo servidor de sync:
    def apply_set(self, d):
        if d:
            self._values.update(d)

    def take_pending(self):
        p = self._pending
        self._pending = {}
        return p


class PlantLinkServer(BaseProtocolGenerator):
    """Servidor de sync no RTU: o coordenador conecta e, a cada poll, empurra os
    valores calculados e leva os comandos pendentes."""

    def __init__(self, config: dict, remote: RemoteModelClient):
        super().__init__(config)
        self.bind = config.get("host", "0.0.0.0")
        self.port = int(config.get("port", 7070))
        self.remote = remote
        self.server = None

    @property
    def protocol_name(self) -> str:
        return "PlantLink-RTU"

    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self.server = await asyncio.start_server(self._handle, self.bind, self.port)
        addr = self.server.sockets[0].getsockname()
        self.state = SessionState.ACTIVE
        logger.info(f"PlantLink (RTU) ouvindo em {addr[0]}:{addr[1]} "
                    f"(canal fisico com o coordenador)")
        try:
            async with self.server:
                await self.server.serve_forever()
        except asyncio.CancelledError:
            pass
        finally:
            self.state = SessionState.STOPPED

    async def _handle(self, reader, writer):
        peer = writer.get_extra_info("peername")
        logger.info(f"PlantLink: coordenador conectou de {peer}")
        try:
            while not self._stop_event.is_set():
                linha = await reader.readline()
                if not linha:
                    break
                try:
                    msg = json.loads(linha.decode())
                except (ValueError, UnicodeDecodeError):
                    continue
                self.remote.apply_set(msg.get("set"))
                resp = json.dumps({"inputs": self.remote.take_pending()}) + "\n"
                writer.write(resp.encode())
                await writer.drain()
        except (asyncio.CancelledError, ConnectionError):
            pass
        finally:
            writer.close()
            logger.info("PlantLink: coordenador desconectou")


# ---------------------------------------------------------------------------
# Lado do coordenador
# ---------------------------------------------------------------------------
class PlantCoordinator(BaseProtocolGenerator):
    """Roda a fisica (CityGridModel) e sincroniza cada RTU remoto a cada tick.

    remotes: [{id?, host, port}]. O coordenador e o cliente (poll): envia os
    valores calculados de TODOS os sinais e recebe os comandos pendentes de cada
    RTU, aplicando-os no modelo (que entao cascateia)."""

    def __init__(self, config: dict, model, engine=None):
        super().__init__(config)
        self.model = model
        self.tick = float(config.get("tick_s", 1.0))
        self.remotes = list(config.get("remotes", []) or [])
        self._conns = {}       # key -> (reader, writer)

    @property
    def protocol_name(self) -> str:
        return "PlantLink-Coord"

    def _key(self, r):
        return f"{r.get('id', r['host'])}:{r['port']}"

    async def _ensure_conn(self, r):
        key = self._key(r)
        if key in self._conns:
            return self._conns[key]
        try:
            reader, writer = await asyncio.open_connection(r["host"], r["port"])
            self._conns[key] = (reader, writer)
            logger.info(f"PlantLink (coord): conectado ao RTU {key}")
            return self._conns[key]
        except OSError as e:
            logger.warning(f"PlantLink (coord): RTU {key} indisponivel ({e})")
            return None

    async def _sync_one(self, r, snapshot):
        conn = await self._ensure_conn(r)
        if conn is None:
            return
        reader, writer = conn
        key = self._key(r)
        try:
            writer.write((json.dumps({"set": snapshot}) + "\n").encode())
            await writer.drain()
            # 5s (nao 2s): nas VMs DNP3 o event loop as vezes atende o master DNP3
            # + o Modbus do SCADA e responde este poll com >2s de atraso. Timeout
            # curto derrubava a conexao e gerava "erro (...)" (TimeoutError, str vazio).
            linha = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not linha:
                raise ConnectionError("RTU fechou a conexao")
            msg = json.loads(linha.decode())
            for sig, val in (msg.get("inputs") or {}).items():
                self.model.command(sig, val)
        except (asyncio.TimeoutError, ConnectionError, OSError, ValueError) as e:
            logger.warning(f"PlantLink (coord): erro com {key} ({e}); vai reconectar")
            try:
                writer.close()
            except OSError:
                pass
            self._conns.pop(key, None)

    async def start(self):
        self.state = SessionState.ACTIVE
        self._stop_event.clear()
        logger.info(f"Coordenador da planta ativo: {len(self.remotes)} RTUs, "
                    f"tick {self.tick}s")
        while not self._stop_event.is_set():
            self.model.advance(self.tick)
            snapshot = {sig: self.model.get(sig) for sig in self.model.signals()}
            for r in self.remotes:
                await self._sync_one(r, snapshot)
            try:
                await asyncio.sleep(self.tick)
            except asyncio.CancelledError:
                break
        for _, writer in self._conns.values():
            try:
                writer.close()
            except OSError:
                pass
        self.state = SessionState.STOPPED
