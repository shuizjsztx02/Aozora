# Infrastructure

This directory owns Docker deployment, observability, and operational artifacts.

## Structure

```text
infra/
├── backup/               # T-M5-03: Backup & recovery
│   ├── backup.sh         # pg_dump automation (ADR-005)
│   ├── cleanup.sh        # Retention policy cleanup
│   ├── verify-backup.sh  # Daily restore + assertion
│   └── production-params.md  # D-EXEC-02 backfill checklist
└── monitoring/           # T-M5-05: Observability
    └── alert-rules.yml   # Prometheus alerting rules
```

Secret values belong only in external secret storage and never in this repository.

## Related compose files (repo root)

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base dev stack (db + api) |
| `docker-compose.backup.yml` | T-M5-03 backup overlay |
| `docker-compose.drill.yml` | T-M5-03b drill environment (isolated postgres 18) |
| `docker-compose.staging.yml` | T-M5-05a staging full stack |

## Runbooks

See `docs/runbooks/` for operational procedures:

- `disaster-recovery.md` — Database restore runbook (T-M5-03b)
- `secret-management.md` — Secret rotation procedures
