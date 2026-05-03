# Handle Ctrl+C (graceful shutdown)
$jobsList = @()

$null = Register-EngineEvent PowerShell.Exiting -Action {
    Write-Host "`nShutting down backend services..."
    foreach ($job in $jobsList) {
        try {
            Stop-Process -Id $job.Id -Force -ErrorAction SilentlyContinue
        } catch {}
    }
}

# Load environment variables from .env
if (Test-Path ".env") {
    Write-Host "Loading environment variables from .env..."
    Get-Content .env | ForEach-Object {
        if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
            [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
}

Write-Host "Starting Rust Ingestion Service..."
$jobsList += Start-Process powershell -ArgumentList "cd ingestion; cargo run --release" -PassThru

Write-Host "Starting Rust Technical Agent..."
$jobsList += Start-Process powershell -ArgumentList "cd agents/technical; cargo run --release" -PassThru

Write-Host "Starting Node Sentiment Agent..."
$jobsList += Start-Process powershell -ArgumentList "cd agents/sentiment; npm start" -PassThru

Write-Host "Starting Rust Aggregator..."
$jobsList += Start-Process powershell -ArgumentList "cd aggregator; cargo run --release" -PassThru

Write-Host "Backend services are running. Press Ctrl+C to stop."

# Keep script alive
while ($true) {
    Start-Sleep -Seconds 5
}