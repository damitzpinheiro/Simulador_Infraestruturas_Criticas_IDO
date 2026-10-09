# Roteiro — Teste Multi-Máquina (operação distribuída)

Guia prático para rodar o gerador com cada lado em uma máquina diferente e **comprovar** que
funciona pela rede real. Usa o que já está implementado: `bind` no lado servidor, a flag
`--role`, e `config/distribuido.yaml`.

> **O que já foi validado vs. o que este roteiro cobre.** A operação distribuída foi validada
> em **dois processos no mesmo host**, conversando pela **interface de rede real** (não
> loopback): o slave em `bind: 0.0.0.0` e o master apontando para o IP da LAN da máquina, com
> RTT de dezenas/centenas de ms (vs. ~0 no loopback). Isso prova o **mecanismo do software**.
> O que este roteiro adiciona é o cenário físico completo entre **hosts distintos**
> (firewall atravessando de uma máquina a outra, rede real no meio) — ainda não exercitado.

Convenção: **Máquina A** = servidores (slave / outstation / server). **Máquina B** = clientes
(master / client).

---

## Opção A — Duas máquinas reais na mesma rede (prova mais forte)

**1. Descubra o IP da Máquina A** (nela, PowerShell):
```
ipconfig
```
Anote o "Endereço IPv4" (ex.: `192.168.0.10`).

**2. Ajuste `config/distribuido.yaml`** (o mesmo arquivo nas duas máquinas): troque
`192.168.0.10` pelo IP real da A. O lado servidor fica com `bind: 0.0.0.0`; o lado cliente com
`host: <IP-da-A>` (e a URL do OPC-UA com esse IP).

**3. Libere o firewall na Máquina A** (PowerShell **como administrador**):
```
New-NetFirewallRule -DisplayName "PFC ICS" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 2404,20000,4840
```

**4. Teste a alcançabilidade** (na B, antes de rodar):
```
Test-NetConnection 192.168.0.10 -Port 2404
```
Precisa dar `TcpTestSucceeded : True`. Se der `False`, é firewall ou rede — resolva antes.

**5. Rode** (A primeiro, depois B):
```bash
# Máquina A
python main.py -c config/distribuido.yaml --role slave
```
```bash
# Máquina B
python main.py -c config/distribuido.yaml --role master
```

**6. Verifique:**
- No console da B, `status` -> masters `ativo`.
- No log do A, o master aparece com o **IP da B** (ex.: `Master conectado: ('192.168.0.11', ...)`).
- Wireshark na **interface de rede** (Wi-Fi/Ethernet, NÃO "Adapter for loopback"), filtro:
  `tcp.port==2404 || tcp.port==20000 || tcp.port==4840` -> tráfego real entre os dois IPs.

---

## Opção B — WSL2 como "segunda máquina" (num laptop só, sem instalar VM)

O WSL2 roda um Linux real com IP próprio, atrás de um NAT — Windows <-> WSL2 atravessa uma
**fronteira de rede de verdade** (não é loopback). Forma mais rápida de testar cross-host sem
hardware extra. Direção mais fácil: **servidor no WSL2, cliente no Windows**.

**1. No WSL2** (`wsl` no terminal; precisa de Python 3 e o projeto acessível):
```bash
hostname -I          # ex.: 172.28.34.5
python3 main.py -c config/distribuido.yaml --role slave
```

**2. No Windows**, ponha `host: 172.28.34.5` (o IP do passo 1) no config e rode:
```bash
python main.py -c config/distribuido.yaml --role master
```

O contrário (servidor no Windows, cliente no WSL2) esbarra em port-forwarding/firewall do
Windows e dá mais trabalho — prefira servidor no WSL2.

---

## Opção C — Duas VMs (a mais fiel a "duas máquinas de verdade")

Hyper-V (Win11 Pro) ou VirtualBox:

1. Crie 2 VMs (Linux ou Windows), cada uma com Python 3 + o projeto copiado.
2. **Rede:** um modo em que elas se enxergam — **bridged** (recebem IP da sua LAN, ideal) ou
   **internal/host-only** (rede isolada entre as VMs). Evite NAT puro sem port-forward.
3. Confirme que uma pinga a outra (`ping <IP-da-outra>`).
4. Daí é igual à Opção A: ajuste os IPs no config, firewall, `--role slave` numa e
   `--role master` na outra.

---

## Qual escolher

| Situação | Opção | Por quê |
|----------|-------|---------|
| Tem dois computadores | **A** | prova definitiva para a banca |
| Só o laptop, algo rápido | **B (WSL2)** | cruza uma fronteira de rede real em minutos |
| Quer o cenário mais "de campo" | **C (VMs bridged)** | duas máquinas isoladas de verdade |

Ponto de verificação em todas: **Wireshark na interface de rede** (não loopback) mostrando os
dois IPs distintos conversando. É isso que comprova o multi-máquina físico — o que a validação
em um processo só (ROTEIRO_DE_TESTES.md §7) não cobre.

---

## Captura no Wireshark (distribuído)

Para **só capturar/observar** o tráfego não é preciso MITM nem proxy: o servidor e o cliente
são as duas pontas da comunicação, então capturar na interface de rede de **qualquer um dos
dois** já vê todo o tráfego entre eles. É captura passiva.

**Opção 1 — no host (VMware, mais fácil):**
1. Abra o Wireshark e escolha a interface **VMware Network Adapter VMnet8** (NAT) ou **VMnet1**
   (host-only) — a que tem o IP da vmnet (ex.: `192.168.21.1`). **Não** a Wi-Fi, **não** o
   loopback. Confirme qual adaptador é com `ipconfig`.
2. Filtro de exibição:
   ```
   tcp.port == 2404 || tcp.port == 20000 || tcp.port == 4840
   ```
3. Suba o host (`--role slave`) e a VM (`--role master`). Aparecem os pacotes entre os dois IPs
   (ex.: `192.168.21.1` <-> `192.168.21.129`).

**Opção 2 — na VM (Linux), via tcpdump:**
```bash
sudo tcpdump -i eth0 -w /root/captura.pcap 'tcp port 2404 or tcp port 20000 or tcp port 4840'
```
Depois copie o `.pcap` para o host (`scp`) e abra no Wireshark.

**Decodificação nativa (o print forte para a banca):** o Wireshark disseca os três protocolos
automaticamente — IEC 60870-5-104 na 2404, DNP3 na 20000 e OPC-UA na 4840. Você vê o handshake
STARTDT, a Interrogação Geral e os ASDUs do IEC 104; o CRC e as 3 camadas do DNP3; o handshake
do OPC-UA — agora entre **dois IPs reais**, não loopback.

**Armadilha comum:** capturar no **loopback** ou na **Wi-Fi** não mostra nada — o tráfego da
vmnet vive no adaptador VMnet (o que tem o IP `192.168.21.x`). Escolha esse.

---

## Para vários nós na mesma máquina (sem distribuir)

Veja `config/multi_instancia.yaml` (2 slaves + 2 masters IEC 104, 1 outstation servindo 2
masters DNP3, 1 server + 2 clients OPC-UA). Roda com um comando só:
```bash
python main.py -c config/multi_instancia.yaml
```
