# Worker onboarding and notifications

Active implementation: this backend and `../CrewCrate-iOS`. `../RebuiltCrewCrate` and archives are historical. No applicable AGENTS.md was present. No live migration, deployment, external invitation or push was performed.

## Business team flow

Business administrators open Account → Manage business team, enter a worker email, create a code and share it using the device share sheet. This is manual delivery; no invitation email adapter exists. Creating a code does not contact the recipient. The code is shown only in the creation response and stored server-side as a SHA-256 digest. A new invitation for the same email/business revokes older unused codes. Codes expire after seven days and may be revoked.

Workers create their own account using the worker option in signup, verify their email through the existing Supabase flow, sign in and open Accept worker invitation. Acceptance requires the exact verified recipient email supplied by Supabase's authenticated `/auth/v1/user` response, a valid unused code and an approved business. Workers enter their name during acceptance. A pending business application does not block an independently authorized worker membership. Acceptance does not approve that application or create a new business.

Membership is explicitly `admin` or `worker`, with an active flag. Existing memberships retain administrator status; review those records before rollout if historical accounts were ever intended as workers. New invitations always create workers and never downgrade an administrator. Platform-owner oversight is separate from business team management. Removed workers lose team data, incoming leads, alerts and device registration privileges; pending pushes are checked again before dispatch. Administrators can issue a fresh invitation to restore a removed worker.

Workers open Incoming leads first. Interested, handoff and appointment records for their business appear with contact details and internal conversation notes. They can record follow-up/close a record using revisions, but cannot edit business information, imports, contact permission, campaigns, launch, practice, team settings or owner analytics. Campaign assignments and real job-outcome reporting remain future work. The current inbox is **manual tracking**, not received SMS: no customer messages are fabricated, and internal notes do not send SMS. Live inbound SMS, real AI handoff and human reply delivery remain provider-integration work.

## API

- GET `/v1/partners` adds `member_role`, filters inactive members and redacts worker business setup/readiness details.
- GET `/v1/partners/{id}/team`; POST `/worker-invitations` with email; DELETE `/worker-invitations/{invitation_id}`; DELETE `/workers/{user_id}`. These require that business's administrator.
- POST `/v1/me/worker-invitations/accept` with token/display_name.
- GET `/v1/partners/{id}/incoming-leads` with limit/offset; GET `/{conversation_id}`; POST `/{conversation_id}/notes` with expected_revision, note and status. Every request checks approved active business membership and record scope.
- GET `/v1/me/worker-notifications` with limit/offset; POST `/{id}/read`. Alerts belong to the verified recipient and remain on the server across app closure.
- POST `/v1/me/worker-devices` with token/environment; DELETE `/{token}`. Active workers only. Device token registration is serialized and binds the token to the current account. Jobs recheck that binding and membership before sending. Device registration confers no business access.

All active workers in the business receive a durable alert when a conversation enters interested/handoff from another state. Interested → handoff does not produce a second alert. A later closed → interested transition is a new event. `(worker, conversation, revision)` and `(notification, device)` uniqueness prevent duplicate fan-out. The record transition and fan-out commit together. `notify_interested` is the integration hook for future verified real inbound interest/human-contact events; practice sessions never call it. Both administrator manual status changes and worker manual status changes use the hook.

## Rollout

1. Review/apply `supabase/migrations/202610120003_worker_onboarding.sql` to development PostgreSQL after `202610120002_business_access_gate.sql`. It adds only cc_* fields/tables, enables RLS and revokes direct client access. API/consumer use the existing trusted server database role. Never apply migrations automatically at startup.
2. Install dependencies and deploy the API. `openapi.json` contains the regenerated contract.
3. Enable Push Notifications for `com.CrewCrate.CrewCrate` in Apple Developer, regenerate provisioning profiles, and create an APNs P-256 `.p8` authentication key. The iOS target includes capability/entitlement support: Debug uses development/sandbox, Release uses production. No signing identity or bundle identity was changed.
4. Store `APNS_KEY_ID`, `APNS_TEAM_ID`, `APNS_TOPIC=com.CrewCrate.CrewCrate` and `APNS_PRIVATE_KEY` (actual multiline PEM text) as server secrets. The key must never enter the app, repository, logs or chat. API does not require the key; the separate consumer does. `.env.example` has blank placeholders.
5. Create a separate background worker with the same backend source, `pip install .` build command and `python -m app.worker_push` start command. Supply the database/Supabase settings and APNs secrets. `render.worker.yaml.example` supplies a reviewable service configuration; it has not been applied. No worker deployment was created here. One consumer is sufficient initially; PostgreSQL SKIP LOCKED supports competing consumers, which still need staging verification.
6. Test two businesses and at least two active workers per business on development devices. Validate wrong-email/unverified/expired/reused codes; removal; cross-business IDs; concurrent acceptance and revision conflicts; foreground silence/notice/badge; background/closed notifications; tap routing; denied permission; logout/account switching; invalid tokens and retry recovery. SQLite/mocks do not establish PostgreSQL locking, deployed RLS, provisioning or device behavior.

The consumer uses HTTP/2 and ES256 provider tokens cached for 50 minutes. It queues one job per currently registered active device, sends generic content without lead PII, retries network/429/503/500/authentication failures with backoff, disables 410 tokens and cancels jobs if read, expired after one day, membership removed, business unapproved, device inactive or token reassigned. Stable APNs IDs/collapse IDs limit retries; exactly-once device delivery is not promised. `accepted` means APNs accepted the request, not that the worker saw it. Check pending/failed job rows operationally; no push monitoring dashboard exists yet.

In foreground, Apple's system presentation is suppressed entirely, and a small SwiftUI notice plus unread badge opens the conversation. Polling every 15 seconds also supports foreground alerts when push permission is denied. Background/closed alert pushes request the default sound and a badge; actual presentation respects device permissions/settings. No critical-alert or permission bypass exists. Tap payloads are resolved through authenticated alert lists and scoped conversation endpoints. Opening a conversation marks its recipient alerts read. Logout unregisters the current token before clearing authentication; if unregister fails, retry logout while connected to avoid leaving a shared-device binding active.

Apple references: [APNs requests](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns), [notification handling](https://developer.apple.com/documentation/usernotifications/handling-notifications-and-notification-related-actions).

## Verification

136 backend tests pass, including ten worker/push cases. Coverage includes verified recipient binding, token hashing/rotation/expiry/reuse, administrative denial, business isolation, revocation, revisions, all-worker fan-out, recipient read authorization, deduplication, ES256 signature verification/cache reuse, mocked APNs payloads/retries/invalid tokens, disabled configuration and cancellation after device reassignment/removal. Ruff and diff checks pass. No live APNs request was made.
