# Aozora Production Rebaseline Design

**Task:** T-M0-09 Production rebaseline and repository initialization  
**Date:** 2026-09-03  
**Status:** Proposed for implementation

## Goal

Turn the existing prototype directory into a safe, version-controlled Aozora production baseline without carrying unsafe runtime code, secrets, generated artifacts, or obsolete plans into the new Git history.

## Scope and non-goals

This task establishes the baseline only. It does not implement authentication, Room, the backend API, database migrations, sync, or production AI. Those are subsequent, independently testable tasks under the existing M1--M6 roadmap.

The baseline preserves evidence and reusable product knowledge, but does not claim that prototype implementation is production-ready.

## Chosen approach

Use the current directory as the future repository root. Clean it in place, retain only current source and authoritative production documentation, delete obsolete implementation and references, then initialize a new Git repository with a verified first commit.

This deliberately discards the UI and Android prototype implementation. It prevents unsafe prototype storage, key handling, build artifacts, and accidental historical dependencies from becoming inherited production foundations.

## Content disposition

| Class | Content | Baseline action |
|---|---|---|
| Keep as active | Root Gradle configuration and current product/architecture/design/engineering documentation | Retain only after conflict cleanup. |
| Delete from workspace | Root historical APKs, `keystore/`, all existing `app/src/`, `app/public/`, `app/build/`, `.gradle/`, `tools/`, local build caches, legacy documents, generated copies, and obsolete package/build configuration | Permanently delete. They must not be committed or reconstructed from repository history. |
| Rebuild from zero | `app/` Android and React source | Create a minimal compileable Aozora shell with no domain behavior, storage, bridge, AI, voice, image, reminder, widget, or release-signing implementation. |
| Replace | Old milestone overview, risk register evidence that points to removed artifacts, old local-only architecture/security claims | Replace with current Production Baseline references and T-M0 status. |
| Add | `.gitignore`, `.env.example`, repository README, contracts/server/infra placeholders, baseline validation scripts and CI-neutral package commands | Add only the minimal artifacts needed to define ownership, safe configuration, and repeatable checks. |

`local.properties` is machine-local Android configuration and will remain untracked. No live key, signing credential, APK, database dump, or user ledger may enter the new repository.

## Target directory boundary

```text
best-bills/
├─ app/                 # New minimal Android shell and React client; production implementation evolves here
├─ server/              # Empty production API boundary; implementation begins in M2
├─ contracts/           # Versioned OpenAPI and JSON Schema source of truth
├─ infra/               # Deployment/observability definitions, not secrets
├─ docs/                # Current authoritative documentation and plans
├─ tools/               # Reproducible developer validation and migration helpers
├─ .env.example         # Names only, never values
├─ .gitignore
└─ README.md
```

No prototype asset, including the bundled Vosk model, is retained. The new Android shell uses a static local baseline screen; a later client-build task will establish the React-to-WebView packaging boundary in an ignored build path.

## Security baseline

1. Remove the exposed AI key and all files that package it. The key must be revoked or rotated by the account owner outside this repository before any build is distributed.
2. Remove the existing signing keystore and all password literals. A replacement signing process will use external secret storage and CI/runtime injection only.
3. Add repository ignores and a secret scan that rejects key patterns, keystores, APK/AAB artifacts, local properties, environment files, and generated WebView assets.
4. Do not preserve sensitive files in Git, tags, LFS, or any newly created archive.

## Documentation baseline

`docs/APP-CONTEXT.md` remains the product and architecture source of truth. `docs/milestones/overview.md`, `docs/architecture/overview.md`, `docs/architecture/security.md`, and `docs/risk-register.md` will be rewritten only where they conflict with it. The obsolete local-only milestone sequence and all legacy documents are removed; task status begins at T-M0-09 and then proceeds through the production M0 tasks documented in APP-CONTEXT.

## Verification gates

Before the first commit, all of the following must have direct command evidence:

1. No tracked or working-tree file matches known provider-key, signing-password, keystore, APK/AAB, or generated `ai-config.js` patterns.
2. Every active documentation link and repository path resolves.
3. React lint and production build pass from `app/`; Android configuration parses with the available Gradle tooling.
4. Baseline tests explicitly report that there are no production tests yet rather than silently succeeding through an empty command. The first M1 domain task must introduce Vitest and its failing-first test.
5. `git status --short` is clean after the initial commit, and `git ls-files` contains none of the prohibited material.

## Delivery sequence

1. Record the current inventory and create a migration manifest.
2. Remove prohibited material and all obsolete non-authoritative references.
3. Add target boundaries, ignores, documentation corrections, and validation commands.
4. Run security scans and build/lint baseline checks; resolve all failures inside T-M0 scope.
5. Initialize Git, make the first `chore(T-M0-09): establish Aozora production baseline` commit, and record its commit ID in the milestone document.
6. Begin the next task as a separately planned T-M1-01 foundation task, with tests written first.

## Acceptance criteria

- The directory can be cloned as a safe Aozora source repository without exposing a credential or shipping an old prototype artifact.
- The repository structure matches the target ownership boundaries.
- Active documentation has one consistent production roadmap and no legacy implementation is presented as an available path.
- The baseline build/lint commands and security scan provide reproducible evidence.
- The initial Git commit is clean and contains only approved baseline content.
