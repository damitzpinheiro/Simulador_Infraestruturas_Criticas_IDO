# Cenário da Cidade (multimáquina) — apresentação do PFC

Simulação de uma rede elétrica de cidade com **2 usinas + 3 bairros**, acopladas
pela **frequência do sistema**, observada pelo **SCADA-LTS** via gateway Modbus.
Dois ataques MITM são demonstrados de forma visível:

- **Caso 1 — Cascata:** injeção de comando (IEC 104) abre o disjuntor da usina
  térmica → déficit de geração → a frequência despenca → a proteção de
  subfrequência (UFLS) corta bairros em cascata. **Visível no SCADA-LTS.**
- **Caso 2 — Falsificação de telemetria:** substituição de certificado (OPC-UA)
  faz o operador ver valores normais enquanto a realidade diverge. Só a
  **validação de certificado** (`secure validate`) derrota o ataque.

> Por que dois protocolos diferentes? Os dois ataques funcionam em qualquer um dos
> três protocolos, mas cada caso é ligado ao protocolo onde ele é mais forte: a
> injeção de comando ao **IEC 104** (byte-proxy clássico) e a falsificação de
> telemetria ao **OPC-UA** (o achado central da tese: cripto ligada não basta).

---

## 1. Topologia

```
                 BARRA DE TRANSMISSÃO 138 kV
   [USINA HIDRO]────────────┬────────────[USINA TÉRMICA]
    OPC-UA :4840            │              IEC104 :2404   <- alvo do Caso 1
    45 MW (sem reserva)     │              100 MW
                    ┌───────┼───────┐
              [INDUSTRIAL] [CENTRO] [HOSPITAL]
               IEC104:2414  DNP3:20000  DNP3:20010
               75 MW        50 MW       20 MW (carga crítica)
               corta 1º     corta 2º    corta por último
```

| Entidade    | Papel   | Protocolo | Porta | Observação                         |
|-------------|---------|-----------|-------|------------------------------------|
| `termica`   | usina   | IEC 104   | 2404  | **alvo do Caso 1** (injeção)       |
| `industrial`| bairro  | IEC 104   | 2414  | maior carga, cortado primeiro      |
| `centro`    | bairro  | DNP3      | 20000 | cortado em segundo                 |
| `hospital`  | bairro  | DNP3      | 20010 | carga crítica, cortado por último  |
| `hidro`     | usina   | OPC-UA    | 4840  | **alvo do Caso 2** (falsificação)  |

O **SCADA-LTS** é o **master observador**: fala Modbus com o gateway (`:5020`),
que espelha os pontos dos 5 slaves. O SCADA-LTS mostra a **realidade física** da
planta (caminho não atacado).

---

## 2. Subir o simulador

```bash
python main.py -c config/cidade_multimaquina.yaml
```

Sobe os 5 slaves + masters/clientes + gateway Modbus + modelo de rede. Abre o
**console interativo** (é dele que se dispara o Caso 1). Confira no log o bloco
`MAPA DE REGISTRADORES MODBUS` — é o mapa que o SCADA-LTS usa.

Comandos úteis do console:

```
grid                 estado da rede (frequência, usinas, bairros)
grid restore         religa tudo e volta a 60 Hz (reset entre demos)
grid trip <ent>      abre um disjuntor sem MITM (ensaio: grid trip termica)
grid close <ent>     fecha um disjuntor
mitm on              liga o proxy MITM IEC 104 na frente da térmica
```

---

## 3. SCADA-LTS: importar a tela

Os arquivos estão em `scadalts/`:

- `cidade_emport.json` — 1 data source Modbus + 19 data points + 1 graphical view
- `cidade_bg.png` — diagrama unifilar da cidade (fundo da view; o SCADA-LTS
  aceita PNG/JPG/GIF, **não** SVG). O `cidade_bg.svg` é o mesmo desenho em vetor,
  caso precise editar.

**Passo a passo (SCADA-LTS 2.7.8.1):**

1. Login `admin/admin` em `http://localhost:8080/Scada-LTS`.
2. Menu **Import/Export** (ou *System settings → Emport*). Cole o conteúdo de
   `cidade_emport.json` e clique **Import**. Isso cria:
   - o data source **Simulador Cidade** (Modbus IP TCP → `127.0.0.1:5020`, unit 1);
   - os **19 data points** já mapeados nos endereços Modbus corretos;
   - a graphical view **Cidade - Rede Eletrica**.
   > Se o simulador roda em outra máquina, ajuste o host do data source na UI
   > depois de importar (editar → host/porta do Modbus).
3. **Watch list / Data sources:** confirme que os pontos estão a atualizar (verde).
4. **Graphical Views → Cidade - Rede Eletrica.** Para o fundo: *Edit* → envie
   `cidade_bg.png` como *background* → salve. Os componentes já caem sobre as caixas.
   > A view usa componentes `SIMPLE` (não dependem de image sets, importam de
   > forma robusta). Se algum componente não posicionar bem, arraste na UI — os
   > data points já estão criados, que é o trabalho pesado.

### Mapa de endereços Modbus (base 0)

**Coils (FC01/FC05) — disjuntores:**

| Coil | Entidade    | XID do ponto        |
|------|-------------|---------------------|
| 0    | térmica     | `DP_termica_breaker`    |
| 1    | industrial  | `DP_industrial_breaker` |
| 2    | centro      | `DP_centro_breaker`     |
| 3    | hospital    | `DP_hospital_breaker`   |
| 4    | hidro       | `DP_hidro_breaker`      |

**Holding registers (FC03, float32 "4 Byte Float", 2 regs cada):**

| Reg | Medida                       | Reg | Medida                       |
|-----|------------------------------|-----|------------------------------|
| 0   | térmica tensão (kV)          | 14  | centro corrente (A)          |
| 2   | térmica potência (MW)        | 16  | centro carga (MW)            |
| 4   | **FREQUÊNCIA do sistema (Hz)** | 18  | hospital tensão (kV)       |
| 6   | industrial tensão (kV)       | 20  | hospital corrente (A)        |
| 8   | industrial corrente (A)      | 22  | hospital carga (MW)          |
| 10  | industrial carga (MW)        | 24  | hidro potência (MW)          |
| 12  | centro tensão (kV)           | 26  | hidro tensão (kV)            |

---

## 4. Caso 1 — Cascata (IEC 104, injeção de comando)

No **console do simulador**:

```
mitm on              # entra no meio da conversa master <-> térmica (porta 2404)
s command 100 off    # injeta "abrir disjuntor" (IOA 100) como se fosse o master
```

**O que acontece / o que a plateia vê no SCADA-LTS** (≈10 s):

1. `termica.potência` cai para 0 (usina desligada pela injeção).
2. `FREQUÊNCIA` despenca de 60 Hz.
3. Ao cruzar ~58,5 Hz, a UFLS corta o **BAIRRO INDUSTRIAL** → fica **APAGADO** (vermelho).
4. A frequência continua caindo → corta o **BAIRRO CENTRO** → **APAGADO**.
5. Sobra energizado só o **HOSPITAL** (carga crítica, protegida) + a hidro.
6. A frequência **recupera** para ~60 Hz com a rede reduzida.

**Reset para repetir:**

```
mitm off
grid restore
```

> Sem MITM, dá para ensaiar a mesma cascata com `grid trip termica` (e voltar com
> `grid restore`). Útil para treinar o timing antes da apresentação.

---

## 5. Caso 2 — Falsificação de telemetria (OPC-UA, substituição de certificado)

Em **outro terminal** (portas 4850/4851, não conflita com o simulador):

```bash
python mitm.py --proto opcua --lab
```

Console do ataque (menu com `help`):

```
nodes                       lista os nós e o valor REAL de cada um
secure trustall             cifrado, vítima confia em tudo  -> MITM por troca de cert
spoof Frequencia_Hz 60.0    a vítima (operador) passa a ver 60 Hz forjado
spoof Potencia_Ativa 55     esconde a queda de potência
observe on                  loga cada valor repassado/adulterado
secure validate             vítima VALIDA o certificado      -> MITM DERROTADO
```

**Narrativa (duas telas):**

- **Tela da realidade (SCADA-LTS / Modbus):** mostra os valores verdadeiros da planta.
- **Tela do operador (cliente OPC-UA, atrás do proxy):** mostra o que o atacante
  quer — frequência/potência "normais". As duas **divergem**: o operador está cego.
- **Defesa:** com `secure validate`, a vítima rejeita o certificado forjado e o MITM
  cai — a tela do operador volta a bater com a realidade. **Cripto ligada não basta;
  a validação de certificado é o que derrota o ataque.**

---

## 6. Ajustar a "dose de drama" (física)

Em `config/cidade_multimaquina.yaml`, bloco `grid_model:`:

- `generators.hidro.p_max` — **maior** = mais reserva = cascata mais suave (corta
  menos bairros). Com 45 MW (padrão) a rede perde industrial **e** centro.
- `inertia_mws` — **maior** = a frequência cai mais devagar (cascata mais lenta,
  boa para narrar). Padrão 120.
- `ufls_stages` / `ufls_delay_s` — limiares e atraso dos relés de subfrequência.
- `damping_mw_hz` — autorregulação de carga; **menor** aprofunda a queda.
- `gen_trip_hz` — abaixo disto as usinas restantes disparam (**colapso total**);
  desligue a UFLS (`ufls_enabled: false`) para demonstrar o blecaute completo.

Depois de mudar pontos no config, rode o simulador e confira o `MAPA DE
REGISTRADORES` no log; os endereços Modbus da tela (`scadalts/cidade_emport.json`)
devem bater com esse mapa. Se mudar a topologia, ajuste os data points na UI do
SCADA-LTS para os novos endereços.

---

## 7. O que é config vs. o que exige código

Este cenário é **data-driven**: a física da rede (`src/core/grid_model.py`) foi
escrita **uma vez** e lê a topologia do config. Montar uma simulação **diferente**
é, na grande maioria dos casos, editar YAML — **sem programar**.

### Reconfigurável só no `.yaml` (sem tocar em código)

| O que você quer mudar | Onde |
|---|---|
| Nº de usinas / bairros (adicionar ou remover) | `grid_model.generators` / `grid_model.districts` |
| Capacidade, despacho, reserva e droop de uma usina | `p_max`, `p_set`, `droop_mw_hz` |
| Carga, tensão de barra e prioridade de corte de um bairro | `load_mw`, `bus_kv`, `priority` |
| Velocidade / profundidade da cascata | `inertia_mws`, `damping_mw_hz` |
| Proteção de subfrequência (ligar/desligar, limiares, atraso) | `ufls_enabled`, `ufls_stages`, `ufls_delay_s` |
| Blecaute total vs. controlado | `ufls_enabled: false` e/ou `gen_trip_hz` |
| Qual protocolo/porta cada entidade usa; quais pontos expõe | blocos `iec104:`/`dnp3:`/`opcua:` (nós) |
| Alvo e tipo de ataque | ferramenta MITM única (`mitm.py --proto iec104\|dnp3\|opcua`) |

> Ex.: adicionar uma 3ª usina é **uma linha** em `generators:`. Trocar a cidade
> inteira é reescrever esses dois blocos. Nenhuma linha de Python.

### Exige código (função nova em `grid_model.py`) — só p/ fenômeno físico novo

Apenas quando o efeito que você quer **não existe** no modelo hoje, por exemplo:

- colapso de **tensão** / potência reativa (VAr);
- **coordenação de proteção** com curvas tempo-corrente, diferencial de trafo;
- **ilhamento**, controle de excitação, rampa de partida de usina;
- outra dinâmica de frequência que não a equação de swing agregada.

Mesmo nesses casos, todo o encanamento (pontos, protocolos, gateway Modbus,
superfície de ataque, tela do SCADA-LTS) é reaproveitado — muda só a física.

**Regra prática:** "outra rede elétrica, maior/menor/diferente" = **config**.
"Outro fenômeno físico" = **código** (uma vez, e reusável dali em diante).
