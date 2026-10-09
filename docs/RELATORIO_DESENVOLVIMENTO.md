# Relatório de Desenvolvimento — Gerador de Tráfego para Infraestruturas Críticas

**PFC — Engenharia de Comunicações (IME), 2026**
1º Ten Luciano Damitz Pinheiro · 1º Ten Al Davi de Oliveira Rabelo
Orientadores: Cel Clayton Escouper das Chagas · TC Antônio da Silva Nascimento Filho

**Data do relatório:** 28/07/2026
**Versão de referência:** `PFC_Pinheiro_Rabelo/`
**Ambiente de validação:** Windows 11, Python 3.14.6

---

## 1. Ponto de partida

O planejamento oficial está na apresentação de **20/05/2026** (`Apresentações/Apresentação_PFC _Rabelo_Pinheiro.pdf`, MVE), que define quatro fases:

| Fase | Escopo | Situação em 20/05 |
|------|--------|-------------------|
| 1 | IEC 104 + engine base | Quase completa |
| 2 | DNP3 | Não iniciada |
| 3 | OPC-UA | Não iniciada |
| Final | Validação e documentação | Não iniciada |

Auditando o código contra os slides 23, 24 e 27, tudo que a Fase 1 declarava existia — codec APDU I/S/U, 24 TypeIDs, janela k/w, timers T1/T2/T3, quality bits, condições de rede, perfis de carga e métricas de RTT/PLR — **exceto o Dashboard Web**, nomeado explicitamente no slide 24 ("FastAPI e Uvicorn... via WebSockets, `core/event_bus.py`") e desenhado no slide 27. Não havia `event_bus.py`, nem FastAPI, nem nenhum arquivo web no projeto.

A decisão foi fechar esse gap antes de abrir a Fase 2, para que a Fase 1 entregasse o que foi apresentado à banca — e porque o dashboard, uma vez construído sobre a classe base, seria herdado pelas fases seguintes sem trabalho adicional.

---

## 2. Fase 1 — Event Bus e Dashboard Web

### 2.1 O que foi implementado

**`src/core/event_bus.py`** — Pub/sub em memória sobre `asyncio.Queue`.

O ponto de projeto crítico: `publish()` **nunca bloqueia**. Sem clientes conectados é um no-op (checagem de set vazio); com a fila de um cliente lento cheia, o evento mais antigo é descartado. O caminho de envio de frames jamais espera pelo dashboard — se esperasse, contaminaria a medição de RTT, que é justamente o instrumento de análise defendido no slide 28.

O `snapshot()` reaproveita `TrafficStats.summary()`, já existente em `core/engine.py`. Nenhuma métrica foi reimplementada.

**`src/web/server.py`** — FastAPI com `GET /` e `WebSocket /ws`. Um único laço escreve no socket, alternando entre snapshot de métricas a 1 Hz e eventos de frame via `asyncio.wait_for` — websockets não suportam envios concorrentes. O uvicorn sobe como task no **mesmo event loop** (`uvicorn.Server(...).serve()`); `uvicorn.run()` criaria um segundo loop e deixaria o event bus inacessível.

**`src/web/static/index.html`** — Página única, sem CDN, funcional em máquina isolada da internet: cards por gerador, gráfico de PPS em canvas (janela de 60 s), barras de RTT P50/P95/P99, histograma de ASDU por TypeID e log rolante dos últimos 200 frames.

**Instrumentação** — O método `_publish_frame()` ficou em `BaseProtocolGenerator` (`core/engine.py`), não nos protocolos. Consequência direta: DNP3 e OPC-UA passaram a alimentar o dashboard sem uma linha de código adicional.

### 2.2 Verificação executada

| Item | Resultado |
|------|-----------|
| `GET /` | 200, 12.687 bytes, marcadores presentes |
| WebSocket | 13 mensagens de stats + 83 eventos de frame |
| Ordem do handshake (slide 29) | `STARTDT_ACT` → `STARTDT_CON` → `M_EI_NA_1` → `C_IC_NA_1` |
| Dois clientes simultâneos | 125 frames cada, 68 nomes em comum |
| PLR sob preset `lossy` (10%) | slave 8,89% (4 frames), master 20,0% (2 frames) |
| RTT `ideal` → `wan_degraded` | 106 ms → 493 ms |
| Eventos de descarte no dashboard | `tx: 24, rx: 23, drop: 6` |
| Snapshot × relatório final do console | Consistentes (mesma origem, crescimento monotônico) |
| Execução sem FastAPI (`enabled: false`) | Funciona; import é tardio |

> **Não executado:** validação no Wireshark, que exige inspeção manual.

---

## 3. Defeitos encontrados

Os três defeitos abaixo **não apareceram na revisão manual do código** — só surgiram ao executar.

### 3.1 Dependência `websockets` ausente

O `uvicorn` não instala o `websockets` como dependência e **recusa o upgrade da conexão** sem ele. O endpoint `/ws` simplesmente não funcionaria numa instalação limpa a partir do `requirements.txt`. Corrigido com a dependência explícita e comentário justificando.

### 3.2 Falha de bind derrubava o simulador inteiro

Quando não consegue fazer bind da porta, o uvicorn chama `sys.exit(1)`. Um `SystemExit` que escapa de uma task **sobe pelo event loop e encerra o processo** — nem um `except Exception` no chamador o intercepta.

Reproduzido acidentalmente (porta 8000 ocupada por execução anterior): o simulador morreu sem relatório final. Corrigido isolando a exceção **dentro da corrotina** (`src/web/server.py`, `_run()`), convertendo-a em erro comum. Validado ocupando a porta 8000 de propósito: o simulador avisa `Dashboard web falhou: nao foi possivel iniciar o dashboard na porta 8000 (ja em uso?)` e segue rodando até o relatório final.

### 3.3 Deadlock no encerramento — pré-existente

**Este é o achado mais relevante para o projeto.** O defeito existe desde a versão de 18/05 e não tem relação com o dashboard.

Em `src/protocols/iec104/slave.py`, o bloco `async with self.server` chama `wait_closed()` na saída. A cadeia:

```
wait_closed() → espera _handle_connection terminar
              → _handle_connection espera as 6 tasks da conexão
              → essas tasks só seriam canceladas depois, por stop()
```

Deadlock. O simulador travava indefinidamente no encerramento, **sem emitir o relatório final** — perdendo exatamente os dados que os experimentos do PFC precisam.

Não travava sempre porque o `ConnectionError` lançado pelo timer T1 quebrava o ciclo **por acidente**. Daí a correlação medida: toda execução travada era uma execução em que o T1 não disparou.

Diagnóstico feito com dump das tasks asyncio pendentes (o `py-spy` não suporta Python 3.14), que mostrou as tasks do slave `pendentes e não canceladas` 40 s após o início do encerramento.

**Agravante:** o Python 3.12.1+ passou a fazer `Server.wait_closed()` realmente esperar os handlers de conexão. O defeito tende a se manifestar mais em Python recente do que na versão em que o código foi escrito.

Correção: cancelar as tasks da conexão **antes** da saída do `async with`, e limitar o `writer.wait_closed()` a 5 s.

| Métrica | Antes | Depois |
|---------|-------|--------|
| Travamentos | 1 em 4 execuções | 0 em 8 execuções |
| Overhead de encerramento | ~14 s (esperando T1) | ~1,3 s |

O mesmo cuidado foi aplicado ao `DNP3Outstation`, que tem estrutura idêntica.

---

## 4. Fase 2 — DNP3 (IEEE 1815-2012)

Codec próprio das **três camadas do modelo EPA**, em Python puro, porta 20000.

### 4.1 Estrutura

| Arquivo | Conteúdo |
|---------|----------|
| `src/protocols/dnp3/frames.py` | Codec das 3 camadas, CRC-16/DNP, Object Groups |
| `src/protocols/dnp3/outstation.py` | Servidor TCP, base de dados, buffers de evento por classe |
| `src/protocols/dnp3/master.py` | Cliente TCP, polls, comandos, clock sync |

**Data Link** — frame `0x05 0x64 | len | ctrl | dest | src | CRC`, com CRC-16/DNP obrigatório no cabeçalho e a cada bloco de 16 bytes.

**Transport** — segmentação e remontagem com FIR/FIN e sequência de 6 bits; fragmentos de aplicação de até 2048 bytes viram múltiplos frames de link de 250 bytes.

**Application** — Function Codes e Object Groups 1, 2, 10, 12, 20, 22, 30, 32, 40, 41, 50 e 60.

### 4.2 Dinâmica implementada

- **Integrity Poll (Class 0)** — base estática completa numa resposta
- **Eventos por classe (1/2/3)** — varredura por deadband, com timestamp, drenados por prioridade
- **Unsolicited Responses** — envio espontâneo sem poll do master
- **Select-Before-Operate** — `SELECT` → `OPERATE` com validação de seleção pendente e expiração de 10 s
- **IIN** — bits de classe pendente, restart e need-time nas respostas

### 4.3 Validação do codec

Suíte de **26 verificações**, todas aprovadas, cobrindo CRC, round-trip das três camadas isoladas e da pilha completa, detecção de corrupção, extração de frames de stream contínuo e fragmentação de 100 pontos analógicos em múltiplos frames de link.

O ponto mais importante: o CRC é conferido contra o **check value oficial do catálogo CRC-16/DNP** — `CRC("123456789") == 0xEA82`. Esse teste pegou um bug real no encoder: o byte `ctrl` não estava sendo empacotado, produzindo cabeçalho de 7 bytes em vez de 8.

### 4.4 Execução

Ciclo SBO completo confirmado em log:

```
>> SELECT BO[0] = True
   RX <- SELECT seq=6
   >> SELECT ponto 0 codigo=0x03
>> OPERATE BO[0]
   RX <- OPERATE seq=8
   >> Comando aplicado: BO[0] = True
```

Histograma de function codes ao fim de uma execução de 30 s, confirmando o perfil de tráfego:

- Outstation recebeu: `{CONFIRM: 2, READ: 6, WRITE: 1, SELECT: 1, OPERATE: 1}`
- Master recebeu: `{RESPONSE: 9, UNSOLICITED_RESP: 2}`

Zero erros de CRC.

---

## 5. Fase 3 — OPC-UA (IEC 62541)

Client/Server via **`asyncua`** na porta 4840, endpoint `opc.tcp://host:4840/scada/server/`.

| Arquivo | Conteúdo |
|---------|----------|
| `src/protocols/opcua/server.py` | Address Space `Objects/Subestacao/`, 8 nós, atualização periódica |
| `src/protocols/opcua/client.py` | Browse, Subscription, MonitoredItems, Read/Write |

Implementado: handshake completo (HEL/ACK, `OpenSecureChannel`, `CreateSession` — feitos pela biblioteca), Browse periódico do Address Space, Subscription com MonitoredItems operando por `DataChange`, Read de todos os nós e Write em set-point com medição de RTT.

O endpoint sobe sem criptografia (`NoSecurity`) deliberadamente: o objeto de estudo é o padrão de tráfego, e um canal aberto mantém a captura no Wireshark legível.

Medições de uma execução: Browse de 8 nós em 21,9 ms; Read x8 entre 2,7 e 4,1 ms; Subscription com 8 MonitoredItems a 1000 ms.

### ⚠️ Ressalva sobre as métricas — relevante para a monografia

No **IEC 104** e no **DNP3** o codec é próprio, então as estatísticas são **por frame**.

No **OPC-UA** o enquadramento binário fica dentro da `asyncua`, e as métricas são **no nível de serviço**: cada Read, Write, Browse ou notificação DataChange conta como uma operação, com tamanho estimado.

**Os números em bytes dos três protocolos não são diretamente comparáveis.** Uma tabela comparativa que ignore essa diferença seria contestável na defesa. A ressalva está registrada também no `README.md`.

---

## 6. Estado final

### 6.1 Estrutura

```
PFC_Pinheiro_Rabelo/
├── main.py                   config/default.yaml    requirements.txt
├── src/core/                 engine · event_bus · network_conditions · load_profile
├── src/web/                  server.py · static/index.html
└── src/protocols/            iec104/ · dnp3/ · opcua/
```

### 6.2 Verificação de integração

Seis geradores simultâneos (IEC 104 slave/master, DNP3 outstation/master, OPC-UA server/client):

| Verificação | Resultado |
|-------------|-----------|
| Execuções limpas | 4 de 4 |
| Travamentos | 0 |
| Erros reportados | 0 em todos os seis geradores |
| Geradores no dashboard | 6 de 6, com RTT nos três masters/clients |
| Instalação a partir do `requirements.txt` | Sem erros |
| Compilação de todos os fontes | Sem erros |

---

## 7. Laboratório de ataque interativo (IEC 104)

Console interativo que transforma o simulador num **testbed de segurança defensiva**: o usuário
envia pacotes manualmente a um endpoint IEC 104 real e observa, por pacote, como o alvo reage.
Alinhado ao objetivo do PFC ("validação de mecanismos de defesa... sem impactar sistemas
críticos"). Uso acadêmico/defensivo.

### 7.1 Estrutura

| Arquivo | Conteúdo |
|---------|----------|
| `src/attacks/iec104_lib.py` | Construtores de frame + classificador de resposta |
| `attack.py` | Console REPL (você = master/atacante) |
| `config/target_iec104.yaml` | Perfil de alvo: só o slave + dashboard |
| `ATAQUES.md` | Guia dos ataques, respostas e achados de segurança |

Fluxo de dois terminais: `python main.py -c config/target_iec104.yaml` (alvo) +
`python attack.py` (console). Nenhuma alteração de protocolo foi necessária no núcleo — o alvo
é o slave que já existia; o console é apenas um novo peer.

### 7.2 Três níveis de controle

O usuário **não fica preso a um menu**: presets (`gi`, `unauth`, `flood`, `malformed`, `oos`…),
`craft type=… cot=… …` para montar qualquer ASDU campo a campo, e `raw <hex>` para injetar
bytes literais. O construtor genérico reusa `APDUCodec`/`ASDU` do codec existente.

### 7.3 Classificação da resposta

Cada pacote é rotulado: `POSITIVO`, `DADOS`, `S-FRAME`, `NEGATIVO`, `SILENCIO` ou
`CONEXAO ENCERRADA`. Ponto conceitual central: **o IEC 104 não responde a tudo** — silêncio é
desfecho legítimo (frame malformado, type_id desconhecido, ASDU truncado, comando sem STARTDT).
O classificador separa a resposta ao pacote da **telemetria autônoma de fundo** do RTU
(`COT=SPONTANEOUS`/`PERIODIC`), para não confundir "o RTU falou sozinho" com "meu pacote gerou
resposta".

### 7.4 Achados de segurança evidenciados

1. **Sem autenticação.** `unauth <ioa> on` altera o disjuntor sem STARTDT e sem credencial. O
   alvo fica em `SILENCIO` (ACK suprimido), mas `verify <ioa>` prova a mudança de estado.
2. **Common address não validado.** `badaddr` executa e confirma um comando endereçado a um
   ASDU address alheio.
3. **Sequenciamento frouxo.** `oos` (SSN fora de ordem) é só logado; o comando é executado.
4. **Truncação dessincroniza o parser de stream.** Um frame com length exagerado funde-se ao
   próximo no buffer do alvo, corrompendo a leitura seguinte (vulnerabilidade de reassembly).

Esses pontos justificam a extensão natural: mecanismos de defesa (IEC 62351, validação estrita
de sequência/common address, rate-limiting).

### 7.5 Verificação

- **Teste headless da lib (14 verificações)** contra o slave real, afirmando a classificação de
  cada categoria — inclusive o `unauth` silencioso com prova de mudança de estado via GI, e a
  demonstração determinística do desync por truncação. Todas passam.
- **Sanidade do REPL** dirigido por stdin com sequência completa (sessão, comandos, ataques,
  `reconnect`, `craft`, `raw`).
- **Cruzamento com o dashboard**: os pacotes do atacante aparecem no log de frames ao vivo.

### 7.6 Novo defeito encontrado e corrigido

O laboratório expôs uma **segunda variante do deadlock de encerramento** (§3.3): quando uma
conexão está ativa no momento do shutdown — situação comum com o atacante conectado — a saída
do bloco `async with self.server` chamava `wait_closed()` durante o desenrolar do cancelamento
e travava. O alvo parava em "Parando engine..." sem completar. Corrigido removendo o
`async with` (o fechamento do servidor fica só no `stop()`, com `wait_for(..., timeout=5)`),
no slave IEC 104 e no outstation DNP3. Verificado: com uma conexão de ataque deliberadamente
mantida aberta, o alvo encerra limpo e libera as portas.

---

## 8. Pendências

0. **Estender o laboratório de ataque a DNP3 e OPC-UA** — o framework (`src/attacks/`) foi
   desenhado para isso; falta `dnp3_lib.py` (CRC inválido, Unsolicited forjado, SBO sem select)
   e, no OPC-UA, ataques no nível de serviço. Um painel de ataque no dashboard reusaria a lib.
1. **Validação no Wireshark do DNP3** — o dissector nativo confirma o CRC e a estrutura das três camadas. É a evidência de conformidade mais forte para a banca, equivalente ao que o slide 29 já mostra para o IEC 104.
2. **Testes automatizados (Fase Final)** — a suíte de 26 verificações do codec DNP3 já existe e serve de base; falta movê-la para `tests/` e escrever a cobertura equivalente para o IEC 104.
3. **Experimentos comparativos (Fase Final)** — com a ressalva da seção 5 sobre comparabilidade de métricas.
4. **Exportação de métricas para CSV/JSON** — hoje as estatísticas saem por log e pelo dashboard; para gerar os gráficos da monografia seria útil persistir em arquivo.
5. **Atualizar a apresentação** — os slides 23 e 27 descrevem o dashboard como entregue em 20/05, quando na verdade foi implementado depois.
