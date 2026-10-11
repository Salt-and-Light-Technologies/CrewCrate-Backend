# Test AI deployment and device check

This feature sends practice text to OpenAI's Responses API from the backend. All SMS,
email and real handoff delivery remains disabled. It never reads or changes real lead
permission records. Opt-outs, declines and handoffs are simulated and stored only in
cc_ai_test_sessions, not manual conversations, campaign analytics or real outcomes.
Use fictional practice messages rather than real personal contact information.

## Before deployment

1. Keep OPENAI_API_KEY configured on the Render backend service. Never put it in Swift,
   Git, SQL or a client request. API requests use the existing httpx dependency; no new
   OpenAI SDK installation is needed.
2. Optionally set OPENAI_MODEL. Default: gpt-4o-mini (supports strict structured output).
   The model must be available to this API project and support Responses JSON schemas.
3. Apply supabase/migrations/202610110001_ai_test_sessions.sql through Supabase SQL
   Editor after the existing campaign migration. This adds one table with RLS enabled
   and revokes direct client access. The existing trusted backend database role needs
   access as for the other cc_* tables. The migration is not run at startup.
4. Push develop, merge the tested changes to main and push main. Confirm Render's
   deployment is using that main commit; manually deploy the latest commit if needed.
5. Build and run the iOS app manually after deploying the backend.

## Try it in CrewCrate

Log in as a user with an assigned partner workspace. Open Campaigns, then a saved
campaign, then Test AI. Alternatively tap +, select a workspace, fill in a campaign
name, offer and AI instructions, and press Test AI without saving a campaign. Unsaved
settings are captured as a practice snapshot; they do not modify the actual campaign.

The assistant generates the first outreach message. Type replies as a fictional lead.
Try an offer question (continue), a request to speak to sales (simulated handoff),
"No thanks" (campaign ended), and "STOP" (simulated opt-out). Start a New test for each
scenario. Ended, handed-off and opted-out sessions cannot accept additional messages.
Qualification rules determine whether other expressions of interest merit a handoff.
No handoff notification or SMS is delivered, and a handoff is not a sale.

The screen shows the latest decision/reason and factual handoff summary. All decisions
and messages remain recorded on the backend. Each session is scoped to its partner AND
creator; another member or even an owner cannot read that user's practice transcript.
Closing the screen leaves the record on the server; this version has no transcript
history browser. The GET endpoint can retrieve a known session ID for retry/recovery.

## API and limits

POST /v1/partners/{partner_id}/ai-tests accepts exactly one of campaign_id (use saved
server campaign settings) or config (validated CampaignConfig for an unsaved preview).
Server business information always comes from partner onboarding/workspace records.
GET /v1/partners/{partner_id}/ai-tests/{test_id} retrieves the creator's session.
POST .../{test_id}/messages accepts text, expected_revision and a UUID request_id.

Messages are bounded to 2,000 characters, replies to 1,600 characters, each test to 40
lead turns, and each user to 10 new tests per hour. PostgreSQL row locks serialize
session turns. Duplicate request IDs with identical text replay the committed session
without a second model request; reuse with different text returns 409. Failed provider
calls roll back the complete turn. The app retains the turn request ID for retry and
provides Refresh conversation for ambiguous responses/conflicts. Initial test creation
is not idempotent; retrying a lost creation response can create another practice record.

Explicit STOP keywords/requests and clear declines are handled deterministically before
calling OpenAI. Other wording is classified by the model using the fixed policy plus
campaign context. Structured outputs validate the shape and decision enum, not semantic
correctness. Review live practice behavior before any future sending integration.

OpenAI requests have store=false, no tools/delivery capability, a 40-second timeout and
bounded output. store=false does not promise zero provider retention. Missing keys,
rate limits, malformed/incomplete responses and refusals surface safe errors; keys and
raw provider errors are never sent to iOS. Conversations use captured context throughout,
so changing a campaign requires starting a new test to exercise its updated settings.

## Validation

Backend tests use isolated SQLite, mocked auth and mocked OpenAI HTTP. They verify
context, actor/tenant access, stopping decisions, handoffs, retry/revision behavior,
provider failures, quotas and lack of live lead mutations. They do not establish
PostgreSQL locking, applied migrations, Render configuration or real model quality.
Standalone macOS Swift checks validate DTO/service requests; they do not build the
SwiftUI app. Device testing is performed by the user.
