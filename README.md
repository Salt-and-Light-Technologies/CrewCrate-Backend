# CrewCrate Revenue Recovery API

Active backend for the CrewCrate rebuild, reusing the original FastAPI, Supabase Auth/Postgres and Render foundation. The archived original is a reference and remains untouched. This version is local code; no deployment or database migration has been performed.

## Run locally

1. Use Python 3.12 or newer, create a virtual environment, and install `pip install -e '.[dev]'`.
2. Copy `.env.example` to `.env` and supply the existing project's database connection, Supabase URL and publishable/anon key. Existing `SUPABASE_ANON_KEY` remains accepted. Use a development database first. No service-role key or Storage bucket is needed for this phase.
3. Apply `supabase/migrations/202610080001_revenue_recovery.sql` once to that development database. It adds only `cc_*` tables. It does not drop, rewrite or migrate the original CRM tables or records. Re-running deliberately fails rather than silently accepting incompatible tables.
4. Bootstrap your owner account manually through the trusted database administrator: `INSERT INTO cc_owners (user_id) VALUES ('YOUR_EXISTING_SUPABASE_AUTH_USER_UUID');`. There is no API for self-elevation. IDs must belong to existing Supabase Auth users. Create partner accounts in Supabase Auth before assigning them to a partner.
5. Run `uvicorn app.main:app --reload`; inspect `/docs` for the complete OpenAPI contract.

Keep database credentials on the server. The migration enables RLS and revokes public/anon/authenticated access to the new tables. Mobile clients use the API, not direct Supabase table access. The API's trusted database role must have the required table privileges and RLS bypass (e.g. the existing server's postgres role); do not give that role to clients. No RLS policies grant direct client access.

## Authentication and isolation

The iOS client sends the existing Supabase JWT as `Authorization: Bearer ...`. The API verifies it using Supabase `/auth/v1/user` with an asynchronous HTTP client and a timeout; it does not trust decoded claims or accept a user ID header. Owner elevation comes from the protected `cc_owners` table. Partner membership is checked for every partner route; unauthorized partners return 404. Only the owner can provision partners, attest launch readiness, decide reviews or read the global overview. Existing CRM workspace membership is not automatically migrated to partner ownership.

## Workflows

- `GET /v1/me`: verified user ID and owner flag.
- `POST /v1/partners`: owner creates a partner and assigns its existing Supabase user.
- `GET /v1/partners`: owner sees all partners, other users see their memberships; paginated, with optional status filter.
- `GET /v1/partners/{id}`: setup, readiness, revision and missing launch requirements.
- `PUT /v1/partners/{id}/onboarding`: save draft/change-request edits. Full typed replacement; unknown onboarding fields are rejected. Edits clear readiness attestations.
- `POST /v1/partners/{id}/submit`: requires valid universal setup and confirmations. Imports may follow during an editable stage before submission; launch approval requires stored lead records.
- `PUT /v1/partners/{id}/readiness`: owner records four manual attestations with a reason; they are not automated verification or signed agreements.
- `POST /v1/partners/{id}/decisions`: approve a complete submitted pilot, request changes, pause, or resume its previous status. Pauses/change requests require a reason. Approval records a status and sends no messages or campaigns.
- `GET /v1/partners/{id}/history`: append-only API history including actor, timestamps, revisions and reasons. It also records draft saves, submissions, readiness reviews, imports and lead classifications. Database administrators still have database-level access; this is not a tamper-proof external ledger.
- `POST /v1/partners/{id}/imports`: multipart CSV plus phone/name/email column mapping and expected partner revision. Imports only in draft/change-request status. Maximum file 5 MB, 20,000 rows, request body 6 MB. Unique headers, quoted fields and UTF-8/BOM supported. Reject malformed row structure. Store normalized contacts and immutable batch totals; discard original upload. Deduplicate phone digits within and across batches per partner. Numbers are plausible digit strings, not validated international numbers, reachability or consent. All leads start unreviewed.
- `GET /v1/partners/{id}/imports` and `/leads`: paginated records scoped to the partner.
- `PATCH /v1/partners/{id}/leads/{lead_id}`: classify unreviewed/eligible/excluded, with lead revision and partner history. Classification alone does not send messages or establish consent.
- `GET /v1/owner/overview`: measured stored partner-status, lead-status and import counts. No invented revenue or campaign metrics.

Mutation bodies include `expected_revision`. PostgreSQL row locks serialize partner changes; stale clients receive 409 and must refresh. Lead mutations use a lead revision. Workflow mutations and their history commit in the same transaction. History/read lists default to 50 and cap at 100; clients paginate using offset. Creating partner records has no idempotency key yet; refresh after an ambiguous network failure before retrying creation.

## iOS adapter contract

The current iOS app remains on its labeled local demo repository. Connecting it is a separate phase. Implement `PartnerRepository` with these endpoints at the app composition root, inject a Supabase session/token provider, and preserve server conflicts/errors. Explicitly map snake_case fields, UUIDs, ISO8601 timestamps, and statuses: `draft` -> Draft, `submitted` -> Awaiting review, `changes_requested` -> Changes requested, `pilot_approved` -> Pilot approved, `paused` -> Paused. Decision actions are `approve`, `request_changes`, `pause`, `resume`. UI raw titles are not API wire values. Server history includes setup/import events beyond the current demo model's four review actions; extend the adapter/domain event mapping before integration. CSV inspection summaries in the app are not server imports and must not satisfy server approval gates.

## Render and migration rollout

`render.yaml` retains service name, health route and Python deployment commands. It is configuration, not proof of a live connection. The active backend is now outside the iOS repository: confirm Render's connected repository/root directory points here before deploying. Existing Render secrets must be reviewed in Render rather than copied into source. Additional legacy environment variables are ignored. Do not automatically apply migrations at startup.

Before production, validate the migration and row-lock conflicts on development PostgreSQL/Supabase, verify a real owner and two isolated partner sessions, and review deployment settings. The migration and rollout are not executed by this build. No legacy CRM routes are exposed by the new service; plan any required old-client compatibility separately.

## Validation and remaining scope

Run `pytest` and `ruff check .`. Tests use an isolated SQLite database and mocked Supabase verification, with no live credentials or services. They cover access boundaries, review gates, revisions, readiness reset, import counts/deduplication, malformed files, lead scope, history, upload size, invalid auth and auth-service failures. SQLite tests do not prove PostgreSQL row-lock behavior or deployed Supabase/RLS configuration.

Future work: iOS backend integration, account invitations/team management, international contact normalization and consent evidence, campaign/messaging services, conversation events, sales evidence/reconciliation and revenue attribution. No production campaigns, agreements or financial verification are implemented here.

## Lead-management API additions

This phase adds `POST /v1/partners/{id}/imports/preview`, returning current `partner_revision`, total/new/duplicate/invalid counts, row-level issues (first 100) and an `issues_truncated` flag. Record numbers count nonblank data records and exclude the header. Preview stores no leads, import batches or events. Commit reparses the same document and requires the preview's revision. Changes after preview produce 409. Mapping must use distinct columns; trimmed headers are unique case-insensitively. Optional provided email values must have a plausible email format. File limits now use 5,000,000 bytes consistently with iOS; request envelope remains bounded at 6 MiB.

`GET /v1/partners/{id}/lead-workspace` returns the name/status, revision, actual stored lead totals and status counts plus import count. `GET /v1/owner/lead-workspaces` provides paginated per-partner summaries for the owner. `GET /v1/partners/{id}/lead-activity` returns paginated import/classification events. Lead list GET now accepts optional `status` and `search` (name/phone/email, literal search with SQL wildcard escaping), while retaining tenant scope and limit/offset pagination. Backend lead states are `unreviewed`, `eligible`, `excluded`. These APIs do not verify consent, reachability or sales outcomes. Import batches with zero new contacts remain visible so repeated submissions and data-quality problems can be audited.

No new database tables or migrations are required for this phase beyond the existing un-applied cc_* migration. 18 local tests pass, including preview read-only behavior, counts, cross-import deduplication, stale preview rejection, per-partner totals, literal search, filters, scope and owner-only summaries. The iOS HTTP adapter matches these endpoints but uses local demo composition until connection settings and authentication are available. PostgreSQL/Supabase and device checks remain outstanding before live rollout.

## Campaign and conversation phase (API 0.3.0)

Apply `202610080002_campaign_planning.sql` after the initial cc_* migration in a development database first. It adds four planning/tracking tables plus permission/evidence/opt-out columns to the new cc_leads table. Legacy CRM tables are untouched. RLS/direct-access revocations apply to the new tables too. Neither migration has been applied by this build.

Partners manage their own campaigns after foundational partner setup has been approved. There is no per-campaign owner approval endpoint or required owner-review state. Routes under `/v1/partners/{partner_id}/campaigns` support list/create, GET/PUT detail, POST `/prepare`, POST `/control`, and GET `/history`. `/v1/owner/campaigns` provides paginated cross-partner oversight. Revision checks, partner row locks and transactionally appended campaign events protect changes. Saving resets preparation. Preparation checks current recipient eligibility, recorded SMS permission/evidence, opt-outs, setup readiness and plan validity. Limits are up to 500 distinct recipients, 100 contacts/day, three planned follow-ups, a 9–17 planned window and a 480-character first-message draft. These are planning constraints; no scheduler runs, no segments are estimated and no actual sends occur.

Owner pause sets `owner_hold`; partners cannot resume, edit or archive an owner-held campaign. Owner resume clears the hold and returns it to draft. Partner-created pauses can be resumed by that partner. Global partner pause also blocks preparation. GET detail reports `needs_recheck` and blockers when partner or recipient revisions differ from the prepared snapshot. An owner hold applies to that campaign; use the existing global partner pause when all partner activity needs to stop.

`PUT /v1/partners/{id}/leads/{lead_id}/sms-permission` requires lead revision, `permission` recorded/revoked and an evidence reference/reason of 8–2,000 characters. Revocation makes opt-out permanent through this endpoint. Imported contacts default to unknown permission. Current fields and append-only partner/lead activity retain attribution and evidence references. These are manual assertions, not verified consent or provider suppression data.

Campaign `/conversations` GET/POST and `/conversations/{id}` PATCH support manual follow-up tracking with scoped recipients, revision checks and internal notes; `/conversations/{id}/history` exposes actor/timestamp/status notes. All responses mark source `manual_tracking`. Status names new/interested/appointment/handoff/closed do not prove an incoming reply, booking, handoff completion, sale or payment. The API never fabricates those events. Archived/paused campaigns block tracking changes.

`GET /v1/messaging/capabilities` reports all delivery/inbound capabilities false. Campaign POST `/launch` fails with 503 and queues/sends nothing, even for a prepared campaign. `app/messaging.py` provides an integration protocol and a disabled implementation that always raises. There are no delivery jobs, SMS credentials, webhook integrations or outbound provider calls.

24 local backend tests pass, including partner self-service, owner-only hold release, literal tenant scope, permissions/opt-outs, stale records, readiness invalidation, limits, manual tracking/history and fail-closed provider behavior. Tests use SQLite/mocked Supabase rather than a deployed database. Both migrations, PostgreSQL locking and live credentials remain unvalidated externally. `openapi.json` includes the complete current contract and matches the iOS adapter. The iOS app still uses the labeled demo composition.

Provider integration is the next boundary: authenticated app composition plus tested development migrations, then dispatch-time eligibility/permission/suppression checks, timezone/volume scheduling, idempotent sends, provider opt-out ingestion and signed inbound/delivery receipts. Review the rollout before any production migration or delivery enablement. Revenue attribution remains separate until actual sales/payment evidence exists.
