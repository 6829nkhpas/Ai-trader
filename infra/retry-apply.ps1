# retry-apply.ps1 - keep trying `tofu apply` until OCI A1 free capacity appears.
#
# Oracle's Always Free Ampere A1 pool in busy home regions (e.g. ap-mumbai-1) is
# frequently "Out of host capacity". The reliable workaround is to retry on a
# cadence - capacity is released in short windows, often at odd hours. This
# script loops until the instance is created, then stops.
#
# Usage (from the infra/ directory):
#   powershell -ExecutionPolicy Bypass -File .\retry-apply.ps1
#   powershell -ExecutionPolicy Bypass -File .\retry-apply.ps1 -DelaySeconds 120 -MaxAttempts 0
#
# -DelaySeconds : wait between attempts (default 90).
# -MaxAttempts  : 0 = infinite (default). Set a number to bound it.

param(
    [int]$DelaySeconds = 90,
    [int]$MaxAttempts  = 0
)

$ErrorActionPreference = 'Continue'
$attempt = 0

while ($true) {
    $attempt++
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Write-Host "[$ts] attempt $attempt - tofu apply..." -ForegroundColor Cyan

    $output = & tofu apply -input=false -auto-approve 2>&1
    $text = $output -join "`n"

    if ($text -match 'Apply complete' -or $text -match 'No changes') {
        Write-Host "[$ts] SUCCESS - instance provisioned." -ForegroundColor Green
        & tofu output
        break
    }
    elseif ($text -match 'Out of host capacity') {
        Write-Host "[$ts] capacity miss." -ForegroundColor Yellow
    }
    else {
        # A different (real) error - surface it and stop so we do not loop on a bug.
        Write-Host "[$ts] non-capacity error - stopping:" -ForegroundColor Red
        Write-Host $text
        break
    }

    if ($MaxAttempts -gt 0 -and $attempt -ge $MaxAttempts) {
        Write-Host "Reached MaxAttempts ($MaxAttempts) without capacity. Try again later." -ForegroundColor Red
        break
    }
    Start-Sleep -Seconds $DelaySeconds
}
