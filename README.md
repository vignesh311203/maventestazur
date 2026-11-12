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

Run `cd backend`, `python -m pip install -r requirements-dev.txt`, and `python -m pytest -v`. Tests use isolated databases and test credentials, never the live database. For a browser check, use a dedicated disposable local Compose stack; install dependencies in frontend with npm install and npx playwright install chromium, then set TEST_ADMIN_EMAIL and TEST_ADMIN_PASSWORD to its dedicated administrator and run npm run test:e2e. The script creates test records, so never run against a real customer database. CI executes the complete test automatically.


## Public website and privacy (2026-11-12)

Public homepage: `/`; customer signup/login: `/customer`; administrator: `/admin`; channel partner: `/partner`; privacy notice: `/privacy`. Production single-image hosting provides these direct URLs and the public sitemap and robots rules. Customers can register, send consent-confirmed enquiries and see only their own enquiry status. Guests can submit an enquiry without an account; it is visible to administrators, not linked to a later-created account based solely on email. Partners can submit referrals but see only reference numbers, status and reward; agents have no access to complete lead or review records. Full lead/contact lists and customer administration are admin-only at the API, regardless of the browser page. The existing admin user can manage account activation and change their password; password rotation invalidates previously issued tokens. Previously issued tokens without a password version are intentionally rejected.

Publish only verified property inventory. Public listings are loaded from the inventory database; no fictional properties or legal approvals are seeded. Contact consent is persisted with a time and notice version by migration 0002_contact_consent. Older records retain null consent fields. Admins should confirm permission before contacting offline enquiries. The privacy notice is a starter disclosure, not a legal compliance certification; the operator must establish retention/deletion procedures, privacy contact details and any applicable registration/disclosure requirements.

The public site uses responsive layouts and same-origin HTTPS on Railway. Session storage is separated by panel, no third-party font requests are required, and validation errors omit submitted raw input. Authenticated API responses and panel HTML are not cached. Password rotation requires the current password. Rate limits use Redis in production; behind a reverse proxy, request limits may be shared until a trusted client-IP forwarding policy is verified. MFA, verified email/password recovery, independent penetration testing and backup/restore drills remain required for a higher-assurance production service. Google indexing is not guaranteed; verify the site in Google Search Console and submit /sitemap.xml. No automated payments, bookings, document transfers or outbound messaging were introduced.


## Private website messaging

Customers can create an account or sign in at `/customer` and send a message in Chat with MetroEstate. Messages are saved in PostgreSQL and get a deterministic automatic response about pricing, availability, site visits or general enquiries. The response never confirms a booking or promises an investment return. Administrators open Messages at `/admin`, select a customer conversation and send a human reply. Conversations refresh every ten seconds while the panel is open.

All messaging API routes are authenticated. Customers can read only their own messages; administrator access is checked on the server. Partners and agents cannot read customer conversations. Message submissions are limited to 2,000 characters and rate limited. Each submission has an idempotency key to prevent duplicate messages and responses on retry. Migration `0003_support_messages` creates a new table without deleting existing data. There are no external messaging credentials, provider calls, WhatsApp templates or webhooks in this release.

The original pasted WhatsApp example is not enabled: a valid provider phone-number resource ID, a newly rotated access token and app secret, verified webhook signatures, durable provider event processing and provider-approved messaging policy would be prerequisites for a separate integration. Never commit credentials or send customer data to a messaging provider without the appropriate consent and operational controls.

Messaging is in-website only; it does not send SMS, WhatsApp or email notifications. Customers must open their account to see replies. Staff must review and respond to conversations, and establish data retention and deletion procedures before wider commercial operation.

### Quick property requests

The customer chat includes Book Site Visit, Request Brochure and Call Me Back buttons. Each sends a private, persisted chat request and receives immediate guidance. Administrators see the requests in Messages and can reply. These actions do not automatically book a visit, arrange transport, initiate a call or deliver an unverified brochure. Callback contact details are shared voluntarily in the private chat. No external credentials are needed.


## Stateful customer support and Google Sheets reporting

The assistant now persists its conversation state in PostgreSQL. It collects project, preferred date/time, phone number and explicit permission where relevant, handles price questions during a request, supports cancellation and handoff, and saves completed site-visit, brochure and callback requests as new leads. It remains automated, and does not book, call, send a file or promise a visit. Contact details are optional for chat-only follow-up. Preferred time is recorded as requested, not a confirmed appointment. Migration 0004_chat_state adds state and structured lead fields, and 0005_sheets_outbox adds durable reporting change markers and sync status.

Administrators can edit plot inventory from Layout, see request type/source/preferred time in Leads, see new enquiries refresh in the Admin panel, and record an already verified referral payment (no payment transfer). The Sheets tab shows synchronization status and provides a ZIP of six private CSV reports even when Google is not configured.

To enable one-way Google Sheets reporting:

1. Create a dedicated blank Google spreadsheet and keep sharing Restricted. Use only approved administrators; avoid public link sharing. No password hashes, tokens or provider credentials are exported.
2. Enable Google Sheets API in a Google Cloud project. Create a dedicated service account; share only this spreadsheet with its email as Editor. Do not grant broad Drive access or domain-wide delegation. See https://developers.google.com/workspace/guides/create-credentials.
3. Create an authorized JSON service-account key. Store it privately in Railway service Variables as GOOGLE_SERVICE_ACCOUNT_JSON (raw JSON or base64). Never commit the key or paste it into chat. Set GOOGLE_SHEET_ID to the spreadsheet ID, and GOOGLE_SHEETS_ENABLED=true. Restart/redeploy the application. Google Drive connection in ChatGPT does not confer server credentials.
4. Open Admin > Sheets. Check Last successful sync and pending changes. The first sync exports existing records; subsequent changes are queued transactionally and retried with backoff. The six tabs are Leads, Messages, Plots, Referrals, Partners and Customers. Dedicated tab ranges are rewritten from the database; do not use them as the source of truth or add manual data within managed columns. An hourly reconciliation handles missed/manual edits. The worker acquires a PostgreSQL advisory lock to avoid concurrent writers. A failed sync leaves change markers and the database untouched.
5. If the integration is unavailable, download private CSV reports from the Sheets panel and import them into a separate restricted spreadsheet. Protect copies and implement deletion/retention procedures. The sync is optional and disabled by default; the main website does not depend on it.

The Sheets mirror contains personal data and private chat content. Confirm organizational authorization and privacy disclosure before enabling it; revoke service account access when retiring the integration. Google Sheets is not a database backup; maintain tested PostgreSQL backups. Unit tests use simulated Google API responses, not a production Google account. Live Sheets synchronization is only verified when credentials and the designated document have been configured and the first successful sync is observed. Large datasets may exceed Sheets quotas/capacity; PostgreSQL retains the full records.
