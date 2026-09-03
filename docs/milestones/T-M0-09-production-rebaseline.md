# T-M0-09 · Production Rebaseline

**Status:** In progress  
**Goal:** Delete the prototype workspace implementation and create the first safe Aozora repository baseline.  
**Authority:** `docs/APP-CONTEXT.md` and the approved T-M0-09 design.

## Retained

- Current product, architecture, design, and engineering documentation after conflict cleanup.
- Root Gradle configuration used by the rebuilt Android shell.

## Deleted

- Historical APKs, signing material, all prototype Android and React source, Vosk assets, packaged WebView output, legacy documents, legacy implementation, and historical prototype captures.

## Exit evidence

- `powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1`
- `npm run lint` and `npm run build` from `app/`
- `git status --short` after the first commit
