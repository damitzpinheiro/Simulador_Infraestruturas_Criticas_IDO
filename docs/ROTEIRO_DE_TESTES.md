# Roteiro de Testes — como exercitar todas as funcionalidades

Guia passo a passo para rodar cada funcionalidade e **capturar prints** para a apresentação
e a monografia. Testado no Python 3.14.6 (Windows). No Kali/Linux os comandos são os mesmos.

> Dica de print: no Windows use **Win + Shift + S** (recorte de tela). Para o terminal,
> maximize a janela e aumente a fonte antes de capturar.

---

## 0. Preparação (uma vez)

```bash
cd PFC_Pinheiro_Rabelo
pip install -r requirements.txt
```

Isso instala `pyyaml` e `asyncua`.

---

## 1. Os três protocolos rodando  (o print principal)

```bash
python main.py
```

- Aguarde ~40 s para acumular tráfego (RTT e histograma precisam de algumas amostras).
- No terminal, a cada 10 s sai o bloco **ESTATISTICAS DE TRAFEGO** com os 6 geradores
  `active`, PPS, RTT P50/P95/P99 por master/client e o histograma de ASDU por TypeID.
- `Ctrl+C` (ou espere) para encerrar — sai o **RELATORIO FINAL** com todos os geradores.

Rodar por tempo fixo (útil para capturas reproduzíveis):

```bash
python main.py -d 60
```

Ver cada frame no log (bom para explicar o protocolo):

```bash
python main.py -d 30 -l DEBUG
```

---

## 2. Condições de rede — mostrar impacto na latência e perda

Edite `config/default.yaml`, seção `network_conditions`, e troque o `preset`:

| preset | o que esperar no relatório |
|--------|--------------------------------------|
| `ideal` | RTT baixo, PLR 0% |
| `wan` | RTT sobe para ~50 ms |
| `wan_degraded` | RTT ~100 ms, PLR começa a aparecer |
| `lossy` | RTT ~200 ms, **PLR ~10%** |

```bash
python main.py -d 40
```

- **Print sugerido:** rode com `ideal` e depois com `lossy`, capturando o RTT e o PLR das
  duas — a comparação lado a lado é uma evidência forte de que a simulação de rede funciona.

---

## 3. Perfis de carga — mudar o padrão de tráfego

Em `config/default.yaml`, seção `load_profile`, troque `type`: `constant`, `poisson`,
`burst` ou `sinusoidal`. Rode e observe o gráfico de PPS mudar de forma.

---

## 4. Laboratório de ataque — MITM on-path IEC 104  (o print de segurança)

Você fica **entre** o master e o slave e injeta pacotes para qualquer um dos dois lados.
Forma mais rápida — tudo pelo console do `main.py`, sem config nem segundo terminal:

```bash
python main.py
```
```
gerador> mitm on                # move o slave p/ 2405 e sobe o proxy interno na 2404
gerador> observe on             # mostra o tráfego real do master sendo repassado
gerador> s stopdt               # injeta -> slave (DoS seletivo)
gerador> m spoof 200 140        # injeta tensão falsa -> master
gerador> mitm off               # desfaz e volta ao normal
```

**Print sugerido:** ligue `observe on`, deixe passar alguns frames legítimos, injete `s stopdt`
e `m spoof` — o log mostra os frames `[──► slave]`/`[──► master]` (repassados) misturados com
`[INJETADO ──► slave]`/`[INJETADO ──► master]` (os seus), provando a interceptação.

**Proxy em outro terminal** (ex.: captura de Wireshark isolada) — duas opções equivalentes:

```bash
# opção A: o console prepara as portas
python main.py
gerador> mitm extern
```
```bash
# opção B: totalmente autônomo
python main.py -c config/mitm_iec104.yaml
```

Em ambas, suba o proxy no Terminal B e injete pelo console dele:

```bash
python mitm.py
```

Vocabulário completo de `<cmd>` (`startdt`, `gi`, `command <ioa> <on|off>`, `spoof <ioa> <val>`,
`craft type=… cot=…`, `raw <hex>`, `malformed`) e os achados de segurança estão em `ATAQUES.md`.

---

## 5. Testes automatizados (evidência de rigor)

Os scripts de validação usados no desenvolvimento estão no diretório de trabalho da sessão
(peça para movê-los para `tests/` se quiser versioná-los):

- **Codec DNP3 — 26 verificações** (CRC contra o check value oficial, round-trip das 3 camadas,
  SBO). Resultado esperado: `PASS`.
- **MITM ponta a ponta** — proxy interno e externo, injeção nos dois sentidos, sequência
  observada e reconexão do master através do proxy. Resultado esperado: `PASS`.

---

## 6. Validação com Wireshark (conformidade — a fazer)

```bash
# terminal 1
python main.py
```

Isso já sobe o par IEC 104 legítimo conversando sozinho — suficiente para capturar uma sessão
completa. Se quiser capturar também a interceptação, use o MITM (seção 4) e capture com o
proxy ativo: os frames do master, do slave e as injeções aparecem todos em `tcp.port == 2404`.

No Wireshark, capture na interface de loopback com o filtro:

```
tcp.port == 2404
```

O Wireshark decodifica nativamente o IEC 60870-5-104: você verá o handshake STARTDT, a
Interrogação Geral, os ASDUs por TypeID e as confirmações. Para o DNP3, use `tcp.port == 20000`
(dissector nativo confirma o CRC e as 3 camadas). **Print de Wireshark = evidência de
conformidade mais forte para a banca.**

---

## Resumo — o que capturar para a apresentação

1. **Terminal** com o bloco de estatísticas / relatório final e os 6 geradores ativos (Teste 1).
3. **`ideal` vs `lossy`** — RTT e PLR lado a lado (Teste 2).
4. **MITM** — o log do proxy com frames repassados e `[INJETADO ──► slave/master]` (Teste 4).
5. **Wireshark** — sessão IEC 104 e/ou DNP3 decodificada (Teste 6).

Os itens 1, 2 e 4 já estão como slides de "Demonstração" na apresentação.

---

## 7. Operação multi-máquina (distribuído)

O gerador pode rodar com cada lado em uma máquina diferente. Use o mesmo config
(`config/distribuido.yaml`) nas duas e escolha o lado com `--role`:

```bash
# Máquina A (servidores: slave / outstation / server) — bind em 0.0.0.0
python main.py -c config/distribuido.yaml --role slave
```
```bash
# Máquina B (clientes: master / client) — host aponta para o IP da Máquina A
python main.py -c config/distribuido.yaml --role master
```

Antes: no `config/distribuido.yaml`, troque `192.168.0.10` pelo IP real da Máquina A
e libere as portas no firewall dela (2404/20000/4840). No Windows (PowerShell admin):

```
New-NetFirewallRule -DisplayName "PFC ICS" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 2404,20000,4840
```

**Print sugerido:** o `status` no console da Máquina B mostrando os masters `ativo`, e uma
captura de Wireshark na **interface de rede** (não no loopback) evidenciando o tráfego real
entre as duas máquinas — prova de que a operação distribuída funciona.

> **Validação sem uma segunda máquina.** Dá para exercitar todo o caminho distribuído num host
> só: rode o slave com `bind: 0.0.0.0` e o master com `host: <IP-de-rede-da-maquina>` (não
> `127.0.0.1`), em dois processos (`--role slave` e `--role master`). A conexão passa pela
> pilha de rede real — no log do slave o master aparece com o IP da LAN (ex.: `Master conectado:
> ('172.16.1.198', ...)`) e o RTT sobe para dezenas/centenas de ms (vs. ~0 ms no loopback),
> confirmando que não é loopback. Foi assim que a Fase 5 foi validada.

Para vários nós na mesma máquina (sem distribuir), veja `config/multi_instancia.yaml`
(2 slaves + 2 masters IEC 104, 1 outstation servindo 2 masters DNP3, etc.).
