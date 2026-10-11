"""Recorded-permission eligibility. This is not external legal/DNC verification."""


def exclusion_reasons(lead):
    reasons = []
    if lead.opted_out:
        reasons.append("Contact opted out")
    if lead.sms_permission == "revoked":
        reasons.append("SMS permission revoked")
    elif lead.sms_permission != "recorded":
        reasons.append("SMS permission has not been recorded")
    if lead.sms_permission == "recorded" and len(lead.permission_evidence.strip()) < 8:
        reasons.append("SMS permission evidence is missing or incomplete")
    if lead.status != "eligible":
        reasons.append(
            "Contact has not passed review"
            if lead.status == "unreviewed"
            else "Contact excluded from outreach"
        )
    if not lead.phone.isascii() or not lead.phone.isdigit() or not 7 <= len(lead.phone) <= 15:
        reasons.append("Invalid normalized phone number")
    return reasons


def eligible_expression(model):
    """SQL equivalent of exclusion_reasons, applied before pagination."""
    from sqlalchemy import and_, func

    remaining = model.phone
    for digit in "0123456789":
        remaining = func.replace(remaining, digit, "")
    return and_(model.opted_out.is_(False), model.sms_permission == "recorded",
                func.length(func.trim(model.permission_evidence)) >= 8,
                model.status == "eligible", func.length(model.phone).between(7, 15), remaining == "")
