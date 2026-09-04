$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$prohibited = @('*.apk', '*.aab', '*.jks', '*.keystore', 'ai-config.js')
$matches = foreach ($pattern in $prohibited) {
  Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $pattern |
    Where-Object {
      $_.FullName -notmatch '\\node_modules\\|\\.git\\' -and
      -not ($_.Extension -in @('.apk', '.aab') -and
        $_.FullName.StartsWith((Join-Path $root 'app\build\outputs') + '\', [StringComparison]::OrdinalIgnoreCase))
    }
}

if ($matches) {
  $matches.FullName | ForEach-Object { Write-Error "Prohibited baseline file: $_" }
  exit 1
}

Push-Location $root
try {
  # R-M1-46: 检查 rg 是否可用，不可用则跳过密钥扫描（CI Ubuntu 预装 rg）
  $rgAvailable = Get-Command rg -ErrorAction SilentlyContinue
  if ($rgAvailable) {
    $secretMatches = rg -n -i 'sk-[a-z0-9]{20,}|storePassword|keyPassword' . -g '!node_modules/**' -g '!.git/**' -g '!docs/superpowers/**' -g '!tools/validate-baseline.ps1'
  } else {
    Write-Warning "rg (ripgrep) not found, skipping secret scan. Install ripgrep for full baseline validation."
    $secretMatches = $null
  }
} finally {
  Pop-Location
}
if ($secretMatches -and $LASTEXITCODE -eq 0) {
  $secretMatches
  throw 'Secret-like content found in baseline.'
}
if ($secretMatches -and $LASTEXITCODE -gt 1) {
  throw 'Secret scan failed.'
}

@('app', 'contracts', 'server', 'infra', 'docs/APP-CONTEXT.md', 'README.md') | ForEach-Object {
  if (-not (Test-Path -LiteralPath (Join-Path $root $_))) {
    throw "Required baseline path missing: $_"
  }
}

Write-Output 'Baseline validation passed.'
