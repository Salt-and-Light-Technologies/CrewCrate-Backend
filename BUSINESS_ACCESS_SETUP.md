# Business approval foundation: rollout and review

Current correction: read BUSINESS_APPROVAL_FIX.md before rollout. Membership alone is
no longer sufficient; the additional business access-gate migration is required. The
earlier grandfathering description below has been superseded.

This milestone replaces the iOS signed-in placeholder with a simple application/review
flow. It is the first implementation step of PROJECT_TODO.md, not the finished recovery
platform. No live deployment, database migration, SMS, emails or calendar writes occurred.

## Before device testing

1. Review and apply `supabase/migrations/202610120001_business_applications.sql` to a
   development database after existing CrewCrate migrations. It creates one protected
   cc_* table and index; it does not modify legacy data or auto-run at startup.
2. Deploy the tested backend revision to Render only after the migration is present.
   No new environment variables are needed. Keep the existing Supabase URL/key/database
   configuration. The trusted server role needs access to the new table as for other cc_* tables.
3. Ensure your existing authenticated user has a trusted `cc_owners` record. If you cleared
   all database records/users earlier, create the login again and provision this record
   through the trusted administrator. No client can grant itself owner access.
4. Build and test iOS manually. No iOS/Xcode build was performed by Codex.

## Manual walkthrough

- Sign in as an unassigned business user. Fill in business name, contact name, email,
  phone and optional referral. Submit once. A lost response can be recovered with Refresh:
  retries return the existing application rather than creating another one.
- Check the pending screen and confirm that `/v1/partners` is empty and the old
  self-service business route cannot create a workspace for this user.
- Sign in with the platform owner toggle using a real owner. Requests lists pending
  applications; the badge is the pending count, not an acknowledgment/read counter.
  The app refreshes while active about every 30 seconds, on foregrounding and manually.
  These are persisted in-app request notifications, not push banners or email alerts.
- Review the referral/contact details. Approve or deny with confirmation. Denial requires
  a note visible to the applicant. A stale or already reviewed request returns a conflict.
- Refresh the applicant account. Approval opens only that business's workspace overview.
  Denial shows the note without granting membership. A fresh review after denial is not
  implemented yet.
- Verify another business cannot read the approved business by supplying its ID.
- Select platform owner sign-in with a non-owner account. It must reject owner entry and
  clear the newly established session. Selecting the toggle never creates owner rights.

## Current limits

Existing owner membership provisioning is retained. Invitation tokens/delivery, worker
roles/assignments, campaign UI, conversation inbox, integrations and financial records
remain future implementation steps. Calendar labels explicitly state they are not
connected. Existing memberships without explicit access approval are blocked until
the owner reviews the business; stored records are retained. Owner detection routes a verified owner to the owner experience
even if the optional owner-login toggle was left off. Request review notes are shared
with the applicant; they are not private owner follow-up notes.

Local validation: 80 backend tests pass using SQLite and mocked Supabase; Ruff passes.
Standalone macOS Swift checks pass using fake services with the app's MainActor default;
SwiftUI syntax parsing passes. These checks do not prove production PostgreSQL row locks,
applied RLS, real auth, or device layout. Images, bundle identity and Xcode settings were
not edited by this milestone. Pre-existing uncommitted iOS reset changes remain in place.

## Review and commit commands

Backend (review the diff before committing):

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-Backend
git diff
git add app/applications.py app/main.py app/models.py tests/test_applications.py tests/test_api.py openapi.json supabase/migrations/202610120001_business_applications.sql README.md BUSINESS_ACCESS_SETUP.md TWILIO_SETUP.md
git commit -m "Require platform approval for business signup"
git push origin HEAD
```

iOS has pre-existing reset edits and deletions. Review those together with this feature
before choosing the intended reset hunks to stage; the commands below stage new feature
files and prompt for modified-file hunks rather than silently staging all prior changes.
Ensure the intended removal of old feature sources is included in your reviewed reset
commit so the final committed target matches the working tree.

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-iOS
git status --short
git diff
git add CrewCrate/CrewCrate/Models/BusinessApplication.swift CrewCrate/CrewCrate/Services/BusinessApplicationServicing.swift CrewCrate/CrewCrate/ViewModels/BusinessAccessViewModel.swift CrewCrate/CrewCrate/Views/BusinessAccessView.swift Tests/BusinessAccessChecks.swift
git add -p CrewCrate/CrewCrate/Views/ContentView.swift CrewCrate/CrewCrate/Views/LoginView.swift CrewCrate/CrewCrate/Services/SupabaseSession.swift CrewCrate/CrewCrate/ViewModels/AuthenticationViewModel.swift CLAUDE.md README.md ARCHITECTURE.md
git diff --cached
git commit -m "Add simple business application and owner review screens"
git push origin HEAD
```

No commit or push was performed automatically.
