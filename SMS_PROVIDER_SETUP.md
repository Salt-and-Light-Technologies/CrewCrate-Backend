# Recipient review and SMS connection boundary

## What is implemented

1. Create or edit a campaign, upload the lead file and save it.
2. Set the campaign timezone and start/end times using pickers. Tap the daily limit to edit it directly. The AI creates the opening SMS from the business and campaign information during preparation.
3. Open the saved campaign and choose **Review and select leads**.
4. Choose current campaign leads, all business leads, or an original import source. Reused duplicate contacts retain their original import source; the current campaign list includes attached duplicates.
5. Leads load 250 at a time. Use **Load more** to view the next 250. Eligibility and import-source filters apply to the entire list on the server and restart loading at the first page. Submit a search using the keyboard Search button; clearing it reloads the list. Selections persist across pages and filters. **Select eligible leads shown** selects only the currently loaded matching leads. Review each unknown contact. Record actual permission evidence, its source, consent date and recipient country code; attest that business-specific marketing SMS permission and restrictions have been reviewed. Alternatively exclude the contact or record an opt-out.
6. Filter to contacts with recorded permission, select the desired contacts and save eligible recipients. Up to 10,000 recipients per campaign. Filtering alone does not change the saved selection; save the selection explicitly.
7. Choose **Check campaign readiness**. The backend generates an opening SMS using the existing Responses API agent, validates the business name and opt-out instruction and saves the result for preview. Each successful preparation regenerates it from the current campaign details. Invalid or unavailable AI responses leave preparation unchanged. Existing business onboarding, owner readiness and pilot approval gates still apply; blockers are shown. Changing recipients resets preparation.
8. Choose **Start messages** after preparation succeeds. The backend rechecks current permissions and revisions, then returns `SMS provider connection required. Nothing was queued or sent.`

Unknown/revoked permission, opt-outs, manual exclusions, incomplete evidence and malformed normalized numbers fail eligibility. Opted-out contacts cannot be re-enabled through this review flow. Legacy recorded permission evidence remains supported. Imports never establish consent. JWT authorization, workspace membership, expected revisions, row locks and audit events are preserved.

## Limits of these checks

This is a recorded-permission review, not automated legal clearance. CrewCrate does not verify the evidence against its source, query external DNC/reassigned-number databases, infer recipient location, or validate consent against every jurisdiction. Phone checks validate normalized shape, not ownership, deliverability or country. The country code is review metadata. Campaign timezone is not recipient-local legal quiet-hour screening. Confirm recipient countries and the actual consent process before enabling live sending.

No provider credentials, send queue, scheduler, inbound SMS handler, delivery receipt handler or live AI SMS loop is enabled. No database migration is needed for this change; existing migrations must already be applied. The next implementation must add the provider and delivery machinery together; adding an API key alone does not enable SMS.

## Provider options

- **Twilio**: Programmable Messaging, sender registration, opt-out handling and an optional Compliance Toolkit. The toolkit currently supports US-terminating English SMS and includes consent, reassigned-number and quiet-hour checks. This is the recommended first option for a US English pilot; it does not establish consent on behalf of the business or cover every legal requirement. https://www.twilio.com/docs/messaging/features/compliance-toolkit
- **Telnyx**: SMS API with messaging profiles and automatic stop-word suppression. https://support.telnyx.com/en/articles/1270091-sms-opt-out-keywords-and-stop-words
- **Plivo**: SMS API with opt-out handling and US sender compliance documentation. https://www.plivo.com/docs/faq/messaging/messaging-compliance

Compare destination-country coverage, sender registration, partner/business onboarding, number rental, message segment charges, carrier fees, screening options and support before choosing. No provider is connected yet.

## Next implementation after choosing a provider

Register the sending businesses/use cases and provision authorized numbers. Keep credentials on Render. Bind each sender to its business. Implement durable message jobs, idempotency and explicit launch history, fresh per-recipient permission/opt-out/hold checks immediately before dispatch, recipient-local restrictions and daily limits. Verify webhook signatures, deduplicate inbound events, persist provider message IDs and delivery receipts, and suppress further outreach for opt-outs/declines/handoffs. Connect inbound conversations to the campaign AI separately from Test AI. Handle uncertain send outcomes without blind retries. Test with controlled opted-in numbers before production enablement. Provider-specific account and Render instructions will follow the selected provider.

## Commit and deploy

Backend (run each command in order; merge stops if there is a conflict):

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-Backend
git switch develop
git add app/main.py app/campaigns.py app/ai_tests.py app/schemas.py app/contact_policy.py tests/test_api.py tests/test_contact_review.py tests/test_spreadsheet_imports.py openapi.json README.md SMS_PROVIDER_SETUP.md
git commit -m "Support 10000 campaign leads with paginated eligibility filtering"
git push origin develop
git switch main
git pull --ff-only origin main
git merge --no-edit develop
git push origin main
```

In Render, open CrewCrate API, confirm main is the deployment branch, then **Manual Deploy → Deploy latest commit** if auto-deploy has not started. Wait for Live. No new environment variables or migrations are required. Deploy the backend before testing the updated app.

```sh
cd /Users/jamesmcdougall/Desktop/Dev/SaltAndLight/CrewCrate/CrewCrate-iOS
git add CrewCrate/CrewCrate/Models/AuthenticatedIdentity.swift CrewCrate/CrewCrate/Services/AuthenticationServicing.swift CrewCrate/CrewCrate/Services/SupabaseSession.swift CrewCrate/CrewCrate/ViewModels/SavedCampaignViewModel.swift CrewCrate/CrewCrate/ViewModels/CampaignDetailsViewModel.swift CrewCrate/CrewCrate/ViewModels/RecipientReviewViewModel.swift CrewCrate/CrewCrate/Views/CampaignDetailsView.swift CrewCrate/CrewCrate/Views/SavedCampaignDetailsView.swift CrewCrate/CrewCrate/Views/RecipientReviewView.swift Tests/AuthenticationChecks.swift
git commit -m "Load campaign leads in 250 contact pages"
git push origin main
```

Preexisting Xcode project edits are excluded. Build/test iOS manually: review an unknown contact, exclude one, record an opt-out, save only permitted recipients, prepare, then confirm Start reports the provider boundary. Existing business setup blockers must be resolved through the existing onboarding/owner flow.
