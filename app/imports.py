import csv
import io
import re
from dataclasses import dataclass

from fastapi import HTTPException

MAX_BYTES = 5_000_000
MAX_ROWS = 20000


@dataclass
class ParsedCSV:
    contacts: list[dict]
    total: int
    invalid: int
    issues: list[dict]


def parse_csv(raw: bytes, phone_column: str, name_column: str | None, email_column: str | None):
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
        mapping = [phone_column] + [x for x in (name_column, email_column) if x]
        if any(x not in headers for x in mapping) or len(set(mapping)) != len(mapping):
            raise ValueError("Choose distinct mapped columns")
        contacts, invalid, total, issues = [], 0, 0, []
        for row in reader:
            if not row or not any(x.strip() for x in row):
                continue
            total += 1
            if total > MAX_ROWS:
                raise HTTPException(413, "CSV exceeds 20,000 records")
            if len(row) != len(headers):
                raise ValueError("CSV row does not match headers")
            values = dict(zip(headers, row))
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
            contacts.append(dict(phone=phone, name=name, email=email, row_number=total))
        if not total:
            raise ValueError("CSV contains no records")
        return ParsedCSV(contacts, total, invalid, issues)
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
