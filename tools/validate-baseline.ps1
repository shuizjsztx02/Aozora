$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$prohibited = @('*.apk', '*.aab', '*.jks', '*.keystore', 'ai-config.js')
$matches = foreach ($pattern in $prohibited) {
  Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $pattern |
    Where-Object { $_.FullName -notmatch '\\node_modules\\|\\.git\\' }
}

if ($matches) {
  $matches.FullName | ForEach-Object { Write-Error "Prohibited baseline file: $_" }
  exit 1
}

Push-Location $root
try {
  $secretMatches = rg -n -i 'sk-[a-z0-9]{20,}|storePassword|keyPassword' . -g '!node_modules/**' -g '!.git/**' -g '!docs/superpowers/**' -g '!tools/validate-baseline.ps1'
} finally {
  Pop-Location
}
if ($LASTEXITCODE -eq 0) {
  $secretMatches
  throw 'Secret-like content found in baseline.'
}
if ($LASTEXITCODE -gt 1) {
  throw 'Secret scan failed.'
}

@('app', 'contracts', 'server', 'infra', 'docs/APP-CONTEXT.md', 'README.md') | ForEach-Object {
  if (-not (Test-Path -LiteralPath (Join-Path $root $_))) {
    throw "Required baseline path missing: $_"
  }
}

Write-Output 'Baseline validation passed.'
