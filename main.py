#!/usr/bin/env python3
"""
SCADA Traffic Generator - IEC 60870-5-104
Gera trafego bidirecional continuo simulando master (SCADA) + slave (RTU).

Uso:
  python main.py                        # Config padrao, roda ate Ctrl+C
  python main.py -d 60                  # Roda por 60 segundos
  python main.py -l DEBUG               # Log detalhado (cada frame)
  python main.py -c config/custom.yaml  # Config customizada
"""

import asyncio
import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent))

from src.core.engine import Engine, MultiTargetGenerator
from src.protocols.iec104.slave import IEC104Slave
from src.protocols.iec104.master import IEC104Master
from src.protocols.dnp3.outstation import DNP3Outstation
from src.protocols.dnp3.master import DNP3Master


# Masters multi-alvo: um gerador que mantem N conexoes (um centro de controle
# polando varios RTUs). Usados quando o no de config traz `targets: [...]`.
class IEC104MultiMaster(MultiTargetGenerator):
    SINGLE_CLASS = IEC104Master
    _PROTO_NAME = "IEC104-Master"


class DNP3MultiMaster(MultiTargetGenerator):
    SINGLE_CLASS = DNP3Master
    _PROTO_NAME = "DNP3-Master"
from src.attacks.iec104_mitm import IEC104Mitm
from src.attacks.iec104_lib import build_from_command as iec104_build_cmd
from src.attacks.dnp3_mitm import DNP3Mitm
from src.attacks.dnp3_lib import build_from_command as dnp3_build_cmd

logger = logging.getLogger("scada_trafgen")



def load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        logger.warning(f"Config nao encontrada: {path}, usando defaults")
        return {
            "engine": {"stats_interval": 10, "retry_delay": 5, "max_retries": -1},
            "iec104": {
                "enabled": True,
                "slave": {
                    "host": "127.0.0.1", "port": 2404,
                    "common_address": 1, "spontaneous_interval": 2.0,
                },
                "master": {
                    "host": "127.0.0.1", "port": 2404,
                    "common_address": 1, "startup_delay": 1.5,
                    "gi_interval": 60.0, "command_interval": 15.0,
                    "testfr_interval": 30.0,
                },
            },
        }
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    logger.info(f"Config carregada: {path}")
    return config


def setup_logging(level: str = "INFO"):
    log_level = getattr(logging, level.upper(), logging.INFO)

    class ColorFmt(logging.Formatter):
        COLORS = {
            logging.DEBUG: "\033[36m", logging.INFO: "\033[32m",
            logging.WARNING: "\033[33m", logging.ERROR: "\033[31m",
        }
        RESET = "\033[0m"

        def format(self, record):
            c = self.COLORS.get(record.levelno, "")
            record.levelname = f"{c}{record.levelname:<7}{self.RESET}"
            return super().format(record)

    handler = logging.StreamHandler()
    handler.setFormatter(ColorFmt(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S"
    ))
    root = logging.getLogger()
    root.setLevel(log_level)
    root.handlers.clear()
    root.addHandler(handler)

    # A asyncua loga cada Browse/Subscribe em INFO e afogaria o log do
    # simulador. Fora do modo DEBUG, sobe para ERROR: os avisos de "No signing
    # policy"/"No encrypting policy"/"session timeout" sao esperados numa conexao
    # anonima de laboratorio (sem certificado/criptografia) e so poluem o console.
    nivel_asyncua = logging.DEBUG if log_level <= logging.DEBUG else logging.ERROR
    for nome in ("asyncua", "opcua"):
        logging.getLogger(nome).setLevel(nivel_asyncua)


# cores ANSI para o console de gerencia
_C = {"g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m",
      "d": "\033[90m", "b": "\033[1m", "0": "\033[0m"}

# Prefixo do nome de gerador por protocolo. Casa tanto os nomes canonicos
# (iec104-slave) quanto os de instancia (iec104-slave-2), por prefixo.
PROTO_PREFIX = {
    "iec104": "iec104", "iec": "iec104",
    "dnp3": "dnp3",
    "opcua": "opcua", "opc": "opcua",
}

MENU = f"""
{_C['b']}Gerenciador do gerador{_C['0']}
  status              estado dos geradores (ativo/pausado)
  stats [alvo]        estatisticas de trafego
  report [alvo]       relatorio completo
  start <alvo>        inicia gerador(es)
  pause <alvo>        pausa gerador(es)          (alias: stop)
  mitm [dnp3] on|off  ataque on-path (IEC 104 / DNP3)  ({_C['d']}help ataque{_C['0']})
  help                este menu                  quit  encerra

  {_C['d']}<alvo>: all | iec104 | dnp3 | opcua | servers | masters | <nome-exato>{_C['0']}
"""

MENU_ATAQUE = f"""
{_C['b']}Ataque on-path (MITM){_C['0']}  --  IEC 104 (padrao) ou DNP3 (prefixo 'dnp3')
  mitm on             proxy INTERNO IEC 104 neste console
  mitm dnp3 on        proxy INTERNO DNP3 neste console
  mitm extern         prepara as portas p/ o proxy EXTERNO IEC 104 (python mitm.py)
  mitm [dnp3] off     desliga e volta o par para a porta original
  mitm [dnp3] status  estado atual do MITM

  {_C['d']}injecao (so no modo interno; age no proxy que estiver ligado):{_C['0']}
  s <cmd>             injeta  --> SLAVE/OUTSTATION  (como se fosse o master)
  m <cmd>             injeta  --> MASTER            (como se fosse o slave/outstation)
  observe on|off      log dos frames repassados
  drop <sentido> <n>  descarta os proximos n frames (to-slave/to-outstation|to-master)

  {_C['d']}<cmd> IEC 104: startdt|stopdt|testfr|gi|clock|command <ioa> <on|off>|{_C['0']}
  {_C['d']}   spoof <ioa> <val>|oos|badcot|badaddr|craft type=.. cot=..|raw|malformed{_C['0']}
  {_C['d']}<cmd> DNP3:   integrity|events|command <idx> <on|off>|clock|spoof <idx> <val>|{_C['0']}
  {_C['d']}   spoofbin <idx> <on|off>|sbo <idx> <on|off>|restart|badcrc|craft func=..|raw|malformed{_C['0']}

  {_C['b']}exemplos{_C['0']}
  s command 101 off   (IEC 104) comanda o disjuntor 101 sem o master pedir
  m spoof 200 132     (IEC 104) injeta tensao falsa para o master
  mitm dnp3 on        entra no meio da conversa DNP3
  s command 0 on      (DNP3) DIRECT_OPERATE no ponto 0 sem o master
  s sbo 1 on          (DNP3) OPERATE sem SELECT (testa o Select-Before-Operate)
  m spoof 5 999.9     (DNP3) Unsolicited com Analog Input forjado para o master
  {_C['d']}watch/dp: prova automatica so no IEC 104; no DNP3 use 'observe on' + 'stats'{_C['0']}
"""


def _resolve_targets(engine, alvo: str):
    alvo = alvo.lower()
    names = list(engine.generators)
    if alvo == "all":
        return names
    if alvo == "servers":
        return [n for n in names if engine.role_of(n).startswith("slave")]
    if alvo == "masters":
        return [n for n in names if engine.role_of(n).startswith("master")]
    if alvo in PROTO_PREFIX:
        pref = PROTO_PREFIX[alvo]
        return [n for n in names if n == pref or n.startswith(pref + "-")]
    if alvo in engine.generators:
        return [alvo]
    return []


def _print_status(engine):
    rows = engine.status_rows()
    print(f"\n  {_C['b']}{'GERADOR':<20}{'PAPEL':<18}{'ESTADO':<13}{'CONTROLE'}{_C['0']}")
    print("  " + "-" * 58)
    for r in rows:
        if not r["enabled"]:
            ctrl = f"{_C['y']}pausado{_C['0']}"
        elif r["alive"]:
            ctrl = f"{_C['g']}ativo{_C['0']}"
        else:
            ctrl = f"{_C['r']}reiniciando{_C['0']}"
        est = r["state"]
        ecol = _C['g'] if est in ("active", "connected") else (
            _C['y'] if est in ("connecting", "idle") else _C['r'])
        print(f"  {r['name']:<20}{r['role']:<18}{ecol}{est:<13}{_C['0']}{ctrl}")
    print()


def _fmt_val(v):
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _fmt_age(ts):
    if ts is None:
        return "-"
    age = time.time() - ts
    return f"{age:.1f}s atras" if age >= 0.05 else "agora"


def _resolve_dp_target(alvo: str):
    """Mapeia 'slave'/'master' (e aliases) para o nome do gerador, ou None."""
    alvo = alvo.lower()
    if alvo in ("slave", "iec104-slave", "s"):
        return "iec104-slave"
    if alvo in ("master", "iec104-master", "m"):
        return "iec104-master"
    return None


def _snapshot_values(engine, name):
    """{ioa: valor} do estado atual - slave le datapoints, master le last_values."""
    gen = engine.generators[name]
    if name == "iec104-slave":
        return {ioa: dp.value for ioa, dp in gen.datapoints.items()}
    return {ioa: v["value"] for ioa, v in gen.last_values.items()}


def _print_watch_diff(before, after, attacked_ioa, ioa_filtro=None):
    """Mostra so o que mudou entre dois snapshots, separando o IOA atacado do ruido."""
    ioas = sorted(set(before) | set(after))
    if ioa_filtro is not None:
        ioas = [i for i in ioas if i == ioa_filtro]
    linhas = []
    for ioa in ioas:
        b, a = before.get(ioa), after.get(ioa)
        mudou = b != a
        if not mudou and ioa != attacked_ioa:
            continue
        if ioa == attacked_ioa:
            tag = (f"{_C['g']}[<== ALVO DO ATAQUE]{_C['0']}" if mudou
                   else f"{_C['y']}[SEM EFEITO - valor ja era esse]{_C['0']}")
        else:
            tag = f"{_C['d']}[ruido simulado]{_C['0']}"
        bstr = "-" if b is None else _fmt_val(b)
        astr = "-" if a is None else _fmt_val(a)
        linhas.append(f"  IOA {ioa:<6}{bstr:>10}  ->  {astr:<10}  {tag}")
    print()
    if not linhas:
        print(f"  {_C['y']}nenhuma mudanca detectada{_C['0']}")
    else:
        for l in linhas:
            print(l)
    print()


def _print_datapoints(engine, alvo: str, ioa_filtro=None):
    """Mostra o estado real (em memoria) do slave ou o ultimo valor visto pelo master,
    sem precisar de 'observe on' - le direto do objeto Python, sem trafego de rede."""
    name = _resolve_dp_target(alvo)
    if name is None:
        print(f"{_C['r']}  alvo invalido: {alvo}  (use 'slave' ou 'master'){_C['0']}"); return
    if name not in engine.generators:
        print(f"{_C['r']}  {name} nao esta habilitado no config{_C['0']}"); return
    gen = engine.generators[name]

    if name == "iec104-slave":
        rows = sorted(gen.datapoints.items())
        if ioa_filtro is not None:
            rows = [(ioa, dp) for ioa, dp in rows if ioa == ioa_filtro]
        if not rows:
            print(f"{_C['y']}  nenhum datapoint encontrado{_C['0']}"); return
        print(f"\n  {_C['b']}{'IOA':<8}{'TIPO':<14}{'VALOR':<14}{'QUALIDADE'}{_C['0']}")
        for ioa, dp in rows:
            q = f"0x{dp.quality:02X}" if dp.quality else "boa"
            print(f"  {ioa:<8}{dp.type_id:<14}{_fmt_val(dp.value):<14}{q}")
        print(f"  {_C['d']}(estado real do slave, lido direto da memoria - ground truth){_C['0']}\n")
    else:
        rows = sorted(gen.last_values.items())
        if ioa_filtro is not None:
            rows = [(ioa, v) for ioa, v in rows if ioa == ioa_filtro]
        if not rows:
            print(f"{_C['y']}  nenhum valor recebido ainda pelo master{_C['0']}"); return
        print(f"\n  {_C['b']}{'IOA':<8}{'TIPO':<16}{'VALOR':<14}{'CAUSA':<14}{'RECEBIDO'}{_C['0']}")
        for ioa, v in rows:
            print(f"  {ioa:<8}{v['type_name']:<16}{_fmt_val(v['value']):<14}"
                  f"{v['cause_name']:<14}{_fmt_age(v['ts'])}")
        print(f"  {_C['d']}(ultimo valor visto pelo master - nao ha estado persistido){_C['0']}\n")


def _print_stats(engine, nome=None):
    names = [nome] if nome and nome in engine.generators else list(engine.generators)
    if nome and nome not in engine.generators:
        print(f"{_C['r']}  gerador '{nome}' nao existe{_C['0']}"); return
    print()
    for name in names:
        s = engine.generators[name].stats.summary()
        rtt = ""
        if "rtt_ms" in s:
            r = s["rtt_ms"]
            rtt = f" | RTT medio={r['mean']}ms P95={r['p95']}ms (n={r['n']})"
        print(f"  {_C['b']}{name}{_C['0']} ({s['protocol']})")
        print(f"    TX: {s['sent']['packets']} pkts ({s['sent']['bytes']} B)"
              f" | RX: {s['received']['packets']} pkts ({s['received']['bytes']} B)"
              f" | PPS: {s['pps_sent']}/{s['pps_received']}")
        print(f"    Erros: {s['errors']} | Perdidos: {s['frames_dropped']} "
              f"(PLR={s['packet_loss_rate_pct']}%) | Bloq.janela: {s['window_blocks']}"
              f" | T1: {s['t1_timeouts']}{rtt}")
    print()


# Configuracao de cada protocolo com superficie de ataque MITM. O IEC 104 e o
# DNP3 seguem o mesmo modelo on-path; so mudam nomes, portas e construtores.
_MITM = {
    "iec104": {
        "label": "IEC 104",
        "_proto": "iec104",
        "slave_gen": "iec104-slave", "master_gen": "iec104-master",
        "slave_word": "slave", "listen": 2404, "moved": 2405,
        "make_slave": lambda cfg: IEC104Slave(cfg),
        "make_mitm": lambda: IEC104Mitm(listen_port=2404,
                                        slave_host="127.0.0.1", slave_port=2405),
        "build": iec104_build_cmd,
        "slave_dir": "to-slave", "impersonate_slave": "as_slave",
        "extern": "python mitm.py",
    },
    "dnp3": {
        "label": "DNP3",
        "_proto": "dnp3",
        "slave_gen": "dnp3-outstation", "master_gen": "dnp3-master",
        "slave_word": "outstation", "listen": 20000, "moved": 20001,
        "make_slave": lambda cfg: DNP3Outstation(cfg),
        "make_mitm": lambda: DNP3Mitm(listen_port=20000,
                                      outstation_host="127.0.0.1", outstation_port=20001),
        "build": dnp3_build_cmd,
        "slave_dir": "to-outstation", "impersonate_slave": "as_outstation",
        "extern": None,
    },
}


def _active_mitm(state):
    """Retorna (proto, sub_state) do MITM interno ativo, ou (None, None)."""
    for proto in _MITM:
        if state[proto]["mitm"] is not None:
            return proto, state[proto]
    return None, None


def _mitm_names(engine, cfg):
    """Nomes efetivos (slave_gen, master_gen) do par a atacar.

    Retrocompativel: se os nomes fixos do layout antigo existirem, usa-os. Caso
    contrario (config multi-instancia com nos nomeados por entidade, ex.
    `iec104-termica`), acha por duck typing o slave na porta `listen` e o master
    que conecta nela.
    """
    if cfg["slave_gen"] in engine.generators:
        return cfg["slave_gen"], cfg["master_gen"]
    proto, listen, moved = cfg["_proto"], cfg["listen"], cfg["moved"]

    def _has_points(gen):
        return (isinstance(getattr(gen, "datapoints", None), dict)
                or isinstance(getattr(gen, "points", None), list))

    slave = master = None
    for name, gen in engine.generators.items():
        if not name.startswith(proto + "-"):
            continue
        try:
            port = int(getattr(gen, "config", {}).get("port", 0))
        except (TypeError, ValueError):
            port = 0
        # o slave pode estar na porta original (listen) OU na movida (durante o MITM)
        if _has_points(gen) and port in (listen, moved) and slave is None:
            slave = name
        elif not _has_points(gen) and port == listen and master is None:
            master = name
    return (slave or cfg["slave_gen"]), (master or cfg["master_gen"])


async def _mitm_move_slave(engine, proto, port):
    """Pausa o par do protocolo e recria o slave/outstation na porta dada."""
    cfg = _MITM[proto]
    slave_gen, master_gen = _mitm_names(engine, cfg)
    await engine.pause_generator(master_gen)
    await engine.pause_generator(slave_gen)
    slave_cfg = dict(engine.generators[slave_gen].config)
    slave_cfg["port"] = port
    engine.replace_generator(slave_gen, cfg["make_slave"](slave_cfg))
    engine.start_generator(slave_gen)
    await asyncio.sleep(0.4)
    # O slave foi reconstruido: seus pontos sao NOVOS e perderam a amarracao com o
    # modelo. Re-amarra (se houver modelo) para a injecao voltar a disparar a fisica.
    rebind = getattr(engine, "rebind_model", None)
    if rebind:
        rebind()


async def _mitm_on(engine, state, proto):
    """MITM interno: proxy embutido neste console (injeta com s/m/drop/observe)."""
    cfg = _MITM[proto]
    sub = state[proto]
    if sub["mode"] is not None:
        print(f"{_C['y']}  MITM {cfg['label']} ja esta ativo ({sub['mode']}){_C['0']}"); return
    slave_gen, master_gen = _mitm_names(engine, cfg)
    if slave_gen not in engine.generators:
        print(f"{_C['r']}  {cfg['label']} nao esta habilitado no config{_C['0']}"); return

    L, M, W = cfg["listen"], cfg["moved"], cfg["slave_word"]
    print(f"{_C['d']}  reconfigurando: {W} ({slave_gen}) -> {M}, proxy interno -> {L} (aguarde ~3s) ...{_C['0']}")
    await _mitm_move_slave(engine, proto, M)
    mitm = cfg["make_mitm"]()
    sub["mitm"], sub["task"], sub["mode"] = mitm, asyncio.create_task(mitm.serve()), "interno"
    await asyncio.sleep(0.4)
    engine.start_generator(master_gen)
    print(f"{_C['g']}  MITM {cfg['label']} ligado: master --> proxy({L}) --> {W}({M}){_C['0']}")
    print(f"{_C['d']}  injete daqui:  s <cmd> / m <cmd> / observe on / drop ...{_C['0']}")


async def _mitm_extern(engine, state, proto):
    """Prepara as portas para o proxy EXTERNO (mitm.py em outro terminal)."""
    cfg = _MITM[proto]
    sub = state[proto]
    if cfg["extern"] is None:
        print(f"{_C['y']}  modo externo disponivel so para IEC 104; use 'mitm {proto} on'{_C['0']}"); return
    if sub["mode"] is not None:
        print(f"{_C['y']}  MITM {cfg['label']} ja esta ativo ({sub['mode']}){_C['0']}"); return
    slave_gen, master_gen = _mitm_names(engine, cfg)
    if slave_gen not in engine.generators:
        print(f"{_C['r']}  {cfg['label']} nao esta habilitado no config{_C['0']}"); return

    L, M, W = cfg["listen"], cfg["moved"], cfg["slave_word"]
    print(f"{_C['d']}  reconfigurando: {W} ({slave_gen}) -> {M}, deixando a {L} livre (aguarde ~3s) ...{_C['0']}")
    await _mitm_move_slave(engine, proto, M)
    sub["mode"] = "externo"
    engine.start_generator(master_gen)
    print(f"{_C['g']}  Pronto: {W} em {M}, porta {L} livre para o proxy externo.{_C['0']}")
    print(f"{_C['b']}  Agora, em OUTRO terminal:{_C['0']}  {cfg['extern']}")
    print(f"{_C['d']}  (o master fica reconectando ate o proxy subir na {L}){_C['0']}")


async def _mitm_off(engine, state, proto):
    cfg = _MITM[proto]
    sub = state[proto]
    if sub["mode"] is None:
        print(f"{_C['y']}  MITM {cfg['label']} ja esta desligado{_C['0']}"); return
    if sub["mode"] == "externo":
        print(f"{_C['y']}  feche o proxy no outro terminal antes, senao a {cfg['listen']} fica ocupada{_C['0']}")
    if sub["mitm"] is not None:
        await sub["mitm"].shutdown()
        sub["task"].cancel()
        try:
            await sub["task"]
        except (asyncio.CancelledError, Exception):
            pass
        sub["mitm"], sub["task"] = None, None
    await _mitm_move_slave(engine, proto, cfg["listen"])   # devolve p/ a porta original
    _, master_gen = _mitm_names(engine, cfg)
    engine.start_generator(master_gen)
    sub["mode"] = None
    print(f"{_C['g']}  MITM {cfg['label']} desligado; par de volta na {cfg['listen']}{_C['0']}")


async def _mitm_inject(state, side, args):
    """side = 'slave' (--> slave/outstation) ou 'master' (--> master)."""
    proto, sub = _active_mitm(state)
    if proto is None:
        # algum modo externo ativo?
        ext = [p for p in _MITM if state[p]["mode"] == "externo"]
        if ext:
            print(f"{_C['y']}  modo externo: injete pelo console do proxy (outro terminal){_C['0']}")
        else:
            print(f"{_C['r']}  MITM desligado --- rode 'mitm on' (ou 'mitm dnp3 on') primeiro{_C['0']}")
        return
    cfg = _MITM[proto]
    mitm = sub["mitm"]
    if side == "slave":
        builder, target = mitm.as_master, cfg["slave_dir"]
    else:
        builder, target = getattr(mitm, cfg["impersonate_slave"]), "to-master"
    if not args:
        exemplo = "command 101 off" if proto == "iec104" else "command 0 on"
        print(f"{_C['r']}  falta o comando (ex.: {'s' if side=='slave' else 'm'} {exemplo}){_C['0']}"); return
    scmd, sargs = args[0].lower(), args[1:]
    try:
        frame = cfg["build"](builder, scmd, sargs)
    except (ValueError, IndexError) as e:
        print(f"{_C['r']}  argumento invalido: {e}{_C['0']}  (veja 'help ataque')"); return
    await mitm.inject(frame, target)


def _is_coordinator(engine):
    """True no coordenador distribuido: tem grid model e NENHUM slave local
    (so a fisica central + o PlantCoordinator). Assim o console mostra o painel
    limpo da rede, sem o menu de MITM/protocolo que nao se aplica aqui."""
    if not getattr(engine, "grid_model", None):
        return False
    for g in engine.generators.values():
        if (isinstance(getattr(g, "datapoints", None), dict)
                or isinstance(getattr(g, "points", None), list)
                or isinstance(getattr(g, "variaveis", None), list)):
            return False
    return True


def _menu_grid(engine):
    m = engine.grid_model
    ents = ", ".join(list(m.generators) + list(m.districts))
    return f"""
{_C['b']}Coordenador da rede (cidade){_C['0']}
  {_C['g']}status{_C['0']}              estado da rede: frequencia, usinas e bairros
  {_C['g']}trip{_C['0']} <entidade>     abre o disjuntor (derruba)     ex.: {_C['d']}trip termica{_C['0']}
  {_C['g']}close{_C['0']} <entidade>    fecha o disjuntor (religa)     ex.: {_C['d']}close termica{_C['0']}
  {_C['g']}restore{_C['0']}             religa tudo, volta a 60 Hz (reset da demo)
  help                este menu                  quit  encerra

  {_C['d']}entidades: {ents}{_C['0']}
"""


def _grid_console(engine, args):
    """Comandos da rede (usado pelo 'grid ...' e pelo painel do coordenador)."""
    model = getattr(engine, "grid_model", None)
    if model is None:
        print(f"{_C['r']}  modelo de rede nao esta ativo neste config{_C['0']}"); return
    g = model.get
    sub = args[0].lower() if args else "status"
    if sub in ("status", "st"):
        f = g("sys.frequency")
        cor = _C['g'] if 59.5 <= f <= 60.5 else (_C['y'] if f >= 58.0 else _C['r'])
        print(f"  {_C['b']}frequencia{_C['0']}: {cor}{f:.2f} Hz{_C['0']}   "
              f"geracao {g('sys.gen_total'):.1f} MW / carga {g('sys.load_total'):.1f} MW")
        for gid in model.generators:
            on = g(gid + ".breaker")
            print(f"    usina  {gid:11s}: {(_C['g']+'LIGADA   ' if on else _C['r']+'DESLIGADA')}{_C['0']}  "
                  f"P={g(gid+'.power'):.1f} MW")
        for did in model.districts:
            on = g(did + ".breaker")
            print(f"    bairro {did:11s}: {(_C['g']+'ENERGIZADO' if on else _C['r']+'APAGADO   ')}{_C['0']}  "
                  f"carga={g(did+'.load'):.1f} MW")
    elif sub in ("restore", "reset", "religar"):
        model.restore()
        print(f"{_C['g']}  rede restaurada (todas as usinas/bairros religados, f=60 Hz){_C['0']}")
    elif sub in ("trip", "abrir") and len(args) > 1:
        model.command(f"{args[1]}.breaker", False)
        print(f"{_C['y']}  disjuntor de '{args[1]}' ABERTO{_C['0']}")
    elif sub in ("close", "fechar") and len(args) > 1:
        model.command(f"{args[1]}.breaker", True)
        print(f"{_C['g']}  disjuntor de '{args[1]}' FECHADO{_C['0']}")
    else:
        print(f"{_C['d']}  uso: status | trip <ent> | close <ent> | restore{_C['0']}")


async def console(engine):
    # um sub-estado por protocolo com superficie de ataque
    state = {proto: {"mitm": None, "task": None, "mode": None} for proto in _MITM}
    coord = _is_coordinator(engine)
    print(_menu_grid(engine) if coord else MENU)
    prompt = f"{_C['b']}coordenador>{_C['0']} " if coord else f"{_C['b']}gerador>{_C['0']} "
    while True:
        try:
            linha = await asyncio.to_thread(input, prompt)
        except (EOFError, KeyboardInterrupt):
            break
        linha = linha.lstrip("﻿").strip()
        if not linha:
            continue
        parts = linha.split()
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit", "q"):
            break
        elif cmd in ("help", "?", "menu"):
            if coord:
                print(_menu_grid(engine))
            elif args and args[0].lower() in ("ataque", "ataques", "mitm", "attack"):
                print(MENU_ATAQUE)
            else:
                print(MENU)
        # Painel do coordenador: comandos da rede diretos (sem o prefixo 'grid').
        elif coord and cmd in ("status", "st", "trip", "abrir", "close", "fechar",
                               "restore", "reset", "religar"):
            _grid_console(engine, [cmd] + args)
        elif cmd in ("status", "st", "ls"):
            _print_status(engine)
        elif cmd in ("stats", "stat"):
            _print_stats(engine, args[0] if args else None)
        elif cmd in ("report", "relatorio", "rel"):
            if not args or args[0].lower() == "all":
                engine.print_report(None, emit=print)
            else:
                targets = _resolve_targets(engine, args[0])
                if not targets:
                    print(f"{_C['r']}  alvo desconhecido: {args[0]}  (veja 'help'){_C['0']}"); continue
                engine.print_report(targets, titulo=f"RELATORIO: {args[0]}", emit=print)
        elif cmd in ("start", "pause", "stop"):
            if not args:
                print(f"{_C['r']}  uso: {cmd} <alvo>  (veja 'help'){_C['0']}"); continue
            targets = _resolve_targets(engine, args[0])
            if not targets:
                print(f"{_C['r']}  alvo desconhecido: {args[0]}  (veja 'help'){_C['0']}"); continue
            for name in targets:
                if cmd == "start":
                    ok = engine.start_generator(name)
                    print(f"  {name}: {'iniciado' if ok else 'ja estava ativo'}")
                else:
                    ok = await engine.pause_generator(name)
                    print(f"  {name}: {'pausado' if ok else 'ja estava pausado'}")
        elif cmd == "mitm":
            # aceita 'mitm on' (IEC 104 por padrao) ou 'mitm dnp3 on'
            rest = list(args)
            proto = "iec104"
            if rest and rest[0].lower() in _MITM:
                proto = rest.pop(0).lower()
            sub = rest[0].lower() if rest else "status"
            if sub == "on":
                await _mitm_on(engine, state, proto)
            elif sub in ("extern", "externo", "ext"):
                await _mitm_extern(engine, state, proto)
            elif sub == "off":
                await _mitm_off(engine, state, proto)
            else:
                ss = state[proto]
                if ss["mode"] is None:
                    print(f"  MITM {_MITM[proto]['label']}: {_C['y']}desligado{_C['0']}")
                elif ss["mode"] == "externo":
                    print(f"  MITM {_MITM[proto]['label']}: {_C['g']}externo{_C['0']} "
                          f"({_MITM[proto]['slave_word']} {_MITM[proto]['moved']}, "
                          f"{_MITM[proto]['listen']} p/ o proxy)")
                else:
                    ss["mitm"].status()
        elif cmd in ("s", "to-slave"):
            await _mitm_inject(state, "slave", args)
        elif cmd in ("m", "to-master"):
            await _mitm_inject(state, "master", args)
        elif cmd == "observe":
            _, sub = _active_mitm(state)
            if sub is None:
                print(f"{_C['r']}  MITM desligado{_C['0']}")
            else:
                sub["mitm"].observe = (len(args) > 0 and args[0].lower() == "on")
                print(f"  observe = {'on' if sub['mitm'].observe else 'off'}")
        elif cmd == "dp":
            if not args:
                print(f"{_C['r']}  uso: dp slave|master [ioa]{_C['0']}"); continue
            ioa_filtro = None
            if len(args) > 1:
                try:
                    ioa_filtro = int(args[1])
                except ValueError:
                    print(f"{_C['r']}  ioa invalido: {args[1]}{_C['0']}"); continue
            _print_datapoints(engine, args[0], ioa_filtro)
        elif cmd in ("grid", "rede"):
            _grid_console(engine, args)
        elif cmd == "watch":
            if "--" not in args:
                print(f"{_C['r']}  uso: watch slave|master [ioa] -- s|m <cmd...>{_C['0']}")
                print(f"{_C['d']}  ex.: watch slave 101 -- s command 101 off{_C['0']}"); continue
            sep = args.index("--")
            left, inject_parts = args[:sep], args[sep + 1:]
            if not left or not inject_parts:
                print(f"{_C['r']}  faltou o alvo ou o comando de injecao{_C['0']}"); continue
            name = _resolve_dp_target(left[0])
            if name is None:
                print(f"{_C['r']}  alvo invalido: {left[0]}  (use 'slave' ou 'master'){_C['0']}"); continue
            if name not in engine.generators:
                print(f"{_C['r']}  {name} nao esta habilitado no config{_C['0']}"); continue
            ioa_filtro = None
            if len(left) > 1:
                try:
                    ioa_filtro = int(left[1])
                except ValueError:
                    print(f"{_C['r']}  ioa invalido: {left[1]}{_C['0']}"); continue
            if _active_mitm(state)[0] is None:
                print(f"{_C['r']}  MITM desligado --- rode 'mitm on' primeiro{_C['0']}"); continue
            inj_cmd = inject_parts[0].lower()
            if inj_cmd in ("s", "to-slave"):
                direction = "slave"
            elif inj_cmd in ("m", "to-master"):
                direction = "master"
            else:
                print(f"{_C['r']}  a injecao deve comecar com 's' (-> slave) ou 'm' (-> master){_C['0']}"); continue
            inj_args = inject_parts[1:]
            # IOA atacado = primeiro inteiro apos o subcomando (command <ioa>, spoof <ioa>, ...)
            attacked_ioa = None
            for tok in inj_args[1:]:
                try:
                    attacked_ioa = int(tok); break
                except ValueError:
                    continue
            before = _snapshot_values(engine, name)
            await _mitm_inject(state, direction, inj_args)
            await asyncio.sleep(0.4)
            after = _snapshot_values(engine, name)
            _print_watch_diff(before, after, attacked_ioa, ioa_filtro)
        elif cmd == "drop":
            proto, sub = _active_mitm(state)
            if sub is None:
                print(f"{_C['r']}  MITM desligado{_C['0']}"); continue
            valid = tuple(sub["mitm"].drop.keys())   # to-slave/to-master ou to-outstation/to-master
            try:
                direction, n = args[0], int(args[1])
                if direction not in valid:
                    raise ValueError
                sub["mitm"].drop[direction] = n
                print(f"  vai descartar os proximos {n} frames {direction}")
            except (IndexError, ValueError):
                print(f"{_C['r']}  uso: drop {valid[0]}|{valid[1]} <n>{_C['0']}")
        else:
            print(f"{_C['r']}  comando desconhecido: {cmd}{_C['0']}  (veja 'help')")

    # ao sair, garante que todos os proxies sejam encerrados
    for sub in state.values():
        if sub["mitm"] is not None:
            await sub["mitm"].shutdown()
            sub["task"].cancel()
            try:
                await sub["task"]
            except (asyncio.CancelledError, Exception):
                pass


# Palavras de role do lado servidor (escuta); as demais sao lado cliente.
_SERVER_ROLE_WORDS = {"slave", "outstation", "server"}


def _generic_role(role_word: str) -> str:
    """Papel generico ('slave' = lado que escuta, 'master' = lado que conecta),
    para o filtro --role funcionar independente do nome por protocolo."""
    return "slave" if role_word in _SERVER_ROLE_WORDS else "master"


def _register_instances(engine, proto, pcfg, role_classes, old_layout,
                        inject_globals, role_filter=None, multi_classes=None):
    """Registra os geradores de um protocolo aceitando dois formatos de config:

      - NOVO (multi-instancia): `nodes: [{id, role, host, port, ...}, ...]`
        registra um gerador por item, com nome `<proto>-<id>`.
      - ANTIGO (1+1): blocos fixos (ex. iec104.slave / iec104.master),
        registra com nome `<proto>-<sufixo>` (retrocompativel).

    role_classes: {palavra-de-role: Classe}. old_layout: [(chave, Classe, sufixo, role)].
    role_filter: conjunto de papeis genericos ('slave'/'master') a registrar (--role);
    None registra todos.
    multi_classes: {palavra-de-role: ClasseMulti} usada quando o no traz `targets:`
    (um master/client que mantem N conexoes).
    """
    multi_classes = multi_classes or {}
    registrados = []
    nodes = pcfg.get("nodes")
    if nodes:
        for node in nodes:
            role = str(node.get("role", "")).lower()
            cls = role_classes.get(role)
            if cls is None:
                logger.error(f"{proto}: role desconhecido '{role}' no no "
                             f"'{node.get('id', '?')}' (ignorado)")
                continue
            if role_filter and _generic_role(role) not in role_filter:
                continue
            if "targets" in node and role in multi_classes:
                cls = multi_classes[role]   # master/client multi-alvo
            nid = node.get("id", role)
            cfg = {k: v for k, v in node.items() if k not in ("id", "role")}
            inject_globals(cfg)
            name = f"{proto}-{nid}"
            engine.register_generator(name, cls(cfg))
            registrados.append((name, cfg))
    else:
        for key, cls, suffix, role in old_layout:
            if role_filter and role not in role_filter:
                continue
            cfg = pcfg.get(key, {})
            inject_globals(cfg)
            name = f"{proto}-{suffix}"
            engine.register_generator(name, cls(cfg))
            registrados.append((name, cfg))
    return registrados


async def run(config: dict, duration: float = 0, interactive: bool = False,
              autostart: bool = True, role_filter=None):
    engine_cfg = config.get("engine", {})
    engine = Engine(engine_cfg)

    # Condicoes de rede e perfil de carga globais: injetados em cada sub-config
    # se o proprio sub-config nao definir os seus proprios.
    global_net = config.get("network_conditions", {})
    global_load = config.get("load_profile", {})

    def _inject_globals(*cfgs):
        """Aplica condicoes de rede e perfil de carga globais em cada sub-config."""
        for cfg in cfgs:
            if "network_conditions" not in cfg and global_net:
                cfg["network_conditions"] = global_net
            if "load_profile" not in cfg and global_load:
                cfg["load_profile"] = global_load

    iec_cfg = config.get("iec104", {})
    # default False (alinhado com dnp3/opcua): config sem secao iec104 NAO cria
    # um slave/master IEC104 fantasma. Os configs que querem IEC104 poem enabled: true.
    if iec_cfg.get("enabled", False):
        regs = _register_instances(
            engine, "iec104", iec_cfg,
            {"slave": IEC104Slave, "master": IEC104Master},
            [("slave", IEC104Slave, "slave", "slave"),
             ("master", IEC104Master, "master", "master")],
            _inject_globals, role_filter,
            multi_classes={"master": IEC104MultiMaster})
        for name, cfg in regs:
            logger.info(f"IEC 104: {name} em {cfg.get('host', '127.0.0.1')}:"
                        f"{cfg.get('port', 2404)}")

    dnp3_cfg = config.get("dnp3", {})
    if dnp3_cfg.get("enabled", False):
        regs = _register_instances(
            engine, "dnp3", dnp3_cfg,
            {"outstation": DNP3Outstation, "slave": DNP3Outstation,
             "master": DNP3Master},
            [("outstation", DNP3Outstation, "outstation", "slave"),
             ("master", DNP3Master, "master", "master")],
            _inject_globals, role_filter,
            multi_classes={"master": DNP3MultiMaster})
        for name, cfg in regs:
            logger.info(f"DNP3: {name} em {cfg.get('host', '127.0.0.1')}:"
                        f"{cfg.get('port', 20000)}")

    opcua_cfg = config.get("opcua", {})
    if opcua_cfg.get("enabled", False):
        try:
            from src.protocols.opcua.server import OPCUAServer
            from src.protocols.opcua.client import OPCUAClient
        except ImportError as e:
            logger.error(f"OPC-UA desabilitado - dependencia ausente: {e}")
            logger.error("Instale com: pip install -r requirements.txt")
        else:
            class OPCUAMultiClient(MultiTargetGenerator):
                SINGLE_CLASS = OPCUAClient
                _PROTO_NAME = "OPCUA-Client"

            regs = _register_instances(
                engine, "opcua", opcua_cfg,
                {"server": OPCUAServer, "client": OPCUAClient},
                [("server", OPCUAServer, "server", "slave"),
                 ("client", OPCUAClient, "client", "master")],
                _inject_globals, role_filter,
                multi_classes={"client": OPCUAMultiClient})
            for name, cfg in regs:
                logger.info(f"OPC-UA: {name} em {cfg.get('host', '127.0.0.1')}:"
                            f"{cfg.get('port', 4840)}")

    # Gateway Modbus-TCP: espelha os pontos dos slaves como registradores Modbus,
    # para o SCADA-LTS (que nao fala IEC104) observar a planta via Modbus e montar a HMI.
    # Registrado por ultimo para que os slaves-fonte ja existam quando ele monta o mapa.
    modbus_cfg = config.get("modbus", {})
    if modbus_cfg.get("enabled", False):
        from src.protocols.modbus.gateway import ModbusGateway
        gw = ModbusGateway(modbus_cfg)
        gw.attach_engine(engine)
        engine.register_generator("modbus-gateway", gw)
        logger.info(f"Modbus: gateway em {modbus_cfg.get('host', '0.0.0.0')}:"
                    f"{modbus_cfg.get('port', 5020)} (unit id {modbus_cfg.get('unit_id', 1)})")

    # Mapa {nome_do_gerador: entidade} a partir dos nos de cada protocolo.
    def _node_entities():
        ne = {}
        for proto in ("iec104", "dnp3", "opcua"):
            for node in (config.get(proto, {}).get("nodes") or []):
                nid = node.get("id")
                if nid is not None:
                    ne[f"{proto}-{nid}"] = node.get("grid_entity", nid)
        return ne

    coord_cfg = config.get("coordinator", {})
    plant_cfg = config.get("plant_link", {})
    grid_cfg = config.get("grid_model", {})
    pm_cfg = config.get("process_model", {})

    # COORDENADOR (operacao distribuida): roda a fisica central e sincroniza os RTUs
    # remotos. NAO tem slaves locais; a planta e acoplada pela frequencia global.
    if coord_cfg.get("enabled", False):
        from src.core.grid_model import CityGridModel
        from src.core.plant_sync import PlantCoordinator
        model = CityGridModel(grid_cfg)
        engine.register_generator("plant-coordinator", PlantCoordinator(coord_cfg, model, engine))
        engine.grid_model = model      # habilita o comando de console 'grid'
        logger.info(f"Coordenador da planta: {len(model.generators)} usinas + "
                    f"{len(model.districts)} bairros, {len(coord_cfg.get('remotes', []))} RTUs remotos")
    # RTU (operacao distribuida): so o front-end de protocolo. Os pontos sao amarrados
    # a um modelo REMOTO (RemoteModelClient) e sincronizados com o coordenador.
    elif plant_cfg.get("enabled", False):
        from src.core.grid_model import bind_grid
        from src.core.process_model import ProcessModelDriver
        from src.core.plant_sync import RemoteModelClient, PlantLinkServer
        remote = RemoteModelClient()
        node_entities = _node_entities()
        nb = bind_grid(remote, engine, node_entities, strict=False)
        _rtu_driver = ProcessModelDriver(plant_cfg, remote, engine)
        engine.register_generator("process-model", _rtu_driver)
        engine.register_generator("plant-link", PlantLinkServer(plant_cfg, remote))
        engine.rebind_model = lambda: (bind_grid(remote, engine, node_entities, strict=False),
                                       _rtu_driver.rebind())
        logger.info(f"RTU distribuido: {nb} pontos amarrados ao modelo remoto "
                    f"(sync na porta {plant_cfg.get('port', 7070)})")
    # Modelo de rede da cidade (multimaquina) em HOST UNICO: varias usinas/bairros
    # acoplados pela frequencia, todos no mesmo processo. Usa o ProcessModelDriver.
    elif grid_cfg.get("enabled", False):
        from src.core.grid_model import CityGridModel, bind_grid
        from src.core.process_model import ProcessModelDriver
        model = CityGridModel(grid_cfg)
        node_entities = _node_entities()
        nb = bind_grid(model, engine, node_entities)
        _grid_driver = ProcessModelDriver(grid_cfg, model, engine)
        engine.register_generator("process-model", _grid_driver)
        # Callback p/ re-amarrar apos um slave ser recriado (ex.: MITM move a porta):
        engine.rebind_model = lambda: (bind_grid(model, engine, node_entities),
                                       _grid_driver.rebind())
        engine.grid_model = model
        logger.info(f"Modelo de rede (cidade): {len(model.generators)} usinas + "
                    f"{len(model.districts)} bairros, {nb} pontos amarrados")
    # Modelo de processo (Passo 2): da fisica coerente aos pontos (disjuntor abre ->
    # corrente cai -> tensao/potencia reagem). Amarra os pontos dos slaves ao modelo e
    # sobe um driver que evolui a fisica e empurra os valores.
    elif pm_cfg.get("enabled", False):
        from src.core.process_model import SubstationModel, ProcessModelDriver, bind_slaves
        model = SubstationModel(pm_cfg)
        nb = bind_slaves(model, engine)
        _pm_driver = ProcessModelDriver(pm_cfg, model, engine)
        engine.register_generator("process-model", _pm_driver)
        engine.rebind_model = lambda: (bind_slaves(model, engine), _pm_driver.rebind())
        logger.info(f"Modelo de processo: subestacao coerente, {nb} pontos amarrados")

    if not engine.generators:
        logger.error("Nenhum gerador registrado!")
        return

    if interactive:
        logger.info("Modo interativo: digite 'help' no console para gerenciar")
    elif duration > 0:
        logger.info(f"Iniciando geracao de trafego... (duracao: {duration}s)")
    else:
        logger.info("Iniciando geracao de trafego... (Ctrl+C para parar)")

    await engine.start(autostart=autostart)

    try:
        if interactive:
            await console(engine)
        else:
            await asyncio.sleep(duration if duration > 0 else float('inf'))
    except asyncio.CancelledError:
        pass
    finally:
        await engine.stop()


def main():
    parser = argparse.ArgumentParser(
        description="SCADA Traffic Generator - IEC 60870-5-104",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  python main.py                  Console interativo de gerencia
  python main.py -m               Console, mas sem iniciar nada (start manual)
  python main.py -d 60            Roda 60s sem console (scripts/captura)
  python main.py --no-console     Roda ate Ctrl+C sem console
  python main.py -l DEBUG         Mostra cada frame
        """
    )
    parser.add_argument("-c", "--config", default="config/default.yaml",
                        help="Arquivo de configuracao YAML")
    parser.add_argument("-d", "--duration", type=float, default=0,
                        help="Duracao em segundos (0 = ate Ctrl+C). Desliga o console.")
    parser.add_argument("-m", "--manual", action="store_true",
                        help="Nao inicia os geradores automaticamente (start pelo console)")
    parser.add_argument("--no-console", action="store_true",
                        help="Nao abre o console interativo (modo headless)")
    parser.add_argument("--console", action="store_true",
                        help="Forca o console mesmo se a entrada nao for um terminal")
    parser.add_argument("-l", "--log-level", default=None,
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Nivel de log")
    parser.add_argument("--role", default="both",
                        choices=["both", "slave", "master"],
                        help="Sobe so um lado (multi-maquina): 'slave' = servidores "
                             "(slave/outstation/server); 'master' = clientes "
                             "(master/client). Padrao 'both'.")

    args = parser.parse_args()
    role_filter = None if args.role == "both" else {args.role}

    # Console interativo quando: sem duracao, nao pedido headless, e a entrada
    # e um terminal (ou --console forcado). -m implica console.
    interactive = (args.duration == 0 and not args.no_console
                   and (args.console or args.manual or sys.stdin.isatty()))

    config = load_config(args.config)
    # No console, logs continuos poluiriam o prompt: sobe para WARNING por padrao
    # (as estatisticas ficam sob demanda via 'status'/'stats'). -l sobrepoe.
    default_level = "WARNING" if interactive else config.get("engine", {}).get("log_level", "INFO")
    log_level = args.log_level or default_level
    setup_logging(log_level)

    try:
        asyncio.run(run(config, args.duration, interactive=interactive,
                        autostart=not args.manual, role_filter=role_filter))
    except KeyboardInterrupt:
        pass

    logger.info("Gerador encerrado.")


if __name__ == "__main__":
    main()
