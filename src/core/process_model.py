"""Modelo de processo de uma subestacao (Passo 2 da integracao com o SCADA-LTS).

Ate aqui cada ponto dos slaves variava de forma independente (senoide + ruido ou
toggle aleatorio). Isso da uma HMI "viva", mas sem sentido fisico: a corrente nao
tem relacao com o disjuntor, a tensao nao reage a carga. Este modulo introduz uma
FISICA COERENTE compartilhada: uma unica subestacao cujo estado e observado pelos
tres protocolos (IEC104, DNP3, OPC-UA) e pelo gateway Modbus.

Relacoes modeladas (simples, mas coerentes e demonstraveis):

  disjuntor aberto  -> corrente do alimentador cai a ~0
  corrente          -> potencia (P ~ V*I) e afunda a tensao da barra (queda com a carga)
  regulador de tap  -> corrige a tensao de volta ao setpoint
  carga total       -> temperatura do trafo sobe (atraso termico de 1a ordem)
  potencia          -> integra em energia (contadores)
  trip aleatorio    -> abre um disjuntor sozinho; religa depois de um tempo
  comando da HMI    -> abre/fecha disjuntor (via Modbus/IEC104) e a cascata reage

O modelo NAO fala nenhum protocolo: ele so mantem o estado fisico. Um driver
(`ProcessModelDriver`) chama `advance(dt)` periodicamente e empurra os valores para
os pontos dos slaves (que passam a ser meras "vistas" do modelo). Os pontos param de
randomizar sozinhos e apenas reportam o que o modelo dita.
"""

import math
import random
import time
import logging

from .engine import BaseProtocolGenerator, SessionState

logger = logging.getLogger("scada_trafgen.process_model")


class SubstationModel:
    """Estado fisico de uma subestacao com dois alimentadores medidos."""

    def __init__(self, config: dict = None):
        cfg = config or {}
        # Disjuntores (entradas do modelo): True = fechado. breaker.3 comeca aberto.
        self.breaker = {1: True, 2: True, 3: False, 4: True}
        self._trip_reclose_at = {}         # {id: instante para religar}

        # Cargas nominais dos alimentadores medidos (A) e suave variacao
        self._base_load = {1: cfg.get("feeder1_load", 450.0),
                           2: cfg.get("feeder2_load", 320.0)}
        self._current = {1: 450.0, 2: 320.0}     # corrente atual (suavizada)
        self._phase = {1: random.random() * math.tau, 2: random.random() * math.tau}

        # Tensoes de barra (kV)
        self.nominal_a = cfg.get("bus_a_kv", 138.5)   # barra AT
        self.nominal_b = cfg.get("bus_b_kv", 69.0)    # barra MT (a jusante do trafo)
        self._volt_a = self.nominal_a
        self._volt_b = self.nominal_b

        # Regulador de tap: corrige a tensao da barra A para o setpoint
        self.setpoint_voltage = cfg.get("setpoint_kv", 138.0)
        self._tap = 0                                 # passos (cada passo ~0.5 kV)
        self._tap_max = 8

        # Termico do trafo (atraso de 1a ordem)
        self._ambient = cfg.get("ambient_c", 25.0)
        self._temp = 45.0
        self._temp2 = 42.0
        self._tau = cfg.get("thermal_tau_s", 90.0)    # constante de tempo termica

        # Energia acumulada (contadores)
        self._energy_active = cfg.get("energy0_mwh", 10520.0)
        self._energy_react = cfg.get("energy0_mvarh", 3140.0)

        # Probabilidade de trip espontaneo por disjuntor por segundo
        self._trip_rate = cfg.get("trip_rate", 0.0008)
        self._reclose_s = cfg.get("reclose_s", 20.0)

        self._pf = 0.92                                # fator de potencia
        self._power = 62.0                             # potencia ativa do alimentador 1 (MW)
        self._sig = {}
        self._compute_signals()

    # ------------------------------------------------------------------
    # Fisica
    # ------------------------------------------------------------------
    def advance(self, dt: float):
        now = time.monotonic()

        # 1) Trips espontaneos e religamento automatico
        for b in (1, 2, 3, 4):
            if self.breaker[b] and random.random() < self._trip_rate * dt:
                self.breaker[b] = False
                self._trip_reclose_at[b] = now + self._reclose_s
                logger.info(f"[modelo] TRIP espontaneo do disjuntor {b} "
                            f"(religa em {self._reclose_s:.0f}s)")
        for b, t in list(self._trip_reclose_at.items()):
            if now >= t:
                self.breaker[b] = True
                del self._trip_reclose_at[b]
                logger.info(f"[modelo] religamento automatico do disjuntor {b}")

        # 2) Correntes dos alimentadores (aproximam suavemente o alvo)
        t = now
        for i in (1, 2):
            if self.breaker[i]:
                target = self._base_load[i] * (1.0 + 0.18 * math.sin(t * 0.03 + self._phase[i]))
                target += random.gauss(0, self._base_load[i] * 0.01)
            else:
                target = random.uniform(0.0, 2.0)     # corrente de fuga ~0
            # suavizacao exponencial (rampa fisica, sem degraus bruscos)
            alpha = min(1.0, dt / 2.0)
            self._current[i] += (target - self._current[i]) * alpha

        i1 = max(0.0, self._current[1])
        i2 = max(0.0, self._current[2])
        # cargas fixas das outras baias (contribuem para a queda de tensao)
        extra = (150.0 if self.breaker[3] else 0.0) + (90.0 if self.breaker[4] else 0.0)
        total_current = i1 + i2 + extra

        # 3) Regulador de tap: puxa a tensao da barra A para o setpoint
        v_no_reg = self.nominal_a - 8.0 * (total_current / 1000.0)
        erro = self.setpoint_voltage - (v_no_reg + self._tap * 0.5)
        if erro > 0.4 and self._tap < self._tap_max:
            self._tap += 1
        elif erro < -0.4 and self._tap > -self._tap_max:
            self._tap -= 1

        # 4) Tensoes de barra (afundam com a carga; ruido leve)
        va = v_no_reg + self._tap * 0.5 + random.gauss(0, 0.15)
        self._volt_a = max(120.0, min(145.0, va))
        # barra B (a jusante): so existe se o alimentador 1 (entrada do trafo) fechado
        if self.breaker[1]:
            ratio = self.nominal_b / self.nominal_a
            vb = self._volt_a * ratio - 4.0 * (i2 / 1000.0) + random.gauss(0, 0.1)
            self._volt_b = max(60.0, min(75.0, vb))
        else:
            self._volt_b += (0.0 - self._volt_b) * min(1.0, dt / 3.0)   # colapsa

        # 5) Potencia ativa do alimentador 1 (MW): P ~ sqrt(3)*V*I*fp, escalada
        #    para bater com a faixa do datapoint (0..100 MW, nominal ~62)
        p1 = 1.732 * self._volt_a * (i1 / 1000.0) * self._pf * 0.45
        self._power = max(0.0, min(100.0, p1))
        p_total = self._power + 1.732 * self._volt_b * (i2 / 1000.0) * self._pf * 0.45

        # 6) Temperatura do trafo (atraso de 1a ordem em direcao a ambiente + carga)
        load_frac = total_current / 900.0
        alvo_temp = self._ambient + 55.0 * load_frac
        k = min(1.0, dt / self._tau)
        self._temp += (alvo_temp - self._temp) * k
        self._temp2 += ((alvo_temp - 3.0) - self._temp2) * k

        # 7) Energia acumulada (MWh / MVarh)
        self._energy_active += p_total * dt / 3600.0
        self._energy_react += p_total * 0.3 * dt / 3600.0

        self._compute_signals()

    def _compute_signals(self):
        self._sig = {
            "breaker.1": self.breaker[1],
            "breaker.2": self.breaker[2],
            "breaker.3": self.breaker[3],
            "breaker.4": self.breaker[4],
            "isolator.1": 2 if self.breaker[1] else 1,     # double-point (2=fechado,1=aberto)
            "bus.a.voltage": round(self._volt_a, 2),
            "bus.b.voltage": round(self._volt_b, 2),
            "feeder.1.current": round(max(0.0, self._current[1]), 1),
            "feeder.2.current": round(max(0.0, self._current[2]), 1),
            "power.active": round(self._power, 2),
            "transformer.temp": round(self._temp, 1),
            "transformer.temp2": round(self._temp2, 1),
            "tap.position": self._tap,
            "energy.active": int(self._energy_active),
            "energy.reactive": int(self._energy_react),
            "setpoint.voltage": round(self.setpoint_voltage, 1),
        }

    # ------------------------------------------------------------------
    # Interface para os pontos
    # ------------------------------------------------------------------
    def get(self, signal: str):
        return self._sig.get(signal)

    def command(self, signal: str, value):
        """Aplica um comando externo (HMI/MITM/master) a uma ENTRADA do modelo."""
        if signal.startswith("breaker."):
            b = int(signal.split(".")[1])
            novo = bool(value)
            if self.breaker.get(b) != novo:
                self.breaker[b] = novo
                self._trip_reclose_at.pop(b, None)   # comando manual cancela religamento
                logger.info(f"[modelo] comando externo: disjuntor {b} -> "
                            f"{'FECHADO' if novo else 'ABERTO'}")
        elif signal == "setpoint.voltage":
            try:
                self.setpoint_voltage = float(value)
                logger.info(f"[modelo] novo setpoint de tensao: {self.setpoint_voltage:.1f} kV")
            except (TypeError, ValueError):
                pass
        # sinais de saida (tensao, corrente, ...) nao sao comandaveis: ignora


# Mapas de binding padrao: ponto do slave -> sinal do modelo. is_input=True nas
# entradas (disjuntores, setpoint) para que escrita da HMI va ao modelo.
IEC104_MAP = {
    100: ("breaker.1", True), 101: ("breaker.2", True),
    102: ("breaker.3", True), 103: ("breaker.4", True),
    200: ("bus.a.voltage", False), 201: ("bus.b.voltage", False),
    300: ("feeder.1.current", False), 301: ("feeder.2.current", False),
    400: ("power.active", False),
    500: ("transformer.temp", False), 501: ("transformer.temp2", False),
    600: ("isolator.1", False),
}

# DNP3: chave (group, index) -> (sinal, is_input)
DNP3_MAP = {
    (1, 0): ("breaker.1", True), (1, 1): ("breaker.2", True),
    (1, 2): ("breaker.3", True), (1, 3): ("breaker.4", True),
    (30, 0): ("bus.a.voltage", False), (30, 1): ("bus.b.voltage", False),
    (30, 2): ("feeder.1.current", False), (30, 3): ("feeder.2.current", False),
    (30, 4): ("power.active", False),
    (20, 0): ("energy.active", False), (20, 1): ("energy.reactive", False),
}

OPCUA_MAP = {
    "Disjuntor_1": ("breaker.1", True), "Disjuntor_2": ("breaker.2", True),
    "Tensao_Barra_A": ("bus.a.voltage", False), "Tensao_Barra_B": ("bus.b.voltage", False),
    "Corrente_Fase_1": ("feeder.1.current", False),
    "Potencia_Ativa": ("power.active", False),
    "Temperatura_Trafo": ("transformer.temp", False),
    "Setpoint_Tensao": ("setpoint.voltage", True),
}


def bind_slaves(model: SubstationModel, engine) -> int:
    """Amarra os pontos dos slaves registrados aos sinais do modelo. Retorna o total amarrado."""
    n = 0
    for name, gen in engine.generators.items():
        # IEC104 slave
        if isinstance(getattr(gen, "datapoints", None), dict):
            for ioa, dp in gen.datapoints.items():
                if ioa in IEC104_MAP:
                    sig, is_in = IEC104_MAP[ioa]
                    dp.bind_model(model, sig, is_in); n += 1
        # DNP3 outstation
        elif isinstance(getattr(gen, "points", None), list):
            for p in gen.points:
                key = (p.group, p.index)
                if key in DNP3_MAP:
                    sig, is_in = DNP3_MAP[key]
                    p.bind_model(model, sig, is_in); n += 1
        # OPC-UA server
        elif isinstance(getattr(gen, "variaveis", None), list):
            for v in gen.variaveis:
                if v.nome in OPCUA_MAP:
                    sig, is_in = OPCUA_MAP[v.nome]
                    v.bind_model(model, sig, is_in); n += 1
    return n


class ProcessModelDriver(BaseProtocolGenerator):
    """Task que evolui a fisica e empurra os valores para os pontos dos slaves."""

    def __init__(self, config: dict, model: SubstationModel, engine):
        super().__init__(config)
        self.model = model
        self._engine = engine
        self.tick = float(config.get("tick_s", 1.0))
        self._bindings = []      # [(point, signal)]

    @property
    def protocol_name(self) -> str:
        return "ProcessModel"

    def rebind(self):
        """Recoleta os pontos amarrados. Chamar apos recriar um slave (ex.: MITM
        move a porta e o slave e reconstruido -> os pontos novos precisam voltar
        para a lista que o driver empurra)."""
        self._collect_bindings()
        logger.info(f"Modelo de processo: rebind, {len(self._bindings)} pontos amarrados")

    def _collect_bindings(self):
        self._bindings = []
        for name, gen in self._engine.generators.items():
            if isinstance(getattr(gen, "datapoints", None), dict):
                for dp in gen.datapoints.values():
                    if getattr(dp, "_model_signal", None):
                        self._bindings.append(dp)
            elif isinstance(getattr(gen, "points", None), list):
                for p in gen.points:
                    if getattr(p, "_model_signal", None):
                        self._bindings.append(p)
            elif isinstance(getattr(gen, "variaveis", None), list):
                for v in gen.variaveis:
                    if getattr(v, "_model_signal", None):
                        self._bindings.append(v)

    async def start(self):
        import asyncio
        self.state = SessionState.ACTIVE
        self._stop_event.clear()
        self._collect_bindings()
        logger.info(f"Modelo de processo ativo: {len(self._bindings)} pontos amarrados "
                    f"(tick {self.tick}s)")
        while not self._stop_event.is_set():
            self.model.advance(self.tick)
            for pt in self._bindings:
                pt._set_modeled_value(self.model.get(pt._model_signal))
            try:
                await asyncio.sleep(self.tick)
            except asyncio.CancelledError:
                break
        self.state = SessionState.STOPPED
