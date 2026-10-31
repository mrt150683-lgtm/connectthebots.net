# One-command production update for connectthebots.net, run by the owner from
# a dedicated clone via DEPLOY.cmd. It builds the app from this checkout, takes
# a verified backup, uploads with deploy.ps1 and checks health. Running it is
# the owner's approval for that deploy; nothing here runs automatically.
param(
  [string]$Server = "root@connectthebots.net",
  [string]$Email = "info@connectthebots.net",
  [string]$Domain = "connectthebots.net"
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

Step "Release"
$commit = (& git rev-parse --short HEAD).Trim()
Write-Host "Deploying main at $commit"

Step "Build the app package"
$pyExe = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = "python" }
if (-not $pyExe) { throw "Python 3.11 or later is needed to build the app. Install it from python.org, then run DEPLOY again." }
$buildEnv = Join-Path ([IO.Path]::GetTempPath()) ("ctb-build-" + [guid]::NewGuid().ToString("N"))
try {
& $pyExe @pyArgs -m venv $buildEnv
if ($LASTEXITCODE -ne 0) { throw "Could not create a build environment." }
$buildPython = Join-Path $buildEnv "Scripts\python.exe"
& $buildPython -m pip install --quiet --disable-pip-version-check setuptools==84.1.0 wheel==0.48.0
if ($LASTEXITCODE -ne 0) { throw "Could not install the pinned build tools." }
$dist = Join-Path $buildEnv "dist"
& $buildPython -m pip wheel --quiet --no-index --no-deps --no-build-isolation . --wheel-dir $dist
if ($LASTEXITCODE -ne 0) { throw "The app package did not build." }
$wheel = Get-ChildItem -LiteralPath $dist -Filter "agentmatch-*-py3-none-any.whl" | Select-Object -First 1
if (-not $wheel) { throw "The app package is missing after the build." }
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $wheel.FullName).Hash.ToLowerInvariant()
Write-Host "Built $($wheel.Name) sha256 $hash"

  Step "Fresh verified backup"
  $backup = (& ssh $Server 'systemd-run --quiet --wait --pipe --collect -p EnvironmentFile=/etc/connectthebots/app.env -p User=ctb -p Group=ctb /opt/connectthebots/venv/bin/python -m connectthebots_server.admin backup' | Out-String)
  Write-Host $backup.Trim()
  if ($LASTEXITCODE -ne 0 -or $backup -notmatch '"offsite_verified": true') {
    throw "The backup was not verified, so nothing was changed. Paste this window's text in the thread."
  }

  Step "Upload and update the server"
  & (Join-Path $PSScriptRoot "deploy.ps1") -Server $Server -Environment production -Domain $Domain -WithApp -Wheel $wheel.FullName -Email $Email
  if (-not $?) { throw "The update stopped. Do not run DEPLOY again; paste this window's text in the thread." }

  Step "Health check"
  $healthy = $false
  foreach ($attempt in 1..10) {
    try {
      $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 10 "https://$Domain/healthz"
      if ($response.StatusCode -eq 200 -and $response.Content -match '"ok"') { $healthy = $true; break }
    } catch { }
    Start-Sleep -Seconds 3
  }
  if (-not $healthy) { throw "The site did not report healthy. Paste this window's text in the thread." }
  Write-Host ""
  Write-Host "Done: https://$Domain is live at $commit." -ForegroundColor Green
}
finally {
  $resolved = [IO.Path]::GetFullPath($buildEnv)
  if ([IO.Path]::GetFileName($resolved) -match '^ctb-build-[a-f0-9]{32}\z') { Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue }
}
