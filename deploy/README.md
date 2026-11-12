# MetroEstate production deployment

This configuration targets a Linux host with Docker Engine and Compose v2, a real public domain and HTTPS ports 80/443. It is not an assertion that a cloud server has been provisioned.

## First deployment

1. Choose an approved host and configure a domain's DNS A/AAAA records. Restrict SSH to authorized administrators; allow public ports 80/443 only. Do not expose PostgreSQL, Redis or the API port.
2. Clone the repository and check out a reviewed main commit whose CI is green.
3. Copy .env.production.example to .env.production. Keep the file outside Git (chmod 600). Set PUBLIC_DOMAIN to your real host name. Generate separate random hex values for POSTGRES_PASSWORD and APP_SECRET_KEY, using `python -c "import secrets; print(secrets.token_hex(32))"` twice. Use a real admin email and a strong unique password of at least 16 characters. Never reuse development or test credentials.
4. Run `docker compose --env-file .env.production -f compose.production.yml -p metroestate-prod config --quiet`.
5. Run `docker compose --env-file .env.production -f compose.production.yml -p metroestate-prod up --build -d`.
6. Confirm services with the same Compose flags and `ps`. Confirm logs and `curl -fsS https://YOUR_REAL_DOMAIN/ready`. Caddy obtains TLS when DNS and ports are configured. Verify HTTPS, administrator login, lead inbox and the other business workflows with dedicated test records.
7. After verifying the administrator account, remove BOOTSTRAP_ADMIN_PASSWORD and BOOTSTRAP_ADMIN_EMAIL from the deployment environment and recreate the API container. Existing administrator accounts are retained. Do not use default credentials.
8. Configure external uptime/error monitoring, regular encrypted off-site database backups, an operator for alerts, and incident response. Restrict access to deployment secrets and backups.

Only the Caddy HTTPS ingress is published. The API trusts forwarded client addresses only from the configured Caddy IP on the isolated proxy subnet (172.30.253.0/24). Choose a nonconflicting subnet and change both the Caddy IP and FORWARDED_ALLOW_IPS together if necessary. Redis and PostgreSQL are not publicly exposed. The frontend uses same-origin API paths.

## Upgrades and migrations

Before every deployment, run a database backup and test restoring it to a disposable database. Never run `docker compose down -v` on the production stack.

Example backup command on Linux (protect the resulting file):
```bash
mkdir -p backups
chmod 700 backups
umask 077
docker compose --env-file .env.production -f compose.production.yml -p metroestate-prod exec -T db pg_dump -U app -d realestate -Fc > backups/metroestate.dump
```

Stop the old API during migrations if needed; do not allow incompatible releases to write concurrently. Deploy the reviewed commit with the same Compose project name and existing volumes. API startup invokes `alembic upgrade head` before serving traffic. The initial revision checks compatibility of existing business tables, adds missing baseline tables/indexes, and retains legacy provider data. It refuses incompatible schema rather than dropping tables. Inspect `docker compose --env-file .env.production -f compose.production.yml -p metroestate-prod exec api alembic current`. For incompatible pre-existing schema, resolve on a restored copy first.

Do not merely change POSTGRES_PASSWORD on an existing initialized PostgreSQL volume: perform database password rotation in PostgreSQL and update the application secret together. Do not use the baseline downgrade to delete live business data. Roll back the application only if schema compatibility is established; otherwise use a verified restore plan.

## Release gate

Confirm the current revision passes backend, frontend, browser, migrations and security jobs on GitHub. Check backup/restore, privileged access, browser behavior and live HTTPS on the actual host. A clean automated scan does not prove absence of vulnerabilities. MFA, session revocation, external monitoring, independent penetration testing, booking/payment/document integrations and approved property data remain deployment-specific requirements.
