# Ensure cargo and cmake are on PATH for this session
$env:PATH = "$env:USERPROFILE\.cargo\bin;C:\Program Files\CMake\bin;" + $env:PATH

# ── Force vibrant ANSI colors for terminal logging across all microservices ──
$env:RUST_LOG_STYLE = "always"
$env:CARGO_TERM_COLOR = "always"
$env:FORCE_COLOR = "1"
$env:CLICOLOR_FORCE = "1"
$env:COLORTERM = "truecolor"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"

# Anchor to the repository root regardless of where this script is launched from.
# This script lives at <repo>/scripts/powershell/start_system.ps1, so the repo
# root is two directories up. Every relative path below (docker-compose, .env,
# alpha-terminal, agents/*, ingestion, aggregator, frontend) is resolved against
# this root, so running the script from any directory works correctly.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot
Write-Host "Repository root: $RepoRoot" -ForegroundColor DarkCyan

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
    
    & docker info >$null 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Stopping Docker infrastructure..." -ForegroundColor Yellow
        docker-compose down
    } else {
        Write-Host "Docker daemon not reachable, skipping container cleanup." -ForegroundColor Yellow
    }
    Write-Host "System shutdown complete." -ForegroundColor Green
}

try {
    # Ports: 3000=Next.js, 8080-8083=WS agents, 8084=Tauri Tool Server, 8085=Ingestion Control,
    #        8086=Python Agent, 8087=Kite REST API, 9000/9009=QuestDB, 5432=PG (QuestDB/Default), 6379=Redis, 19092=Kafka
    Write-Host "==> Cleaning up stale processes and ports..." -ForegroundColor Magenta

    # Exclude Docker-managed ports (5432, 6379, 9000, 9009, 19092) from direct taskkill,
    # as killing those PIDs terminates the Docker/WSL daemon on the host.
    $portsToKill = @(3000, 8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087)
    $netstatOut = netstat -ano
    foreach ($port in $portsToKill) {
        $matched = $netstatOut | Select-String (":$port\s")
        foreach ($line in $matched) {
            $parts = ("$line".Trim() -split "\s+")
            $procId = $parts[-1]
            if ($procId -match "^\d+$" -and [int]$procId -gt 4) {
                # Safety check: do not kill Docker backend, WSL, Hyper-V or System processes
                $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
                if ($proc) {
                    $procName = $proc.ProcessName
                    if ($procName -match "docker|vpnkit|wsl|hyperv|vmmem|system") {
                        Write-Host "  [skipped] System/Docker process ($procName, PID $procId) on port $port" -ForegroundColor Yellow
                        continue
                    }
                }
                taskkill /PID $procId /T /F 2>$null | Out-Null
                Write-Host "  [killed] PID $procId on port $port" -ForegroundColor DarkGray
            }
        }
    }

    Write-Host "  Pre-flight cleanup done." -ForegroundColor Green
    Start-Sleep -Seconds 2

    # ── Verify Docker is running and clean up old containers gracefully ──────
    Write-Host "Verifying Docker daemon..." -ForegroundColor Cyan
    & docker info >$null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Docker is not running or unreachable." -ForegroundColor Red
        Write-Host "Please start Docker Desktop and ensure the daemon is running before launching the system." -ForegroundColor Yellow
        return
    }

    Write-Host "Stopping any stale Docker containers from previous runs..." -ForegroundColor Cyan
    docker-compose down 2>$null | Out-Null

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
    Write-Host "Starting infrastructure (Kafka/Redpanda, QuestDB, Redis)..." -ForegroundColor Cyan
    docker-compose up -d redpanda questdb redis

    # Wait for each infra service to be reachable before proceeding
    Wait-ForPort -Port 6379  -TimeoutSec 60 -Label "Redis (:6379)"
    Wait-ForPort -Port 9000  -TimeoutSec 90 -Label "QuestDB (:9000)"
    Wait-ForPort -Port 19092 -TimeoutSec 90 -Label "Redpanda/Kafka (:19092)"

    # ── Install Node.js Dependencies ─────────────────────────────────────────
    Write-Host "Checking and installing Node.js dependencies..." -ForegroundColor Cyan

    # 1. agents/sentiment
    if (Test-Path agents/sentiment) {
        Write-Host "Checking agents/sentiment dependencies..." -ForegroundColor DarkCyan
        Push-Location agents/sentiment
        if (!(Test-Path "node_modules")) {
            Write-Host "Installing agents/sentiment dependencies..." -ForegroundColor Yellow
            npm install
        } else {
            Write-Host "agents/sentiment dependencies already installed." -ForegroundColor Green
        }
        Pop-Location
    }

    # 2. frontend
    if (Test-Path frontend) {
        Write-Host "Checking frontend dependencies..." -ForegroundColor DarkCyan
        Push-Location frontend
        if (!(Test-Path "node_modules")) {
            Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
            npm install
        } else {
            Write-Host "frontend dependencies already installed." -ForegroundColor Green
        }
        Pop-Location
    }

    # ── Pre-create Kafka topics via rpk ─────────────────────────────────────
    Write-Host "Pre-creating Kafka topics via rpk..." -ForegroundColor Cyan
    $topics = @("market.ticks", "market.ohlc.10m", "technical_signals", "sentiment_signals", "trade_decisions", "signals.predictive", "signals.insights")
    foreach ($topic in $topics) {
        docker exec stratai-redpanda rpk topic create $topic --partitions 3 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [+] Topic created: $topic" -ForegroundColor Green
        } else {
            Write-Host "  [=] Topic already exists: $topic" -ForegroundColor DarkGray
        }
    }
    docker exec stratai-redpanda rpk topic list
    Write-Host "All infrastructure is ready!" -ForegroundColor Green

    # ── Start PRODUCERS first, then CONSUMERS ───────────────────────────────
    # Order: ingestion -> technical -> sentiment -> aggregator -> frontend
    # NOTE: The standalone auth/profile service has been removed from the app.
    # The dashboard at "/" is now directly accessible — no JWT keys, no /api/auth.

    Write-Host "Starting Rust Ingestion Service (Kite -> Kafka)..." -ForegroundColor Cyan
    Push-Location ingestion
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --color=always --release"
    Pop-Location

    Start-Sleep -Seconds 5

    Write-Host "Starting Rust Technical Agent (Kafka ticks -> signals)..." -ForegroundColor Cyan
    Push-Location agents/technical
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --color=always --release"
    Pop-Location

    Write-Host "Starting Node Sentiment Agent (News -> Kafka signals)..." -ForegroundColor Cyan
    Push-Location agents/sentiment
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm start"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Starting Rust Aggregator (signals -> WS 8080 + OHLC -> WS 8081)..." -ForegroundColor Cyan
    Push-Location aggregator
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --color=always --release"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Starting Predictive Agent (OHLC -> LinReg -> WS 8082)..." -ForegroundColor Cyan
    Push-Location agents/predictive
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --color=always --release"
    Pop-Location

    Write-Host "Starting Quant-RAG Agent (anomalies -> DeepSeek -> WS 8083)..." -ForegroundColor Cyan
    Push-Location agents/quant-rag
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --color=always --release"
    Pop-Location

    Start-Sleep -Seconds 3

    Write-Host "Installing Python dependencies for Deep Quant Agent..." -ForegroundColor Cyan
    Push-Location agents/deep-quant-loop
    if (Test-Path requirements.txt) {
        python -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARNING: pip install reported errors - continuing anyway." -ForegroundColor Yellow
        } else {
            Write-Host "  Python dependencies installed." -ForegroundColor Green
        }
    } else {
        Write-Host "  WARNING: requirements.txt not found in agents/deep-quant-loop - skipping install." -ForegroundColor Yellow
    }
    Pop-Location

    Write-Host "Starting Python LangGraph Deep Quant Agent (Port 8086)..." -ForegroundColor Cyan
    Push-Location agents/deep-quant-loop
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "python" -ArgumentList "main.py"
    Pop-Location

    Wait-ForPort -Port 8086 -TimeoutSec 60 -Label "Python Deep Quant Loop (:8086)"

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