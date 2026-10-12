# Platform owner sees analytics; businesses create campaigns

The platform-owner business list previously opened the same campaign workspace used
by business users. It now routes to OwnerBusinessAnalyticsView, with no creation, edit,
AI-practice or contact-mutation controls. Platform-owner Account also no longer routes
to business profile editing. Business users retain their existing campaign screens.

The new GET `/v1/owner/partners/{id}/analytics` endpoint requires verified owner access.
It counts actual stored contacts, imports, campaign plans and manual follow-up statuses
for that one business. It does not equate manual closed/appointment statuses with verified
sales/bookings. Revenue, direct costs and commission are unavailable, not zero-valued
projections. The owner screen says financial reporting is not connected.

POST `/v1/partners/{id}/campaigns` and campaign/import creation now reject platform owners
with 403. Existing oversight permissions and owner review/hold endpoints are retained;
this change is not a blanket revocation of every existing owner API mutation. Future
worker-role enforcement remains separate work.

## Rollout

Deploy the updated backend first, then build/install iOS manually. No new environment
variables or database migrations are needed for this change, but existing business
application/access-gate migrations must already be applied. No deployment, live mutation,
commit, push or iOS build was performed by Codex.

## Validation

83 backend tests pass, including both owner creation blocks, business creation, owner-only
analytics, business-scoped aggregates and unavailable financial values. Ruff passes.
Standalone macOS Swift checks pass for analytics decoding, loading and authenticated GET
requests. SwiftUI syntax parsing and diff checks pass. Device layout/navigation and live
deployment remain for manual verification.

Sign in as the owner → Businesses → Bob's Roofing: Business analytics should open and
there must be no Create campaign/Edit controls. Check an empty business and one with
contacts/campaigns; confirm metrics match its stored records. Sign in as Bob: the normal
campaign creation/editing flow remains. A direct owner creation request must return 403.

## Commit and push after reviewing

Backend:

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-Backend
git diff
git add app/owner_analytics.py app/main.py app/campaigns.py tests/test_owner_analytics.py tests/test_api.py openapi.json README.md OWNER_ANALYTICS_SETUP.md
git commit -m "Add owner business analytics and restrict campaign creation"
git push origin HEAD
```

iOS:

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-iOS
git diff
git add CrewCrate/CrewCrate/Models/OwnerBusinessAnalytics.swift CrewCrate/CrewCrate/Services/OwnerAnalyticsServicing.swift CrewCrate/CrewCrate/ViewModels/OwnerBusinessAnalyticsModel.swift CrewCrate/CrewCrate/Views/OwnerBusinessAnalyticsView.swift CrewCrate/CrewCrate/Views/BusinessAccessView.swift Tests/CampaignWorkspaceChecks.swift README.md
git commit -m "Route platform owners to read-only business analytics"
git push origin HEAD
```

Review any pre-existing uncommitted changes in these files before staging.
