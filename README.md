# Gerador de Tráfego e Laboratório de Ataque SCADA/ICS

Projeto Final de Curso (IME) — um simulador que recria, em Python, a comunicação de três
protocolos industriais usados em infraestruturas críticas, e um laboratório de ataque
**MITM on-path** sobre eles. Serve para gerar tráfego realista (para captura em Wireshark,
testes e ensino) e para demonstrar, de forma controlada, por que esses protocolos são
vulneráveis quando não têm autenticação.

Autores: 1º Ten Luciano Damitz Pinheiro e 1º Ten Davi de Oliveira Rabelo.

| Protocolo | Porta padrão | Papéis | Codec |
|-----------|--------------|--------|-------|
| **IEC 60870-5-104** | 2404 | Master (SCADA) ⇄ Slave (RTU) | próprio, `struct` |
| **DNP3 (IEEE 1815)** | 20000 | Master ⇄ Outstation | próprio, `struct` + CRC-16/DNP |
| **OPC-UA (IEC 62541)** | 4840 | Client ⇄ Server | biblioteca `asyncua` |

---

## Como o projeto está organizado

O código tem **duas portas de entrada** e **três camadas** sem dependência circular.

```
main.py    → o GERADOR: sobe RTUs, masters e o modelo físico (o simulador em si)
mitm.py    → o ATACANTE: um console único de MITM para os três protocolos

src/
├── protocols/   CAMADA 1 — fala o fio (codifica/decodifica cada protocolo)
│   ├── iec104/  frames · slave · master
│   ├── dnp3/    frames · outstation · master
│   ├── opcua/   server · client
│   └── modbus/  gateway  (espelha os pontos para a HMI SCADA-LTS)
│
├── core/        CAMADA 2 — a simulação
│   ├── engine.py       orquestra todos os geradores como tasks asyncio
│   ├── grid_model.py   a física da rede elétrica (frequência, UFLS, cascata)
│   ├── plant_sync.py   canal "físico" entre o coordenador e cada RTU
│   └── load_profile.py · network_conditions.py · process_model.py
│
└── attacks/     CAMADA 3 — a superfície de ataque (usada só pelo mitm.py)
    ├── iec104_mitm.py + iec104_lib.py    proxy byte-level + injeção
    ├── dnp3_mitm.py   + dnp3_lib.py       idem para DNP3
    ├── opcua_mitm.py  + opcua_certs.py    substituição de certificado + laboratório
    └── transparent.py                     descoberta de destino (modo transparente)

config/    um YAML por cenário (host único, distribuído, uma VM por entidade)
docs/       guias detalhados (ataques, subir as VMs, cenário da cidade)
scadalts/   telas prontas para importar no SCADA-LTS
```

As dependências sobem numa direção só (`attacks`/`core` → `protocols`). A mesma `engine`
sobe "o que o YAML mandar", então o **mesmo código** roda num host só ou distribuído em
várias máquinas, mudando apenas a configuração.

---

## Como funciona

**O gerador (`main.py`).** Cada lado de cada protocolo é um gerador independente (slave,
master, outstation, server, client…). A `engine` roda todos juntos, com reconexão
automática e coleta de métricas (pacotes, bytes, RTT, perdas, timeouts). Os codecs de
IEC 104 e DNP3 são próprios (stdlib pura); só o OPC-UA usa a `asyncua`.

**O modelo físico (`grid_model.py`).** No cenário da cidade, um **coordenador** calcula a
física de uma rede elétrica (2 usinas + 3 bairros acoplados pela frequência). Quando uma
usina cai, a frequência despenca e a proteção de subfrequência (UFLS) corta bairros em
cascata. Cada RTU só expõe seus pontos; a física mora num lugar só.

**Os ataques (`mitm.py`).** Um console único para os três protocolos. O atacante se põe
**no meio** da conexão e injeta ou adultera mensagens, mostrando que o tráfego não tem
autenticação nem verificação de integridade:

- **IEC 104 / DNP3** — proxy byte-level: forja frames válidos e injeta comandos ou
  telemetria falsa para qualquer dos dois lados.
- **OPC-UA** — não é byte-proxy (tem segurança nativa); é **substituição de certificado**.
  O achado central: cifra ligada **não basta** — só a *validação do certificado* derrota o
  ataque.

---

## Como abrir e rodar

### 1. Pré-requisitos

- **Python 3.10+**
- Instalar as dependências:

```bash
pip install -r requirements.txt
```

> Só `pyyaml` é obrigatório. O `asyncua` é necessário apenas para o OPC-UA — os ataques de
> IEC 104 e DNP3 rodam com a biblioteca padrão (útil na Kali, sem instalar nada).

### 2. Rodar o simulador (host único — o jeito mais simples)

```bash
python main.py
```

Sobe os três protocolos e abre um **console interativo**. Comandos úteis:

| Comando | Ação |
|---------|------|
| `status` | lista os geradores e seus estados |
| `stats [nome]` | métricas de tráfego (todos ou de um) |
| `start <alvo>` / `pause <alvo>` | inicia / pausa (`all`, um protocolo, ou um nome) |
| `help` · `quit` | menu · encerra |

Variações:

```bash
python main.py -c config/cidade_multimaquina.yaml   # o cenário da cidade num host só
python main.py -d 60                                 # roda 60 s, sem console (p/ captura)
python main.py -l DEBUG                               # mostra cada frame enviado/recebido
```

### 3. Rodar o cenário distribuído (uma VM por entidade)

Cada VM roda o mesmo `main.py` com o seu YAML (`config/vm_*.yaml`). O passo a passo completo
(IPs, firewall, ordem de subida, SCADA-LTS) está em **[docs/GUIA_VMS.md](docs/GUIA_VMS.md)**.

### 4. Executar os ataques

Num terminal separado (ou na VM atacante), com o gerador no ar:

```bash
# IEC 104 — injeção de comando (cascata)
python mitm.py --proto iec104 --transparent

# DNP3
python mitm.py --proto dnp3 --transparent

# OPC-UA contra a usina real
python mitm.py --proto opcua --transparent

# OPC-UA — laboratório dos 3 casos de segurança, em localhost (não precisa das VMs)
python mitm.py --proto opcua --lab
```

- `--transparent` usa ARP spoofing + `iptables` (só Linux/Kali): a vítima **não** é
  reconfigurada e reconecta sozinha pelo proxy.
- Sem `--transparent`, informe o alvo explicitamente com `--down-host`/`--down-port`
  (IEC 104/DNP3) ou `--real`/`--listen` (OPC-UA) — funciona em qualquer sistema.

O vocabulário de injeção (`command`, `spoof`, `craft`, `raw`, `malformed`…) e os achados de
segurança estão em **[docs/ATAQUES.md](docs/ATAQUES.md)**; o passo a passo do ataque de ARP
spoofing em **[docs/CASO1_ARPSPOOFING.md](docs/CASO1_ARPSPOOFING.md)**.

---

## Documentação

| Documento | Conteúdo |
|-----------|----------|
| [docs/GUIA_SUBIR_SIMULACAO.md](docs/GUIA_SUBIR_SIMULACAO.md) | Subir a simulação e os dois ataques, passo a passo |
| [docs/GUIA_VMS.md](docs/GUIA_VMS.md) | Montar o cenário distribuído em VMs |
| [docs/CENARIO_CIDADE.md](docs/CENARIO_CIDADE.md) | O cenário da cidade (física, topologia, SCADA-LTS) |
| [docs/ATAQUES.md](docs/ATAQUES.md) | Superfície de ataque, comandos de injeção e achados |
| [docs/CASO1_ARPSPOOFING.md](docs/CASO1_ARPSPOOFING.md) | ARP spoofing on-path (Caso 1) |
| [docs/INTEGRACAO_SCADA_LTS.md](docs/INTEGRACAO_SCADA_LTS.md) | Integrar a HMI SCADA-LTS via Modbus |

---

Uso **estritamente acadêmico e defensivo**. As ferramentas de MITM destinam-se apenas ao
próprio simulador e a ambientes de laboratório autorizados.
