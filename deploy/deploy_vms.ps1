# ===========================================================================
# Deploy da simulacao nas VMs (RTUs) via SSH/SCP.  Rode no PowerShell do Windows.
#
#   .\deploy\deploy_vms.ps1 deploy    # copia o projeto + instala deps (venv) + cria swap em cada RTU
#   .\deploy\deploy_vms.ps1 start     # sobe a simulacao de cada RTU (em background)
#   .\deploy\deploy_vms.ps1 stop      # para a simulacao em cada RTU
#   .\deploy\deploy_vms.ps1 status    # mostra se esta rodando + ultimas linhas do log
#   .\deploy\deploy_vms.ps1 swap      # so cria/garante o swapfile em cada RTU (idempotente)
#   .\deploy\deploy_vms.ps1 all       # deploy (+swap) + start
#
# Pre-requisito: chave SSH SEM passphrase instalada nas VMs (ssh pfc@IP responde
# sem pedir nada). O coordenador, o centro-de-controle e o SCADA-LTS rodam no
# HOST Windows, NAO por este script.
# ===========================================================================
param(
  [ValidateSet('deploy','start','stop','status','swap','all')]
  [string]$action = 'all'
)

$ErrorActionPreference = 'Continue'
$src  = "C:\LUCIANO\IME\5.1\PFC\PFC_Pinheiro_Rabelo"
$user = "pfc"
$pass = "pfc"     # senha de LABORATORIO (so p/ o sudo do apt); troque se mudar a senha
$swapMB = 512     # swap por RTU (RAM baixa: evita OOM no import do asyncua / apt)

# entidade -> IP / config / precisa de OPC-UA (asyncua)?
$vms = @(
  @{ ip='192.168.21.11'; name='hidro';      cfg='vm_hidro.yaml';      opcua=$true  },
  @{ ip='192.168.21.12'; name='termica';    cfg='vm_termica.yaml';    opcua=$false },
  @{ ip='192.168.21.13'; name='industrial'; cfg='vm_industrial.yaml'; opcua=$false },
  @{ ip='192.168.21.14'; name='centro';     cfg='vm_centro.yaml';     opcua=$false },
  @{ ip='192.168.21.15'; name='hospital';   cfg='vm_hospital.yaml';   opcua=$false }
)

$SSHOPT = @('-o','StrictHostKeyChecking=accept-new')

function Invoke-RemoteSsh($ip, $cmd) { ssh $SSHOPT "$user@$ip" $cmd }

function Do-Deploy($vm) {
  Write-Host "[$($vm.name) @ $($vm.ip)] copiando projeto..." -ForegroundColor Cyan
  scp -r $SSHOPT $src "$($user)@$($vm.ip):~/"
  Write-Host "[$($vm.name)] instalando dependencias (venv)..." -ForegroundColor Cyan
  # python3-venv via apt (precisa sudo); o resto no venv (isola do PEP 668)
  Invoke-RemoteSsh $vm.ip "echo $pass | sudo -S apt-get update -y >/dev/null 2>&1; echo $pass | sudo -S apt-get install -y python3-venv >/dev/null 2>&1; cd ~/PFC_Pinheiro_Rabelo && python3 -m venv venv && ./venv/bin/pip -q install pyyaml"
  if ($vm.opcua) {
    Write-Host "[$($vm.name)] instalando asyncua (OPC-UA)..." -ForegroundColor Cyan
    Invoke-RemoteSsh $vm.ip "cd ~/PFC_Pinheiro_Rabelo && ./venv/bin/pip -q install asyncua"
  }
  Do-Swap $vm
  Write-Host "[$($vm.name)] deploy OK." -ForegroundColor Green
}

function Do-Swap($vm) {
  Write-Host "[$($vm.name)] garantindo swap de $($swapMB)M (idempotente)..." -ForegroundColor Cyan
  # tudo num sudo -S so (1 senha): cria /swapfile, ativa e persiste no fstab -- so
  # se ainda nao existir. Sem aspas duplas nem '$' no script bash (evita conflito
  # com a interpolacao do PowerShell). RAM baixa nas RTUs -> swap evita OOM.
  $cmd = "echo $pass | sudo -S bash -c 'swapon --show | grep -q /swapfile " +
         "&& echo swap ja existe " +
         "|| ( fallocate -l $($swapMB)M /swapfile && chmod 600 /swapfile " +
         "&& mkswap /swapfile >/dev/null && swapon /swapfile " +
         "&& ( grep -q /swapfile /etc/fstab || echo /swapfile none swap sw 0 0 >> /etc/fstab ) " +
         "&& echo swap criado $($swapMB)M )'"
  Invoke-RemoteSsh $vm.ip $cmd
}

function Do-Start($vm) {
  Write-Host "[$($vm.name)] iniciando simulacao ($($vm.cfg))..." -ForegroundColor Green
  # usa arquivo de PID (~/sim.pid) em vez de casar string -- assim o pkill NAO
  # mata o proprio wrapper bash (que tambem contem 'main.py -c').
  $remote = "cd ~/PFC_Pinheiro_Rabelo; " +
            "[ -f ~/sim.pid ] && kill `$(cat ~/sim.pid) 2>/dev/null; " +
            "nohup ./venv/bin/python main.py -c config/$($vm.cfg) > ~/sim.log 2>&1 < /dev/null & echo `$! > ~/sim.pid; " +
            "sleep 2; kill -0 `$(cat ~/sim.pid) 2>/dev/null && echo '  -> rodando (PID '`$(cat ~/sim.pid)')' || (echo '  -> FALHOU. log:'; tail -n 8 ~/sim.log)"
  Invoke-RemoteSsh $vm.ip $remote
}

function Do-Stop($vm) {
  Write-Host "[$($vm.name)] parando..." -ForegroundColor Yellow
  Invoke-RemoteSsh $vm.ip "[ -f ~/sim.pid ] && kill `$(cat ~/sim.pid) 2>/dev/null && echo '  -> parado' || echo '  -> ja estava parado'; rm -f ~/sim.pid"
}

function Do-Status($vm) {
  Write-Host "=== [$($vm.name) @ $($vm.ip)] ===" -ForegroundColor Cyan
  Invoke-RemoteSsh $vm.ip "if [ -f ~/sim.pid ] && kill -0 `$(cat ~/sim.pid) 2>/dev/null; then echo '  rodando (PID '`$(cat ~/sim.pid)')'; else echo '  (nao esta rodando)'; fi; echo '  --- ultimas linhas do log ---'; tail -n 5 ~/sim.log 2>/dev/null"
}

foreach ($vm in $vms) {
  switch ($action) {
    'deploy' { Do-Deploy $vm }
    'start'  { Do-Start  $vm }
    'stop'   { Do-Stop   $vm }
    'status' { Do-Status $vm }
    'swap'   { Do-Swap   $vm }
    'all'    { Do-Deploy $vm; Do-Start $vm }
  }
}
Write-Host "`nConcluido: acao '$action' nas $($vms.Count) VMs." -ForegroundColor Green
