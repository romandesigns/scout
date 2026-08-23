param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,
    [switch]$NoPush
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'PowerShell 7 or newer is required.'
}

$Repo = (git rev-parse --show-toplevel 2>$null)
if (-not $Repo) { throw 'Run this command inside the permanent Scout Git repository.' }
Set-Location $Repo

if ((Get-Content .\VERSION -Raw).Trim() -ne $Version) {
    throw "VERSION does not equal $Version."
}

$Branch = git branch --show-current
if ($Branch -eq 'main') {
    $Branch = "integrate/scout-v$Version"
    git switch -c $Branch
}
if ($Branch -ne "integrate/scout-v$Version") {
    throw "Expected main or integrate/scout-v$Version; current branch is $Branch."
}

if (-not (git check-ignore --no-index .env 2>$null)) {
    throw '.env is not protected by .gitignore.'
}

python .\scripts\check_release_integrity.py
if ($LASTEXITCODE -ne 0) { throw 'Release integrity validation failed.' }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Python tests failed.' }

cargo test --manifest-path .\rust\market-replay\Cargo.toml
if ($LASTEXITCODE -ne 0) { throw 'Rust market replay tests failed.' }

Push-Location .\web
try {
    bun install
    bun run build
} finally {
    Pop-Location
}

$Image = "stockhunter-scout:$Version-test"
docker build --tag $Image .
docker run --rm $Image python -c "from app.hybrid import RustPerceptionBridge; from app.config import settings; print('Scout image import OK', settings.app_version)"
if ($LASTEXITCODE -ne 0) { throw 'Built Scout image smoke test failed.' }

git diff --check
if ($LASTEXITCODE -ne 0) { throw 'Whitespace validation failed.' }

git add .
$Staged = @(git diff --cached --name-only)
if (-not $Staged) { throw 'No release changes were staged.' }
$PrivateEnvironmentFiles = @(
    $Staged | Where-Object {
        $_ -match '(^|/)(\.env|\.env\..+)$' -and $_ -notmatch '\.env\.example$'
    }
)
if ($PrivateEnvironmentFiles) {
    throw 'Safety stop: a private environment file was staged.'
}
git diff --cached --check
if ($LASTEXITCODE -ne 0) { throw 'Staged whitespace validation failed.' }

git commit -m "Release StockHunter Scout $Version"
if (-not $NoPush) {
    git push -u origin $Branch
}

Write-Host "Release $Version prepared on $Branch." -ForegroundColor Green
