# Production Deployment & Infrastructure Guide

This guide details configuration for Alembic migrations, database permissions, TLS, external monitoring, and secret rotation.

---

## 1. Schema Migrations (Alembic)

Database schema changes must be deployed via Alembic rather than `create_all`:

```sh
# Apply pending migrations
alembic upgrade head

# Generate a new migration after model changes
alembic revision --autogenerate -m "describe_changes"
```

The database user executing migrations (`support_admin`) has DDL privileges on schema `public`.

---

## 2. Least-Privilege Database Users

The production PostgreSQL database enforces role segregation:
- **`support_admin`**: DDL owner for Alembic migrations.
- **`support_app`**: Used by FastAPI runtime. Allowed DML (`SELECT`, `INSERT`, `UPDATE`) only. Cannot `DROP` or `ALTER` tables.
- **`support_n8n`**: Used by the notification worker. Has `SELECT` and `UPDATE` on `notifications`, `SELECT` on `support_requests` and `customers`.

Run `scripts/init_postgres_least_privilege.sql` when provisioning new database clusters.

---

## 3. TLS & Reverse Proxy

Production uses Caddy (`docker-compose.prod.yml` & `Caddyfile`) to terminate TLS via automated ACME certificates (Let's Encrypt / ZeroSSL).

- HTTP (port 80) automatically redirects to HTTPS (port 443).
- Enforces HTTP Strict Transport Security (`HSTS`), `X-Content-Type-Options: nosniff`, and `X-Frame-Options: DENY`.

---

## 4. Health Checks & External Monitoring

The API exposes standard monitoring probes:

1. **Readiness Probe**: `GET /health/ready`
   - Executes an active `SELECT 1` query against PostgreSQL.
   - Returns `200 OK` with `{"status":"ready","database":"connected"}` when healthy.
   - Returns `503 Service Unavailable` if database connectivity fails.
2. **Liveness Probe**: `GET /health/live`
   - Verifies HTTP process responsiveness. Returns `200 OK` with `{"status":"alive"}`.
3. **Basic Ping**: `GET /health`
   - Returns `{"status":"ok"}`.

### External Monitoring Setup
- **UptimeRobot / Datadog / Prometheus Blackbox Exporter**:
  - Point synthetic checks to `https://api.support.example.com/health/ready` with a 30s interval.
  - Alert on consecutive 5xx errors or latency > 2000ms.
- **n8n Monitoring**:
  - Configure external heartbeat check on n8n webhook/health endpoint to detect workflow runner failure.

---

## 5. Automated Backups & Rotation

The backup script `scripts/backup_db.sh` runs `pg_dump` with custom gzip compression and prunes backups older than `RETENTION_DAYS`:

```sh
# Run manually or via cron (e.g. daily at 02:00 UTC)
DATABASE_URL="postgresql+psycopg://support_app:password@postgres:5432/support" \
BACKUP_DIR="/mnt/secure-backups" \
RETENTION_DAYS=14 \
./scripts/backup_db.sh
```

---

## 6. Secret Rotation Procedure

1. Generate new high-entropy secrets:
   ```sh
   openssl rand -base64 48
   ```
2. Update the target key in `.env.production`.
3. Reload FastAPI without dropping active connections:
   ```sh
   docker compose -f docker-compose.prod.yml exec api kill -HUP 1
   ```
4. For database passwords, execute password change in `psql` before updating configuration:
   ```sql
   ALTER USER support_app WITH PASSWORD 'NEW_STRONG_PASSWORD';
   ```
