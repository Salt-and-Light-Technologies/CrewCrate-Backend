# Shared campaign AI behavior (October 12, 2026)

`app/ai_behavior.py` owns the built-in rules; `ResponsesAgent` always includes them
for campaign preparation and practice turns. Businesses supply profile, offer and
optional name/tone, without copying system instructions. Identity belongs to the
partner, with an honest AI disclosure. Blank names use `AI Assistant`. Each provider
request receives fresh UTC system time; partner time zone, follow-up settings and
available profile fields are scoped to that partner. Missing booking/hours/lead
records remain unknown. Practice is explicitly simulated.

The existing persisted `CampaignConfig.qualification` JSON key now means **optional
additional strong-response signal**, including for existing campaigns. It is no
longer a preparation requirement. No database migration is needed. Default readiness
uses current supported-service interest, service scope/area and enough stated facts
for a human next step; explicit human requests may also route to handoff.

The provider returns independent `default_qualified`, `human_requested`,
`additional_signal`, `signal_evidence` and `signal_reason`. The server promotes
qualified continue decisions to handoff regardless of the extra criterion; missing
handoff summaries fail validation. An unqualified handoff without a human request
is demoted to continue. Opt-out/end decisions take precedence. `strong_response`
is server-derived only for qualified handoffs with a configured matched criterion,
a nonempty reason and an exact quote present in a lead message. Missing, fabricated
or assistant-only quotes become unknown; an extra match never qualifies a lead.
Semantic qualification and criterion matching remain model judgments, not proof.

Practice decisions persist these fields in existing JSON storage and expose them
through the API. The iOS practice outcome displays Strong response, reason and quote.
Old stored outcomes decode with optional/default metadata. Campaign Details includes
an optional name and explains the extra signal; steps two and three retain their flow.

## Integration boundary

SMS launch remains disabled. No real campaign replies, worker inbox/assignment,
notification, scheduling, external suppression or data-deletion execution is added.
Consequently the rules run in every existing agent path (opening preparation and
practice), but there is no launched-campaign delivery pipeline to attach them to.
Future inbound/dispatch integration must use this shared agent policy, enforce actual
permission/suppression and opt-out execution, supply verified scoped lead records and
booking results, and forward strong-response reason/evidence to the receiving worker.
No claim of a completed external action is permitted without verified execution.
Practice opt-outs never modify real leads. Existing manual conversation statuses are
not AI-derived outcomes. No deployment, live migration or real messages were performed.

Verification: 99 local SQLite/mocked-provider backend tests and Ruff pass. Coverage
includes routing independent of unmet/unknown extra criteria, matched evidence,
unqualified matches, blank criteria, invented/assistant quotes, stop precedence,
partner context and persisted API outcomes. iOS builds are excluded by project
instructions; device UI testing and real model/provider behavior remain unverified.

## Review, commit and push when ready

Run from the SaltAndLight project root after reviewing the diffs:

```sh
git -C CrewCrate/CrewCrate-Backend add README.md AI_BEHAVIOR.md app/ai_behavior.py app/ai_tests.py app/campaigns.py app/schemas.py openapi.json tests/test_ai_behavior.py tests/test_ai_tests.py
git -C CrewCrate/CrewCrate-Backend commit -m "Add shared partner AI policy and additive campaign signals"
git -C CrewCrate/CrewCrate-Backend push
git -C CrewCrate/CrewCrate-iOS add CrewCrate/CrewCrate/Models/CampaignWorkspace.swift CrewCrate/CrewCrate/Views/CampaignWorkspaceView.swift CrewCrate/CrewCrate/ViewModels/CampaignWorkspaceViewModel.swift
git -C CrewCrate/CrewCrate-iOS commit -m "Clarify campaign details and show strong response evidence"
git -C CrewCrate/CrewCrate-iOS push
```

## Inclusive campaign dates

`CampaignConfig.start_date` and `end_date` are nullable ISO calendar dates stored in
existing campaign JSON. Both must be present or both absent; start must be on or before
end. No migration or live data rewrite is required. Existing absent/null dates retain
no date restriction. Create, edit, file-import campaign saves and preparation use the
same config schema and retain the range. API/OpenAPI includes nullable date fields.

`app/campaign_schedule.py::campaign_date_eligible` converts an aware system instant to
the selected campaign/business time zone and compares its local date inclusively.
Invalid zones or naive instants fail closed. The launch route evaluates the predicate
immediately before the disabled provider boundary; out-of-range launches return 422
without jobs, sends or status changes. Planning/preparation can occur before the start
or after the end, and AI practice remains a simulation. Date eligibility does not
replace existing permission, holds, hours or limit checks, and is not a send authorization.

There is no active daily scheduler, follow-up dispatcher or weekday-selection field in
this implementation. Future initial/follow-up dispatch MUST reevaluate this central
predicate immediately before each send, in addition to all other eligibility checks;
a launch-time check alone cannot constrain future queued work. Nothing here enables
live daily campaigns. The end date allows its entire local calendar day, with sending
hours still applying when delivery is implemented. DST follows the IANA zone rules.

Validation: 115 backend tests pass, including inclusive Monday-to-Monday and same-day
ranges, before/after dates, DST and UTC/business-date differences, invalid/partial
ranges, no-range compatibility, persisted create/edit round trips and expired/future
launch rejection. Standalone Swift checks pass for date serialization/legacy decoding,
picker date preservation across time-zone changes, equal/reversed ranges and saving.


## Designated AI identity

The iOS designated AI picker is the sole identity editor; step one no longer contains
a name text field. Its selected value uses the existing `ai_name` wire property, which
feeds campaign opening and new practice-session context. There is no separate AI-profile
ID in the current model. Shared rules explicitly prevent alternate names in tone,
profile, offer or lead text from overriding it. Legacy custom names are visible saved
picker choices; null/blank falls back to AI Assistant in both UI and backend. Existing
practice sessions retain their original context snapshot until a new session is started.
