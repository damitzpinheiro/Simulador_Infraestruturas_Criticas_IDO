# Planejamento — Operação Multi-Máquina e Múltiplos Nós por Protocolo

Objetivo: evoluir o gerador de um processo único em `127.0.0.1` para (A) operação
**distribuída** (cada lado em uma máquina) e (B/C) **vários masters e vários slaves**
(ou equivalentes) por protocolo.

---

## 1. Situação atual (diagnóstico do código)

| Protocolo | Lado servidor | Multi-cliente hoje? | Registro |
|-----------|---------------|---------------------|----------|
| IEC 104 | Slave (`start_server`) | **Sim** — `_MasterConn` por conexão | 1 slave + 1 master fixos |
| DNP3 | Outstation (`start_server`) | **Sim** ✅ — `_OutstationConn` por conexão (Fase 1 concluída) | 1 outstation + 1 master fixos |
| OPC-UA | Server (`asyncua`) | **Sim** — nativo (múltiplas sessions) | 1 server + 1 client fixos |

**Bloqueios para multi-máquina:**
1. `host` default `127.0.0.1` → o `start_server` faz bind só no loopback (recusa conexão remota).
2. `main.py` sobe os **dois** lados no mesmo processo; `enabled` é por protocolo, não por lado.
3. MITM hardcoda `slave_host="127.0.0.1"` (`_MITM` em `main.py` e as classes de ataque).

**O que já ajuda:** `host`/`port` são parametrizados via config em todos os geradores; o
master/cliente já usa `host` no `open_connection`.

---

## 2. Eixos de trabalho

- **Eixo A — Multi-máquina:** cada nó pode rodar em uma máquina diferente.
- **Eixo B — Múltiplos masters/clients** por protocolo.
- **Eixo C — Múltiplos slaves/outstations/servers** por protocolo.

Os três convergem para **uma mudança estrutural comum**: sair de blocos fixos
(`iec104.slave`, `iec104.master`) para **listas de instâncias**, cada uma com id, papel,
host e porta próprios, registradas dinamicamente na `Engine`.

---

## 3. Mudança estrutural comum — config orientada a instâncias

Hoje (`config/default.yaml`):
```yaml
iec104:
  enabled: true
  slave:  { host: 127.0.0.1, port: 2404, common_address: 1 }
  master: { host: 127.0.0.1, port: 2404 }
```

Proposta (retrocompatível — manter o parser antigo como atalho de "1+1"):
```yaml
iec104:
  enabled: true
  nodes:
    - { id: slave-1,  role: slave,  bind: 0.0.0.0, port: 2404, common_address: 1 }
    - { id: slave-2,  role: slave,  bind: 0.0.0.0, port: 2414, common_address: 2 }
    - { id: master-1, role: master, host: 192.168.0.10, port: 2404 }
    - { id: master-2, role: master, host: 192.168.0.11, port: 2414 }
```

- `run()` passa a **iterar `nodes`** e registrar um gerador por instância, com nome
  `iec104-<id>`. `bind` (lado servidor) separado de `host` (para onde o cliente conecta).
- `role` habilita rodar **só um lado** numa máquina (resolve o Eixo A item 2).
- Sem `nodes`, cai no parser atual (1 slave + 1 master) — nada quebra.

**Arquivos:** `main.py` (`run()`, `_resolve_targets`, `role_of`), `config/*.yaml`.

---

## 4. Eixo A — Multi-máquina (detalhe)

| # | Tarefa | Onde | Nota |
|---|--------|------|------|
| A1 | Bind configurável (`bind`), default `0.0.0.0` no lado servidor | slave.py, outstation.py, server.py | `host` de conexão continua separado |
| A2 | Papéis separáveis (`role` no config, ou flag `--role slave\|master\|both`) | main.py | permite 1 lado por máquina |
| A3 | Host de conexão = IP remoto | já suportado (config) | só documentar |
| A4 | MITM host-agnostic (`slave_host` do config, não fixo) | main.py `_MITM`, iec104_mitm.py, dnp3_mitm.py | |
| A5 | Doc de firewall/portas (Windows Defender, `netsh advfirewall`) | ROTEIRO_DE_TESTES.md | 2404/20000/4840 |

> Cuidado: expor em `0.0.0.0` é aceitável em **laboratório fechado**; deixar claro na doc
> que não há autenticação (é justamente o achado do trabalho).

---

## 5. Eixo B — Múltiplos masters/clients

- Com a config de instâncias (§3), basta declarar vários nós `role: master`.
- **IEC 104:** funciona direto — slave já é multi-cliente.
- **OPC-UA:** funciona direto — server é multi-session.
- **DNP3:** **pré-requisito** — tornar o outstation multi-cliente (§7 Fase 1).
- Métricas/estado por instância: cada gerador já tem seu `stats` próprio; o console
  (`status`/`stats`/`report`) itera `engine.generators`, então lista N nós sem mudança.
- **Endereçamento:** no DNP3 cada master precisa de `address` distinto; no IEC 104 cada
  master tem seu `originator`; no OPC-UA cada client tem sua session (automático).

---

## 6. Eixo C — Múltiplos slaves/outstations/servers

- Cada servidor em **porta própria** (mesma máquina) ou **IP próprio** (máquinas distintas).
- IEC 104: `common_address` distinto por slave; DNP3: `address` de outstation distinto;
  OPC-UA: endpoint/porta distintos.
- Registro dinâmico via `nodes` (§3). Um master pode então ser configurado para varrer
  vários slaves (evolução futura: lista de alvos por master).

---

## 7. Ordem de implementação sugerida (incremental, cada fase validável)

**Fase 1 — DNP3 outstation multi-cliente** ✅ **CONCLUÍDA**
- `outstation.py` refatorado com `_OutstationConn` por conexão (seq app/transport, buffers,
  fila de eventos, SBO e IIN por conexão; pontos e stats compartilhados). Scan de eventos
  compartilhado faz broadcast para a fila de cada conexão; comandos idem. Responde ao
  `src` do master de cada conexão.
- Validado: 2 masters simultâneos (endereços 1 e 2) contra 1 outstation, ambos `active`,
  RX>0, 0 erros; single-master via MITM sem regressão.

**Fase 2 — Config orientada a instâncias + registro dinâmico** ✅ **CONCLUÍDA**
- `main.py` ganhou `_register_instances()`: aceita `nodes: [{id, role, ...}]` (novo) ou os
  blocos fixos (antigo, retrocompatível). Nomes `<proto>-<id>`. `_resolve_targets` passou a
  casar por prefixo (`PROTO_PREFIX`), então `iec104`/`servers`/`masters` funcionam com nomes
  de instância. `run()` recebe `role_filter` (plumbing pronto para o `--role` da Fase 3).
- Exemplo pronto: `config/multi_instancia.yaml` (2 slaves+2 masters IEC104, 1 outstation+2
  masters DNP3, 1 server+2 clients OPC-UA).
- Validado: 10 geradores registrados com config por instância correta; 2 pares IEC104
  independentes rodando (TX/RX casados, 0 erros); 2 masters DNP3 num outstation (Fase 1).

**Fase 3 — Multi-máquina real** ✅ **CONCLUÍDA**
- `bind` configurável no lado servidor (slave IEC 104 e outstation DNP3); `bind: 0.0.0.0`
  aceita conexões remotas. OPC-UA usa o `host` do endpoint (deve ser o IP real da máquina).
- Flag `--role both|slave|master` (papel genérico: server-side vs client-side), sobe só um
  lado por máquina. `_register_instances` filtra por `_generic_role`.
- Exemplo pronto: `config/distribuido.yaml` (mesmo arquivo nas duas máquinas, IPs a ajustar).
  Doc de firewall no header do config e em `ROTEIRO_DE_TESTES.md` §7.
- Validado: `bind: 0.0.0.0` aceita conexão; `--role slave` sobe só servidores, `--role
  master` só clientes; smoke test do `multi_instancia.yaml --role slave` OK.

> Pendente (Fase 4): MITM host-agnostic — o `mitm on` ainda move o slave LOCAL de porta e
> assume `127.0.0.1`; um proxy entre máquinas distintas é a próxima extensão.

**Fase 4 — MITM host-agnostic** ✅ **CONCLUÍDA**
- `mitm.py` unificado por `--proto iec104|dnp3`, com `--listen-host` (0.0.0.0 aceita master
  remoto) e `--down-host`/`--down-port` (IP do slave/outstation real, mesmo em outra máquina).
  As classes `IEC104Mitm`/`DNP3Mitm` já eram parametrizadas; faltava a CLI.
- Validado: dispatch por proto gera frames válidos; standalone DNP3 e IEC104 sobem/encerram
  limpos; IEC104 default sem regressão. Doc em `ATAQUES.md`.

> Nota: o `mitm on` interno do `main.py` continua local (move o slave da própria máquina); o
> proxy distribuído entre máquinas é feito com o `mitm.py` standalone (host-agnostic).

**Fase 5 — Validação distribuída e documentação** ✅ **CONCLUÍDA**
- Validado o caminho distribuído completo num host só: dois processos `main.py`
  (`--role slave` com `bind: 0.0.0.0` e `--role master` com `host: <IP-da-LAN>`) conversando
  pela pilha de rede real — o slave registra o master pelo IP da LAN (172.16.1.198) e o RTT
  sobe para ~156 ms (vs. ~0 no loopback), 0 erros, GI completa. Numa 2a máquina física é
  idêntico, só muda o IP.
- Doc: `ROTEIRO_DE_TESTES.md` §7 (roteiro de 2 máquinas, firewall, e a validação num host só).
- Pendente (fora do escopo de código): atualizar a apresentação (`main_atual.tex`) — ainda
  lista multi-máquina/vários nós/ataque DNP3-OPCUA como próximos passos/limitações.

---

## 8. Riscos e cuidados

- **DNP3 multi-cliente** é o maior item de código; tudo em B/C depende dele.
- **Nomes de gerador** precisam ser únicos (hoje são fixos); o console e o MITM referenciam
  `iec104-slave`/`iec104-master` por nome — generalizar essas referências.
- **MITM com múltiplos masters:** mover o slave de porta (`mitm on`) com vários masters
  conectados exige reconectar todos; hoje o fluxo assume 1 master.
- **Métricas:** decidir agregado vs por conexão (o slave IEC 104 já compartilha stats entre
  conexões; para relatório por master, seria preciso stats por `_MasterConn`).
- **Windows:** firewall e o event loop Proactor; validar bind `0.0.0.0` e conexões remotas.
- **OPC-UA multi-server:** cada server é um endpoint/porta; certificados por server no caso
  seguro.

---

## 9. Resumo do esforço

| Eixo | Depende de | Esforço | Entrega |
|------|-----------|---------|---------|
| DNP3 multi-cliente | — | Médio | paridade com IEC 104 |
| Config de instâncias | — | Médio | N nós por config |
| Múltiplos masters/clients | DNP3 multi-cliente + config | Baixo | vários masters |
| Múltiplos slaves/servers | config | Baixo | vários slaves |
| Multi-máquina (bind/role/host) | config | Baixo–Médio | operação distribuída |
| MITM host-agnostic | config | Baixo | ataque entre máquinas |

Caminho mínimo para "multi-máquina + vários nós" funcionando: **Fase 1 → 2 → 3**.
