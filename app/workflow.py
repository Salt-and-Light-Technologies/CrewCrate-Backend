import re
from datetime import datetime, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from .models import Event, Partner
from .schemas import Onboarding, Readiness


def email_valid(value):
    return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def onboarding_issues(data: dict) -> list[str]:
    d = Onboarding.model_validate(data)
    required = [
        "contact_name",
        "business_name",
        "industry",
        "services",
        "service_area",
        "offer",
        "qualification",
        "sales_contact",
        "ai_boundaries",
        "lead_source",
        "eligibility_notes",
        "reporting_system",
    ]
    issues = [f"Complete {field.replace('_', ' ')}" for field in required if not getattr(d, field).strip()]
    if (d.fee_amount or 0) <= 0 and (d.fee_rate or 0) <= 0:
        issues.append("Choose a fee amount or percentage rate greater than zero")
    for field in ("email", "handoff_email"):
        if not email_valid(getattr(d, field)):
            issues.append(f"Provide valid {field.replace('_', ' ')}")
    if any(not email_valid(x) for x in d.team_emails):
        issues.append("Provide valid team emails")
    if not d.audience or any(x not in ("dormant", "unclosed", "customers") for x in d.audience):
        issues.append("Choose valid recovery audiences")
    if d.goal not in ("appointment", "estimate", "handoff", "purchase"):
        issues.append("Choose a valid conversion goal")
    for field in ("website", "booking_url"):
        value = getattr(d, field)
        if value and (urlparse(value).scheme not in ("http", "https") or not urlparse(value).hostname):
            issues.append(f"Provide valid {field.replace('_', ' ')}")
    if d.goal == "appointment" and not d.booking_url:
        issues.append("Provide a booking URL")
    if d.evidence_source not in ("manual", "crm", "payments"):
        issues.append("Choose a valid evidence source")
    if d.compensation not in ("revenue", "appointment", "hybrid"):
        issues.append("Choose valid compensation")
    try:
        ZoneInfo(d.time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        issues.append("Provide a valid IANA time zone")
    if not d.accepts_visibility:
        issues.append("Accept owner visibility")
    if not d.confirms_accuracy:
        issues.append("Confirm setup accuracy")
    return issues


def launch_issues(partner: Partner, import_count: int) -> list[str]:
    issues = onboarding_issues(partner.onboarding)
    if not import_count:
        issues.append("Import lead records")
    readiness = Readiness.model_validate(partner.readiness)
    issues += [
        f"Confirm {field.replace('_', ' ')}"
        for field in type(readiness).model_fields
        if not getattr(readiness, field)
    ]
    return issues


def check_revision(partner: Partner, expected: int):
    if partner.revision != expected:
        raise HTTPException(409, "Record changed; refresh before saving")


def record_event(partner, actor, action, previous, reason, session):
    partner.revision += 1
    partner.updated_at = datetime.now(timezone.utc)
    session.add(
        Event(
            partner_id=partner.id,
            actor_id=actor.user_id,
            action=action,
            previous_status=previous,
            resulting_status=partner.status,
            reason=reason,
            revision=partner.revision,
            timestamp=partner.updated_at,
        )
    )


def apply_decision(partner, action, reason, missing):
    previous = partner.status
    reason = reason.strip()
    if action in ("request_changes", "pause") and len(reason) < 5:
        raise HTTPException(422, "A reason of at least five characters is required")
    if action == "approve":
        if previous != "submitted":
            raise HTTPException(409, "Only submitted onboarding can be approved")
        if missing:
            raise HTTPException(422, detail={"missing_launch_requirements": missing})
        partner.status = "pilot_approved"
    elif action == "request_changes":
        if previous != "submitted":
            raise HTTPException(409, "Only submitted onboarding can receive change requests")
        partner.status = "changes_requested"
    elif action == "pause":
        if previous == "paused":
            raise HTTPException(409, "Partner is already paused")
        partner.paused_from = previous
        partner.status = "paused"
    elif action == "resume":
        if previous != "paused" or partner.paused_from not in (
            "draft",
            "submitted",
            "changes_requested",
            "pilot_approved",
        ):
            raise HTTPException(409, "Partner cannot be resumed")
        partner.status = partner.paused_from
        partner.paused_from = None
    return previous, reason
