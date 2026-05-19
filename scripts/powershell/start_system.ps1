# Ensure cargo and cmake are on PATH for this session
$env:PATH = "$env:USERPROFILE\.cargo\bin;C:\Program Files\CMake\bin;" + $env:PATH

# Store process objects to kill them later
$script:processes = @()

# Wait until a TCP port is open (polls every second up to $TimeoutSec)
# Uses async BeginConnect to avoid nested try/catch which breaks PowerShell's outer try/finally
function Wait-ForPort {
    param([int]$Port, [int]$TimeoutSec = 60, [string]$Label = "")
    $name = if ($Label) { $Label } else { "port $Port" }
    Write-Host "  Waiting for $name to be ready..." -ForegroundColor DarkCyan
    $deadline = [DateTime]::Now.AddSeconds($TimeoutSec)
    while ([DateTime]::Now -lt $deadline) {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $async = $tcp.BeginConnect('127.0.0.1', $Port, $null, $null)
        $waited = $async.AsyncWaitHandle.WaitOne(1000, $false)
        if ($waited -and $tcp.Connected) {
            $tcp.Close()
            Write-Host "  [$name] is ready!" -ForegroundColor Green
            return
        }
        $tcp.Close()
        Start-Sleep -Milliseconds 500
    }
    Write-Host "  WARNING: $name did not become ready within ${TimeoutSec}s - continuing anyway." -ForegroundColor Yellow
}

# Clean up function when script exits or is interrupted
function Cleanup {
    Write-Host "`nShutting down system..." -ForegroundColor Yellow
    foreach ($p in $script:processes) {
        if ($null -ne $p -and -not $p.HasExited) {
            taskkill /T /F /PID $p.Id 2>$null
        }
    }
    Write-Host "Stopping Docker infrastructure..." -ForegroundColor Yellow
    docker-compose down
    Write-Host "System shutdown complete." -ForegroundColor Green
}

try {
    # ── Pre-flight: Graceful shutdown of any previous run ────────────────────
    Write-Host "==> Cleaning up stale processes and ports..." -ForegroundColor Magenta

    # 1. Bring down Docker stack cleanly first (avoids network/container state corruption)
    Write-Host "  Stopping Docker stack (if running)..." -ForegroundColor DarkGray
    docker-compose down 2>$null | Out-Null

    # 2. Kill Node and Rust app processes (not Docker — handled above)
    $processesToKill = @("node", "cargo")
    foreach ($procName in $processesToKill) {
        $found = Get-Process -Name $procName -ErrorAction SilentlyContinue
        if ($found) {
            foreach ($proc in $found) {
                taskkill /PID $proc.Id /T /F 2>$null | Out-Null
                Write-Host "  [killed] $procName PID $($proc.Id)" -ForegroundColor DarkGray
            }
        }
    }

    # 3. Kill anything still occupying app ports (3000, 3001, 8080-8083)
    $portsToKill = @(3000, 3001, 8080, 8081, 8082, 8083)
    $netstatOut = netstat -ano 2>$null
    foreach ($port in $portsToKill) {
        $matched = $netstatOut | Select-String (":$port\s")
        foreach ($line in $matched) {
            $parts = ("$line".Trim() -split "\s+")
            $procId = $parts[-1]
            if ($procId -match "^\d+$" -and [int]$procId -gt 4) {
                taskkill /PID $procId /T /F 2>$null | Out-Null
                Write-Host "  [killed] PID $procId on port $port" -ForegroundColor DarkGray
            }
        }
    }

    Write-Host "  Pre-flight cleanup done." -ForegroundColor Green
    Start-Sleep -Seconds 2

    # ── Load environment variables ───────────────────────────────────────────
    Write-Host "Loading environment variables from .env..." -ForegroundColor Cyan
    if (Test-Path .env) {
        $envLines = Get-Content .env | Where-Object { $_ -match "=" -and $_ -notmatch "^#" }
        foreach ($line in $envLines) {
            $parts = $line -split "=", 2
            $varName = $parts[0].Trim()
            $varValue = $parts[1].Trim().Trim([char]34).Trim([char]39)
            Set-Item -Path "Env:\$varName" -Value $varValue
        }
    }

    # ── Start infrastructure ─────────────────────────────────────────────────
    Write-Host "Starting infrastructure (Kafka/Redpanda, QuestDB, Redis, Postgres)..." -ForegroundColor Cyan
    docker-compose up -d redpanda questdb postgres redis

    # Wait for each infra service to be reachable before proceeding
    Wait-ForPort -Port 6379  -TimeoutSec 60 -Label "Redis (:6379)"
    Wait-ForPort -Port 5890  -TimeoutSec 90 -Label "Postgres (:5890)"
    Wait-ForPort -Port 9000  -TimeoutSec 90 -Label "QuestDB (:9000)"
    Wait-ForPort -Port 19092 -TimeoutSec 90 -Label "Redpanda/Kafka (:19092)"

    # ── Pre-create Kafka topics via rpk ─────────────────────────────────────
    Write-Host "Pre-creating Kafka topics via rpk..." -ForegroundColor Cyan
    $topics = @("market.ticks", "market.ohlc.10m", "technical_signals", "sentiment_signals", "trade_decisions", "signals.predictive", "signals.insights")
    foreach ($topic in $topics) {
        docker exec alphasuite-redpanda rpk topic create $topic --partitions 3 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [+] Topic created: $topic" -ForegroundColor Green
        } else {
            Write-Host "  [=] Topic already exists: $topic" -ForegroundColor DarkGray
        }
    }
    docker exec alphasuite-redpanda rpk topic list
    Write-Host "All infrastructure is ready!" -ForegroundColor Green

    # ── Start PRODUCERS first, then CONSUMERS ───────────────────────────────
    # Order: ingestion -> technical -> sentiment -> aggregator -> frontend

    # ── Generate JWT RSA keys if missing ────────────────────────────────────
    if (-not (Test-Path "auth\keys\private.pem")) {
        Write-Host "Generating RSA-2048 JWT key pair for auth service..." -ForegroundColor Cyan
        New-Item -ItemType Directory -Path "auth\keys" -Force | Out-Null
        node -e "const crypto = require('crypto'); const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048, publicKeyEncoding: { type: 'spki', format: 'pem' }, privateKeyEncoding: { type: 'pkcs8', format: 'pem' } }); require('fs').writeFileSync('auth/keys/private.pem', privateKey); require('fs').writeFileSync('auth/keys/public.pem', publicKey); console.log('[KEYGEN] RSA key pair generated at auth/keys/');"
        Write-Host "  [+] JWT key pair ready." -ForegroundColor Green
    } else {
        Write-Host "  [=] JWT key pair already exists, skipping keygen." -ForegroundColor DarkGray
    }

    Write-Host "Starting Auth Service..." -ForegroundColor Cyan
    Push-Location auth
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm run dev"
    Pop-Location
    Wait-ForPort -Port 3001 -TimeoutSec 60 -Label "Auth Service (:3001)"

    Write-Host "Starting Rust Ingestion Service (Kite -> Kafka)..." -ForegroundColor Cyan
    Push-Location ingestion
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Start-Sleep -Seconds 5

    Write-Host "Starting Rust Technical Agent (Kafka ticks -> signals)..." -ForegroundColor Cyan
    Push-Location agents/technical
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Write-Host "Starting Node Sentiment Agent (News -> Kafka signals)..." -ForegroundColor Cyan
    Push-Location agents/sentiment
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm start"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Starting Rust Aggregator (signals -> WS 8080 + OHLC -> WS 8081)..." -ForegroundColor Cyan
    Push-Location aggregator
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Starting Predictive Agent (OHLC -> LinReg -> WS 8082)..." -ForegroundColor Cyan
    Push-Location agents/predictive
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Write-Host "Starting Quant-RAG Agent (anomalies -> DeepSeek -> WS 8083)..." -ForegroundColor Cyan
    Push-Location agents/quant-rag
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Starting Next.js Frontend (Tauri)..." -ForegroundColor Cyan
    Push-Location frontend
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm run tauri:dev"
    Pop-Location

    Write-Host "`nAll services are running! Power Phase 3.1 FULLY ENGAGED." -ForegroundColor Green
    Write-Host "Press Ctrl+C to stop all services and infrastructure." -ForegroundColor Yellow

    while ($true) {
        Start-Sleep -Seconds 1
    }
}
finally {
    Cleanup
}