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
├── deploy/               # T-M5-05a: Deployment scripts
│   ├── staging-deploy.sh # Zero-to-staging deployment
│   └── prod/             # T-M6-01a: 双服务器生产迁移（RUNBOOK + B 建库/A 部署脚本 + nginx）
└── monitoring/           # T-M5-05: Observability
    ├── alert-rules.yml   # Prometheus alerting rules
    ├── otel-collector-config.yaml  # OTel Collector routing
    ├── prometheus.yml    # Prometheus scrape config
    ├── loki-config.yaml  # Loki log aggregation
    ├── tempo-config.yaml # Tempo distributed tracing
    ├── grafana-datasources.yaml  # Grafana datasource provisioning
    └── alertmanager.yml  # Alert routing configuration
```

Secret values belong only in external secret storage and never in this repository.

## Related compose files (repo root)

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base dev stack (db + api) |
| `docker-compose.backup.yml` | T-M5-03 backup overlay |
| `docker-compose.drill.yml` | T-M5-03b drill environment (isolated postgres 18) |
| `docker-compose.staging.yml` | T-M5-05a staging full stack |
| `docker-compose.prod.yml` | T-M6-01a 服务器 A 业务实例（api-test/api-prod profiles；DB 在服务器 B，本文件无 db 服务） |

## Runbooks

See `docs/runbooks/` for operational procedures:

- `disaster-recovery.md` — Database restore runbook (T-M5-03b)
- `secret-management.md` — Secret rotation procedures
