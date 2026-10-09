"""
OPC-UA (IEC 62541) - Server.

Constroi o Address Space hierarquico Objects/Subestacao/ e atualiza os nos
periodicamente, para que os clientes recebam notificacoes DataChange.

Diferente do IEC 104 e do DNP3, aqui o codec binario e a pilha de seguranca
(SecureChannel, Session) ficam a cargo da biblioteca asyncua - por isso as
metricas deste gerador sao no nivel de servico, nao de frame.
"""

import asyncio
import math
import random
import time
import logging
from typing import List, Optional

from asyncua import Server, ua

from ...core.engine import BaseProtocolGenerator, SessionState
from ...core.load_profile import LoadProfile

logger = logging.getLogger("scada_trafgen.opcua.server")

DEFAULT_PORT = 4840


class OPCUAVariable:
    """Variavel do Address Space com modelo fisico de variacao."""

    def __init__(self, nome: str, valor, vmin=0.0, vmax=100.0,
                 noise=1.0, drift=0.0, change_prob=0.02, booleano=False):
        self.nome = nome
        self.valor = valor
        self.min = vmin
        self.max = vmax
        self.noise = noise
        self.drift = drift
        self.change_prob = change_prob
        self.booleano = booleano
        self.node = None
        self._phase = random.random() * math.tau
        self._t0 = time.time()
        # Binding ao modelo de processo (Passo 2).
        self._model = None
        self._model_signal = None
        self._model_input = False

    def bind_model(self, model, signal, is_input=False):
        self._model = model
        self._model_signal = signal
        self._model_input = is_input

    def _set_modeled_value(self, v):
        if v is not None:
            self.valor = bool(v) if isinstance(self.valor, bool) else v

    def apply_write(self, v):
        if self._model is not None and self._model_input:
            self._model.command(self._model_signal, v)
        else:
            self.valor = v

    def atualizar(self):
        if self._model_signal is not None:
            return self.valor          # amarrado ao modelo: valor ja empurrado
        if self.booleano:
            if random.random() < self.change_prob:
                self.valor = not self.valor
        else:
            t = time.time() - self._t0
            base = (self.min + self.max) / 2.0
            span = (self.max - self.min) / 2.0
            v = (base
                 + span * 0.6 * math.sin(t * 0.05 + self._phase)
                 + random.gauss(0, self.noise * 0.3)
                 + self.drift * math.sin(t * 0.001))
            self.valor = max(self.min, min(self.max, v))
        return self.valor


class OPCUAServer(BaseProtocolGenerator):
    """Servidor OPC-UA simulando uma subestacao."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.host = config.get("host", "127.0.0.1")
        self.port = config.get("port", DEFAULT_PORT)
        self.uri = config.get("uri", "http://ime.eb.br/pfc/scada")
        self.update_interval = config.get("update_interval", 2.0)

        self.server: Optional[Server] = None
        self.variaveis: List[OPCUAVariable] = self._build_variables(
            config.get("variables")
        )
        self._load_profile = LoadProfile.from_config(config.get("load_profile", {}))

    @property
    def protocol_name(self) -> str:
        return "OPCUA-Server"

    def _build_variables(self, cfg) -> List[OPCUAVariable]:
        if cfg:
            return [
                OPCUAVariable(
                    nome=v["name"], valor=v.get("value", 0.0),
                    vmin=v.get("min", 0.0), vmax=v.get("max", 100.0),
                    noise=v.get("noise", 1.0), drift=v.get("drift", 0.0),
                    change_prob=v.get("change_prob", 0.02),
                    booleano=isinstance(v.get("value"), bool),
                )
                for v in cfg
            ]

        # Mesma subestacao dos outros dois protocolos, agora modelada como objetos
        return [
            OPCUAVariable("Disjuntor_1", False, booleano=True, change_prob=0.02),
            OPCUAVariable("Disjuntor_2", True, booleano=True, change_prob=0.02),
            OPCUAVariable("Tensao_Barra_A", 138.5, 120.0, 145.0, noise=2.0, drift=0.5),
            OPCUAVariable("Tensao_Barra_B", 69.0, 60.0, 75.0, noise=1.5, drift=0.3),
            OPCUAVariable("Corrente_Fase_1", 420.0, 0.0, 800.0, noise=25.0),
            OPCUAVariable("Potencia_Ativa", 55.0, 0.0, 100.0, noise=5.0),
            OPCUAVariable("Temperatura_Trafo", 45.0, 20.0, 90.0, noise=3.0),
            OPCUAVariable("Setpoint_Tensao", 138.0, 120.0, 145.0, noise=0.0),
        ]

    # ------------------------------------------------------------------
    async def start(self):
        self.state = SessionState.CONNECTING
        self._stop_event.clear()

        self.server = Server()
        await self.server.init()
        endpoint = f"opc.tcp://{self.host}:{self.port}/scada/server/"
        self.server.set_endpoint(endpoint)
        self.server.set_server_name("PFC SCADA Traffic Generator")
        # Sem criptografia: o foco do PFC e o padrao de trafego, e um endpoint
        # aberto mantem a captura no Wireshark legivel.
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])

        idx = await self.server.register_namespace(self.uri)

        # Address Space: Objects/Subestacao/<variaveis>
        subestacao = await self.server.nodes.objects.add_object(idx, "Subestacao")
        for v in self.variaveis:
            v.node = await subestacao.add_variable(idx, v.nome, v.valor)
            await v.node.set_writable()

        logger.info(f"Server OPC-UA em {endpoint} ({len(self.variaveis)} nos)")
        logger.info(f"  Address Space: Objects/Subestacao/ (ns={idx})")

        async with self.server:
            self.state = SessionState.ACTIVE
            try:
                await self._update_loop()
            except asyncio.CancelledError:
                pass
        self.state = SessionState.STOPPED

    async def _update_loop(self):
        while not self._stop_event.is_set():
            interval = self._load_profile.next_interval(self.update_interval)
            await asyncio.sleep(interval)

            alterados = 0
            for v in self.variaveis:
                anterior = v.valor
                novo = v.atualizar()
                if novo != anterior:
                    await v.node.write_value(novo)
                    alterados += 1
                    # Cada escrita gera notificacao DataChange para os
                    # clientes inscritos: contabilizamos como trafego enviado.
                    self.stats.record_sent(self._estimate_size(novo))
            if alterados:
                logger.debug(f"  {alterados} no(s) atualizado(s)")

    @staticmethod
    def _estimate_size(valor) -> int:
        """Tamanho aproximado de um DataValue na notificacao (bytes)."""
        # NodeId + StatusCode + SourceTimestamp + ServerTimestamp + valor
        return 4 + 4 + 8 + 8 + (1 if isinstance(valor, bool) else 8)

    async def stop(self):
        self._stop_event.set()
        await super().stop()
