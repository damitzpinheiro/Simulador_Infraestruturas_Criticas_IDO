# Integração do simulador com o SCADA-LTS via gateway Modbus-TCP

Este documento descreve o **Passo 1** da integração do simulador de tráfego SCADA/ICS
do PFC com o **SCADA-LTS** (SCADA de código aberto usado como HMI / centro de controle).
Ele explica a motivação, a arquitetura, o código implementado, o mapa de registradores
gerado e como configurar o SCADA-LTS para observar a planta simulada.

---

## 1. Motivação e decisão de projeto

O objetivo é dar ao simulador um **comportamento observável por uma HMI real**, em vez
de apenas emitir pacotes. O SCADA-LTS seria o mestre/observador — um terceiro nó que faz
polling dos slaves e monta telas de operação (watch lists, gráficos, *synoptic panels*).

**Problema:** o SCADA-LTS **não possui driver IEC 60870-5-104** (issue upstream #2633 em
aberto). Ele fala, entre outros, **Modbus TCP**, DNP3 e OPC-UA. Como o protocolo principal
do laboratório é o IEC 104, foi preciso uma ponte.

**Solução (Opção C — gateway Modbus):** um **servidor Modbus-TCP embutido no simulador**
que lê, em tempo real, os *datapoints* que os slaves (IEC104 slave, DNP3 outstation,
OPC-UA server) já mantêm em memória e os republica como **coils** (booleanos) e **holding
registers** (analógicos float32). O SCADA-LTS conecta como **mestre Modbus** e faz polling.

Por que essa opção:

- **Não toca no tráfego original.** O gateway só *lê* o estado dos pontos; os fluxos
  IEC104/DNP3/OPC-UA seguem intactos como objeto de estudo e de ataque MITM.
- **Um único ponto de integração** cobre os três protocolos de uma vez (o gateway espelha
  todos os slaves juntos num só espaço de endereços Modbus).
- **Modbus é trivial de configurar no SCADA-LTS** (data source nativo, estável).

---

## 2. Arquitetura

```
   ┌──────────────────────────── Simulador (um processo Python / asyncio) ───────────────────────────┐
   │                                                                                                  │
   │   IEC104 Slave ─┐                                                                                 │
   │   (datapoints)  │                                                                                 │
   │                 │   leitura em tempo real dos valores dos pontos                                  │
   │   DNP3 Outstn. ─┼───────────────►  ModbusGateway  ──────►  Servidor Modbus-TCP  :5020            │
   │   (points)      │                  (mapa de canais)        (coils + holding regs float32)         │
   │                 │                                                    ▲                             │
   │   OPC-UA Server ┘                                                    │                             │
   │   (variaveis)                                                        │ polling Modbus (FC01/03)   │
   └─────────────────────────────────────────────────────────────────────┼─────────────────────────────┘
                                                                          │
   Tráfego IEC104/DNP3/OPC-UA ORIGINAL segue normal ◄── masters ──►       │
   (inclusive alvo dos ataques MITM)                                      │
                                                                   ┌──────┴───────┐
                                                                   │  SCADA-LTS   │  http://localhost:8080/Scada-LTS
                                                                   │  (HMI/mestre │  (Windows host)
                                                                   │   Modbus)    │
                                                                   └──────────────┘
```

O gateway é um `BaseProtocolGenerator` como os demais — registrado na `Engine`, aparece no
`status`, tem estatísticas próprias e é iniciado/pausado como qualquer gerador. A diferença
é que, em vez de gerar tráfego, ele **serve** um socket Modbus e lê os pontos dos irmãos.

---

## 3. O que foi implementado

### 3.1 `src/protocols/modbus/gateway.py` (novo)

Servidor **Modbus-TCP escrito do zero** (asyncio, sem dependências externas — mesmo estilo
dos codecs IEC104/DNP3 do projeto). Componentes:

- **`Channel`** — um ponto do simulador exposto no espaço Modbus: nome, se é booleano,
  endereço, um `getter()` (lê o valor atual do ponto) e um `setter(v)` opcional (escreve de
  volta no ponto do slave — usado para comando da HMI).

- **`ModbusGateway`** —
  - `_iter_sources()`: descobre os slaves a espelhar. Sem lista explícita em `sources`,
    pega automaticamente todos os geradores cujo papel é *slave/servidor* (o master só tem
    `last_values`, não `datapoints`, então é naturalmente ignorado).
  - `_extract_points()`: extrai pontos genéricos por *duck typing* do modelo de cada slave:
    - IEC104 → `self.datapoints = {ioa: DataPoint(.value, .type_id)}`
    - DNP3 → `self.points = [DNP3Point(.index, .group, .value)]`
    - OPC-UA → `self.variaveis = [OPCUAVariable(.nome, .valor, .booleano)]`
  - `_build_channels()`: classifica cada ponto por **tipo Python do valor** — `bool` vira
    **coil**, numérico vira **holding register** (float32, 2 registradores). Atribui
    endereços contíguos base 0.
  - Servidor Modbus: parse do header **MBAP** (7 bytes) + PDU; leitura *on-demand* (a cada
    requisição faz snapshot fresco dos valores dos pontos — sem cache, sem lock, pois é tudo
    no mesmo *event loop* asyncio).
  - **Function codes** suportadas:

    | FC   | Nome                        | Uso |
    |------|-----------------------------|-----|
    | 0x01 | Read Coils                  | lê booleanos |
    | 0x02 | Read Discrete Inputs        | espelho dos coils (conveniência) |
    | 0x03 | Read Holding Registers      | lê analógicos float32 |
    | 0x04 | Read Input Registers        | espelho dos holding (conveniência) |
    | 0x05 | Write Single Coil           | comando da HMI → ponto do slave |
    | 0x10 | Write Multiple Registers    | setpoint float da HMI → ponto do slave |

  - **Codificação float32:** IEEE-754 big-endian, **palavra alta primeiro**
    (`struct.pack(">f")` → reg[n]=16 bits altos, reg[n+1]=16 bits baixos). No SCADA-LTS,
    corresponde ao tipo *"4 Byte Float"* sem swap. Se o valor aparecer trocado, basta
    escolher a variante com swap no data point.

### 3.2 `main.py` (alterado)

Após registrar os geradores dos protocolos, se `modbus.enabled`, cria o gateway, injeta a
referência da `Engine` (`attach_engine`) e o registra como `modbus-gateway`. É registrado
por último para que os slaves-fonte já existam quando ele monta o mapa.

### 3.3 `config/default.yaml` (alterado)

Nova seção:

```yaml
modbus:
  enabled: true
  host: "0.0.0.0"       # 0.0.0.0 aceita o SCADA-LTS de outra maquina/VM
  port: 5020            # 502 exige admin; 5020 nao
  unit_id: 1            # slave/unit id que o SCADA-LTS deve usar
  allow_writes: true    # comando da HMI de volta ao ponto (FC05/FC16)
  sources: []           # vazio = auto (todos os slaves); ou ["iec104-slave", ...]
```

---

## 4. Mapa de registradores gerado

Impresso no log ao subir (procure por `MAPA DE REGISTRADORES`) e retornado por
`ModbusGateway.register_map_text()`. Com a config padrão (slaves IEC104 + DNP3 + OPC-UA):

### Coils (booleanos) — FC01 / FC05

| Coil | Ponto de origem            | Significado (planta)      |
|------|----------------------------|---------------------------|
| 0    | IEC104 IOA 100 (type 1)    | Disjuntor 1               |
| 1    | IEC104 IOA 101 (type 1)    | Disjuntor 2               |
| 2    | IEC104 IOA 102 (type 1)    | Disjuntor 3               |
| 3    | IEC104 IOA 103 (type 1)    | Disjuntor 4               |
| 4    | DNP3 group 1 index 0       | Binary Input 0            |
| 5    | DNP3 group 1 index 1       | Binary Input 1            |
| 6    | DNP3 group 1 index 2       | Binary Input 2            |
| 7    | DNP3 group 1 index 3       | Binary Input 3            |
| 8    | DNP3 group 10 index 0      | Binary Output 0           |
| 9    | DNP3 group 10 index 1      | Binary Output 1           |
| 10   | OPC-UA Disjuntor_1         | Disjuntor (OPC-UA)        |
| 11   | OPC-UA Disjuntor_2         | Disjuntor (OPC-UA)        |

### Holding Registers (float32, 2 registradores cada) — FC03 / FC16

| Reg  | Ponto de origem            | Significado (planta)      |
|------|----------------------------|---------------------------|
| 0    | IEC104 IOA 200 (type 13)   | Tensão barra A (kV)       |
| 2    | IEC104 IOA 201 (type 13)   | Tensão barra B (kV)       |
| 4    | IEC104 IOA 300 (type 13)   | Corrente 1 (A)            |
| 6    | IEC104 IOA 301 (type 13)   | Corrente 2 (A)            |
| 8    | IEC104 IOA 400 (type 13)   | Potência ativa (MW)       |
| 10   | IEC104 IOA 500 (type 11)   | Temperatura (°C)          |
| 12   | IEC104 IOA 501 (type 11)   | Temperatura 2 (°C)        |
| 14   | IEC104 IOA 600 (type 3)    | Posição do tap changer    |
| 16   | DNP3 group 20 index 0      | Counter 0                 |
| 18   | DNP3 group 20 index 1      | Counter 1                 |
| 20   | DNP3 group 30 index 0      | Analog Input 0            |
| 22   | DNP3 group 30 index 1      | Analog Input 1            |
| 24   | DNP3 group 30 index 2      | Analog Input 2            |
| 26   | DNP3 group 30 index 3      | Analog Input 3            |
| 28   | DNP3 group 30 index 4      | Analog Input 4            |
| 30   | OPC-UA Tensao_Barra_A      | Tensão barra A (OPC-UA)   |
| 32   | OPC-UA Tensao_Barra_B      | Tensão barra B (OPC-UA)   |
| 34   | OPC-UA Corrente_Fase_1     | Corrente fase 1 (OPC-UA)  |
| 36   | OPC-UA Potencia_Ativa      | Potência ativa (OPC-UA)   |
| 38   | OPC-UA Temperatura_Trafo   | Temperatura trafo (OPC-UA)|
| 40   | OPC-UA Setpoint_Tensao     | Setpoint de tensão        |

> O mapa é montado dinamicamente a partir dos pontos existentes. Se você mudar os
> datapoints no YAML, os endereços se reordenam — sempre confira o `MAPA DE REGISTRADORES`
> impresso no log ao subir.

---

## 5. Como validar o gateway (sem o SCADA-LTS)

Subir só os slaves + gateway:

```
python main.py --role slave --no-console -l INFO
```

Ler com qualquer cliente Modbus (ex.: o script de teste `mb_test.py`, ou `mbpoll`):

```
COILS 0..11:  [False, True, False, True, False, True, True, False, False, False, False, True]
FLOATS:       [136.81, 69.57, 451.05, 319.44, ...]   # tensao, tensao, corrente, corrente...
```

Os coils batem com os disjuntores (IOA 100=aberto, 101=fechado, ...) e os floats com
tensão/corrente/potência dos slaves — e mudam a cada leitura, confirmando que são valores
vivos.

**Validado:** um cliente Modbus real lê os valores corretos e atualizando em tempo real,
com a codificação float32 correta. (Camada Modbus verificada de ponta a ponta.)

---

## 6. Como configurar o SCADA-LTS para ler o gateway

No SCADA-LTS (http://localhost:8080/Scada-LTS, login `admin`/`admin`):

### 6.1 Criar o Data Source (Modbus IP)

1. Menu **Data sources** → adicionar → tipo **Modbus IP**.
2. Configurar:
   - **Name:** `Simulador PFC`
   - **Transport type:** `TCP`
   - **Host:** `127.0.0.1` (ou o IP do host onde roda o simulador)
   - **Port:** `5020`
   - **Update period:** `1 second` (intervalo de polling)
   - **Timeout:** `500 ms`, **Retries:** `2` (padrões servem)
3. Salvar e **habilitar** (ícone de play).

### 6.2 Adicionar Data Points

Para cada ponto, no data source, adicionar um data point com o **locator Modbus**:

**Exemplo — booleano (Disjuntor 1, coil 0):**
- **Slave id:** `1`
- **Range:** `Coil status (0x)`
- **Modbus data type:** `Binary`
- **Offset:** `0`
- **Settable:** marque se quiser comandar pela HMI (usa FC05)

**Exemplo — analógico (Tensão barra A, reg 0):**
- **Slave id:** `1`
- **Range:** `Holding register (4x)`
- **Modbus data type:** `4 Byte Float`  *(se o número vier trocado, use a variante com swap)*
- **Offset:** `0`
- **Settable:** marque para setpoint (usa FC16)

Repita usando os endereços da tabela da seção 4. Habilite cada data point.

### 6.3 Verificar

- Na tela do data source, os pontos passam a mostrar valores atualizando a cada segundo.
- No **log do simulador** aparece `Mestre Modbus conectado: (<ip>, <porta>)` quando o
  SCADA-LTS conecta — prova da ligação.
- Monte a HMI: **Watch list** (lista rápida) e depois um **Graphic View** ou **Synoptic
  Panel** amarrando os pontos a elementos gráficos (disjuntor verde/vermelho, medidor de
  tensão, etc.).

> Observação: a configuração da UI do SCADA-LTS é feita no navegador da máquina (o host
> Windows). O gateway já está validado na camada Modbus; esta seção é a amarração final na
> HMI.

---

## 7. HMI interativa (comando de volta)

Com `allow_writes: true`, um ponto **Settable** no SCADA-LTS escreve de volta:
- **Coil (FC05):** comandar um disjuntor pela HMI seta o `.value` do datapoint do slave.
- **Holding register (FC16):** um setpoint float seta o valor do ponto analógico.

Isso já dá interatividade básica na tela. O **comportamento físico coerente** (abrir o
disjuntor → corrente cai → tensão a jusante muda) é o **Passo 2** (mini-modelo de processo
por slave), a ser implementado depois.

---

## 8. Arquivos tocados

| Arquivo | Mudança |
|---------|---------|
| `src/protocols/modbus/__init__.py`  | novo (pacote) |
| `src/protocols/modbus/gateway.py`   | **novo** — servidor Modbus-TCP + mapeamento dos pontos |
| `main.py`                           | registra o gateway após os protocolos |
| `config/default.yaml`               | nova seção `modbus:` |
