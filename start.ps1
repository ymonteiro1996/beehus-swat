# SWAT launcher: starts Flask, waits for it to come up, then opens the
# browser with the session token so the cookie gets set transparently.
# Run this instead of `python app.py` for normal daily use.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $scriptDir

$port = 5000
$tokenPath = Join-Path $env:USERPROFILE ".swat\session.token"

# Kill any stale Flask already bound to $port. Without this, a previous
# `python app.py` (e.g. left over from a prior run) keeps answering and
# the new process we spawn below fails silently to bind — the operator
# would think they restarted but be talking to the old code.
$existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($existing) {
    Write-Host "[SWAT] Encerrando instância anterior (PID $($existing.OwningProcess))..."
    Stop-Process -Id $existing.OwningProcess -Force -ErrorAction SilentlyContinue
    # Wait up to 5 s for the port to actually free. Stop-Process returns
    # immediately on Windows; the OS may still be releasing the socket.
    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
}

# Start Flask in the background. The app creates the token file on boot if
# missing, so we wait for /healthz before reading it.
# stdout/stderr are captured to .swat-server.{out,err} so an import-time
# crash (which would otherwise just silently exit the hidden window) is
# visible to the operator. Without this redirect the script would only
# report "Servidor não respondeu em 30s" with no clue why.
$outLog = Join-Path $scriptDir ".swat-server.out"
$errLog = Join-Path $scriptDir ".swat-server.err"
Remove-Item -Path $outLog, $errLog -ErrorAction SilentlyContinue
# Libera o /api/repetir-posicoes/analyze (e a captura de cenário da
# Conciliação) a spawnar `claude -p ...`. O guard `SWAT_ALLOW_CLAUDE_CLI`
# existe pra deploys remotos onde execução arbitrária via HTTP seria
# perigosa — não é o caso aqui (Flask escuta só em 127.0.0.1, single-user).
# Setado no escopo do processo PowerShell ($env:...) — o `Start-Process`
# abaixo herda automaticamente todas as env vars do parent.
$env:SWAT_ALLOW_CLAUDE_CLI = "1"

# Chave Mongo POR MÁQUINA (não versionada, não sincronizada): se existir o
# arquivo %USERPROFILE%\.swat\no-mongo nesta máquina, o dashboard sobe SEM Mongo
# (SWAT_IDENTIFICAR=0 → sem conexão, sem carregar pymongo; só a feature
# "Identificar / Posições da carteira" do Painel some). A instância de
# identificação NÃO cria esse arquivo, então continua com Mongo ligado.
# Mora em ~/.swat (junto do token) de propósito: NÃO sincroniza via OneDrive
# para a outra instância e o `git pull` do iniciar.bat nunca o sobrescreve.
$noMongoMarker = Join-Path $env:USERPROFILE ".swat\no-mongo"
if (Test-Path $noMongoMarker) {
    $env:SWAT_IDENTIFICAR = "0"
    Write-Host "[SWAT] .swat\no-mongo presente -> SWAT_IDENTIFICAR=0 (Mongo desligado nesta instancia)"
}

# Garante que todas as dependências do requirements.txt estão instaladas.
# Roda silenciosamente; só exibe saída se houver pacote faltando/atualizado.
Write-Host "[SWAT] Verificando dependências..."
$pipResult = & python -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "[SWAT] Erro ao instalar dependências. Verifique o requirements.txt." -ForegroundColor Red
    exit 1
}
if ($pipResult) {
    Write-Host "[SWAT] Dependências instaladas/atualizadas." -ForegroundColor Green
} else {
    Write-Host "[SWAT] Dependências OK." -ForegroundColor Green
}

Write-Host "[SWAT] Iniciando servidor em http://127.0.0.1:$port ..."
$proc = Start-Process -FilePath "python" -ArgumentList "app.py" -PassThru `
    -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog

try {
    # Wait up to ~30 seconds for the healthcheck — bail early if Python crashed.
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        if ($proc.HasExited) { break }
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port/healthz" -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        if ($proc.HasExited) {
            Write-Host "[SWAT] Python encerrou (exit $($proc.ExitCode)) durante o boot." -ForegroundColor Red
        } else {
            Write-Host "[SWAT] Servidor não respondeu em 30s." -ForegroundColor Red
        }
        if (Test-Path $errLog) {
            $errTail = Get-Content -Path $errLog -Tail 40 -ErrorAction SilentlyContinue
            if ($errTail) {
                Write-Host "--- últimas linhas de $errLog ---" -ForegroundColor Yellow
                $errTail | ForEach-Object { Write-Host $_ }
            }
        }
        exit 1
    }

    if (-not (Test-Path $tokenPath)) {
        Write-Host "[SWAT] Token não encontrado em $tokenPath" -ForegroundColor Red
        exit 1
    }
    $token = (Get-Content -Path $tokenPath -Raw).Trim()
    $url = "http://127.0.0.1:$port/bootstrap?token=$token"
    Write-Host "[SWAT] Abrindo navegador..."
    Start-Process $url

    Write-Host "[SWAT] Servidor rodando. Feche esta janela para encerrar."
    Wait-Process -Id $proc.Id
} finally {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}
