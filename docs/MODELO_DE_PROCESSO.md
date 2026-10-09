# Passo 2 — Modelo de processo (comportamento físico coerente)

Este documento descreve o **Passo 2** da integração com o SCADA-LTS: dar aos pontos do
simulador um **comportamento físico coerente**, em vez de cada ponto variar sozinho.

---

## 1. Problema que o Passo 2 resolve

Até aqui cada *datapoint* variava de forma independente (senoide + ruído nos analógicos,
toggle aleatório nos booleanos). A HMI ficava "viva", mas **sem sentido físico**: a corrente
não tinha relação com o disjuntor, a tensão não reagia à carga, abrir um disjuntor não fazia
nada a jusante. Para uma HMI realista — e, principalmente, para uma **demonstração de ataque
MITM convincente** — os pontos precisam obedecer a uma física comum.

## 2. O modelo — uma subestação coerente

Foi criado um **modelo de processo único** (`SubstationModel`) que mantém o estado físico de
uma subestação e é observado pelos três protocolos ao mesmo tempo. Relações modeladas:

```
   disjuntor aberto ─────────► corrente do alimentador cai a ~0
        │                              │
        │                              ▼
        │                    potência ativa (P ≈ √3·V·I·fp) cai
        │                              │
        ▼                              ▼
   barra a jusante colapsa    tensão da barra afunda com a carga
   (se for a entrada do trafo)        │
                                      ▼
                        regulador de tap corrige a tensão ao setpoint
                                      │
   carga total ──────────────────────┴──► temperatura do trafo sobe
                                           (atraso térmico de 1ª ordem)
   potência ──────────────────────────────► integra em energia (contadores)
```

Além disso:
- **Trips espontâneos:** cada disjuntor tem uma pequena probabilidade de abrir sozinho
  (falta), com **religamento automático** após um tempo.
- **Comando da HMI/MITM:** abrir/fechar um disjuntor (via Modbus FC05, ou comando IEC104)
  entra no modelo e dispara toda a cascata acima.

## 3. Arquitetura — pontos viram "vistas" do modelo

O modelo **não fala nenhum protocolo**; só mantém o estado físico. A integração é feita por
*binding*: cada ponto de cada slave é amarrado a um **sinal** do modelo.

```
   SubstationModel  (estado físico: disjuntores, correntes, tensões, temp, energia)
        ▲   │
 command │   │ advance(dt) + get(signal)
        │   ▼
   ProcessModelDriver  ── a cada tick: model.advance(dt); empurra get(signal) → cada ponto
        │
        ├──► IEC104  DataPoint   (._model_signal)   ──► emite spontânea/GI normalmente
        ├──► DNP3    DNP3Point   (._model_signal)   ──► emite eventos normalmente
        └──► OPC-UA  OPCUAVariable(._model_signal)  ──► emite DataChange normalmente
                       ▲
                       │ apply_write(v)  (escrita da HMI via Modbus/IEC104)
                 gateway Modbus / comando IEC104  ──► roteia p/ model.command(signal, v)
```

- Cada classe de ponto ganhou: `bind_model(model, signal, is_input)`, `_set_modeled_value(v)`
  e `apply_write(v)`. Quando amarrado, o `update()`/`atualizar()` **não randomiza** — apenas
  reporta o valor que o modelo dita (mantendo a detecção de mudança, então o protocolo segue
  emitindo espontâneas/eventos normalmente).
- O **`ProcessModelDriver`** é um gerador (aparece no `status`, para junto com a engine) que a
  cada `tick_s` chama `model.advance(dt)` e empurra os sinais para os pontos amarrados.
- **Escrita da HMI:** o *setter* do gateway Modbus e o handler de comando IEC104 chamam
  `ponto.apply_write(v)`. Se o ponto for uma **entrada** do modelo (disjuntor, setpoint), a
  escrita vai para `model.command()` — senão o disjuntor voltaria ao estado do modelo no
  próximo tick. Assim o comando da HMI realmente comanda a planta.

## 4. Mapeamento (mesma planta, três protocolos)

O mesmo sinal físico é observado pelos três protocolos — ex.: o disjuntor 1 é `IOA 100` no
IEC104, `Binary Input 0` no DNP3 e `Disjuntor_1` no OPC-UA. Falsificar um deles via MITM o faz
**discordar dos outros e da física** — daí a força da demonstração de ataque.

| Sinal do modelo        | IEC104 (IOA) | DNP3 (grupo,índice) | OPC-UA (nó)        | Entrada? |
|------------------------|--------------|---------------------|--------------------|----------|
| breaker.1              | 100          | (1,0)               | Disjuntor_1        | sim      |
| breaker.2              | 101          | (1,1)               | Disjuntor_2        | sim      |
| breaker.3              | 102          | (1,2)               | —                  | sim      |
| breaker.4              | 103          | (1,3)               | —                  | sim      |
| bus.a.voltage          | 200          | (30,0)              | Tensao_Barra_A     | não      |
| bus.b.voltage          | 201          | (30,1)              | Tensao_Barra_B     | não      |
| feeder.1.current       | 300          | (30,2)              | Corrente_Fase_1    | não      |
| feeder.2.current       | 301          | (30,3)              | —                  | não      |
| power.active           | 400          | (30,4)              | Potencia_Ativa     | não      |
| transformer.temp       | 500          | —                   | Temperatura_Trafo  | não      |
| transformer.temp2      | 501          | —                   | —                  | não      |
| isolator.1             | 600          | —                   | —                  | não      |
| energy.active          | —            | (20,0)              | —                  | não      |
| energy.reactive        | —            | (20,1)              | —                  | não      |
| setpoint.voltage       | —            | —                   | Setpoint_Tensao    | sim      |

> Os mapas ficam em `src/core/process_model.py` (`IEC104_MAP`, `DNP3_MAP`, `OPCUA_MAP`) e são
> facilmente ajustáveis.

## 5. Validação — a cascata via Modbus

Com o simulador rodando (`python main.py --role slave`), escrevendo o coil do disjuntor 1 pela
HMI/Modbus e lendo as medições:

| Estado                    | Corrente F1 | Barra A | Barra B | Potência |
|---------------------------|-------------|---------|---------|----------|
| Disjuntor fechado         | 507.6 A     | 132.4 kV| 64.7 kV | 48.2 MW  |
| **Aberto pela HMI**       | **9.1 A**   | 138.1 kV| **5.7 kV** | **0.9 MW** |
| Fechado de novo           | 521.2 A     | 134.4 kV| 65.5 kV | 50.2 MW  |

Abrir o disjuntor derruba a corrente e a potência, colapsa a barra a jusante e a barra A se
recupera (carga removida). Fechar restaura tudo. O mesmo se vê no IEC104, DNP3 e OPC-UA.

## 6. Valor para o laboratório de ataque

Antes: falsificar um valor via MITM mudava um número solto num stream aleatório.
Agora: a planta **reage fisicamente**, então um ataque fica muito mais evidente e didático:
- **Falsificar uma medida** (ex.: mostrar corrente normal com o disjuntor aberto) cria uma
  **inconsistência física** visível — o operador vê corrente sem tensão a jusante.
- **Comandar um disjuntor** via MITM dispara a cascata real (corrente/tensão/potência caem),
  demonstrando o impacto de um comando não autorizado.
- **Discordância entre protocolos:** falsificar só o IEC104 faz o DNP3/OPC-UA discordarem, um
  vetor de detecção interessante.

## 7. Configuração

Seção `process_model:` em `config/default.yaml` (habilitado por padrão). Parâmetros: cargas
nominais dos alimentadores, tensões de barra, setpoint do tap, constante térmica, taxa de trip
e tempo de religamento, e o `tick_s` (passo da física). `enabled: false` volta ao comportamento
antigo (cada ponto randomiza sozinho).

## 8. Arquivos tocados

| Arquivo | Mudança |
|---------|---------|
| `src/core/process_model.py`        | **novo** — `SubstationModel`, mapas de binding, `ProcessModelDriver` |
| `src/protocols/iec104/slave.py`    | `DataPoint`: bind/apply_write + `update()` não randomiza se amarrado; comando roteia p/ `apply_write` |
| `src/protocols/dnp3/outstation.py` | `DNP3Point`: idem |
| `src/protocols/opcua/server.py`    | `OPCUAVariable`: idem |
| `src/protocols/modbus/gateway.py`  | escrita da HMI usa `apply_write` (comando vai ao modelo) |
| `main.py`                          | cria o modelo, amarra os slaves e registra o driver |
| `config/default.yaml`              | nova seção `process_model:` |
