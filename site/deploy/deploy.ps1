# Upload a reviewed release with built-in Windows OpenSSH and tar.
# No production deploy without the owner's explicit approval for that deploy.
# Example: .\deploy.ps1 -Server user@host -WithApp -Wheel .\dist\agentmatch-0.1.0-py3-none-any.whl -Environment staging -Domain staging.example.test -SkipTls -NoFirewall -DryRun
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [string]$Email = "",
  [string]$Domain = "connectthebots.net",
  [ValidateSet("staging", "production")][string]$Environment = "production",
  [string]$Wheel = "",
  [switch]$WithApp,
  [switch]$SkipTls,
  [switch]$NoFirewall,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if ($Server -notmatch '^([a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\z') { throw "Supply an SSH hostname, optionally user@hostname." }
if ($Domain -notmatch '^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\z' -or $Domain.Contains('..')) { throw "Invalid domain." }
if ($Email -and $Email -notmatch '^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+\z') { throw "Invalid certificate email." }
if (-not $SkipTls -and -not $Email -and -not $DryRun) { throw "-Email is required for HTTPS." }
if ($WithApp -and $SkipTls -and $Environment -ne "staging") { throw "HTTP app deployment is staging-only." }
$siteDir = Split-Path -Parent $PSScriptRoot
$projectDir = Split-Path -Parent $siteDir
$lockPath = Join-Path $projectDir "requirements-server.lock"
foreach ($file in @("index.html", "signin.html", "dashboard.html", "assets/site.css", "deploy/server-setup.sh")) {
  if (-not (Test-Path -LiteralPath (Join-Path $siteDir $file) -PathType Leaf)) { throw "Missing site file: $file" }
}
if ($WithApp) {
  if (-not $Wheel -or -not (Test-Path -LiteralPath $Wheel -PathType Leaf)) { throw "-WithApp requires -Wheel pointing to a built agentmatch wheel." }
  $wheelPath = (Resolve-Path -LiteralPath $Wheel).Path
  $wheelName = Split-Path -Leaf $wheelPath
  if ($wheelName -notmatch '^agentmatch-[A-Za-z0-9_.+]+-py3-none-any\.whl\z') { throw "Invalid application wheel filename." }
  if (-not (Test-Path -LiteralPath $lockPath -PathType Leaf)) { throw "Missing requirements-server.lock." }
}
$setupArgs = @("--domain", $Domain, "--environment", $Environment)
if ($Email) { $setupArgs += "--email", $Email }
if ($SkipTls) { $setupArgs += "--skip-tls" }
if ($NoFirewall) { $setupArgs += "--no-firewall" }
if ($WithApp) { $setupArgs += "--with-app" }
if ($DryRun) {
  Write-Output "Dry run. Site: $siteDir"
  Write-Output "Target: $Server"
  if ($WithApp) { Write-Output "Wheel: $wheelPath"; Write-Output "Lock: $lockPath" }
  Write-Output "Would upload a fresh release bundle and run server-setup.sh $($setupArgs -join ' ')"
  Write-Output "No files uploaded and no server changes made."
  exit 0
}
foreach ($tool in @("ssh", "scp", "tar")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is required (Windows OpenSSH Client and tar)." }
}
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$packagePath = Join-Path $tempBase ("ctb-deploy-" + [guid]::NewGuid().ToString("N"))
[void](New-Item -ItemType Directory -Path (Join-Path $packagePath "site"))
try {
  $siteArchive = Join-Path $packagePath "site.tar"
  & tar -C $siteDir --exclude=tools --exclude=.git '--exclude=.env*' --exclude=app.env '--exclude=*.key' '--exclude=*.pem' '--exclude=*.db' -cf $siteArchive .
  if ($LASTEXITCODE -ne 0) { throw "Site packaging failed." }
  & tar -C (Join-Path $packagePath "site") -xf $siteArchive
  if ($LASTEXITCODE -ne 0) { throw "Site package verification failed." }
  $members = @("site")
  if ($WithApp) {
    $appPath = Join-Path $packagePath "app"
    [void](New-Item -ItemType Directory -Path $appPath)
    Copy-Item -LiteralPath $wheelPath -Destination (Join-Path $appPath $wheelName)
    Copy-Item -LiteralPath $lockPath -Destination (Join-Path $appPath "requirements-server.lock")
    $lines = @()
    foreach ($name in @($wheelName, "requirements-server.lock")) {
      $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $appPath $name)).Hash.ToLowerInvariant()
      $lines += "$hash  $name"
    }
    [IO.File]::WriteAllText((Join-Path $appPath "SHA256SUMS"), ($lines -join "`n") + "`n", [Text.UTF8Encoding]::new($false))
    $members += "app"
  }
  $bundle = Join-Path $packagePath "release.tar.gz"
  & tar -C $packagePath -czf $bundle @members
  if ($LASTEXITCODE -ne 0) { throw "Release packaging failed." }
  $stage = (& ssh $Server "mktemp -d /tmp/connectthebots-deploy.XXXXXXXX" | Out-String).Trim()
  if ($LASTEXITCODE -ne 0 -or $stage -notmatch '^/tmp/connectthebots-deploy\.[a-zA-Z0-9]+\z') { throw "Could not create a unique server staging directory." }
  & scp $bundle "${Server}:$stage/release.tar.gz"
  if ($LASTEXITCODE -ne 0) { throw "Upload failed." }
  # Remote values have restricted character sets. No eval or recursive deletion.
  $remote = "tar -xzf $stage/release.tar.gz -C $stage && sudo bash $stage/site/deploy/server-setup.sh $($setupArgs -join ' ')"
  & ssh -t $Server $remote
  if ($LASTEXITCODE -ne 0) { throw "Setup stopped. Review its message before retrying." }
}
finally {
  # Resolve and confine deletion to this invocation's fresh temporary folder.
  $resolved = [IO.Path]::GetFullPath($packagePath)
  $parentPath = [IO.Path]::GetDirectoryName($resolved).TrimEnd([IO.Path]::DirectorySeparatorChar)
  if ($parentPath -ne $tempBase.TrimEnd([IO.Path]::DirectorySeparatorChar) -or [IO.Path]::GetFileName($resolved) -notmatch '^ctb-deploy-[a-f0-9]{32}\z') {
    throw "Refusing cleanup outside the deployment temporary directory."
  }
  Remove-Item -LiteralPath $resolved -Recurse -Force
}
