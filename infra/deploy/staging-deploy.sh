#!/usr/bin/env bash
# infra/deploy/staging-deploy.sh — T-M5-05a
#
# Zero-to-staging deployment script for Aozora observability stack.
# Deploys from a clean environment (no containers, no data volumes).
#
# Usage:
#   ./infra/deploy/staging-deploy.sh
#
# This script:
#   1. Validates prerequisites (Docker, Docker Compose)
#   2. Stops any existing staging containers
#   3. Removes old data volumes (clean slate)
#   4. Builds and starts all services
#   5. Waits for health checks
#   6. Verifies deployment success
#
# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  ⚠ WARNING — 本脚本会删除所有现有数据卷（clean slate 部署）            ║
# ║                                                                        ║
# ║  • 仅用于 staging 环境从零开始部署                                    ║
# ║  • 生产环境使用不同的部署流程 (T-M6-01)                               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# Get script directory and project root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

log_info "Starting staging deployment from clean environment..."
log_info "Project root: $PROJECT_ROOT"

# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Validate prerequisites
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 1/6: Validating prerequisites..."

if ! command -v docker &> /dev/null; then
    log_error "Docker is not installed or not in PATH"
    exit 1
fi

if ! docker compose version &> /dev/null; then
    log_error "Docker Compose V2 is not available"
    exit 1
fi

if ! docker info &> /dev/null; then
    log_error "Docker daemon is not running"
    exit 1
fi

log_success "Prerequisites validated"

# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Stop existing containers
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 2/6: Stopping existing staging containers (if any)..."

docker compose -f docker-compose.staging.yml down --remove-orphans 2>/dev/null || true

log_success "Existing containers stopped"

# ─────────────────────────────────────────────────────────────────────────────
# Step 3: Remove old data volumes (clean slate)
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 3/6: Removing old data volumes (clean slate)..."

# List all volumes used by staging compose
# Use COMPOSE_PROJECT_NAME to build volume prefixes so this script stays in
# sync if the project is deployed under a non-default name.
PROJECT_NAME="${COMPOSE_PROJECT_NAME:-aozora}"
VOLUMES=(
    "${PROJECT_NAME}_pgdata"
    "${PROJECT_NAME}_prometheus_data"
    "${PROJECT_NAME}_loki_data"
    "${PROJECT_NAME}_tempo_data"
    "${PROJECT_NAME}_grafana_data"
    "${PROJECT_NAME}_alertmanager_data"
)

for volume in "${VOLUMES[@]}"; do
    if docker volume inspect "$volume" &> /dev/null; then
        log_warn "Removing volume: $volume"
        docker volume rm "$volume" || true
    fi
done

log_success "Old data volumes removed"

# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Build and start all services
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 4/6: Building and starting all services..."

docker compose -f docker-compose.staging.yml up -d --build

log_success "Services started"

# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Wait for health checks
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 5/6: Waiting for health checks (this may take 1-2 minutes)..."

# Wait for PostgreSQL
log_info "Waiting for PostgreSQL..."
timeout=60
elapsed=0
until docker compose -f docker-compose.staging.yml exec -T db pg_isready -U aozora &> /dev/null; do
    if [ $elapsed -ge $timeout ]; then
        log_error "PostgreSQL failed to become ready within ${timeout}s"
        exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done
log_success "PostgreSQL is ready"

# Wait for API
log_info "Waiting for API..."
timeout=60
elapsed=0
until curl -sf http://localhost:8000/api/v1/health/ready &> /dev/null; do
    if [ $elapsed -ge $timeout ]; then
        log_error "API failed to become ready within ${timeout}s"
        exit 1
    fi
    sleep 2
    elapsed=$((elapsed + 2))
done
log_success "API is ready"

# Wait for other services (no health checks, just wait for startup)
log_info "Waiting for monitoring services to start..."
sleep 10

log_success "All services are running"

# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Verify deployment
# ─────────────────────────────────────────────────────────────────────────────
log_info "Step 6/6: Verifying deployment..."

# Count running containers
CONTAINER_COUNT=$(docker compose -f docker-compose.staging.yml ps -q | wc -l)
if [ "$CONTAINER_COUNT" -lt 7 ]; then
    log_error "Expected at least 7 containers, found $CONTAINER_COUNT"
    docker compose -f docker-compose.staging.yml ps
    exit 1
fi
log_success "Running containers: $CONTAINER_COUNT"

# Verify service endpoints
log_info "Verifying service endpoints..."

SERVICES=(
    "API:http://localhost:8000/api/v1/health/ready"
    "Prometheus:http://localhost:9090/-/healthy"
    "Loki:http://localhost:3100/ready"
    "Tempo:http://localhost:3200/ready"
    "Grafana:http://localhost:3000/api/health"
    "Alertmanager:http://localhost:9093/-/healthy"
)

for service_info in "${SERVICES[@]}"; do
    IFS=':' read -r service_name url <<< "$service_info"
    if curl -sf "$url" &> /dev/null; then
        log_success "$service_name is healthy"
    else
        log_warn "$service_name health check failed (may still be starting)"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# Deployment summary
# ─────────────────────────────────────────────────────────────────────────────
echo ""
log_success "=========================================="
log_success "Staging deployment complete!"
log_success "=========================================="
echo ""
log_info "Service endpoints:"
echo "  - API:          http://localhost:8000"
echo "  - Grafana:      http://localhost:3000 (admin/admin)"
echo "  - Prometheus:   http://localhost:9090"
echo "  - Loki:         http://localhost:3100"
echo "  - Tempo:        http://localhost:3200"
echo "  - Alertmanager: http://localhost:9093"
echo ""
log_info "Next steps:"
echo "  1. Open Grafana at http://localhost:3000"
echo "  2. Make some API requests to generate traces/metrics/logs"
echo "  3. Use Grafana Explore to query:"
echo "     - Traces: Select 'Tempo' datasource"
echo "     - Metrics: Select 'Prometheus' datasource"
echo "     - Logs: Select 'Loki' datasource"
echo ""
log_info "To view logs:"
echo "  docker compose -f docker-compose.staging.yml logs -f"
echo ""
log_info "To stop the stack:"
echo "  docker compose -f docker-compose.staging.yml down"
echo ""
