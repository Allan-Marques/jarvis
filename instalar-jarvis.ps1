<#
  Instalador do Jarvis para Windows

  Verifica e instala: ferramentas base (Git, Node.js, Python 3.12, uv, eSpeak NG, Claude Code),
  voz de mago (Kokoro), Graphify, comandos /jarvis e /jarvis-sys, regras do CLAUDE.md,
  Anthropic Skills, Claude-Mem e servidores MCP (memory, filesystem, git, fetch,
  sequential-thinking, github e obsidian). No final mostra a tabela de status.

  Como usar (PowerShell, na pasta onde você salvou este arquivo):
    powershell -ExecutionPolicy Bypass -File .\instalar-jarvis.ps1
    powershell -ExecutionPolicy Bypass -File .\instalar-jarvis.ps1 -Cofre "C:\caminho\do\seu\cofre"
    powershell -ExecutionPolicy Bypass -File .\instalar-jarvis.ps1 -SoJarvis
  -SoJarvis instala só o que o Jarvis precisa (Python, Claude Code, voz e servidor), sem Graphify,
  comandos, regras do CLAUDE.md, plugins e servidores MCP.

  Não apaga nada e não sobrescreve arquivos que já existem.
#>
param(
  [string]$Cofre = '',
  [switch]$SemKokoro,
  [switch]$SoJarvis
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Status = New-Object System.Collections.Generic.List[object]
$JarvisDir = Join-Path $HOME 'Jarvis'
$ClaudeDir = Join-Path $HOME '.claude'
$CmdDir = Join-Path $ClaudeDir 'commands'
New-Item -ItemType Directory -Force -Path $JarvisDir, $ClaudeDir, $CmdDir | Out-Null
$Log = Join-Path $JarvisDir 'instalacao.log'
"Instalação do Jarvis - $(Get-Date)" | Out-File -FilePath $Log -Encoding utf8

# ───────────────────────── funções de apoio ─────────────────────────
function Write-Etapa($t) { Write-Host ''; Write-Host "== $t" -ForegroundColor Cyan }

function Add-Status($modulo, $estado, $detalhe) {
  $Status.Add([pscustomobject]@{ 'Módulo' = $modulo; 'Status' = $estado; 'Detalhe' = $detalhe })
  $cores = @{ 'Ativo' = 'Green'; 'Instalado agora' = 'Cyan'; 'Pendente' = 'Yellow'; 'Erro' = 'Red' }
  Write-Host ('   {0}: {1}  {2}' -f $modulo, $estado, $detalhe) -ForegroundColor $cores[$estado]
  "$modulo | $estado | $detalhe" | Out-File -FilePath $Log -Append -Encoding utf8
}

function Update-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
  $extras = @("$HOME\.local\bin", "$env:LOCALAPPDATA\Programs\Python\Launcher", "$env:ProgramFiles\nodejs", "$env:ProgramFiles\Git\cmd", "$env:ProgramFiles\eSpeak NG")
  foreach ($p in $extras) {
    if ((Test-Path $p) -and ($env:Path -notlike "*$p*")) { $env:Path += ";$p" }
  }
}

function Test-Cmd($nome) { return [bool](Get-Command $nome -ErrorAction SilentlyContinue) }

function Invoke-Exe($exe, [string[]]$argumentos) {
  $saida = & $exe @argumentos 2>&1 | Out-String
  $codigo = $LASTEXITCODE
  "> $exe $($argumentos -join ' ')`r`n$saida" | Out-File -FilePath $Log -Append -Encoding utf8
  return [pscustomobject]@{ Codigo = $codigo; Saida = $saida }
}

function Install-Winget($id, $nome) {
  if (-not (Test-Cmd 'winget')) { return $false }
  Write-Host "   instalando $nome (aceite o pedido de administrador se aparecer)..." -ForegroundColor DarkGray
  $r = Invoke-Exe 'winget' @('install', '--id', $id, '-e', '--silent', '--accept-package-agreements', '--accept-source-agreements')
  Update-Path
  return ($r.Codigo -eq 0)
}

function Write-Utf8($caminho, $texto) {
  [System.IO.File]::WriteAllText($caminho, $texto, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Curto($s) {
  $t = (($s | Out-String) -replace '\s+', ' ').Trim()
  if ($t.Length -gt 110) { $t = $t.Substring(0, 110) + '...' }
  return $t
}

function Get-Py312 {
  if (Test-Cmd 'py') {
    $p = & py -3.12 -c "import sys;print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $p) { return ($p | Select-Object -First 1).ToString().Trim() }
  }
  foreach ($c in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", "$env:ProgramFiles\Python312\python.exe")) {
    if (Test-Path $c) { return $c }
  }
  return $null
}

function Get-Cofre {
  if ($Cofre) { return $Cofre }
  $cfg = Join-Path $env:APPDATA 'obsidian\obsidian.json'
  if (-not (Test-Path $cfg)) { return '' }
  try {
    $j = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
    $lista = @($j.vaults.PSObject.Properties | ForEach-Object { $_.Value })
    $aberto = $lista | Where-Object { $_.open } | Select-Object -First 1
    if (-not $aberto) { $aberto = $lista | Select-Object -First 1 }
    if ($aberto -and (Test-Path $aberto.path)) { return $aberto.path }
  } catch {}
  return ''
}

# ───────────────────────── conteúdo dos comandos e regras ─────────────────────────
$TextoJarvis = @'
---
description: Ativa o modo Jarvis, seu assistente pessoal (conciso e com mudanças cirúrgicas)
---
Você agora atua como Jarvis, o assistente pessoal do usuário.

- Fale em português do Brasil. Chame o usuário de "senhor", ou pelo nome que ele preferir.
- Modo conciso: respostas curtas e diretas, sem rodeios e sem resumos longos no final.
- Mudanças cirúrgicas: altere só o necessário para o pedido. Não refatore, renomeie nem reformate o que não foi pedido. Mostre o que mudou.
- Second Brain: antes de responder sobre a vida, os projetos ou o trabalho do usuário, consulte o cofre do Obsidian (MCP obsidian) e a memória (MCP memory). Ao aprender algo importante e duradouro, registre na memória.
- Para entender um projeto de código grande, use o /graphify.

Pedido: $ARGUMENTS
'@

$TextoJarvisSys = @'
---
description: Diagnóstico do sistema Jarvis (MCPs, skills, plugins e voz Kokoro)
---
Faça um diagnóstico do ambiente do Jarvis neste computador. Não altere nada; só verifique e relate.

1. Rode `claude mcp list` e confira: memory, filesystem, git, fetch, sequential-thinking, github e obsidian.
2. Rode `claude plugin list` e confira: document-skills, example-skills e claude-api (anthropic-agent-skills) e claude-mem.
3. Confira se existem ~/.claude/commands/jarvis.md e jarvis-sys.md, e se ~/.claude/CLAUDE.md tem as regras do Jarvis.
4. Rode `py -3.12 -m pip show graphifyy kokoro-onnx`.
5. Teste o servidor do Jarvis: acesse http://127.0.0.1:8880/saude (jarvis_servidor.py; se não responder, diga para dar dois cliques em ~/Jarvis/iniciar-jarvis.bat).

Responda com uma tabela (Módulo | Status | Detalhe), usando Ativo, Pendente ou Erro. Para cada erro, dê o comando exato que corrige.
'@

$TextoRegras = @'
<!-- jarvis:regras -->
## Jarvis: regras de trabalho

- Mudanças cirúrgicas: altere apenas o necessário para o pedido. Não refatore, renomeie nem reformate código fora do escopo. Antes de uma mudança grande ou irreversível, explique e peça confirmação.
- Modo conciso: respostas curtas e diretas, em português do Brasil. Sem repetir o pedido e sem resumos longos no final.
- Preserve o que funciona: depois de cada mudança, rode os testes ou verificações que já existem.
'@

# ───────────────────────── 1. ferramentas base ─────────────────────────
Write-Etapa 'Ferramentas base'
Update-Path
if (-not (Test-Cmd 'winget')) { Add-Status 'winget' 'Erro' 'instale o "Instalador de Aplicativo" pela Microsoft Store e rode de novo' }

if (Test-Cmd 'git') { Add-Status 'Git' 'Ativo' (Get-Curto (git --version)) }
elseif (Install-Winget 'Git.Git' 'Git') { Add-Status 'Git' 'Instalado agora' '' }
else { Add-Status 'Git' 'Erro' 'instale em git-scm.com' }

$NodeOk = $false
if (-not $SoJarvis) {
if (Test-Cmd 'node') {
  $v = ((node -v) | Out-String).Trim() -replace '^v', ''
  $NodeOk = ([int]($v.Split('.')[0])) -ge 20
}
if ($NodeOk) { Add-Status 'Node.js' 'Ativo' "v$v" }
elseif (Install-Winget 'OpenJS.NodeJS.LTS' 'Node.js LTS') {
  $NodeOk = Test-Cmd 'node'
  if ($NodeOk) { Add-Status 'Node.js' 'Instalado agora' (Get-Curto (node -v)) }
  else { Add-Status 'Node.js' 'Erro' 'instalado, mas fora do PATH: feche e abra o PowerShell e rode de novo' }
}
else { Add-Status 'Node.js' 'Erro' 'precisa da versão 20 ou mais nova: instale a LTS em nodejs.org' }

$Py = Get-Py312
if ($Py) { Add-Status 'Python 3.12' 'Ativo' $Py }
elseif (Install-Winget 'Python.Python.3.12' 'Python 3.12') {
  $Py = Get-Py312
  if ($Py) { Add-Status 'Python 3.12' 'Instalado agora' $Py }
  else { Add-Status 'Python 3.12' 'Erro' 'instalado, mas não encontrado: feche e abra o PowerShell e rode de novo' }
}
else { Add-Status 'Python 3.12' 'Erro' 'instale o Python 3.12 em python.org (o Kokoro não roda no 3.13)' }

if (Test-Cmd 'uvx') { Add-Status 'uv (uvx)' 'Ativo' (Get-Curto (uv --version)) }
elseif (Install-Winget 'astral-sh.uv' 'uv') {
  if (Test-Cmd 'uvx') { Add-Status 'uv (uvx)' 'Instalado agora' '' }
  else { Add-Status 'uv (uvx)' 'Erro' 'instalado, mas fora do PATH: feche e abra o PowerShell e rode de novo' }
}
else { Add-Status 'uv (uvx)' 'Erro' 'instale em docs.astral.sh/uv' }

$EspeakExe = Join-Path $env:ProgramFiles 'eSpeak NG\espeak-ng.exe'
if ((Test-Cmd 'espeak-ng') -or (Test-Path $EspeakExe)) { Add-Status 'eSpeak NG' 'Ativo' '' }
elseif (Install-Winget 'eSpeak-NG.eSpeak-NG' 'eSpeak NG') { Add-Status 'eSpeak NG' 'Instalado agora' '' }
else { Add-Status 'eSpeak NG' 'Erro' 'baixe o instalador .msi em github.com/espeak-ng/espeak-ng/releases' }
}   # fim das ferramentas dos extras (Node.js, uv e eSpeak NG)

if (Test-Cmd 'claude') { Add-Status 'Claude Code' 'Ativo' (Get-Curto (claude --version)) }
else {
  $ok = Install-Winget 'Anthropic.ClaudeCode' 'Claude Code'
  if (-not (Test-Cmd 'claude')) {
    Write-Host '   tentando o instalador oficial do Claude Code...' -ForegroundColor DarkGray
    try { Invoke-RestMethod 'https://claude.ai/install.ps1' | Invoke-Expression } catch {}
    Update-Path
  }
  if (Test-Cmd 'claude') { Add-Status 'Claude Code' 'Instalado agora' 'abra com "claude" e faça login uma vez' }
  else { Add-Status 'Claude Code' 'Erro' 'instale com: irm https://claude.ai/install.ps1 | iex' }
}
$HasClaude = Test-Cmd 'claude'
if ($HasClaude) {
  if (Test-Path (Join-Path $ClaudeDir '.credentials.json')) { Add-Status 'Claude Code (login)' 'Ativo' 'login encontrado: o cérebro do Jarvis usa sua assinatura' }
  else { Add-Status 'Claude Code (login)' 'Pendente' 'não encontrei o login: se ainda não entrou, digite claude no PowerShell e faça o login uma vez' }
}

# ───────────────────────── 2. Jarvis 2: servidor local e voz de mago (Kokoro) ─────────────────────────
Write-Etapa 'Jarvis 2: servidor local (cérebro, voz e memória)'
$vault = Get-Cofre
# fecha o Jarvis aberto: no Windows, o pip não consegue trocar arquivos que um programa aberto está usando
$donos = @(Get-NetTCPConnection -LocalPort 8880 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
if ($donos.Count) {
  Write-Host '   fechando o Jarvis aberto para atualizar...' -ForegroundColor DarkGray
  try { Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8880/desligar' -Body '{}' -ContentType 'text/plain' -TimeoutSec 3 | Out-Null } catch {}
  foreach ($id in $donos) {
    try { Wait-Process -Id $id -Timeout 15 -ErrorAction Stop } catch { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
  }
  # e a janela preta antiga, que ficaria parada em "pressione qualquer tecla"
  Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*iniciar-jarvis.bat*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
# usa a cópia mais nova de cada arquivo, mesmo que o navegador tenha salvo como "jarvis (1).html"
foreach ($par in @(@('jarvis_servidor*.py', 'jarvis_servidor.py'), @('jarvis_kokoro*.py', 'jarvis_kokoro.py'), @('jarvis*.html', 'jarvis.html'))) {
  if ($PSScriptRoot -eq $JarvisDir) { break }
  $maisNovo = Get-ChildItem -Path $PSScriptRoot -Filter $par[0] -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime | Select-Object -Last 1
  if ($maisNovo) { Copy-Item $maisNovo.FullName (Join-Path $JarvisDir $par[1]) -Force }
}
if ($SemKokoro) { Add-Status 'Kokoro' 'Pendente' 'pulado (-SemKokoro)' }
elseif (-not $Py) { Add-Status 'Kokoro' 'Erro' 'precisa do Python 3.12' }
else {
  $novo = $false
  $r = Invoke-Exe $Py @('-c', 'import kokoro_onnx')
  if ($r.Codigo -ne 0) {
    Write-Host '   instalando a voz de mago rápida (Kokoro ONNX, roda no processador, sem PyTorch)...' -ForegroundColor DarkGray
    Invoke-Exe $Py @('-m', 'pip', 'install', '--upgrade', 'pip') | Out-Null
    $r = Invoke-Exe $Py @('-m', 'pip', 'install', '--upgrade', 'kokoro-onnx')
    $novo = $true
  }
  if ($r.Codigo -ne 0) { Add-Status 'Kokoro' 'Erro' ('pip falhou: ' + (Get-Curto $r.Saida)) }
  else {
    Write-Host '   baixando o modelo da voz (uns 190 MB na primeira vez) e testando...' -ForegroundColor DarkGray
    $teste = Join-Path $env:TEMP 'jarvis_kokoro_teste.py'
    Write-Utf8 $teste @'
import urllib.request
from pathlib import Path
pasta = Path.home() / 'Jarvis' / 'kokoro-onnx'
pasta.mkdir(parents=True, exist_ok=True)
base = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/'
for nome in ('kokoro-v1.0.fp16.onnx', 'voices-v1.0.bin'):
    alvo = pasta / nome
    if not alvo.exists():
        parcial = alvo.with_name(nome + '.parcial')
        urllib.request.urlretrieve(base + nome, parcial)
        parcial.replace(alvo)
from kokoro_onnx import Kokoro
k = Kokoro(str(pasta / 'kokoro-v1.0.fp16.onnx'), str(pasta / 'voices-v1.0.bin'))
estilo = (k.get_voice_style('pm_alex') * 2 + k.get_voice_style('bm_fable')) / 3
a, taxa = k.create('Sistemas online.', voice=estilo, speed=1.0, lang='pt-br')
print('VOZ_OK', len(a))
'@
    $t = Invoke-Exe $Py @($teste)
    if ($t.Saida -match 'DLL load failed|onnxruntime_pybind11_state') {
      # o ONNX Runtime precisa das bibliotecas do Visual C++; alguns Windows vêm sem elas
      Write-Host '   instalando as bibliotecas do Visual C++ que a voz rápida usa...' -ForegroundColor DarkGray
      Install-Winget 'Microsoft.VCRedist.2015+.x64' 'Visual C++ Redistributable' | Out-Null
      $t = Invoke-Exe $Py @($teste)
    }
    if (($t.Saida -match 'VOZ_OK\s+(\d+)') -and ([int]$Matches[1] -gt 0)) {
      $estado = 'Ativo'; if ($novo) { $estado = 'Instalado agora' }
      Add-Status 'Kokoro' $estado 'voz de mago rápida (ONNX, no processador) gerada com sucesso'
    }
    else { Add-Status 'Kokoro' 'Erro' ('o teste da voz falhou: ' + (Get-Curto $t.Saida)) }
  }
}
$servidor = Join-Path $JarvisDir 'jarvis_servidor.py'
if (-not $Py) { Add-Status 'Servidor do Jarvis' 'Erro' 'precisa do Python 3.12' }
elseif (Test-Path $servidor) {
  $extra = ''
  if ($vault) { $extra = " --cofre `"$($vault.TrimEnd('\'))`"" }
  $bat = "@echo off`r`nchcp 65001 >nul`r`ntitle Jarvis (deixe esta janela aberta)`r`ncd /d `"%~dp0`"`r`nset `"PATH=%USERPROFILE%\.local\bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%APPDATA%\npm;%PATH%`"`r`n`"$Py`" jarvis_servidor.py --abrir$extra`r`npause`r`n"
  [System.IO.File]::WriteAllText((Join-Path $JarvisDir 'iniciar-jarvis.bat'), $bat, (New-Object System.Text.UTF8Encoding($false)))
  $onde = 'notas em ' + $JarvisDir + '\memoria'
  if ($vault) { $onde = 'notas no cofre do Obsidian, pasta Jarvis' }
  Add-Status 'Servidor do Jarvis' 'Ativo' "$JarvisDir\iniciar-jarvis.bat ($onde)"
  try {
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Jarvis.lnk'))
    $lnk.TargetPath = Join-Path $JarvisDir 'iniciar-jarvis.bat'
    $lnk.WorkingDirectory = $JarvisDir
    $lnk.WindowStyle = 7
    $lnk.Description = 'Abrir o Jarvis'
    $lnk.Save()
    Add-Status 'Ícone na área de trabalho' 'Ativo' 'dois cliques em Jarvis para abrir'
  } catch { Add-Status 'Ícone na área de trabalho' 'Erro' (Get-Curto $_) }
}
else { Add-Status 'Servidor do Jarvis' 'Pendente' "salve o jarvis_servidor.py junto deste instalador e rode de novo" }

if ($SoJarvis) { Add-Status 'Extras' 'Pendente' 'pulados (-SoJarvis): Graphify, comandos, regras, plugins e servidores MCP' }
else {
# ───────────────────────── 3. Graphify ─────────────────────────
Write-Etapa 'Graphify'
if (-not $Py) { Add-Status 'Graphify' 'Erro' 'precisa do Python 3.12' }
else {
  $scripts = ((& $Py -c "import sysconfig;print(sysconfig.get_path('scripts'))") | Out-String).Trim()
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (-not $userPath) { $userPath = '' }
  if ($scripts -and ($userPath -notlike "*$scripts*")) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $scripts), 'User')
    Update-Path
  }
  $novo = $false
  $r = Invoke-Exe $Py @('-m', 'pip', 'show', 'graphifyy')
  if ($r.Codigo -ne 0) {
    $r = Invoke-Exe $Py @('-m', 'pip', 'install', 'graphifyy[all]')
    if ($r.Codigo -ne 0) { $r = Invoke-Exe $Py @('-m', 'pip', 'install', 'graphifyy') }
    $novo = $true
  }
  if ($r.Codigo -ne 0) { Add-Status 'Graphify' 'Erro' ('pip falhou: ' + (Get-Curto $r.Saida)) }
  else {
    $exe = Join-Path $scripts 'graphify.exe'
    if (-not (Test-Path $exe)) { $exe = 'graphify' }
    $g = Invoke-Exe $exe @('install')
    if ($g.Codigo -eq 0) {
      $estado = 'Ativo'; if ($novo) { $estado = 'Instalado agora' }
      Add-Status 'Graphify' $estado 'skill /graphify registrada'
    }
    else { Add-Status 'Graphify' 'Erro' ('graphify install falhou: ' + (Get-Curto $g.Saida)) }
    Add-Status 'Graphify (hook)' 'Pendente' 'é por projeto: dentro de cada repositório git, rode "graphify hook install"'
  }
}

# ───────────────────────── 4. comandos /jarvis e regras do CLAUDE.md ─────────────────────────
Write-Etapa 'Comandos do Jarvis e CLAUDE.md'
$arqJarvis = Join-Path $CmdDir 'jarvis.md'
if (Test-Path $arqJarvis) { Add-Status '/jarvis' 'Ativo' $arqJarvis }
else { Write-Utf8 $arqJarvis $TextoJarvis; Add-Status '/jarvis' 'Instalado agora' 'criado com o modo Jarvis' }

$arqSys = Join-Path $CmdDir 'jarvis-sys.md'
if (Test-Path $arqSys) { Add-Status '/jarvis-sys' 'Ativo' $arqSys }
else { Write-Utf8 $arqSys $TextoJarvisSys; Add-Status '/jarvis-sys' 'Instalado agora' 'criado com o diagnóstico do sistema' }

$arqClaude = Join-Path $ClaudeDir 'CLAUDE.md'
$conteudo = ''
if (Test-Path $arqClaude) { $conteudo = Get-Content $arqClaude -Raw -Encoding UTF8 }
if (-not $conteudo) { $conteudo = '' }
if (($conteudo -match 'jarvis:regras') -or (($conteudo -match 'cir[uú]rgic') -and ($conteudo -match 'concis'))) {
  Add-Status 'CLAUDE.md (regras)' 'Ativo' 'regras de mudanças cirúrgicas e modo conciso encontradas'
}
else {
  $final = $TextoRegras
  if ($conteudo.Trim()) { $final = $conteudo.TrimEnd() + "`r`n`r`n" + $TextoRegras }
  Write-Utf8 $arqClaude $final
  Add-Status 'CLAUDE.md (regras)' 'Instalado agora' 'regras adicionadas ao final, sem apagar nada'
}

# ───────────────────────── 5. skills e plugins ─────────────────────────
Write-Etapa 'Anthropic Skills e Claude-Mem'
if (-not $HasClaude) {
  Add-Status 'Anthropic Skills' 'Erro' 'precisa do Claude Code'
  Add-Status 'Claude-Mem' 'Erro' 'precisa do Claude Code'
}
else {
  $plugins = (Invoke-Exe 'claude' @('plugin', 'list')).Saida
  $mercados = (Invoke-Exe 'claude' @('plugin', 'marketplace', 'list')).Saida
  if ($mercados -notmatch 'anthropic-agent-skills') { Invoke-Exe 'claude' @('plugin', 'marketplace', 'add', 'anthropics/skills') | Out-Null }
  foreach ($pl in @('document-skills', 'example-skills', 'claude-api')) {
    $id = "$pl@anthropic-agent-skills"
    if ($plugins -match [regex]::Escape($id)) { Add-Status "Skills: $pl" 'Ativo' $id; continue }
    $r = Invoke-Exe 'claude' @('plugin', 'install', $id)
    if ($r.Codigo -eq 0) { Add-Status "Skills: $pl" 'Instalado agora' $id }
    else { Add-Status "Skills: $pl" 'Erro' (Get-Curto $r.Saida) }
  }
  # Claude-Mem: só o repositório oficial thedotmack/claude-mem
  if ($plugins -match 'claude-mem@') { Add-Status 'Claude-Mem' 'Ativo' 'plugin instalado' }
  elseif (-not $NodeOk) { Add-Status 'Claude-Mem' 'Erro' 'precisa do Node.js 20 ou mais novo' }
  else {
    if ($mercados -notmatch 'thedotmack') { Invoke-Exe 'claude' @('plugin', 'marketplace', 'add', 'thedotmack/claude-mem') | Out-Null }
    $r = Invoke-Exe 'claude' @('plugin', 'install', 'claude-mem@thedotmack')
    if ($r.Codigo -ne 0) { $r = Invoke-Exe 'cmd' @('/c', 'npx', '-y', 'claude-mem', 'install') }
    if ($r.Codigo -eq 0) { Add-Status 'Claude-Mem' 'Instalado agora' 'reinicie o Claude Code; painel em http://localhost:37777' }
    else { Add-Status 'Claude-Mem' 'Erro' (Get-Curto $r.Saida) }
  }
}

# ───────────────────────── 6. servidores MCP ─────────────────────────
Write-Etapa 'Servidores MCP'
if (-not $HasClaude) { Add-Status 'Servidores MCP' 'Erro' 'precisa do Claude Code' }
else {
  $Mcps = (Invoke-Exe 'claude' @('mcp', 'list')).Saida
  function Test-Mcp($nome) { return ($Mcps -match ('(?m)^\s*' + [regex]::Escape($nome) + ':')) }
  function Add-Mcp($nome, [string[]]$resto, $dica) {
    if (Test-Mcp $nome) { Add-Status "MCP $nome" 'Ativo' ''; return }
    $r = Invoke-Exe 'claude' (@('mcp', 'add', '--scope', 'user') + $resto)
    if ($r.Codigo -eq 0) { Add-Status "MCP $nome" 'Instalado agora' $dica }
    else { Add-Status "MCP $nome" 'Erro' (Get-Curto $r.Saida) }
  }

  $docs = (Join-Path $HOME 'Documents') -replace '\\', '/'
  if ($NodeOk) {
    Add-Mcp 'memory' @('memory', '--', 'cmd', '/c', 'npx', '-y', '@modelcontextprotocol/server-memory') ''
    Add-Mcp 'sequential-thinking' @('sequential-thinking', '--', 'cmd', '/c', 'npx', '-y', '@modelcontextprotocol/server-sequential-thinking') ''
    Add-Mcp 'filesystem' @('filesystem', '--', 'cmd', '/c', 'npx', '-y', '@modelcontextprotocol/server-filesystem', $docs) "acesso à pasta $docs"
  }
  else { foreach ($n in @('memory', 'sequential-thinking', 'filesystem')) { Add-Status "MCP $n" 'Erro' 'precisa do Node.js 20 ou mais novo' } }

  if (Test-Cmd 'uvx') {
    Add-Mcp 'git' @('git', '--', 'uvx', 'mcp-server-git') ''
    Add-Mcp 'fetch' @('fetch', '--', 'uvx', 'mcp-server-fetch') ''
  }
  else { foreach ($n in @('git', 'fetch')) { Add-Status "MCP $n" 'Erro' 'precisa do uv' } }

  # GitHub: servidor oficial do próprio GitHub (o antigo foi arquivado). O login é feito depois, no /mcp.
  Add-Mcp 'github' @('--transport', 'http', 'github', 'https://api.githubcopilot.com/mcp/') 'falta entrar: abra o Claude Code, digite /mcp e escolha github'

  # Obsidian: usa o cofre informado em -Cofre, ou o cofre aberto no app do Obsidian
  if (Test-Mcp 'obsidian') { Add-Status 'MCP obsidian' 'Ativo' '' }
  elseif ((-not $vault) -or (-not (Test-Path $vault))) { Add-Status 'MCP obsidian' 'Pendente' 'cofre não encontrado: rode de novo com -Cofre "C:\caminho\do\cofre"' }
  elseif (-not $NodeOk) { Add-Status 'MCP obsidian' 'Erro' 'precisa do Node.js 20 ou mais novo' }
  else { Add-Mcp 'obsidian' @('obsidian', '--', 'cmd', '/c', 'npx', '-y', 'obsidian-mcp', ($vault -replace '\\', '/')) "cofre $vault (faça um backup: ele pode editar notas)" }

  # confere se cada servidor conecta de verdade
  Write-Host '   conferindo as conexões dos servidores (pode demorar na primeira vez)...' -ForegroundColor DarkGray
  $saude = (Invoke-Exe 'claude' @('mcp', 'list')).Saida
  foreach ($s in $Status) {
    if ($s.'Módulo' -notlike 'MCP *' -or $s.Status -eq 'Pendente' -or $s.Status -eq 'Erro') { continue }
    $nome = $s.'Módulo'.Substring(4)
    $linha = ($saude -split "`n") | Where-Object { $_ -match ('^\s*' + [regex]::Escape($nome) + ':') } | Select-Object -First 1
    if (-not $linha) { continue }
    if ($linha -match 'Failed') { $s.Status = 'Erro'; $s.Detalhe = 'não conectou: rode "claude mcp get ' + $nome + '" para ver o motivo' }
    elseif ($linha -match 'auth') { $s.Status = 'Pendente'; $s.Detalhe = 'falta entrar: abra o Claude Code, digite /mcp e escolha ' + $nome }
  }
}

}   # fim dos extras

# ───────────────────────── resultado ─────────────────────────
Write-Etapa 'Resultado'
$Status | Format-Table -AutoSize -Wrap | Out-String -Width 220 | Write-Host
$Status | Format-Table -AutoSize -Wrap | Out-String -Width 220 | Out-File -FilePath $Log -Append -Encoding utf8
Write-Host 'Próximos passos:' -ForegroundColor Cyan
Write-Host "  1. Feche e abra o Claude Code para carregar skills, plugins e servidores."
Write-Host "  2. Para usar o Jarvis: dois cliques no ícone Jarvis da área de trabalho. Não abra o jarvis.html direto."
Write-Host "  3. Se algo deu Erro, me mande um print desta tabela. O registro completo está em $Log"

# abre o login do Claude Code, se faltar, e já liga o Jarvis
$batJarvis = Join-Path $JarvisDir 'iniciar-jarvis.bat'
if ($HasClaude -and -not (Test-Path (Join-Path $ClaudeDir '.credentials.json'))) {
  Write-Host ''
  Write-Host 'Abrindo o Claude Code numa janela nova para você entrar na sua conta (só uma vez). Depois do login, digite /exit.' -ForegroundColor Yellow
  Start-Process powershell -ArgumentList '-NoExit', '-Command', 'claude'
}
if (Test-Path $batJarvis) {
  Write-Host ''
  Write-Host 'Abrindo o Jarvis no navegador...' -ForegroundColor Cyan
  Start-Process -FilePath $batJarvis -WorkingDirectory $JarvisDir -WindowStyle Minimized
}
