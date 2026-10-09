# CrewCrate backend

Use async FastAPI/SQLAlchemy with verified Supabase bearer tokens. Every partner route must enforce owner or explicit partner membership, including imports, leads and history. Owner roles are provisioned outside the API. Never trust client readiness flags for approval; only the owner may attest them. Preserve expected revisions, PostgreSQL row locks and transactionally recorded history. No campaign activation or partner notifications exist in this phase.

Preserve the archived original byte for byte. Use additive cc_* migrations; do not drop legacy tables, auto-create production schema, copy secrets, deploy, or apply live migrations as an incidental build step. The iOS app is still on a demo repository. Read README.md for contracts and known integration gaps. Run local tests before changes are handed back.

## Campaign planning

The user explicitly chose partner self-service campaigns, not mandatory owner approval for every plan. Keep foundational onboarding gates separate. Campaign preparation uses current permissions, opt-outs and revisions. Ready to connect is not launched. Owner holds must be enforced server-side and cannot be released by partners. Contact permission evidence is manually recorded and retained in activity; imports never establish it. Manual conversation tracking is explicitly separate from provider messages/receipts/sales evidence. Messaging provider remains disabled and launch fails without queueing. Do not add a scheduler, live delivery or automatic migration as incidental work. Read README.md for migration order, API contracts and tests.
