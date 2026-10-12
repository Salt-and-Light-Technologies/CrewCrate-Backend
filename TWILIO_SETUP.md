# CrewCrate: new Twilio account and integration setup

Prepared October 11, 2026. Scope: a US SMS pilot using local ten-digit numbers. Other destinations or sender types need their own onboarding.

## Current state

CrewCrate does not yet implement Twilio sending, inbound webhooks, delivery callbacks or a durable message worker. The existing messaging provider is disabled. Creating an account or adding credentials will not enable SMS. The iOS app currently contains authentication and a logged-in placeholder; campaign UI must also be implemented. This guide separates account setup from required development and deployment.

## 1. Create the platform account

Create an account at [Twilio](https://www.twilio.com/), verify your contact details, enable multifactor authentication, and add billing when ready for registration. Trial accounts cannot register A2P 10DLC. Review number rental, SMS segments, carrier fees and registration charges before purchasing. Configure usage alerts; alerts are not a guaranteed spending cap.

CrewCrate sends on behalf of independent businesses, so use the Independent Software Vendor (ISV) onboarding path. Register CrewCrate's primary customer profile as an ISV; do not register every partner's traffic as CrewCrate's own business traffic.

References: [ISV onboarding](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/onboarding-isv), [registration quickstart and trial limitation](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/quickstart), [US pricing](https://www.twilio.com/en-us/sms/pricing/us).

## 2. Gather the first partner's information

Start with one cooperating business and controlled recipients. Gather its legal business name, applicable tax identification, address, website, business type, industry and authorized contact. Follow Twilio's current requirements for the business's registration category.

Gather its actual SMS use case, sample messages, opt-in process and evidence, and public privacy/terms pages. Describe lead reactivation accurately. An old spreadsheet or an earlier inquiry alone does not establish marketing SMS permission. Do not describe marketing outreach as transactional notifications. Use the real business identity and actual opt-in process, not invented examples submitted as fact.

[Required business and campaign information](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/collect-business-info).

## 3. Separate each partner's sending resources

Use one Twilio subaccount per partner business. In that subaccount, follow the ISV flow to create the partner customer profile, register its brand, and register its A2P messaging campaign. Create a Messaging Service and obtain an SMS-capable local number in the subaccount. Associate the service and sender with the approved registration following the current onboarding flow. Wait for required approvals before production outreach.

A Twilio registration campaign describes a messaging use case; it is not necessarily one-to-one with a CrewCrate campaign. Confirm each CrewCrate campaign fits the registered use case.

Store the partner-to-subaccount/service/number mapping on the backend. Do not let the iOS client choose another business's sender. Keep numbers, credentials, usage and webhook routing attributable to their business.

[Customer account architecture](https://www.twilio.com/docs/messaging/onboarding/build-your-account), [Messaging Services](https://www.twilio.com/docs/messaging/services).

## 4. Keep credentials on the server

Create appropriate API credentials for outbound requests in the partner account context. Retain the relevant account Auth Token securely for validating signed Twilio webhooks. Store secrets in Render's secret environment settings or a backend-accessible secret store; use per-business secret references as the platform grows. Never place secrets in Swift, Git, database migrations, screenshots or chat messages.

The current backend has no Twilio environment-variable contract. Exact variable names and deployment instructions must accompany the implementation; arbitrary new variables currently have no effect. Credential rotation and account suspension must disable the affected sender safely.

## 5. Implement the CrewCrate backend connection

Required development before configuring live callbacks:

- Add a provider adapter for Twilio behind the existing messaging abstraction so Telnyx can later be added without replacing campaign logic.
- Add per-business sender configuration and registration/connection status, protected by platform-admin authorization.
- Add durable outbound jobs, message records and delivery events through additive database migrations. Enforce campaign holds, current recipient permission, opt-outs, test-only exclusions, scheduling and volume limits immediately before dispatch.
- Prevent duplicate sends and handle uncertain provider outcomes without blind retry. An internal idempotency key alone does not establish provider-side deduplication.
- Add an HTTPS inbound webhook and delivery-status webhook. Validate Twilio signatures with the appropriate account token and exact public URL; account for Render's reverse proxy. Deduplicate events and enforce business/sender/recipient routing.
- Persist incoming replies before AI processing. Send AI replies through the same queue and eligibility checks. Stop automation on opt-out, decline or handoff, and support human takeover.
- Record real provider messages separately from existing manual tracking and AI practice sessions. Delivery is not proof of a sale.
- Add platform/business/worker permissions, assigned-campaign access and the iOS messaging screens as separate feature work.

[Webhook security](https://www.twilio.com/docs/usage/webhooks/webhooks-security), [incoming message requests](https://www.twilio.com/docs/messaging/guides/webhook-request), [delivery and inbound webhooks](https://www.twilio.com/docs/usage/webhooks/messaging-webhooks).

## 6. Deploy and configure callbacks

After implementation, run backend tests and lint, apply the reviewed additive migrations, configure server secrets and deploy the verified backend revision. No migration should run automatically at startup. The implementation must provide the exact deployed inbound and status callback URLs; these routes do not exist yet.

In each partner's Messaging Service, configure incoming-message routing to the deployed inbound URL and status reporting to the delivery URL. Ensure number-level settings do not accidentally override service routing. Enable Advanced Opt-Out and configure appropriate business-identifying responses. Persist opt-out events in CrewCrate and cancel queued outreach. Twilio keyword responses must not trigger duplicate AI responses. A START event must not silently override CrewCrate's retained opt-out policy; define and implement re-consent separately.

[Advanced Opt-Out setup](https://help.twilio.com/articles/360034798533-Getting-Started-with-Advanced-Opt-Out-for-Messaging-Services).

## 7. Validate a controlled pilot

Use controlled numbers whose owners have explicitly agreed to the test. Verify outbound delivery, inbound conversation, AI replies, delivery failures, STOP suppression, human handoff, duplicate callbacks, network failure recovery and pause/hold cancellation. Reject unsigned callbacks. Verify two businesses cannot see or send through each other's resources. Confirm test-only imported contacts are blocked from real sending.

Inspect provider logs and CrewCrate records together. Review segment counts and actual charges. Expand only after registration, permission handling, scheduling and failure recovery work end to end. The user builds and tests iOS manually.

## What you can do now

Create the Twilio account, secure it, review billing, begin the ISV onboarding flow, and gather the first partner's business/permission information. Backend integration and exact callback/secret configuration remain implementation work. Do not upload a real dead-lead list and expect account setup alone to make outreach ready.
