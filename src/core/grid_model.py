"""Modelo de rede eletrica de uma cidade (multimaquina) para a apresentacao.

Enquanto `process_model.py` modela UMA subestacao isolada (disjuntor -> corrente ->
tensao local), este modulo modela uma REDE ACOPLADA: varias usinas e bairros que
compartilham a MESMA frequencia do sistema. E o acoplamento pela frequencia que
torna o ataque *visivel e em cascata*: derrubar uma usina nao afeta so ela --
falta geracao para a cidade inteira, a frequencia despenca e a protecao de
subfrequencia (UFLS) apaga bairros um a um.

Isto e o "modelo multimaquina" pedido para a apresentacao. Fisica deliberadamente
simples, mas COERENTE e demonstravel em segundos (nao milissegundos):

  geracao total < carga total     -> frequencia do sistema cai (equacao de swing)
  governador (droop)              -> usinas online sobem potencia para segurar f
  f < limiar de subfrequencia     -> UFLS abre o disjuntor de um bairro (corte de carga)
  bairro cortado                  -> carga cai, f se recupera (cascata escalonada)
  f muito baixa                   -> usinas restantes disparam (colapso total)
  disjuntor de barra aberto       -> tensao/corrente daquele no colapsam
  comando externo (HMI/MITM)      -> abre/fecha disjuntor e a rede reage

O modelo NAO fala nenhum protocolo. Ele so mantem o estado fisico e expoe sinais
por chaves pontilhadas (`termica.power`, `centro.breaker`, `sys.frequency`). O
mesmo `ProcessModelDriver` de process_model.py evolui a fisica (advance/get), pois
ele e agnostico ao modelo. `bind_grid()` amarra os pontos de cada slave (um por
usina/bairro) aos sinais da sua entidade.

===========================================================================
RECONFIGURAVEL (so config .yaml, SEM mexer neste arquivo)
===========================================================================
Este modelo e data-driven: as usinas e os bairros vem do bloco `grid_model:` do
config. Para montar OUTRA rede eletrica, edite so o YAML -- nao precisa programar:

  - numero de usinas e de bairros (adicione/remova itens de `generators:`/`districts:`)
  - capacidade (p_max), despacho (p_set), reserva e droop de cada usina
  - capacidade de partida a frio de cada usina (blackstart: true/false)
  - carga (load_mw), tensao de barra e prioridade de corte de cada bairro
  - inercia (inertia_mws), amortecimento (damping_mw_hz) -> velocidade da cascata
  - protecao: ligar/desligar UFLS, limiares (ufls_stages), atraso do rele (ufls_delay_s)
  - limiar de colapso total (gen_trip_hz)
  - qual protocolo/porta cada entidade usa e quais pontos ela expoe (no config dos nos)

===========================================================================
EXIGE CODIGO (adicionar/alterar logica AQUI) -- so para fenomeno FISICO novo
===========================================================================
So se voce quiser simular um efeito que este modelo ainda nao captura, por exemplo:

  - colapso de tensao / potencia reativa (VAr) com curva de tensao vs carga
  - coordenacao de protecao com curvas tempo-corrente, diferencial de trafo
  - ilhamento, controle de excitacao do gerador, rampas de partida de usina
  - outra dinamica de frequencia que nao a equacao de swing agregada

"Mais uma rede eletrica, maior/menor/diferente" e DADO (config), nao codigo.
"""

import math
import random
import logging

logger = logging.getLogger("scada_trafgen.grid_model")

F_NOMINAL = 60.0


class Generator:
    """Uma usina: potencia despachada com acao de governador (droop) sobre f."""

    def __init__(self, gid, p_max, p_set, bus_kv, online=True, droop_mw_hz=8.0,
                 blackstart=False):
        self.gid = gid
        self.p_max = float(p_max)
        self.p_set = float(p_set)      # despacho base (MW)
        self.bus_kv = float(bus_kv)
        self.online = bool(online)
        self.droop = float(droop_mw_hz)  # MW por Hz de queda (ganho do governador)
        # blackstart: usina capaz de partir a frio e energizar uma rede morta
        # (tipicamente hidraulica). As demais so sincronizam numa rede ja viva.
        self.blackstart = bool(blackstart)
        self.output = self.p_set if online else 0.0
        self._volt = self.bus_kv

    def governor(self, f):
        """Potencia que a usina entrega dado o desvio de frequencia."""
        if not self.online:
            return 0.0
        alvo = self.p_set + self.droop * (F_NOMINAL - f)
        return max(0.0, min(self.p_max, alvo))


class District:
    """Um bairro: carga eletrica que pode ser energizada ou cortada (UFLS/manual)."""

    def __init__(self, did, load_mw, bus_kv, priority, energized=True):
        self.did = did
        self.base_load = float(load_mw)
        self.bus_kv = float(bus_kv)
        self.priority = int(priority)   # menor = cortado primeiro (menos critico)
        self.energized = bool(energized)
        self._load = self.base_load if energized else 0.0
        self._volt = self.bus_kv if energized else 0.0
        self._phase = random.random() * math.tau


class CityGridModel:
    """Rede eletrica de uma cidade com N usinas e M bairros, acoplados pela frequencia."""

    # Entidades padrao: 2 usinas + 3 bairros (config pode sobrepor).
    DEFAULT_GENERATORS = [
        # gid,        p_max, p_set, bus_kv
        ("hidro",      60.0,  55.0, 138.0),
        ("termica",   110.0,  90.0, 138.0),
    ]
    DEFAULT_DISTRICTS = [
        # did,        load, bus_kv, priority (1=corta primeiro)
        ("industrial", 75.0, 69.0, 1),
        ("centro",     50.0, 69.0, 2),
        ("hospital",   20.0, 13.8, 3),   # carga critica: cortada por ultimo
    ]

    def __init__(self, config=None):
        cfg = config or {}

        gens = cfg.get("generators") or self.DEFAULT_GENERATORS
        dists = cfg.get("districts") or self.DEFAULT_DISTRICTS
        self.generators = {}
        for g in gens:
            if isinstance(g, dict):
                gen = Generator(g["id"], g["p_max"], g["p_set"],
                                g.get("bus_kv", 138.0), g.get("online", True),
                                g.get("droop_mw_hz", 8.0), g.get("blackstart", False))
            else:
                gen = Generator(*g)
            self.generators[gen.gid] = gen
        # seguranca: se ninguem declarou blackstart, a 1a usina vira a de partida a
        # frio -- assim o religamento pos-colapso nunca fica sem caminho.
        if self.generators and not any(g.blackstart for g in self.generators.values()):
            next(iter(self.generators.values())).blackstart = True
        self.districts = {}
        for d in dists:
            if isinstance(d, dict):
                dist = District(d["id"], d["load_mw"], d.get("bus_kv", 69.0),
                                d.get("priority", 1), d.get("energized", True))
            else:
                dist = District(*d)
            self.districts[dist.did] = dist

        # Frequencia do sistema (equacao de swing agregada)
        self.freq = F_NOMINAL
        self.H = float(cfg.get("inertia_mws", 120.0))     # inercia agregada (MW.s)
        self.D = float(cfg.get("damping_mw_hz", 15.0))    # amortecimento (MW/Hz)

        # Protecao de subfrequencia (UFLS): estagios de corte de carga
        self.ufls_enabled = bool(cfg.get("ufls_enabled", True))
        self.ufls_stages = list(cfg.get("ufls_stages", [59.0, 58.5, 58.0, 57.5]))
        self.ufls_delay = float(cfg.get("ufls_delay_s", 0.6))  # atraso do rele por estagio
        self._ufls_below_since = None
        self._ufls_fired = 0                                    # estagios ja disparados

        # Disparo de gerador por subfrequencia extrema (colapso)
        self.gen_trip_hz = float(cfg.get("gen_trip_hz", 57.0))
        self.gen_trip_delay = float(cfg.get("gen_trip_delay_s", 1.0))
        self._genlow_since = None

        # Ruido de carga (deixa a telemetria "viva")
        self._load_noise = float(cfg.get("load_noise", 0.02))

        self._blackout = False
        self._sig = {}
        self._sim_t = 0.0        # relogio SIMULADO (soma de dt) - base dos reles
        self._compute_signals()

    # ------------------------------------------------------------------
    # Fisica
    # ------------------------------------------------------------------
    def advance(self, dt):
        self._sim_t += dt
        now = self._sim_t
        t = self._sim_t

        # 1) Geracao total (com governador) e carga total
        p_gen = sum(g.governor(self.freq) for g in self.generators.values())
        p_load = 0.0
        for d in self.districts.values():
            if d.energized:
                # pequena ondulacao diaria + ruido
                load = d.base_load * (1.0 + 0.02 * math.sin(t * 0.05 + d._phase))
                load += random.gauss(0, d.base_load * self._load_noise)
                d._load += (max(0.0, load) - d._load) * min(1.0, dt / 1.5)
                p_load += d._load
            else:
                d._load += (0.0 - d._load) * min(1.0, dt / 1.5)

        # 2) Equacao de swing: df/dt = ((Pg - Pl) - D*(f - fn)) / (2H)
        if any(g.online for g in self.generators.values()):
            dfdt = ((p_gen - p_load) - self.D * (self.freq - F_NOMINAL)) / (2.0 * self.H)
            self.freq += dfdt * dt
        else:
            # sem geracao: frequencia colapsa (blecaute total)
            self.freq += (0.0 - self.freq) * min(1.0, dt / 2.0)
        self.freq = max(0.0, min(62.0, self.freq))

        # 3) Guarda potencia realizada de cada usina (para telemetria)
        for g in self.generators.values():
            g.output = g.governor(self.freq)

        # 4) UFLS: se f abaixo do proximo estagio por ufls_delay, corta 1 bairro
        if self.ufls_enabled and self._ufls_fired < len(self.ufls_stages):
            limiar = self.ufls_stages[self._ufls_fired]
            if self.freq < limiar:
                if self._ufls_below_since is None:
                    self._ufls_below_since = now
                elif now - self._ufls_below_since >= self.ufls_delay:
                    self._shed_one_district()
                    self._ufls_fired += 1
                    self._ufls_below_since = None
            else:
                self._ufls_below_since = None

        # 5) Disparo de usina por subfrequencia extrema -> colapso
        if self.freq < self.gen_trip_hz and any(g.online for g in self.generators.values()):
            if self._genlow_since is None:
                self._genlow_since = now
            elif now - self._genlow_since >= self.gen_trip_delay:
                for g in self.generators.values():
                    if g.online:
                        g.online = False
                        logger.warning(f"[grid] COLAPSO: usina '{g.gid}' disparou por "
                                       f"subfrequencia ({self.freq:.2f} Hz)")
                self._blackout = True
                self._genlow_since = None
        else:
            self._genlow_since = None

        # 6) Tensoes de barra (afundam com a carga; colapsam se desenergizado)
        for g in self.generators.values():
            alvo = g.bus_kv if g.online else 0.0
            g._volt += (alvo - g._volt) * min(1.0, dt / 2.0)
        for d in self.districts.values():
            if d.energized and any(g.online for g in self.generators.values()):
                sag = 1.0 - 0.04 * (d._load / max(1.0, d.base_load))
                # tensao tambem cai um pouco com f baixa (colapso de tensao acompanha)
                fscale = 0.5 + 0.5 * (self.freq / F_NOMINAL)
                alvo = d.bus_kv * sag * fscale + random.gauss(0, 0.05)
            else:
                alvo = 0.0
            d._volt += (alvo - d._volt) * min(1.0, dt / 2.0)

        self._compute_signals()

    def _shed_one_district(self):
        """Corta o bairro energizado de menor prioridade (UFLS)."""
        candidatos = [d for d in self.districts.values() if d.energized]
        if not candidatos:
            return
        alvo = min(candidatos, key=lambda d: d.priority)
        alvo.energized = False
        logger.warning(f"[grid] UFLS: corte de carga do bairro '{alvo.did}' "
                       f"(f={self.freq:.2f} Hz, prioridade {alvo.priority})")

    def _compute_signals(self):
        s = {}
        p_gen = 0.0
        for g in self.generators.values():
            s[f"{g.gid}.breaker"] = g.online
            s[f"{g.gid}.power"] = round(g.output, 2)
            s[f"{g.gid}.voltage"] = round(g._volt, 2)
            p_gen += g.output
        p_load = 0.0
        for d in self.districts.values():
            s[f"{d.did}.breaker"] = d.energized
            s[f"{d.did}.load"] = round(max(0.0, d._load), 2)
            s[f"{d.did}.voltage"] = round(max(0.0, d._volt), 2)
            # corrente aproximada I = P/(sqrt3*V) em A (kV->V, MW->W)
            v = max(1.0, d._volt)
            s[f"{d.did}.current"] = round(d._load * 1e6 / (1.732 * v * 1e3), 1) if d.energized else 0.0
            p_load += max(0.0, d._load)
        s["sys.frequency"] = round(self.freq, 3)
        s["sys.gen_total"] = round(p_gen, 2)
        s["sys.load_total"] = round(p_load, 2)
        s["sys.blackout"] = self._blackout
        self._sig = s

    # ------------------------------------------------------------------
    # Interface para os pontos / comandos externos (HMI, master, MITM)
    # ------------------------------------------------------------------
    def signals(self):
        return set(self._sig.keys())

    def get(self, signal):
        return self._sig.get(signal)

    def command(self, signal, value):
        """Aplica um comando externo a uma ENTRADA do modelo (disjuntores)."""
        if not signal or "." not in signal:
            return
        ent, attr = signal.split(".", 1)
        if attr != "breaker":
            return   # so disjuntores sao comandaveis
        novo = bool(value)
        if ent in self.generators:
            g = self.generators[ent]
            if g.online == novo:
                return
            if not novo:
                # TRIP: derruba a usina e rearma a protecao para a nova falta
                g.online = False
                logger.warning(f"[grid] comando externo: usina '{ent}' -> DESLIGADA (trip)")
                self._ufls_fired = 0
                self._ufls_below_since = None
                return
            # RELIGAR: depende de a rede estar viva ou morta (blackstart)
            rede_morta = not any(gg.online for gg in self.generators.values())
            if rede_morta:
                if not g.blackstart:
                    # termica (etc.) nao parte a frio: precisa de referencia viva
                    logger.warning(f"[grid] usina '{ent}' nao parte a frio (sem blackstart); "
                                   f"energize antes uma usina blackstart e sincronize")
                    return
                # BLACKSTART: a maquina (ja girando a ~60 Hz) energiza a rede morta e
                # vira a referencia de frequencia. A carga NAO volta junto -- o operador
                # recupera os bairros em degraus (close <bairro>) para nao re-colapsar.
                g.online = True
                self.freq = F_NOMINAL - 0.5
                self._blackout = False
                self._genlow_since = None
                self._ufls_below_since = None
                logger.warning(f"[grid] BLACKSTART: usina '{ent}' energizou a rede morta "
                               f"(freq -> {self.freq:.1f} Hz; bairros ainda cortados)")
                self._compute_signals()
            else:
                # rede ja viva: sincroniza a usina (referencia existe)
                g.online = True
                self._blackout = False
                logger.warning(f"[grid] comando externo: usina '{ent}' -> ONLINE (sincronizada)")
        elif ent in self.districts:
            d = self.districts[ent]
            if d.energized != novo:
                d.energized = novo
                logger.info(f"[grid] comando externo: bairro '{ent}' -> "
                            f"{'ENERGIZADO' if novo else 'CORTADO'}")

    def restore(self):
        """Reenergiza tudo (util para reiniciar a demonstracao sem derrubar o processo)."""
        for g in self.generators.values():
            g.online = True
        for d in self.districts.values():
            d.energized = True
        self.freq = F_NOMINAL
        self._blackout = False
        self._ufls_fired = 0
        self._ufls_below_since = None
        self._genlow_since = None
        logger.info("[grid] rede restaurada (todas as usinas e bairros religados)")


# ---------------------------------------------------------------------------
# Amarracao dos pontos de cada slave aos sinais da sua entidade
# ---------------------------------------------------------------------------
# Cada no do config representa UMA entidade (usina ou bairro), indicada por
# `grid_entity` (ou pelo id do no). Os pontos de cada protocolo seguem uma
# convencao fixa de "papeis" (breaker/power/voltage/frequency/load/current);
# o papel vira o sinal `<entidade>.<papel>`. Sinais inexistentes para a entidade
# (ex.: `power` num bairro) sao simplesmente ignorados.

# IEC104: ioa -> (papel, is_input)
IEC104_ROLES = {
    100: ("breaker", True),
    400: ("power", False),
    200: ("voltage", False),
    300: ("current", False),
    410: ("load", False),
    700: ("__sys.frequency", False),   # __ prefixo = sinal de sistema (nao leva entidade)
    710: ("__sys.gen_total", False),
    720: ("__sys.load_total", False),
}

# DNP3: (group, index) -> (papel, is_input)
DNP3_ROLES = {
    (1, 0): ("breaker", True),
    (30, 0): ("power", False),
    (30, 1): ("voltage", False),
    (30, 2): ("current", False),
    (30, 3): ("load", False),
    (30, 7): ("__sys.frequency", False),
}

# OPC-UA: nome da variavel -> (papel, is_input)
OPCUA_ROLES = {
    "Disjuntor": ("breaker", True),
    "Potencia_MW": ("power", False),
    "Tensao_kV": ("voltage", False),
    "Corrente_A": ("current", False),
    "Carga_MW": ("load", False),
    "Frequencia_Hz": ("__sys.frequency", False),
}


def _resolve_signal(papel, entity, model, strict=True):
    """Converte um papel + entidade no sinal do modelo, ou None se nao existir.

    strict=True (padrao): so retorna o sinal se ele existir no modelo (usado com o
    CityGridModel real, que sabe quais sinais cada entidade tem). strict=False:
    retorna o sinal sem checar -- usado no lado do RTU (modelo remoto), que nao
    conhece a lista de sinais; o binder amarra todo ponto de papel conhecido.
    """
    if papel.startswith("__"):
        sig = papel[2:]              # sinal de sistema: 'sys.frequency'
    else:
        sig = f"{entity}.{papel}"
    if not strict:
        return sig
    return sig if sig in model.signals() else None


def bind_grid(model, engine, node_entities: dict, strict: bool = True) -> int:
    """Amarra os pontos de cada slave aos sinais da sua entidade no grid.

    node_entities: {nome_do_gerador: entidade}, ex.: {'iec104-termica': 'termica'}.
    strict=False amarra sem checar model.signals() (modelo remoto, lado RTU).
    Retorna quantos pontos foram amarrados.
    """
    n = 0
    for name, gen in engine.generators.items():
        entity = node_entities.get(name)
        if not entity:
            continue

        # IEC104 slave
        if isinstance(getattr(gen, "datapoints", None), dict):
            for ioa, dp in gen.datapoints.items():
                if ioa in IEC104_ROLES:
                    papel, is_in = IEC104_ROLES[ioa]
                    sig = _resolve_signal(papel, entity, model, strict)
                    if sig:
                        dp.bind_model(model, sig, is_in); n += 1
        # DNP3 outstation
        elif isinstance(getattr(gen, "points", None), list):
            for p in gen.points:
                key = (p.group, p.index)
                if key in DNP3_ROLES:
                    papel, is_in = DNP3_ROLES[key]
                    sig = _resolve_signal(papel, entity, model, strict)
                    if sig:
                        p.bind_model(model, sig, is_in); n += 1
        # OPC-UA server
        elif isinstance(getattr(gen, "variaveis", None), list):
            for v in gen.variaveis:
                if v.nome in OPCUA_ROLES:
                    papel, is_in = OPCUA_ROLES[v.nome]
                    sig = _resolve_signal(papel, entity, model, strict)
                    if sig:
                        v.bind_model(model, sig, is_in); n += 1
    return n
