# Production security, privacy and recovery

## Release gate

Production Railway is pinned to the successful 0010_inventory_review release. The default branch was subsequently reverted by PR #30. This phase is based on the live commit, not the reverted main branch. Reconcile the intended inventory behavior and migration lineage before merging or changing the Railway source. Never deploy a build lacking the current database revision. Migration 0011_security_privacy is additive and preserves existing records; historical consent wording is not fabricated.

## Administrator enrollment

1. Sign in using the existing account and open Administrator two-factor authentication.
2. Enter the current password, create an authenticator setup key, add it manually as a time-based account, and confirm with a six-digit code within ten minutes.
3. Store the ten single-use recovery codes securely. All existing sessions are revoked. Sign in again with a fresh code or a recovery code plus password.
4. Enroll every active administrator, then explicitly set ADMIN_MFA_REQUIRED=true in the application environment. Unenrolled admins can still use the enrollment endpoints but not protected administrator business operations. Do not set the global policy until enrollment is verified.
5. Replace recovery codes using the current password and a fresh TOTP. This invalidates old codes and revokes existing sessions.
6. TOTP secrets are authenticated-encrypted using a purpose-derived key from APP_SECRET_KEY, with the administrator ID as associated data. Changing that application secret invalidates existing MFA encryption and recovery hashes; use a planned maintenance/re-enrollment procedure. Never put secrets or recovery codes into tickets, Sheets or logs.

## Emergency operator recovery

An authorized operator with private infrastructure access and a verified administrator identity may execute, inside the app environment:

    python -m app.operator_recovery --email administrator@example.com

The command prompts interactively for confirmation and a new password; it revokes all account sessions and preserves enrolled MFA. For a lost authenticator, use a saved recovery code with the password. Only after both device and codes are lost and an operator verifies identity, use the additional --reset-mfa flag; this is a privileged break-glass action, revokes sessions, and requires re-enrollment. Never pass passwords as command-line arguments, publish this command as an HTTP endpoint, or use the bootstrap password setting as a password reset. Audit records preserve the recovery action. There is no self-service email password reset or identity-verification service in this phase.

## Privacy workflows and limitations

- New registrations explicitly acknowledge a versioned notice; new website/chat contact consents and AI opt-ins record the version, text and time. Existing historical records retain their original version/timestamp, with missing text left null.
- Customers can export account-owned application data, withdraw contact consent and request correction or erasure. Withdrawal is visible in the lead tracker and external reporting omits withdrawn phone/email even when full reporting is explicitly enabled. New consent for a separate future enquiry is possible.
- A verified erasure request first enters review. An authorized administrator must re-enter their password, affirm legal-retention and external-copy review, and type ERASE ACCOUNT. Erasure deletes the specified account-owned application records and sessions, then replaces identifying account fields with a non-identifying disabled tombstone. It does not identify guest records or third-party referrals by guessing, and it does not delete external provider copies or backups. An account with owned referral records is blocked for separate review. Correction requests require manual handling.
- Before commercial use, designate a responsible privacy contact, establish identity-verification and response procedures, decide and document lawful retention periods by record class, create a process to handle copies with Google/email/AI providers, and review the notice/consent text and workflows with Indian privacy counsel. Do not claim DPDP compliance merely because these endpoints exist. There is no scheduled destructive retention purge. See the official MeitY DPDP materials and applicable commencement notifications.
- Production privacy actions are not tested against real customer records; tests use disposable fixtures only.

## PostgreSQL backups and restore

The backup-restore CI job migrates a disposable PostgreSQL database, seeds synthetic account, property/photo, consent, chat and privacy-request records, takes a custom-format pg_dump, restores it into a separate empty database and verifies records and the migration revision. No production data is uploaded to CI.

This does not establish a backup of the production database. Before deploying:
1. In Railway, open the production Postgres service, then Backups. Check existing backups and schedule. Enable an appropriate daily schedule, subject to storage capacity and costs, and capture an on-demand pre-release backup. Verify the backup actually completes. See https://docs.railway.com/volumes/backups.
2. Choose a documented recovery point and recovery time objective with the owner. Establish encrypted logical backups to a separate restricted storage destination and lifecycle policies reviewed against privacy retention needs. Protect APP_SECRET_KEY and other required configuration separately; never include them in GitHub or public logs.
3. Restore a backup to an isolated database/service using authorized private infrastructure access. Railway's volume restore stages a replacement volume in the original service environment; do not apply it to production simply to run a drill. For an isolated logical restore use pg_dump -Fc and pg_restore --exit-on-error --no-owner --no-acl against an empty target with no production network exposure.
4. Verify migration revision, row counts, property photo retrieval, customer data isolation, authentication and MFA decryption (using the authorized original secret only in the isolated environment), chat history and audit records. Disable outbound AI, Sheets and SMTP during the drill; do not send test messages to customers.
5. Record date, backup identifier, checksum, recovery duration, results and responsible operator. Restrict or destroy the isolated copy according to the approved policy.
6. Do not execute destructive database restores, erasure, or rollback of live inventory automatically. Keep the production database and active release untouched until the owner approves the release reconciliation and the backup gate is satisfied.

## Remaining phases

No dependency lockfile or base-image digest lock, production backup schedule, external error-tracker account, dedicated worker deployment, complete Decimal migration, load-test result, or full architecture refactor is claimed. Plan these in follow-up releases. PostgreSQL concurrency and worker tests must exercise simultaneous transactions rather than merely run single-user tests against PostgreSQL.
