[CmdletBinding()]
param(
  [string]$ApiBase = "https://srv1170872.tail86523.ts.net:8444",
  [string]$VpsHost = "srv1170872.tail86523.ts.net",
  [string]$VpsUser = "wavystack",
  [int]$VpsPort = 22,
  [string]$RemoteApp = "/opt/apps/scout",
  [string]$SshKey = "$HOME\.ssh\id_ed25519",
  [ValidateRange(1,5)][int]$DeployAttempts = 3,
  [switch]$SkipApiCheck,
  [switch]$SkipBuild,
  [switch]$SkipDesktopInstall,
  [switch]$SkipVpsDeploy,
  [switch]$UseDockerCache
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$PSNativeCommandUseErrorActionPreference = $true
if ($PSVersionTable.PSVersion.Major -lt 7) { throw "Use PowerShell 7 or newer (pwsh)." }

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Version = (Get-Content (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
$BuildScript = Join-Path $ProjectRoot "build-windows.ps1"
$RemoteDeployScript = Join-Path $ProjectRoot "scripts\deploy-vps-release.sh"
$ReleaseDirectory = Join-Path $ProjectRoot "release\windows"
$Results = [ordered]@{ Build="SKIPPED"; Desktop="SKIPPED"; VPS="SKIPPED"; PWA="SKIPPED" }
$Failures = [System.Collections.Generic.List[string]]::new()
$Installer = $null
$StagingBase = $null

function Invoke-WithRetry {
  param([string]$Label,[scriptblock]$Action)
  for ($Attempt=1; $Attempt -le $DeployAttempts; $Attempt++) {
    try { & $Action; return }
    catch {
      if ($Attempt -eq $DeployAttempts) { throw }
      Write-Warning "$Label failed (attempt $Attempt/$DeployAttempts). Retrying in $($Attempt * 2)s..."
      Start-Sleep -Seconds ($Attempt * 2)
    }
  }
}

Write-Host "StockHunter Scout coordinated release $Version" -ForegroundColor Cyan
try {
  if (-not $SkipBuild) {
    $BuildArguments = @{ ApiBase = $ApiBase }
    if ($SkipApiCheck) { $BuildArguments.SkipApiCheck = $true }
    & $BuildScript @BuildArguments
    $Results.Build = "READY"
  }
  $Installer = Get-ChildItem $ReleaseDirectory -Filter "*${Version}*setup.exe" -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $Installer -and -not $SkipDesktopInstall) { throw "No $Version Windows installer was found under $ReleaseDirectory." }
} catch {
  $Results.Build = "FAILED"
  $Failures.Add("Build: $($_.Exception.Message)")
}

if (-not $SkipVpsDeploy) {
  try {
    foreach ($Command in @("ssh","scp","robocopy")) { if (-not (Get-Command $Command -ErrorAction SilentlyContinue)) { throw "$Command is required." } }
    $StagingBase = Join-Path ([System.IO.Path]::GetTempPath()) ("scout-release-" + [guid]::NewGuid().ToString("N"))
    $StagedProject = Join-Path $StagingBase "stockhunter-scout-$Version"
    $ServerArchive = Join-Path $StagingBase "stockhunter-scout-$Version-server.zip"
    New-Item -ItemType Directory -Force -Path $StagedProject | Out-Null
    $RoboArgs = @($ProjectRoot,$StagedProject,"/E","/NFL","/NDL","/NJH","/NJS","/NP","/XD",".git",".next","node_modules","target","release","data","charts","__pycache__","/XF",".env","*.pyc","*.db","*.db-wal","*.db-shm")
    $NativePreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
    & robocopy @RoboArgs
    $RoboExitCode = $LASTEXITCODE
    $PSNativeCommandUseErrorActionPreference = $NativePreference
    if ($RoboExitCode -ge 8) { throw "Unable to stage server release (robocopy exit $RoboExitCode)." }
    Compress-Archive -Path $StagedProject -DestinationPath $ServerArchive -CompressionLevel Optimal
    $Target = "${VpsUser}@${VpsHost}"
    $SshOptions = @("-o","ConnectTimeout=20","-o","ServerAliveInterval=15","-o","ServerAliveCountMax=3")
    if (Test-Path $SshKey) { $SshOptions += @("-i",$SshKey,"-o","IdentitiesOnly=yes") }
    Write-Host "Uploading server and PWA through Tailscale..." -ForegroundColor Cyan
    Invoke-WithRetry "Upload" { & scp @SshOptions -P $VpsPort $ServerArchive $RemoteDeployScript "${Target}:/tmp/" }
    $RemoteArchive = "/tmp/$([IO.Path]::GetFileName($ServerArchive))"
    $RemoteScript = "/tmp/$([IO.Path]::GetFileName($RemoteDeployScript))"
    $CacheFlag = if ($UseDockerCache) { "1" } else { "0" }
    Invoke-WithRetry "VPS deployment" { & ssh @SshOptions -p $VpsPort $Target "bash '$RemoteScript' '$RemoteArchive' '$Version' '$RemoteApp' '$CacheFlag'" }
    $Results.VPS = "DEPLOYED"
    $Results.PWA = "DEPLOYED"
  } catch {
    $Results.VPS = "FAILED"
    $Results.PWA = "FAILED"
    $Failures.Add("VPS/PWA: $($_.Exception.Message)")
  } finally {
    if ($StagingBase -and (Test-Path $StagingBase)) { Remove-Item -LiteralPath $StagingBase -Recurse -Force }
  }
}

if (-not $SkipDesktopInstall) {
  try {
    if (-not $Installer) { throw "Desktop installer is unavailable because the build did not complete." }
    Write-Host "Installing Windows desktop $Version..." -ForegroundColor Cyan
    Get-Process "stockhunter-scout" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Process -FilePath $Installer.FullName -ArgumentList "/S" -Wait
    $Results.Desktop = "INSTALLED"
  } catch {
    $Results.Desktop = "FAILED"
    $Failures.Add("Desktop: $($_.Exception.Message)")
  }
}

Write-Host ""
Write-Host "Release $Version summary" -ForegroundColor Cyan
$Results.GetEnumerator() | ForEach-Object { [pscustomobject]@{ Stage=$_.Key; Status=$_.Value } } | Format-Table -AutoSize
Write-Host "Backend/PWA: $ApiBase"
if ($Installer) { Write-Host "Installer: $($Installer.FullName)" }
if ($Failures.Count) { throw ($Failures -join "`n") }
Write-Host "Coordinated release $Version completed." -ForegroundColor Green
