# Store process objects to kill them later
$script:processes = @()

# Clean up function when script exits or is interrupted
function Cleanup {
    Write-Host "`nShutting down system..." -ForegroundColor Yellow
    foreach ($p in $script:processes) {
        if ($null -ne $p -and -not $p.HasExited) {
            # taskkill with /T kills child processes as well, which is important for npm and cargo
            taskkill /T /F /PID $p.Id 2>$null
        }
    }
    Write-Host "Stopping Docker infrastructure..." -ForegroundColor Yellow
    docker-compose down
    Write-Host "System shutdown complete." -ForegroundColor Green
}

try {
    Write-Host "Loading environment variables from .env..." -ForegroundColor Cyan
    if (Test-Path .env) {
        Get-Content .env | Where-Object { $_ -match '=' -and $_ -notmatch '^#' } | ForEach-Object {
            $name, $value = $_ -split '=', 2
            # Strip surrounding double quotes from the value (e.g. "738561:RELIANCE,..." → 738561:RELIANCE,...)
            $cleanValue = $value.Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name.Trim(), $cleanValue)
        }
    }

    Write-Host "Starting infrastructure (Kafka/Redpanda, QuestDB, Redis, Postgres)..." -ForegroundColor Cyan
    docker-compose up -d

    Write-Host "Infrastructure started. Waiting 15 seconds for initialization..." -ForegroundColor Cyan
    Start-Sleep -Seconds 15

    # ── Pre-create Kafka topics via Redpanda's rpk CLI ──────────────────────
    # This eliminates the UnknownTopicOrPartition race condition:
    # consumers can subscribe immediately without waiting for producers to
    # publish their first message and trigger auto-create.
    Write-Host "Pre-creating Kafka topics via rpk..." -ForegroundColor Cyan

    $topics = @("market.ticks", "market.ohlc.10m", "technical_signals", "sentiment_signals", "trade_decisions", "signals.predictive", "signals.insights")
    foreach ($topic in $topics) {
        docker exec ai-trader-redpanda-1 rpk topic create $topic --partitions 3 2>$null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "  [+] Topic '$topic' created" -ForegroundColor Green
        } else {
            Write-Host "  [=] Topic '$topic' already exists (ok)" -ForegroundColor DarkGray
        }
    }

    # Verify topics were created
    Write-Host "Verifying Kafka topics..." -ForegroundColor Cyan
    docker exec ai-trader-redpanda-1 rpk topic list
    Write-Host "Infrastructure is ready!" -ForegroundColor Green

    # ── Start PRODUCERS first, then CONSUMERS ─────────────────────────────────
    # Order matters: ingestion → technical → sentiment → aggregator → frontend
    # This ensures data flows downstream before consumers try to read.

    Write-Host "Starting Auth Service..." -ForegroundColor Cyan
    Push-Location auth
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm run dev"
    Pop-Location

    Write-Host "Starting Rust Ingestion Service (Kite → Kafka)..." -ForegroundColor Cyan
    Push-Location ingestion
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    # Give ingestion a moment to connect to Kite and start publishing ticks
    Start-Sleep -Seconds 5

    Write-Host "Starting Rust Technical Agent (Kafka ticks → signals)..." -ForegroundColor Cyan
    Push-Location agents/technical
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Write-Host "Starting Node Sentiment Agent (News → Kafka signals)..." -ForegroundColor Cyan
    Push-Location agents/sentiment
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cmd.exe" -ArgumentList "/c npm start"
    Pop-Location

    # Give producers a moment to publish their first messages
    Start-Sleep -Seconds 3

    Write-Host "Starting Rust Aggregator (signals → decisions → WS 8080 + OHLC → WS 8081)..." -ForegroundColor Cyan
    Push-Location aggregator
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    # Give aggregator time to start WS server before frontend connects
    Start-Sleep -Seconds 3

    Write-Host "Starting Predictive Agent (OHLC → LinReg → WS 8082)..." -ForegroundColor Cyan
    Push-Location agents/predictive
    $script:processes += Start-Process -NoNewWindow -PassThru -FilePath "cargo" -ArgumentList "run --release"
    Pop-Location

    Write-Host "Starting Quant-RAG Agent (anomalies → Gemini → WS 8083)..." -ForegroundColor Cyan
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

    # Wait indefinitely until user presses Ctrl+C
    while ($true) {
        Start-Sleep -Seconds 1
    }
}
finally {
    Cleanup
}
