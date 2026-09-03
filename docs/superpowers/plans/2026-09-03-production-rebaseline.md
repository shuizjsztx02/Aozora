# Aozora Production Rebaseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish a safe, clean, version-controlled Aozora production baseline in the current directory.

**Architecture:** Treat `docs/APP-CONTEXT.md` as the sole source for the future product architecture. Remove all prototype runtime artifacts and source, rebuild only a minimal compileable Android/React Aozora shell, then introduce directory and validation boundaries before creating the repository's first commit.

**Tech Stack:** Kotlin/Gradle Android shell, React 19/Vite 8 client, PowerShell validation scripts, Git.

**Spec:** `docs/superpowers/specs/2026-09-03-production-rebaseline-design.md`

## Global Constraints

- The formal product and app name is Aozora; no new runtime code, documentation, or UI may introduce historical product names.
- No live provider key, signing credential, APK/AAB, keystore, local property file, environment file, generated WebView output, database dump, or user ledger may enter Git.
- `docs/APP-CONTEXT.md` is the current architecture source of truth; conflicts are resolved in its favor.
- This plan establishes a baseline only. It does not implement Room, backend APIs, database schema, authentication, synchronization, or production AI.
- All destructive deletes are authorized by the user and must be limited to the explicit paths listed in Task 2.

---

## File structure after this plan

| Path | Responsibility |
|---|---|
| `app/` | Minimal new Android/React Aozora shell with no prototype domain behavior. |
| `contracts/README.md` | States contract ownership and the future OpenAPI/JSON Schema boundary. |
| `server/README.md` | States that the production API has no implementation yet. |
| `infra/README.md` | States deployment configuration ownership and prohibits secrets. |
| `tools/validate-baseline.ps1` | Runs repository safety, path, and active-documentation validation. |
| `.gitignore` | Prevents credentials, local Android setup, build products, generated WebView output, and releases from entering Git. |
| `.env.example` | Documents configuration variable names without values. |
| `README.md` | Gives a safe, current entry point for contributors. |
| `docs/milestones/overview.md` | Records T-M0-09 and points to APP-CONTEXT for the production roadmap. |

### Task 1: Record the pre-clean inventory

**Files:**
- Create: `docs/milestones/T-M0-09-production-rebaseline.md`
- Read: `docs/APP-CONTEXT.md`, `app/package.json`, `app/build.gradle.kts`

**Interfaces:**
- Consumes: current working tree paths and the production target defined by `docs/APP-CONTEXT.md`.
- Produces: an auditable list of retained and deleted path classes for the baseline task.

- [ ] **Step 1: Create the migration record**

Write `docs/milestones/T-M0-09-production-rebaseline.md` with these headings and values:

```markdown
# T-M0-09 · Production Rebaseline

**Status:** In progress
**Goal:** Delete the prototype workspace implementation and create the first safe Aozora repository baseline.
**Authority:** `docs/APP-CONTEXT.md` and the approved T-M0-09 design.

## Retained
- Current product, architecture, design, and engineering documentation after conflict cleanup.
- Root Gradle configuration used by the rebuilt Android shell.

## Deleted
- Historical APKs, signing material, all prototype Android/React source, Vosk assets, packaged WebView output, legacy documents, legacy implementation, and historical prototype captures.

## Exit evidence
- `powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1`
- `npm run lint` and `npm run build` from `app/`
- `git status --short` after the first commit
```

- [ ] **Step 2: Inspect the record**

Run: `Get-Content -Raw docs/milestones/T-M0-09-production-rebaseline.md`

Expected: retained, deleted, and exit-evidence sections match this plan.

### Task 2: Remove prohibited and obsolete workspace content

**Files:**
- Delete: root `*.apk`
- Delete: `keystore/`
- Delete: `app/src/`
- Delete: `app/public/`
- Delete: `app/index.html`, `app/vite.config.js`, `app/package.json`, `app/package-lock.json`
- Delete: `docs/legacy/`
- Delete: `tools/`
- Delete: `app/README.md`, `app/node_modules/`, `app/dist/`, `app/build/`, `.gradle/`, `local.properties`
- Modify: `app/build.gradle.kts`

**Interfaces:**
- Consumes: the approved destructive-delete authorization and baseline disposition table.
- Produces: a workspace with no prototype runtime implementation, shipped credential, signing material, historical binary, legacy source, or generated WebView bundle.

- [ ] **Step 1: Verify every destructive target exists under the workspace root**

Run this PowerShell inspection before deleting:

```powershell
$root = (Resolve-Path .).Path
$targets = @(
  (Get-ChildItem -File -Filter '*.apk').FullName,
  (Join-Path $root 'keystore'),
  (Join-Path $root 'app/src'),
  (Join-Path $root 'app/public'),
  (Join-Path $root 'docs/legacy'),
  (Join-Path $root 'tools'),
  (Join-Path $root 'app/README.md'),
  (Join-Path $root 'app/node_modules'),
  (Join-Path $root 'app/dist'),
  (Join-Path $root 'app/build'),
  (Join-Path $root '.gradle'),
  (Join-Path $root 'local.properties')
) | Where-Object { $_ }
$targets | ForEach-Object {
  if (-not $_.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing target outside workspace: $_" }
  $_
}
```

Expected: each resolved target begins with the repository root.

- [ ] **Step 2: Delete the approved targets**

Run only after Step 1 reports safe paths:

```powershell
$root = (Resolve-Path .).Path
$exact = @(
  (Join-Path $root 'keystore'),
  (Join-Path $root 'app/src'),
  (Join-Path $root 'app/public'),
  (Join-Path $root 'docs/legacy'),
  (Join-Path $root 'tools'),
  (Join-Path $root 'app/README.md'),
  (Join-Path $root 'app/node_modules'),
  (Join-Path $root 'app/dist'),
  (Join-Path $root 'app/build'),
  (Join-Path $root '.gradle'),
  (Join-Path $root 'local.properties')
)
Get-ChildItem -File -Filter '*.apk' | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
$exact | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { Remove-Item -LiteralPath $_ -Recurse -Force }
Remove-Item -LiteralPath 'app/index.html','app/vite.config.js','app/package.json','app/package-lock.json' -Force
```

Expected: no listed prototype target remains; `app/` contains only Gradle configuration until Task 3 rebuilds the clean shell.

- [ ] **Step 3: Remove credential-based signing configuration**

Replace the `signingConfigs` block and `release.signingConfig` assignment in `app/build.gradle.kts` with an unsigned release configuration that keeps `isMinifyEnabled = false` and has no literal password, alias, or keystore path.

- [ ] **Step 4: Verify deletion and secret absence**

Run:

```powershell
rg -n -i 'sk-[a-z0-9]|openrouter|storePassword|keyPassword|bestbills\.jks|bestbills\.json|bestbills_prefs' . -g '!node_modules'
```

Expected: no credential, signing-password, old data-file, or historical product identifier remains outside explicitly allowed migration comments that will be removed in Task 3.

### Task 3: Rebuild the clean application shell and align documentation

**Files:**
- Modify: `app/build.gradle.kts`
- Create: `app/package.json`
- Create: `app/vite.config.js`
- Create: `app/index.html`
- Create: `app/src/main.jsx`
- Create: `app/src/App.jsx`
- Create: `app/src/index.css`
- Create: `app/src/main/AndroidManifest.xml`
- Create: `app/src/main/java/com/aozora/app/MainActivity.kt`
- Create: `app/src/main/assets/index.html`
- Create: `app/src/main/res/values/strings.xml`
- Create: `app/src/main/res/values/themes.xml`
- Modify: `docs/milestones/overview.md`
- Modify: `docs/APP-CONTEXT.md`
- Modify: `docs/architecture/overview.md`
- Modify: `docs/architecture/security.md`
- Modify: `docs/risk-register.md`

**Interfaces:**
- Consumes: source naming constraints and production ownership rules from `docs/APP-CONTEXT.md`.
- Produces: a compileable empty Aozora shell and active files that do not describe prototype-only storage, client-held keys, or superseded milestones as current architecture.

- [ ] **Step 1: Recreate the Android and React shell without domain implementation**

Create a React/Vite application with only `react`, `react-dom`, `vite`, `@vitejs/plugin-react`, and `oxlint`; its `App` component must render the text `Aozora production baseline` and no ledger, amount, category, AI, or storage behavior.

Create these exact web files:

```json
// app/package.json
{
  "name": "aozora-app",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "lint": "oxlint",
    "validate:baseline": "powershell -ExecutionPolicy Bypass -File ../tools/validate-baseline.ps1"
  },
  "dependencies": {
    "react": "^19.2.8",
    "react-dom": "^19.2.8"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^6.1.0",
    "oxlint": "^1.79.0",
    "vite": "^8.2.2"
  }
}
```

```js
// app/vite.config.js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({ plugins: [react()] })
```

```html
<!-- app/index.html -->
<!doctype html><html lang="zh-CN"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" /><title>Aozora</title></head><body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body></html>
```

```jsx
// app/src/main.jsx
import { createRoot } from 'react-dom/client'
import App from './App.jsx'
import './index.css'

createRoot(document.getElementById('root')).render(<App />)

// app/src/App.jsx
export default function App() {
  return <main>Aozora production baseline</main>
}
```

```css
/* app/src/index.css */
:root { font-family: system-ui, sans-serif; color: #101828; background: #f7fbff; }
body { margin: 0; min-width: 320px; }
main { display: grid; min-height: 100vh; place-items: center; }
```

Create `MainActivity` as a WebView host with JavaScript enabled, DOM storage enabled, file/content access disabled, no JavaScript interface, and `loadUrl("file:///android_asset/index.html")`. Create `assets/index.html` with the same baseline text. The Android manifest must declare only `INTERNET`, set `android:allowBackup="false"` and `android:usesCleartextTraffic="false"`, and declare only the launcher activity.

Use this exact `app/build.gradle.kts` dependency block:

```kotlin
dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
```

Create these exact Android files:

```xml
<!-- app/src/main/AndroidManifest.xml -->
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application android:allowBackup="false" android:label="@string/app_name" android:supportsRtl="true" android:usesCleartextTraffic="false" android:theme="@style/Theme.Aozora">
        <activity android:name=".MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
```

```kotlin
// app/src/main/java/com/aozora/app/MainActivity.kt
package com.aozora.app

import android.annotation.SuppressLint
import android.os.Bundle
import android.webkit.WebView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
            loadUrl("file:///android_asset/index.html")
        })
    }
}
```

```xml
<!-- app/src/main/res/values/strings.xml -->
<resources><string name="app_name">Aozora</string></resources>
<!-- app/src/main/res/values/themes.xml -->
<resources><style name="Theme.Aozora" parent="Theme.AppCompat.DayNight.NoActionBar" /></resources>
<!-- app/src/main/assets/index.html -->
<!doctype html><html lang="zh-CN"><body>Aozora production baseline</body></html>
```

- [ ] **Step 2: Replace obsolete milestone overview**

Replace `docs/milestones/overview.md` with a compact M0--M6 table copied from `docs/APP-CONTEXT.md` section 9, set the current work to `T-M0-09`, and link to `T-M0-09-production-rebaseline.md`.

- [ ] **Step 3: Correct architecture, security, and risk sources**

Rewrite only contradictory passages so they state: server authority, Room local replica, integer cents, server-side AI gateway, no client secrets, and no current production release. Remove obsolete risk rows whose evidence was deleted; retain or add risk rows for unimplemented Room, server, sync, and secret rotation with the required next task IDs. In `docs/APP-CONTEXT.md`, delete the historical-name and legacy-reference sections because no legacy artifact is retained.

- [ ] **Step 4: Verify no active source references deleted runtime design**

Run:

```powershell
rg -n -i 'bestbills|小贝账本|openrouter|ai-config' app docs -g '!app/node_modules/**' -g '!docs/superpowers/**'
```

Expected: no match outside the T-M0 design/plan files explaining removed material.

### Task 4: Add safe repository boundaries and reproducible validation

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `README.md`
- Create: `contracts/README.md`
- Create: `server/README.md`
- Create: `infra/README.md`
- Create: `tools/validate-baseline.ps1`
- Modify: `app/package.json`

**Interfaces:**
- Consumes: the target repository boundary in the T-M0 design.
- Produces: a contributor entry point, no-secret configuration contract, and one validation command returning non-zero for baseline violations.

- [ ] **Step 1: Add ignore and environment contracts**

Create `.gitignore` that ignores exactly these classes:

```gitignore
node_modules/
dist/
build/
.gradle/
local.properties
*.keystore
*.jks
*.apk
*.aab
.env
.env.*
!/.env.example
app/src/main/assets/www/
```

Create `.env.example` with comments and names only:

```dotenv
# Configure these only in deployment secret storage; do not put values in this file.
DATABASE_URL=
REDIS_URL=
AI_PROVIDER_API_KEY=
AI_PROVIDER_MODEL=qwen3.7-flash
```

- [ ] **Step 2: Add boundary READMEs**

Create the three boundary READMEs with one paragraph each: `contracts/` owns versioned machine-readable contracts; `server/` will own authenticated API modules and migrations; `infra/` will own Docker and observability configuration without secret values.

- [ ] **Step 3: Add the baseline validator**

Create `tools/validate-baseline.ps1` with this behavior:

```powershell
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
$secretMatches = rg -n -i 'sk-[a-z0-9]{20,}|storePassword|keyPassword' $root -g '!node_modules' -g '!.git'
if ($LASTEXITCODE -eq 0) {
  $secretMatches
  throw 'Secret-like content found in baseline.'
}
if ($LASTEXITCODE -gt 1) { throw 'Secret scan failed.' }
@('app', 'contracts', 'server', 'infra', 'docs/APP-CONTEXT.md', 'README.md') | ForEach-Object {
  if (-not (Test-Path -LiteralPath (Join-Path $root $_))) { throw "Required baseline path missing: $_" }
}
Write-Output 'Baseline validation passed.'
```

- [ ] **Step 4: Add client commands without inventing test coverage**

Add an `validate:baseline` script to `app/package.json` equal to `powershell -ExecutionPolicy Bypass -File ../tools/validate-baseline.ps1`. Do not add a passing empty test script.

- [ ] **Step 5: Verify the baseline validator fails when it should**

Create a temporary root file named `_baseline-secret-probe.txt` containing `sk-abcdefghijklmnopqrstuvwxyz123456`, run `powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1`, confirm it exits non-zero because of the probe, then delete only `_baseline-secret-probe.txt`.

- [ ] **Step 6: Verify the baseline validator passes after cleanup**

Run: `powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1`

Expected: `Baseline validation passed.` and exit code 0.

### Task 5: Verify client baseline and create the repository

**Files:**
- Modify: `README.md`
- Modify: `docs/milestones/T-M0-09-production-rebaseline.md`
- Create: `.git/` through `git init`

**Interfaces:**
- Consumes: validation command, client package scripts, and cleaned working tree.
- Produces: the first Git commit and a record of its exact verification evidence.

- [ ] **Step 1: Install the new client dependency set**

Run from `app/`: `npm install`

Expected: dependencies resolve and generate a new `package-lock.json` matching the minimal `package.json`.

- [ ] **Step 2: Run full client and baseline verification**

Run from `app/`: `npm run lint`, then `npm run build`, then `npm run validate:baseline`.

Expected: all commands exit 0. If build requires the deleted packaged output, change the build pipeline to generate output under an ignored directory rather than restoring `app/src/main/assets/www/`.

- [ ] **Step 3: Initialize repository and inspect staged content**

Run:

```powershell
git init
git add .
git diff --cached --name-only
git diff --cached | rg -n -i 'sk-[a-z0-9]{20,}|storePassword|keyPassword|bestbills'
```

Expected: the staged-name list excludes all prohibited classes and the secret scan has no match.

- [ ] **Step 4: Create the baseline commit**

Run:

```powershell
git commit -m "chore(T-M0-09): establish Aozora production baseline"
git status --short
git log -1 --oneline
```

Expected: status has no output and the log displays the T-M0-09 baseline commit.

- [ ] **Step 5: Record evidence**

Update `docs/milestones/T-M0-09-production-rebaseline.md` with the exact commit ID, validation command outputs summarized by exit code, and `Status: Complete`; amend the baseline commit to include this record.
