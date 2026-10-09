"""
OPC-UA (IEC 62541) - Client.

Estabelece SecureChannel e Session, explora o Address Space com Browse, cria
uma Subscription com MonitoredItems (operacao orientada a DataChange) e faz
Read/Write periodicos.

As metricas sao no nivel de servico (cada Read/Write/Browse/notificacao conta
como uma operacao), porque o enquadramento binario fica dentro da asyncua.
"""

import asyncio
import random
import time
import logging
from typing import List, Optional

from asyncua import Client, ua

from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.opcua.client")


class _SubHandler:
    """Recebe as notificacoes DataChange da Subscription."""

    def __init__(self, gerador: "OPCUAClient"):
        self.gerador = gerador

    def datachange_notification(self, node, val, data):
        self.gerador._on_datachange(node, val)

    def event_notification(self, event):
        self.gerador.stats.record_received(32)

    def status_change_notification(self, status):
        logger.debug(f"  StatusChange: {status}")


class OPCUAClient(BaseProtocolGenerator):
    """Cliente OPC-UA: browse, subscription e leituras/escritas periodicas."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.url = config.get("url", "opc.tcp://127.0.0.1:4840/scada/server/")
        self.startup_delay = config.get("startup_delay", 4.0)
        self.publishing_interval = config.get("publishing_interval", 1000.0)
        self.browse_interval = config.get("browse_interval", 60.0)
        self.read_interval = config.get("read_interval", 10.0)
        self.write_interval = config.get("write_interval", 25.0)
        # teto do backoff de reconexao do supervisor nativo (s)
        self.reconnect_max_delay = config.get("reconnect_max_delay", 30.0)

        self.client: Optional[Client] = None
        self.subscription = None
        self.nodes: List = []
        self._node_names = {}
        self._notificacoes = 0

        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @property
    def protocol_name(self) -> str:
        return "OPCUA-Client"

    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()
        self._notificacoes = 0

        await asyncio.sleep(self.startup_delay)
        logger.info(f"Conectando ao server OPC-UA em {self.url}...")

        self.client = Client(url=self.url)
        # auto_reconnect=True: liga o supervisor nativo do asyncua (2.x). Sem ele,
        # se o canal cair uma vez (qualquer transiente), o cliente vai a DISCONNECTED
        # e o _publish_loop interno fica preso em "client is disconnected" para
        # sempre -- os laços de leitura engolem o erro, então a Engine nunca reinicia
        # este gerador. Com o supervisor, a queda vira RECONNECTING: os requests
        # aguardam e a sessão + subscriptions se re-estabelecem sozinhas.
        await self.client.connect(auto_reconnect=True,
                                  reconnect_max_delay=self.reconnect_max_delay)
        # HEL/ACK + OpenSecureChannel + CreateSession
        self.state = SessionState.CONNECTED
        logger.info("SecureChannel e Session estabelecidos")

        try:
            await self._browse_address_space()
            await self._create_subscription()
            self.state = SessionState.ACTIVE

            tasks = [
                asyncio.create_task(self._read_loop()),
                asyncio.create_task(self._write_loop()),
                asyncio.create_task(self._browse_loop()),
            ]
            self._tasks = tasks
            await asyncio.gather(*tasks)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Erro no cliente OPC-UA: {e}")
            self.state = SessionState.ERROR
            raise
        finally:
            await self._close()

    async def _close(self):
        # O asyncua mantem um _publish_loop interno que, ao ver o canal caindo
        # durante o encerramento, loga "Publish iteration crashed" e avisos de
        # sessao/canal ja fechados. Sao ruido de teardown, nao erros reais:
        # elevamos temporariamente o nivel dos loggers do asyncua para escondê-los.
        silenced = [
            logging.getLogger("asyncua.client.ua_client"),
            logging.getLogger("asyncua.client.ua_session"),
            logging.getLogger("asyncua.client.client"),
        ]
        prev = [(lg, lg.level) for lg in silenced]
        for lg in silenced:
            lg.setLevel(logging.CRITICAL)

        self._stop_event.set()
        try:
            if self.subscription is not None:
                await asyncio.wait_for(self.subscription.delete(), timeout=2)
        except Exception:
            pass
        try:
            if self.client is not None:
                await asyncio.wait_for(self.client.disconnect(), timeout=2)
        except Exception:
            pass
        finally:
            self.subscription = None
            self.client = None
            for lg, level in prev:
                lg.setLevel(level)
        self.state = SessionState.STOPPED

    # ------------------------------------------------------------------
    async def _browse_address_space(self):
        """Servico Browse: descobre Objects/Subestacao/ e seus nos."""
        t0 = time.time()
        objects = self.client.nodes.objects
        filhos = await objects.get_children()
        self.stats.record_sent(24)
        self.stats.record_received(16 * max(1, len(filhos)))

        subestacao = None
        for filho in filhos:
            nome = (await filho.read_browse_name()).Name
            if nome == "Subestacao":
                subestacao = filho
                break

        if subestacao is None:
            logger.warning("Objeto 'Subestacao' nao encontrado no Address Space")
            return

        self.nodes = await subestacao.get_children()
        for n in self.nodes:
            self._node_names[n.nodeid] = (await n.read_browse_name()).Name

        rtt_ms = (time.time() - t0) * 1000
        self.stats.record_rtt(rtt_ms)
        logger.info(f"Browse: {len(self.nodes)} nos em Objects/Subestacao/ "
                    f"({rtt_ms:.1f}ms)")
        logger.debug(f"  nos: {list(self._node_names.values())}")

    async def _create_subscription(self):
        """Subscription + MonitoredItems: operacao orientada a mudanca."""
        handler = _SubHandler(self)
        self.subscription = await self.client.create_subscription(
            self.publishing_interval, handler
        )
        await self.subscription.subscribe_data_change(self.nodes)
        self.stats.record_sent(48)
        logger.info(f"Subscription ativa: {len(self.nodes)} MonitoredItems "
                    f"@ {self.publishing_interval:.0f}ms")

    def _on_datachange(self, node, val):
        """Callback de DataChange - o Server so publica quando o dado muda."""
        self._notificacoes += 1
        nome = self._node_names.get(node.nodeid, str(node))
        tamanho = 25 if not isinstance(val, bool) else 18
        self.stats.record_received(tamanho)
        logger.debug(f"  << DataChange {nome} = {val}")

    # ------------------------------------------------------------------
    async def _read_loop(self):
        """Read explicito de todos os nos, medindo RTT ponta a ponta."""
        while not self._stop_event.is_set():
            await asyncio.sleep(self.read_interval)
            if not self.nodes:
                continue
            try:
                t0 = time.time()
                valores = await asyncio.gather(
                    *(n.read_value() for n in self.nodes), return_exceptions=True
                )
                rtt_ms = (time.time() - t0) * 1000
                self.stats.record_rtt(rtt_ms)
                self.stats.record_sent(8 * len(self.nodes))
                self.stats.record_received(25 * len(self.nodes))
                ok = sum(1 for v in valores if not isinstance(v, Exception))
                # mostra os VALORES lidos (a "tela do operador"): assim uma
                # telemetria falsificada por um MITM OPC-UA fica visivel aqui.
                pares = []
                for n, v in zip(self.nodes, valores):
                    nome = self._node_names.get(n.nodeid, str(n))
                    pares.append(f"{nome}={v}" if not isinstance(v, Exception)
                                 else f"{nome}=ERR")
                logger.info(f">> Read x{len(self.nodes)}: {ok} valores "
                            f"({rtt_ms:.1f}ms) | " + " | ".join(pares))
            except Exception as e:
                self.stats.record_error()
                logger.warning(f"Falha no Read: {e}")

    async def _write_loop(self):
        """Write em setpoint - o equivalente OPC-UA de um comando."""
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.write_interval)
            await asyncio.sleep(interval)

            alvo = next((n for n in self.nodes
                         if "Setpoint" in self._node_names.get(n.nodeid, "")), None)
            if alvo is None:
                continue
            try:
                novo = round(random.uniform(130.0, 142.0), 2)
                await alvo.write_value(ua.DataValue(ua.Variant(novo, ua.VariantType.Double)))
                self.stats.record_sent(32)
                logger.info(f">> Write Setpoint_Tensao = {novo}")
            except Exception as e:
                self.stats.record_error()
                logger.warning(f"Falha no Write: {e}")

    async def _browse_loop(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(self.browse_interval)
            try:
                await self._browse_address_space()
            except Exception as e:
                self.stats.record_error()
                logger.warning(f"Falha no Browse: {e}")
