# Campaign and account deletion

In Campaign details, open the (…) menu and choose Delete campaign. Confirmation explains that campaign details, conversations and associated practice sessions are permanently removed; business contacts and opt-outs are preserved. The endpoint checks JWT, partner access, the expected revision and owner holds. A business activity entry records the deletion.

In Account, choose Delete account and confirm. This deletes the authenticated Supabase login, removes memberships and owner access through foreign-key cascades, deletes that user's practice sessions, and anonymizes activity actor references. Shared business records, campaigns, imports and permission records remain. Users cannot specify another account ID. Deletion and application cleanup run in one PostgreSQL transaction. Existing tokens fail subsequent Supabase user verification. The app clears its session and returns to login after success.

The backend database connection must have permission to delete from auth.users. The existing PostgreSQL connection is used; no service-role key is added to the app. If database permissions or other foreign keys prevent deletion, the transaction rolls back. Verify this on a disposable account after deployment; SQLite tests do not verify Supabase auth-table permissions, cascades or token invalidation.

## Apply migration before deploying

Open Supabase → SQL Editor. Paste the complete contents of `supabase/migrations/202610110001_account_deletion.sql` and run it once. It retains business records and changes login/history foreign keys to support account deletion. No live migration was applied by Codex.

## Backend commit and deployment

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-Backend
git add app/deletion.py app/main.py app/models.py app/schemas.py tests/test_api.py openapi.json supabase/migrations/202610110001_account_deletion.sql DELETION_SETUP.md
git commit -m "Add authenticated campaign and account deletion"
git push origin main
```

In Render, open CrewCrate API → Manual Deploy → Deploy latest commit (or wait for automatic deployment). Confirm the deployed commit matches your push. No new environment variables are required.

## iOS commit

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-iOS
git add CrewCrate/CrewCrate/Services/AuthenticationServicing.swift CrewCrate/CrewCrate/Services/SupabaseSession.swift CrewCrate/CrewCrate/ViewModels/AuthenticationViewModel.swift CrewCrate/CrewCrate/ViewModels/SavedCampaignViewModel.swift CrewCrate/CrewCrate/Views/AccountView.swift CrewCrate/CrewCrate/Views/SavedCampaignDetailsView.swift CrewCrate/CrewCrate/Views/CampaignsView.swift
git commit -m "Add campaign and account deletion confirmations"
git push origin main
```

Build the iOS app yourself after backend deployment. Test canceling both confirmations, deleting a disposable campaign, and deleting a disposable user. Confirm campaign removal refreshes the list and user deletion returns to login and prevents signing in. Do not test account deletion with your administrator account.

Validation: 76 backend tests passed, Ruff passed, standalone macOS Swift checks passed, and SwiftUI syntax parsing passed. No iOS build was performed.
