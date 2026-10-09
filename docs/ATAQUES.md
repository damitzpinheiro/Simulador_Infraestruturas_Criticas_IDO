# Laboratório de Ataque — MITM On-Path (IEC 60870-5-104 e DNP3)

Um proxy interceptador transparente: o atacante fica **entre** o master e o slave/outstation
reais, observa toda a conversa e injeta pacotes maliciosos para qualquer um dos dois lados.
É o ataque *on-path* clássico de ICS. Uso acadêmico/defensivo: evidenciar que os protocolos
não têm autenticação nem verificação de integridade em trânsito.

O mesmo modelo está implementado para **IEC 104** (`s`/`m` no console, `mitm on`) e para
**DNP3** (`mitm dnp3 on`). Este documento detalha o IEC 104; o DNP3 é análogo, com o
vocabulário próprio de comandos (ver a seção **DNP3** ao final e `help ataque` no console).

> Não use contra equipamentos de terceiros sem autorização. O alvo aqui é o próprio simulador.

```
master  ──►  [ proxy :2404 ]  ──►  slave real :2405
        ◄──                   ◄──
```

O proxy escuta onde o master espera o slave (2404), conecta ao slave real (2405) e repassa
os dois sentidos frame a frame. De um console, você pode:

- **observar** toda a conversa (frames decodificados, nos dois sentidos);
- **injetar** frames maliciosos → slave (como se fosse o master);
- **injetar** frames maliciosos → master (como se fosse o slave);
- **descartar** frames em trânsito (tamper / DoS seletivo).

---

## Como rodar

Há três formas de montar o cenário — as duas primeiras não precisam de config nenhuma.

### 1. Proxy interno (tudo num console só)

```bash
python main.py
```
```
gerador> mitm on                # move o slave p/ 2405 e sobe o proxy interno na 2404
gerador> s stopdt               # injeta -> slave
gerador> m spoof 200 132        # injeta -> master
gerador> mitm off               # desfaz e volta ao normal
```

### 2. Proxy externo, preparado pelo console

```bash
# Terminal A
python main.py
gerador> mitm extern            # move o slave p/ 2405, deixa a 2404 livre
```
```bash
# Terminal B
python mitm.py                  # o proxy sobe na 2404; o master reconecta por ele
```

Feche o `mitm.py` **antes** de dar `mitm off` no Terminal A, senão a 2404 fica ocupada por ele.

### 3. Proxy externo, totalmente autônomo (sem o console)

```bash
# Terminal A — o par real (slave :2405, master -> :2404)
python main.py -c config/mitm_iec104.yaml
```
```bash
# Terminal B — o proxy MITM interativo
python mitm.py
```

Nas formas 2 e 3 você injeta pelo próprio console do `mitm.py`. Os frames repassados e
injetados aparecem no log ao vivo do proxy (e podem ser confirmados no Wireshark).

### Multi-máquina (proxy entre máquinas distintas)

O `mitm.py` é host-agnostic: o proxy pode ficar numa máquina C, entre o master (máquina B) e
o slave real (máquina A). Suba o proxy escutando em todas as interfaces e apontando para o
IP do alvo; configure o master da máquina B para conectar na máquina C.

```bash
# Maquina C (o atacante on-path):
python mitm.py --listen-host 0.0.0.0 --down-host 192.168.0.10 --down-port 2404
#   escuta na 2404 (todas as interfaces) e repassa para o slave real em 192.168.0.10:2404
```

Vale para DNP3 com `--proto dnp3` (portas padrão 20000/20001). Libere as portas no firewall
da máquina C (ver `ROTEIRO_DE_TESTES.md` §7).

---

## Comandos do console MITM

| Comando | Efeito |
|---------|--------|
| `s <cmd>` / `to-slave <cmd>` | injeta um frame **──► SLAVE** (como se fosse o master) |
| `m <cmd>` / `to-master <cmd>` | injeta um frame **──► MASTER** (como se fosse o slave) |
| `observe on\|off` | liga/desliga o log de todos os frames repassados |
| `drop to-slave\|to-master <n>` | descarta os próximos `n` frames daquele sentido (tamper/DoS) |
| `status` | estado do proxy e sequência (SSN/RSN) observada |

`<cmd>` pode ser:

| Comando | Envia |
|---------|-------|
| `startdt` / `stopdt` / `testfr` | U-frame de sessão |
| `gi` | Interrogação Geral (C_IC_NA_1) |
| `clock` | Sincronização de relógio (C_CS_NA_1) |
| `command <ioa> <on\|off>` | Comando single (C_SC_NA_1) |
| `read <ioa>` | Comando de leitura (C_RD_NA_1) |
| `spoof <ioa> <valor>` | Medição forjada (M_ME_NC_1) — telemetria falsa |
| `oos <ioa> <on\|off>` | Comando com SSN muito à frente do esperado |
| `badcot <ioa> <on\|off>` | Comando com causa de transmissão espúria |
| `badaddr <ioa> <on\|off>` | Comando a um common_address alheio |
| `craft type=<id> cot=<n> [ioa=] [val=] [ca=] [oa=] [ssn=] [rsn=]` | monta **qualquer** ASDU campo a campo |
| `raw <hex...>` | bytes literais do APDU |
| `malformed [start\|length\|trunc]` | frame que viola o enquadramento APDU |

### Exemplos

```
s stopdt                            # para o fluxo de dados no slave (DoS seletivo)
s command 101 on                    # comanda o disjuntor 101 sem o master pedir
m spoof 200 132.0                   # injeta tensão FALSA para o master ver
m craft type=1 cot=3 ioa=100 val=1  # evento espontâneo forjado -> master
drop to-master 5                    # some com 5 frames de telemetria antes de chegarem ao master
```

> **Sequência.** O proxy acompanha os SSN/RSN que passam e sincroniza os frames injetados com
> eles. Injetar **U-frames** (STOPDT/STARTDT/TESTFR), `raw` e `malformed` é limpo. Injetar
> **I-frames** (comandos, telemetria) avança a sequência no lado injetado e pode
> **dessincronizar** a conversa real — o que é, em si, um efeito de ataque observável: um
> atacante on-path pode quebrar a contagem de sequência que o master ou o slave mantêm.

---

## Achados de segurança

Validados executando o proxy contra o par real (master legítimo + slave) deste simulador:

1. **Sem autenticação nem integridade em trânsito.** Nada no protocolo permite ao slave
   distinguir um I-frame do master legítimo de um injetado pelo proxy no meio do caminho — o
   comando `s command <ioa> on` é executado e confirmado como se tivesse vindo do master real.

2. **Telemetria é falsificável.** `m spoof <ioa> <valor>` entrega ao master uma medição
   forjada; ele processa como se fosse dado real do slave, sem nenhuma verificação de origem.

3. **Descarte seletivo não é detectado automaticamente.** `drop to-slave/to-master <n>` some
   com frames em trânsito sem que nenhum dos lados reaja de imediato — só o timeout T1
   (ausência prolongada de confirmação) eventualmente derruba a sessão.

4. **A sessão sobrevive à interceptação.** O master reconecta pelo proxy exatamente como
   reconectaria pelo slave direto — nada no handshake (STARTDT/STARTDT_CON) detecta que há um
   intermediário na conexão.

Esses pontos justificam mecanismos de defesa (IEC 62351: TLS + autenticação sobre o 104,
verificação de integridade por frame) — extensíveis a DNP3 e OPC-UA, que compartilham a
mesma falta de proteção em trânsito.

---

## DNP3 (IEEE 1815-2012)

Mesmo modelo on-path, adaptado às três camadas do DNP3 (Data Link + Transport + Application).
O proxy escuta na 20000 (onde o master espera o outstation) e repassa para o outstation real
na 20001.

```bash
python main.py
```
```
gerador> mitm dnp3 on            # move o outstation p/ 20001 e sobe o proxy interno na 20000
gerador> observe on              # mostra os frames DNP3 repassados (nos dois sentidos)
gerador> s command 0 on          # DIRECT_OPERATE no ponto 0 sem o master pedir
gerador> s sbo 1 on              # OPERATE sem SELECT prévio (testa o Select-Before-Operate)
gerador> m spoof 5 999.9         # Unsolicited com Analog Input forjado -> master
gerador> mitm dnp3 off           # desfaz e volta o outstation p/ a 20000
```

Também há o proxy **standalone** DNP3 (host-agnostic, mesmo `mitm.py`):

```bash
python mitm.py --proto dnp3                     # local: proxy :20000 -> outstation :20001
```

Vocabulário de `<cmd>` do DNP3 (impersonando o master ao injetar `s`, o outstation ao `m`):

| Comando | Envia |
|---------|-------|
| `integrity` | READ Class 0 (varredura completa) |
| `events` | READ Class 1/2/3 (eventos pendentes) |
| `command <idx> <on\|off>` | DIRECT_OPERATE de um CROB — comanda o outstation sem SELECT |
| `sbo <idx> <on\|off>` | OPERATE **sem** o SELECT prévio (viola o Select-Before-Operate) |
| `clock` | WRITE g50v1 (sincronismo de relógio) |
| `spoof <idx> <valor>` | Unsolicited com Analog Input forjado — telemetria falsa ao master |
| `spoofbin <idx> <on\|off>` | Unsolicited com Binary Input forjado — estado falso ao master |
| `restart` | COLD_RESTART — manda o outstation reiniciar (DoS) |
| `badcrc` | frame válido com o CRC deliberadamente corrompido (testa a validação de CRC) |
| `craft func=<n> [group= var= qual= index= val= dir=]` | monta **qualquer** requisição/resposta |
| `raw <hex...>` | bytes literais do frame de link |
| `malformed [start\|length\|trunc]` | frame que viola o enquadramento do Data Link |

> **Prova do efeito.** No DNP3, `dp`/`watch` (a prova automática do IEC 104) ainda não estão
> ligados ao modelo de dados do outstation; use `observe on` para ver o outstation processar
> e **responder** ao comando injetado, e `stats` para os contadores dos dois lados.

### Achados de segurança específicos do DNP3

1. **DIRECT_OPERATE aceito sem SELECT.** `s command <idx> on` executa direto no outstation,
   que responde como se o comando tivesse partido do master real.
2. **Select-Before-Operate contornável.** `s sbo <idx> on` envia um OPERATE sem o SELECT
   correspondente — o SBO é uma proteção de aplicação, não um controle de segurança.
3. **Unsolicited Response é falsificável.** `m spoof <idx> <valor>` entrega ao master uma
   medição forjada como se fosse um evento espontâneo do outstation.
4. **CRC não é proteção de integridade.** O CRC-16/DNP detecta corrupção de transmissão, mas
   um atacante on-path recalcula o CRC de cada frame injetado — não há autenticação.

---

## OPC-UA (IEC 62541) — o contraexemplo seguro

O OPC-UA é diferente dos outros dois: **tem segurança nativa** (certificados X.509,
assinatura e criptografia). Não se "quebra" a criptografia — ataca-se o **modelo de
confiança**. O proxy aqui não é um repassador de bytes: é um endpoint OPC-UA completo dos
dois lados (substituição de certificado). Ele se apresenta à vítima como "o servidor", com o
**seu próprio** certificado, espelha o Address Space do servidor real e adultera as leituras.

```
vitima ──(canal com o cert DO PROXY)──► [ proxy ] ──(canal com o server real)──► server real
```

### Console interativo (escolha o nó e o ataque ao vivo)

```bash
python mitm.py --proto opcua --lab
```

Sobe um laboratório auto-contido (servidor real + proxy + vítima embutida) e abre um console:

```
opcua> nodes                          lista os nós e o valor real de cada um
opcua> check Tensao_Barra_A           compara o valor REAL com o que a VÍTIMA vê
opcua> spoof Tensao_Barra_A 999.9     a vítima passa a ver um valor forjado
opcua> write Setpoint_Tensao 130      injeta um Write no servidor REAL, sem a vítima pedir
opcua> observe on                     loga cada valor repassado/adulterado
opcua> secure none|trustall|validate  alterna a política (mostra os 3 casos AO VIVO)
opcua> status                         modo atual, vítima conectada, nós adulterados
```

O comando `secure` reconstrói o laboratório na política escolhida: em `validate` a vítima
recusa o certificado do proxy e o console avisa *"o proxy não consegue interpor"* — o MITM
derrotado, ao vivo.

### Os três casos (o experimento)

No próprio console acima, o comando `secure none|trustall|validate` reconstrói o laboratório
em cada política e reproduz os três casos ao vivo:

| Caso | Configuração | Resultado |
|------|--------------|-----------|
| 1 | **NoSecurity** (sem criptografia) | MITM transparente **funciona** |
| 2 | **SignAndEncrypt** + vítima *trust-all* | MITM por troca de cert **funciona** (apesar da criptografia) |
| 3 | **SignAndEncrypt** + vítima que **valida** o certificado | MITM **derrotado** |

O único fator que muda entre o Caso 2 e o 3 é a vítima **fixar (validar)** o certificado do
servidor real. Isso prova, empiricamente, que **criptografia ligada não basta**: o que protege
é a validação de identidade (PKI). É a justificativa concreta para o IEC 62351 — levar
TLS + autenticação **com validação** ao IEC 104 e ao DNP3, que hoje não têm proteção alguma.

### Componentes

- `src/attacks/opcua_certs.py` — gera os certificados self-signed de laboratório (servidor,
  proxy e cliente) em `certs/`.
- `src/attacks/opcua_mitm.py` — o proxy de substituição de certificado (`OPCUAMitm`): servidor
  malicioso + cliente para o real, com `spoof(no, valor)` (adultera a leitura) e
  `write_real(no, valor)` (injeta Write no servidor real).
- `mitm.py --proto opcua --lab` — console interativo que sobe o laboratório auto-contido e
  roda os três casos pela opção `secure`. (O mesmo `mitm.py --proto opcua [--transparent]` faz
  o ataque on-path contra a hidro real.)

> Nota: não se decifra tráfego `SignAndEncrypt` por força bruta — isso é inviável e não é o
> objetivo. O ataque é sobre confiança de certificado, não sobre a cifra.
