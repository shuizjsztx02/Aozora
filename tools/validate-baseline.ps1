$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$prohibited = @('*.apk', '*.aab', '*.jks', '*.keystore', 'ai-config.js')
$matches = foreach ($pattern in $prohibited) {
  Get-ChildItem -LiteralPath $root -Recurse -Force -File -Filter $pattern |
    Where-Object {
      $_.FullName -notmatch '[/\\]node_modules[/\\]|[/\\]\.git[/\\]' -and
      -not ($_.Extension -in @('.apk', '.aab') -and
        $_.FullName.StartsWith((Join-Path $root (Join-Path 'app' 'build' 'outputs')) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase))
    }
}

if ($matches) {
  $matches.FullName | ForEach-Object { Write-Error "Prohibited baseline file: $_" }
  exit 1
}

Push-Location $root
try {
  # R-M1-46 / R-P1-Baseline: 检查 rg 是否可用，不可用则失败（避免假绿）
  $rgAvailable = Get-Command rg -ErrorAction SilentlyContinue
  if ($rgAvailable) {
    $secretMatches = rg -n -i 'sk-[a-z0-9]{20,}|storePassword|keyPassword' . -g '!node_modules/**' -g '!.git/**' -g '!docs/superpowers/**' -g '!tools/validate-baseline.ps1'
  } else {
    throw "rg (ripgrep) not found. Install ripgrep to run secret scan — skipping is not allowed (R-P1-Baseline)."
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

# ─── T-M5-01d: Three-platform test gates ───

Write-Output 'Running three-platform test gates...'

# 1. TypeScript (vitest)
Write-Output '  [1/3] TypeScript tests...'
Push-Location (Join-Path $root 'app')
try {
    $npxAvailable = Get-Command npx -ErrorAction SilentlyContinue
    if ($npxAvailable) {
        & npx vitest run --reporter=basic 2>&1 | ForEach-Object { Write-Output "    $_" }
        if ($LASTEXITCODE -ne 0) {
            throw 'TypeScript tests failed.'
        }
        Write-Output '  ✓ TypeScript tests passed'
    } else {
        throw 'npx not found on PATH — cannot run TypeScript tests. Install Node.js and ensure npx is available.'
    }
} finally {
    Pop-Location
}

# 2. Python (pytest)
Write-Output '  [2/3] Python tests...'
Push-Location (Join-Path $root 'server')
try {
    $pytestAvailable = Get-Command pytest -ErrorAction SilentlyContinue
    if ($pytestAvailable) {
        & pytest tests/ --tb=short -q 2>&1 | ForEach-Object { Write-Output "    $_" }
        if ($LASTEXITCODE -ne 0) {
            throw 'Python tests failed.'
        }
        Write-Output '  ✓ Python tests passed'
    } else {
        throw 'pytest not found on PATH — cannot run Python tests. Install from server/pyproject.toml dev dependencies.'
    }
} finally {
    Pop-Location
}

# 3. Kotlin (JVM unit tests via gradle)
Write-Output '  [3/3] Kotlin JVM tests...'
Push-Location $root
try {
    $gradlewExists = Test-Path (Join-Path $root 'gradlew')
    if ($gradlewExists) {
        if ($IsWindows -or $env:OS -match 'Windows') {
            & .\gradlew.bat :app:testDebugUnitTest --no-daemon 2>&1 | ForEach-Object { Write-Output "    $_" }
        } else {
            & ./gradlew :app:testDebugUnitTest --no-daemon 2>&1 | ForEach-Object { Write-Output "    $_" }
        }
        if ($LASTEXITCODE -ne 0) {
            throw 'Kotlin JVM tests failed.'
        }
        Write-Output '  ✓ Kotlin JVM tests passed'
    } else {
        throw 'gradlew not found at repo root — cannot run Kotlin tests.'
    }
} finally {
    Pop-Location
}

Write-Output 'Baseline validation passed (including three-platform test gates).'
