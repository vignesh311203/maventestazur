# MetroEstate — Real Estate Platform

FastAPI + SQLAlchemy + PostgreSQL backend, React + Vite frontend, Docker Compose with PostgreSQL and Redis.

## Modules in this starter
- EMI, plot purchase cost and compound-growth calculators
- Lead capture, status tracker with per-user visibility and admin new-enquiry inbox
- Schematic interactive plot inventory map
- Channel partner referral submission and partner-scoped referral records
- Consent-gated review request API (delivery adapter not enabled)
- Customer account portal API
- JWT authentication, Argon2 password hashing, role checks, request rate limits and security headers

## Run locally
1. Copy .env.example to .env.
2. Replace APP_SECRET_KEY, BOOTSTRAP_ADMIN_PASSWORD and database password with unique secrets. Never commit .env.
3. Run docker compose up --build.
4. Open http://localhost:5173; API health is at http://localhost:8000/health.
5. Bootstrap admin is created on first API startup when both bootstrap variables are set. Change/remove bootstrap credentials after first use.

For backend-only development: create a virtual environment, install backend/requirements.txt, set DATABASE_URL, then run uvicorn app.main:app --app-dir backend --reload.

## API outline
- POST /api/auth/register, POST /api/auth/login, GET /api/me
- POST /api/calculators/emi, /plot-cost, /growth
- GET/POST /api/leads, PATCH /api/leads/{id}/status
- GET/POST /api/plots (write is admin-only)
- GET/POST /api/partners/referrals
- POST /api/reviews/requests (staff-only, explicit consent required)
- GET /api/admin/lead-notifications (admin-only new enquiry inbox)
- GET /api/customer/portal

API docs are enabled outside production at /docs.

## Production work still required
This is an application foundation, not production certification:
- Add Alembic migrations; do not rely on create_all for schema upgrades.
- Replace in-memory SlowAPI with Redis-backed distributed limits and configure trusted proxy IP handling.
- Add refresh/session revocation, password reset, MFA for privileged roles, audit logs and admin-managed staff/partner provisioning.
- Enforce HTTPS at ingress, deployment-specific CSP, trusted hosts, backups, monitoring and alerting.
- Move statutory charges and referral reward policies into audited admin-editable database settings; verify state rules with a qualified professional.
- Use approved survey/GIS geometry for the plot map; current map is schematic.
- Implement bookings, payments and document access against verified business systems with per-record authorization.
- Add end-to-end tests, dependency/secret scanning, pinned dependencies and deployment CI.

Never put API keys in frontend source or Vite variables. Keep secrets server-side or in a secret manager. Rotate any credential ever committed. Example compose passwords are local-development-only.

## Built-in lead notifications (no external provider)

1. Sign in and create an enquiry under Leads. It is stored in the database with status new.
2. Sign in as an administrator and open Admin > New enquiry notifications. Refresh to see the latest 50 new enquiries. No messaging credentials or public webhook are required.
3. Open the lead tracker and change a lead's status to contacted, qualified, visit booked, won or lost. It will no longer appear in the new-enquiry inbox after refresh.
4. The inbox is admin-only. Non-admin users continue to see only their own leads. No external messages are sent, including email or SMS. Review requests remain manual records.

### Upgrading an existing installation
Remove obsolete WHATSAPP_* variables from your local .env and deployment secrets; revoke unused Meta credentials separately. The legacy consent column and any previous messaging tables are retained in existing databases to avoid destroying historical data. The API no longer reads or writes historical consent records or delivers messages. Back up the database before upgrades; no destructive schema migration is included.
