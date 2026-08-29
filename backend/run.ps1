# FluxSwarm backend supervisor (Windows PowerShell).
#
#   - If the app is already healthy on the port, exits (no duplicate instance).
#   - Otherwise starts uvicorn hidden and RESTARTS it if /health fails.
#   - The lock inside main.py (serverlock) refuses second live instances.
#
# Run:  powershell -ExecutionPolicy Bypass -File backend\run.ps1

param(
    [int]$Port = 8787
)

$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$venvPy  = "C:\Users\DELL\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
$backend = "C:\Users\DELL\fluxswarm\backend"
$logDir  = Join-Path $env:TEMP "opencode"
$outLog  = Join-Path $logDir "fluxswarm.log"
$errLog  = Join-Path $logDir "fluxswarm_err.log"
$health  = "http://127.0.0.1:$Port/health"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# --- optional .env loader (KEY=VALUE, 'quotes' or "quotes" stripped) ---
$envFile = Join-Path $backend ".env"
if (Test-Path $envFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        $t = $line.Trim()
        if ($t -eq "" -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $kv = $t.Split("=", 2)
        $k = $kv[0].Trim(); $v = $kv[1].Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

function Test-Healthy {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri $health -TimeoutSec 3
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Get-PortOwner {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return $conn.OwningProcess } else { return $null }
}

function Stop-Server {
    $procId = Get-PortOwner
    if ($procId) { Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue }
}

# --- already running? --------------------------------
if (Test-Healthy) {
    Write-Output "FluxSwarm already healthy on :$Port - no second instance started."
    exit 0
}
Stop-Server
Start-Sleep -Seconds 1

# --- spawn loop ---------------------------------------
$deadRounds = 0
while ($true) {
    if (-not (Test-Healthy)) {
        if ($deadRounds -ge 3) {
            Stop-Server
            Start-Sleep -Seconds 2
            $deadRounds = 0
        }
        Start-Process -FilePath $venvPy `
            -ArgumentList "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", $Port `
            -WorkingDirectory $backend `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog `
            -WindowStyle Hidden
        $deadRounds = 0
        $startedPid = Get-PortOwner
        Write-Output "Started FluxSwarm on :$Port (pid $startedPid)"
    }
    Start-Sleep -Seconds 15
    if (-not (Test-Healthy)) { $deadRounds += 1 } else { $deadRounds = 0 }
}