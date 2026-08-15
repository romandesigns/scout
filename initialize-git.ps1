param(
  [string]$Remote = "https://github.com/romandesigns/scout.git",
  [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
  throw "Git is not installed or is not available in PATH."
}

if (-not (Test-Path ".git")) {
  git init
  if ($LASTEXITCODE -ne 0) { throw "git init failed." }
}

git branch -M $Branch
if ($LASTEXITCODE -ne 0) { throw "Unable to select branch $Branch." }

$Origin = git remote get-url origin 2>$null
if ($LASTEXITCODE -eq 0 -and $Origin) {
  if ($Origin -ne $Remote) {
    git remote set-url origin $Remote
    if ($LASTEXITCODE -ne 0) { throw "Unable to update origin." }
  }
} else {
  git remote add origin $Remote
  if ($LASTEXITCODE -ne 0) { throw "Unable to add origin." }
}

$IgnoredEnv = git check-ignore .env 2>$null
if ($LASTEXITCODE -ne 0 -or $IgnoredEnv -ne ".env") {
  throw ".env is not ignored. Refusing to continue because credentials could be committed."
}

Write-Host "Repository ready." -ForegroundColor Green
Write-Host "Remote: $Remote"
Write-Host "Branch: $Branch"
Write-Host "Next: git add .; git commit -m 'Scout 6.0.0 Replay Spine'; git push -u origin $Branch"
