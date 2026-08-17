$ErrorActionPreference = "Stop"
$Page = Join-Path $PSScriptRoot "web\app\page.tsx"
if (-not (Test-Path $Page)) { throw "web/app/page.tsx not found." }
$Text = Get-Content $Page -Raw
foreach ($Marker in @("TwentyFourHourPanel","getTwentyFourHourStocks",'id:"24h"',"twentyFourHourResult")) {
    if ($Text -notmatch [regex]::Escape($Marker)) {
        throw "Corrected v6.7.0 requires the packaged full page.tsx; marker missing: $Marker"
    }
}
Write-Host "24H panel is already integrated in packaged page.tsx." -ForegroundColor Green
