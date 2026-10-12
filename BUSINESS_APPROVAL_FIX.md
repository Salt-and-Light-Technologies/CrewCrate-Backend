# Business signup must await owner approval

## Fixed locally

An existing membership previously opened business screens without checking platform
approval. The backend also allowed membership-based access without an independent
business-access gate. Those paths are now closed:

- Each business has `access_approved`, default false.
- Only trusted owner provisioning or an owner application approval sets it true.
- Unapproved businesses are excluded from the member's list and blocked on partner routes,
  including direct requests for campaigns, leads, imports and profile edits.
- iOS checks the business application first and shows Waiting for owner approval for
  pending requests. Memberships missing an explicit approval flag cannot open screens.
- An existing self-service workspace can receive a pending request. Approving reuses it
  and preserves its stored data. A rejected request does not grant access.

Creating a Supabase login is necessary for authentication and reviewing that user's
request; it does not grant operational app access. Email verification is separate from
owner approval. With email confirmation enabled, the saved signup request is submitted
on the first verified login, then appears in the owner's Requests inbox. Without email
confirmation, authenticated signup enters that request/pending flow immediately.

## Required live rollout

These changes are local. No live migration, account change, deployment or iOS build was
performed. The live deployment could not be inspected through the web tool.

1. Ensure the existing CrewCrate migrations are applied. Apply
   `supabase/migrations/202610120001_business_applications.sql` if it has not already been
   applied. Do not re-run an already applied CREATE TABLE migration.
2. Review and apply `supabase/migrations/202610120002_business_access_gate.sql` once.
   It adds the gate and preserves explicit recorded application approvals. Other legacy
   businesses remain blocked for business users until owner review. No records are deleted.
3. Deploy the updated backend to Render after the migrations. Existing server environment
   settings suffice. No migrations should run at startup. Verify the deployed revision.
4. Build/install the updated iOS app manually.
5. Verify `james.mcdougall@saltandlighttechnologies.com` is assigned a `cc_owners` record,
   using PLATFORM_OWNER_SETUP.md. Creating the auth account alone does not grant that role.

## Bob's test

Log out and log in as `bob@bobsroofing.com`. A valid saved signup draft becomes a pending
request. If that older account has no saved business details, complete Request access
once. It must not open campaigns. Then sign in as James, open Requests, inspect Bob's
request and approve or deny it. Bob remains blocked after denial; approval opens only
Bob's workspace after refresh. Test a direct campaign/lead request before approval and
confirm a different business still receives no access after approval.

Validation: 81 mocked/SQLite backend tests passed, including a legacy-membership bypass
regression and owner approval preserving the existing workspace. Standalone Swift state
checks cover pending and missing-approval memberships. SQLite does not establish live
PostgreSQL locking or the migration's applied state.

## Commit and push after reviewing

Backend:

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-Backend
git diff
git add app/auth.py app/main.py app/models.py app/schemas.py app/applications.py tests/test_applications.py openapi.json supabase/migrations/202610120002_business_access_gate.sql README.md BUSINESS_ACCESS_SETUP.md BUSINESS_APPROVAL_FIX.md
git diff --cached
git commit -m "Block business access until platform owner approval"
git push origin HEAD
```

Ensure the previously added business-application endpoint/migration files are also part
of the deployed history; follow BUSINESS_ACCESS_SETUP.md for staging the earlier work.

iOS: review the uncommitted reset and prior feature work before staging. New foundation
files may need `git add` before interactive hunk staging if not already tracked.

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-iOS
git status --short
git add -p CrewCrate/CrewCrate/Models/BusinessApplication.swift CrewCrate/CrewCrate/ViewModels/BusinessAccessViewModel.swift CrewCrate/CrewCrate/Services/BusinessApplicationServicing.swift CrewCrate/CrewCrate/Views/BusinessAccessView.swift CrewCrate/CrewCrate/Views/SignupBusinessView.swift Tests/BusinessAccessChecks.swift Tests/SignupChecks.swift README.md
git diff --cached
git commit -m "Keep business signup pending until owner approval"
git push origin HEAD
```

No commit or push was performed automatically.
