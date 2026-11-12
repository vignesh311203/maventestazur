# MetroEstate — Real Estate Platform

FastAPI + SQLAlchemy + PostgreSQL backend, React + Vite frontend, Docker Compose with PostgreSQL and Redis.

## Modules in this starter
- EMI, plot purchase cost and compound-growth calculators
- Lead capture and lead status tracker with per-user visibility
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
- GET/POST /api/whatsapp/webhook (Meta webhook verification and signed events)
- GET /api/admin/whatsapp/status, /consents, /deliveries; POST /api/admin/whatsapp/test
- GET /api/customer/portal

API docs are enabled outside production at /docs.

## Production work still required
This is an application foundation, not production certification:
- Add Alembic migrations; do not rely on create_all for schema upgrades.
- Replace in-memory SlowAPI with Redis-backed distributed limits and configure trusted proxy IP handling.
- Add refresh/session revocation, password reset, MFA for privileged roles, audit logs and admin-managed staff/partner provisioning.
- Enforce HTTPS at ingress, deployment-specific CSP, trusted hosts, backups, monitoring and alerting.
- Move statutory charges and referral reward policies into audited admin-editable database settings; verify state rules with a qualified professional.
- Connect official WhatsApp Cloud API only after business verification, approved templates, webhook signature verification, opt-in records and opt-out processing.
- Use approved survey/GIS geometry for the plot map; current map is schematic.
- Implement bookings, payments and document access against verified business systems with per-record authorization.
- Add end-to-end tests, dependency/secret scanning, pinned dependencies and deployment CI.

Never put API keys in frontend source or Vite variables. Keep secrets server-side or in a secret manager. Rotate any credential ever committed. Example compose passwords are local-development-only.


## WhatsApp Cloud API setup

1. Create/verify a Meta Business portfolio and WhatsApp Business Account, add a phone number, and complete required business/number verification.
2. In the Meta developer app, add the WhatsApp product and configure a server-side access token with required WhatsApp messaging permissions. Use a suitable system-user token; never put it in Vite/frontend variables.
3. Copy the Phone Number ID (not the visible phone number), App Secret, and a random webhook verify token into the backend .env.
4. Set WHATSAPP_GRAPH_API_VERSION to a currently supported Graph API version from Meta's developer documentation. This project deliberately does not guess a current version.
5. Create and obtain approval for a WhatsApp message template named exactly as WHATSAPP_LEAD_ALERT_TEMPLATE. The lead alert implementation supplies four body text parameters: lead name, lead phone, property interest, lead ID. Ensure the approved template has four matching body placeholders.
6. Set WHATSAPP_ALERT_RECIPIENTS to one or more internal staff WhatsApp recipients, comma-separated, in international digits format (for India, include country code 91). Staff recipients must be authorized to receive these business alerts.
7. Set WHATSAPP_TEMPLATE_LANGUAGE to the exact approved template language code.
8. Deploy the API behind HTTPS and set the Meta webhook callback URL to https://YOUR_API_HOST/api/whatsapp/webhook. Enter the same WHATSAPP_VERIFY_TOKEN in Meta and subscribe the WhatsApp webhook to the messages field.
9. The endpoint validates X-Hub-Signature-256 using WHATSAPP_APP_SECRET; invalid signatures are rejected. Delivery/read updates are processed from webhook status events.
10. Restart/redeploy the API and sign in as an admin. Check the Admin > WhatsApp Cloud API status panel and use the test recipient action. A successful API response means Meta accepted the message; delivery/read status is updated from subsequent webhook events.

### Consent and opt-out behavior

- The lead form's WhatsApp consent box is unchecked by default. A checked box records explicit opt-in evidence in the backend consent ledger.
- Lead alerts go only to configured internal staff recipients, using the approved template. This implementation does not send marketing messages to leads.
- Inbound STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, or QUIT turns off consent for that sender. An explicit START, UNSTOP, or SUBSCRIBE keyword records renewed consent.
- Admins can inspect recent consent records and delivery outcomes. Manual consent changes require evidence.
- Do not upload purchased contact lists or message people who have not explicitly opted in. Honor applicable Meta policies and Indian telecom/privacy requirements.

### Environment variables

WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN, WHATSAPP_APP_SECRET, WHATSAPP_GRAPH_API_VERSION, WHATSAPP_ALERT_RECIPIENTS, WHATSAPP_LEAD_ALERT_TEMPLATE, and WHATSAPP_TEMPLATE_LANGUAGE must be set in the backend environment. The .env.example contains names only; do not commit real values.

The integration is not live until you supply Meta credentials, an approved template, an HTTPS webhook endpoint, and successful end-to-end verification in your Meta app. Delivery failures are recorded, but automated retries/queue scheduling and production monitoring remain future hardening work.
