import csv
import io
import json
import re
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import HTTPException

from .contact_policy import exclusion_reasons

MAX_BYTES = 5_000_000
MAX_ROWS = 20000


@dataclass
class ParsedCSV:
    contacts: list[dict]
    total: int
    invalid: int
    issues: list[dict]
    raw_rows: list[dict]


def parse_csv(raw: bytes, phone_column: str, name_column: str | None, email_column: str | None, permission_columns: str = "{}", permission_attested: bool = False):
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "CSV exceeds 5 MB")
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        headers = [x.strip() for x in next(reader)]
        if (
            not headers
            or len(set(x.casefold() for x in headers)) != len(headers)
            or any(not x for x in headers)
        ):
            raise ValueError("CSV requires unique nonempty headers")
        permissions = json.loads(permission_columns)
        if not isinstance(permissions, dict) or any(k not in {"permission", "evidence", "optout", "test"} or not isinstance(v, str) for k, v in permissions.items()):
            raise ValueError("Invalid permission mapping")
        mapping = [phone_column] + [x for x in (name_column, email_column) if x] + [v for v in permissions.values() if v]
        if any(x not in headers for x in mapping) or len(set(mapping)) != len(mapping):
            raise ValueError("Choose distinct mapped columns")
        contacts, invalid, total, issues, raw_rows = [], 0, 0, [], []
        for row in reader:
            if not row or not any(x.strip() for x in row):
                continue
            total += 1
            if total > MAX_ROWS:
                raise HTTPException(413, "CSV exceeds 20,000 records")
            if len(row) != len(headers):
                raise ValueError("CSV row does not match headers")
            values = dict(zip(headers, row))
            raw_rows.append({"row_number": total, "values": values})
            phone = re.sub(r"[+(). -]", "", values[phone_column].strip())
            name = values.get(name_column, "").strip()
            email = values.get(email_column, "").strip()
            valid_email = not email or re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
            if (
                not phone.isascii()
                or not phone.isdigit()
                or not 7 <= len(phone) <= 15
                or len(name) > 200
                or len(email) > 320
                or not valid_email
            ):
                invalid += 1
                issues.append(
                    dict(row_number=total, kind="invalid", detail="Check phone, email or field length")
                )
                continue
            contact = dict(phone=phone, name=name, email=email, row_number=total)
            if permissions:
                flag = values.get(permissions.get("permission"), "").strip().casefold()
                evidence = values.get(permissions.get("evidence"), "").strip()
                optout = values.get(permissions.get("optout"), "").strip().casefold()
                test = values.get(permissions.get("test"), "").strip().casefold()
                if flag not in {"", "unknown", "recorded", "revoked", "yes", "no", "true", "false", "opted_out", "opt-out"} or optout not in {"", "true", "false", "yes", "no", "1", "0"} or test not in {"", "true", "false", "yes", "no", "1", "0"} or len(evidence) > 1400:
                    raise ValueError("Invalid permission value or evidence")
                stopped = flag in {"revoked", "no", "false", "opted_out", "opt-out"} or optout in {"true", "yes", "1"}
                simulated = test in {"true", "yes", "1"} or "simulated" in evidence.casefold()
                recorded = flag in {"recorded", "yes", "true"} and len(evidence) >= 8 and (permission_attested or simulated)
                contact.update(sms_permission="revoked" if stopped else "recorded" if recorded else "unknown",
                               status="excluded" if stopped else "eligible" if recorded else "unreviewed",
                               opted_out=stopped, permission_evidence=("[TEST ONLY] " if simulated else "") + evidence)
            contacts.append(contact)
        if not total:
            raise ValueError("CSV contains no records")
        return ParsedCSV(contacts, total, invalid, issues, raw_rows)
    except (UnicodeDecodeError, StopIteration, csv.Error, ValueError):
        raise HTTPException(422, "Invalid UTF-8 CSV, distinct column mapping or row structure") from None


def inspect_csv(raw, phone_column, name_column, email_column):
    parsed = parse_csv(raw, phone_column, name_column, email_column)
    return (
        [{k: v for k, v in x.items() if k != "row_number"} for x in parsed.contacts],
        parsed.total,
        parsed.invalid,
    )


def preview_rows(parsed, existing):
    seen = set(existing)
    accepted, issues, duplicates = [], list(parsed.issues), 0
    for contact in parsed.contacts:
        if contact["phone"] in seen:
            duplicates += 1
            issues.append(
                dict(
                    row_number=contact["row_number"],
                    kind="duplicate",
                    detail="Phone already appears in this list or partner workspace",
                )
            )
        else:
            seen.add(contact["phone"])
            accepted.append({k: v for k, v in contact.items() if k != "row_number"})
    issues.sort(key=lambda x: x["row_number"])
    return accepted, duplicates, issues


def apply_import_preferences(lead, contact):
    """Shared real/projected merge; opt-outs are sticky, including duplicate rows."""
    if "sms_permission" not in contact:
        return False
    if contact["opted_out"]:
        lead.opted_out = True
        lead.sms_permission = "revoked"
        lead.status = "excluded"
        lead.permission_evidence = contact["permission_evidence"] or "Imported opt-out"
        return True
    if not lead.opted_out and lead.sms_permission != "revoked" and contact["sms_permission"] == "recorded":
        if contact["permission_evidence"].startswith("[TEST ONLY]") and lead.permission_evidence and not lead.permission_evidence.startswith("[TEST ONLY]"):
            return False
        lead.sms_permission = "recorded"
        lead.status = "eligible"
        lead.permission_evidence = ("[TEST ONLY] " if lead.permission_evidence.startswith("[TEST ONLY]") and not contact["permission_evidence"].startswith("[TEST ONLY]") else "") + contact["permission_evidence"]
        return True
    return False


def preview_eligibility(parsed, existing):
    """Project exactly the post-import preferences without mutating stored leads."""
    projected, first_rows = {}, {}
    keys = ("phone", "name", "sms_permission", "permission_evidence", "opted_out", "status")
    for contact in parsed.contacts:
        phone = contact["phone"]
        if phone not in projected:
            prior = existing.get(phone)
            projected[phone] = SimpleNamespace(**(
                {k: getattr(prior, k) for k in keys} if prior else {
                    "phone": phone, "name": contact["name"],
                    "sms_permission": contact.get("sms_permission", "unknown"),
                    "permission_evidence": contact.get("permission_evidence", ""),
                    "opted_out": contact.get("opted_out", False),
                    "status": contact.get("status", "unreviewed"),
                }
            ))
            first_rows[phone] = contact["row_number"]
        apply_import_preferences(projected[phone], contact)
    rows = []
    for phone, lead in projected.items():
        reasons = exclusion_reasons(lead)
        rows.append(dict(row_number=first_rows[phone], phone=phone, name=lead.name,
                         sms_permission=lead.sms_permission, opted_out=lead.opted_out,
                         eligible_for_sms=not reasons, exclusion_reasons=reasons))
    eligible = [r for r in rows if r["eligible_for_sms"]]
    excluded = [r for r in rows if not r["eligible_for_sms"]]
    return dict(eligible_count=len(eligible), excluded_count=len(excluded),
                contacts=eligible[:100] + excluded[:100],
                contacts_truncated=len(eligible) > 100 or len(excluded) > 100)
