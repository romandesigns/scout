param(
    [int]$Limit = 200,
    [switch]$IncludeDeveloping,
    [ValidateSet("none","alphavantage")]
    [string]$IndependentProvider = "none",
    [string]$AlphaVantageApiKey = $env:ALPHAVANTAGE_API_KEY,
    [double]$IndependentTolerancePct = 0.75,
    [int]$IndependentMaxSymbols = 20
)
$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
$Args=@(".\scripts\detection_quality.py","--limit",$Limit,"--independent-provider",$IndependentProvider,
       "--independent-tolerance-pct",$IndependentTolerancePct,"--independent-max-symbols",$IndependentMaxSymbols)
if($IncludeDeveloping){$Args += "--include-developing"}
if($AlphaVantageApiKey){$Args += @("--alpha-vantage-api-key",$AlphaVantageApiKey)}
python @Args
if($LASTEXITCODE-ne 0){throw "Detection-quality v6 audit failed."}
