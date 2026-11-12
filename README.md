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
2. Replace APP_SECRET_KEY and BOOTSTRAP_ADMIN_PASSWORD with unique secrets. For a new local database optionally set POSTGRES_PASSWORD to a URL-safe random hex value; an existing database requires password rotation before changing this value. Never commit .env.
3. Run docker compose up --build. The API runs Alembic migrations before startup; the frontend is built and served by nginx without a dependency bind mount.
4. Open http://localhost:5173; API health is at http://localhost:8000/health.
5. Bootstrap admin is created on first API startup when both bootstrap variables are set. Change/remove bootstrap credentials after first use.

For backend-only development: create a virtual environment, cd backend, install requirements.txt, set DATABASE_URL (SQLite is suitable for isolated development), run alembic upgrade head, then uvicorn app.main:app --reload.

## API outline
- POST /api/auth/register, POST /api/auth/login, GET /api/me
- POST /api/calculators/emi, /plot-cost, /growth
- GET/POST /api/leads, PATCH /api/leads/{id}/status
- GET/POST /api/plots (write is admin-only)
- GET/POST /api/partners/referrals
- POST /api/reviews/requests (staff-only, explicit consent required)
- GET /api/admin/lead-notifications (admin-only new enquiry inbox)
- GET /api/customer/portal

API docs and OpenAPI JSON are enabled outside production only. /ready verifies database connectivity.

## Release validation and remaining production work

- Alembic records schema revisions and upgrades before API startup. The baseline supports fresh databases and compatible existing schema without deleting provider history.
- GitHub Actions runs API tests, production frontend build, a disposable PostgreSQL/Redis browser smoke test and security checks. Confirm the current commit has passed all jobs before release.
- Production configuration requires unique secrets, PostgreSQL, Redis-backed authentication throttling, explicit trusted hosts, and HTTPS ingress.
- Use the separate production Compose configuration; see [deployment guide](deploy/README.md).
- Remaining work before a full production security certification: refresh/session revocation, password recovery, privileged-user MFA, independent penetration testing, dependency lockfiles and verified backups/restore drills, monitoring and incident response.
- The plot map uses schematic coordinates, not approved survey/GIS geometry. Booking, payments and customer documents require verified integrations and business rules. Manual review request records do not send external messages.
- Statutory charges, referral payouts and property data require business verification. Do not treat illustrative calculator returns as guaranteed. 

Never put API keys in frontend source or Vite variables. Keep secrets server-side or in a secret manager. Rotate any credential ever committed. Example compose passwords are local-development-only.

## Built-in lead notifications (no external provider)

1. Sign in and create an enquiry under Leads. It is stored in the database with status new.
2. Sign in as an administrator and open Admin > New enquiry notifications. Refresh to see the latest 50 new enquiries. No messaging credentials or public webhook are required.
3. Open the lead tracker and change a lead's status to contacted, qualified, visit booked, won or lost. It will no longer appear in the new-enquiry inbox after refresh.
4. The inbox is admin-only. Non-admin users continue to see only their own leads. No external messages are sent, including email or SMS. Review requests remain manual records.

### Upgrading an existing installation
Remove obsolete WHATSAPP_* variables from your local .env and deployment secrets; revoke unused Meta credentials separately. The legacy consent column and any previous messaging tables are retained in existing databases to avoid destroying historical data. The API no longer reads or writes historical consent records or delivers messages. Back up the database before upgrades. Alembic baseline migration validates and preserves compatible legacy tables and aborts on incompatible schema; no destructive downgrade is included.

## Tests

Run `cd backend && python -m pytest -v`. Tests use isolated databases and test credentials, never the live database. For a browser check, use a dedicated disposable local Compose stack; install dependencies in frontend with npm install and npx playwright install chromium, then set TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD to its dedicated administrator and run npm run test:e2e. The script creates test records, so never run against a real customer database. CI executes the complete test automatically.
