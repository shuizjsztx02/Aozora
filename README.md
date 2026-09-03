# Aozora

Aozora is an AI-native Android budgeting app for young professionals: users should understand where their money goes without needing to maintain a traditional ledger manually.

The repository is at the T-M0 production baseline. It contains a minimal Android and React shell only; authentication, local database, API, synchronization, and AI actions are intentionally introduced by later milestone tasks.

## Repository boundaries

- `app/`: Android shell and React client.
- `contracts/`: versioned OpenAPI and JSON Schema contracts.
- `server/`: production API and database migrations.
- `infra/`: deployment and observability configuration.
- `docs/APP-CONTEXT.md`: product and architecture source of truth.

## Baseline checks

Run the repository safety check from the root:

```powershell
powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1
```

Run the web shell checks from `app/`:

```powershell
npm run lint
npm run build
```
