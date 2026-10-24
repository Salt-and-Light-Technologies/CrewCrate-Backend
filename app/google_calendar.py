"""Business-selected Google calendar. No platform-owner elevation or client tokens."""
import base64
import hashlib
import re
import secrets
import uuid
from datetime import date as CalendarDate
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import quote, urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, current_actor
from .config import get_settings
from .database import get_session
from .models import CalendarConnection, CalendarOAuthState, Member, Partner


def no_cache(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/v1", dependencies=[Depends(no_cache)])
Session = Annotated[AsyncSession, Depends(get_session)]
User = Annotated[Actor, Depends(current_actor)]
SCOPES = frozenset({"https://www.googleapis.com/auth/calendar.events",
                    "https://www.googleapis.com/auth/calendar.calendarlist.readonly"})
API = "https://www.googleapis.com/calendar/v3/"


def now():
    return datetime.now(timezone.utc)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def configuration():
    settings = get_settings()
    uri = urlparse(settings.google_calendar_redirect_uri)
    if not (settings.google_calendar_client_id and settings.google_calendar_client_secret
            and settings.google_calendar_token_key and uri.scheme == "https" and uri.netloc
            and uri.path == "/v1/integrations/google-calendar/callback" and not uri.query and not uri.fragment):
        raise HTTPException(503, "Google Calendar connection is not configured. Contact the CrewCrate team.")
    try:
        cipher = Fernet(settings.google_calendar_token_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise HTTPException(503, "Google Calendar connection is not configured. Contact the CrewCrate team.") from None
    return settings, cipher


async def business(partner_id, actor, session, *, admin=False):
    # Always require explicit active membership. Global platform ownership grants no Google access.
    partner = await session.scalar(select(Partner).where(Partner.id == partner_id).with_for_update()
                                   .execution_options(populate_existing=True))
    member = await session.scalar(select(Member).where(Member.partner_id == partner_id,
        Member.user_id == actor.user_id).with_for_update().execution_options(populate_existing=True))
    if not partner or not member or not member.active or member.role not in {"admin", "worker"}:
        raise HTTPException(404, "Business not found")
    if not partner.access_approved:
        raise HTTPException(403, "Your business is awaiting platform owner approval")
    if admin and member.role != "admin":
        raise HTTPException(403, "Business administrator access required")
    return partner


class GoogleProvider:
    async def token(self, data):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.post("https://oauth2.googleapis.com/token", data=data)
            if response.status_code == 400 and response.json().get("error") == "invalid_grant":
                raise HTTPException(409, "Google access expired or was revoked. Ask your business admin to reconnect.")
            if response.status_code != 200:
                raise HTTPException(502, "Google connection failed. Please try connecting again.")
            return response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(502, "Google is unavailable. Please try again.") from None

    async def request(self, method, path, token, *, params=None, body=None, etag=None):
        headers = {"Authorization": "Bearer " + token}
        if etag:
            headers["If-Match"] = etag
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.request(method, API + path, headers=headers, params=params, json=body)
            if response.status_code in (401, 403):
                raise HTTPException(409, "Google calendar access is unavailable. Ask your business admin to reconnect or check permissions.")
            if response.status_code in (404, 410):
                raise HTTPException(404, "Calendar or event no longer available. Refresh the calendar.")
            if response.status_code == 409:
                raise HTTPException(409, "This event may already have been saved. Refresh the calendar before trying again.")
            if response.status_code == 412:
                raise HTTPException(409, "This event changed in Google Calendar. Refresh before editing again.")
            if response.status_code == 429:
                raise HTTPException(503, "Google is busy. Please try again later.")
            if response.status_code >= 300:
                raise HTTPException(502, "Google could not complete the calendar request. Refresh before trying again.")
            return {} if response.status_code == 204 else response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            raise HTTPException(502, "Google is unavailable. Refresh the calendar before trying again.") from None


def provider():
    return GoogleProvider()


Provider = Annotated[GoogleProvider, Depends(provider)]


def status(row):
    return {"connected": bool(row and row.refresh_token), "calendar_id": row.calendar_id if row else None,
            "calendar_name": row.calendar_name if row else None,
            "time_zone": row.time_zone if row else None, "revision": row.revision if row else 0}


async def connection(partner_id, session):
    return await session.get(CalendarConnection, partner_id, populate_existing=True)


async def access_token(row, session, google):
    settings, cipher = configuration()
    try:
        if row.access_expires_at and aware(row.access_expires_at) > now() + timedelta(seconds=60):
            return cipher.decrypt(row.access_token.encode()).decode()
        refresh = cipher.decrypt(row.refresh_token.encode()).decode()
    except (InvalidToken, ValueError, UnicodeError):
        raise HTTPException(503, "Calendar token storage is unavailable. Contact the CrewCrate team.") from None
    result = await google.token({"client_id": settings.google_calendar_client_id,
        "client_secret": settings.google_calendar_client_secret.get_secret_value(),
        "grant_type": "refresh_token", "refresh_token": refresh})
    token = result.get("access_token")
    if not token or (result.get("scope") and not SCOPES.issubset(result["scope"].split())):
        raise HTTPException(409, "Google permissions are incomplete. Ask your business admin to reconnect.")
    row.access_token = cipher.encrypt(token.encode()).decode()
    row.access_expires_at = now() + timedelta(seconds=int(result.get("expires_in", 3600)))
    if result.get("refresh_token"):
        row.refresh_token = cipher.encrypt(result["refresh_token"].encode()).decode()
    await session.flush()
    return token


@router.get("/partners/{partner_id}/google-calendar")
async def read_status(partner_id: uuid.UUID, actor: User, session: Session):
    await business(partner_id, actor, session)
    return status(await connection(partner_id, session))


@router.post("/partners/{partner_id}/google-calendar/connect")
async def connect(partner_id: uuid.UUID, actor: User, session: Session):
    await business(partner_id, actor, session, admin=True)
    settings, cipher = configuration()
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    await session.execute(delete(CalendarOAuthState).where(CalendarOAuthState.partner_id == partner_id))
    session.add(CalendarOAuthState(state_hash=digest(state), partner_id=partner_id, user_id=actor.user_id,
        verifier=cipher.encrypt(verifier.encode()).decode(), expires_at=now() + timedelta(minutes=10)))
    await session.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": settings.google_calendar_client_id, "redirect_uri": settings.google_calendar_redirect_uri,
        "response_type": "code", "scope": " ".join(sorted(SCOPES)), "state": state,
        "access_type": "offline", "prompt": "consent select_account",
        "code_challenge": challenge, "code_challenge_method": "S256"})
    return {"authorization_url": url}


def callback_page(message, code=200):
    # Only fixed application messages; never interpolate Google errors, codes, state or tokens.
    return HTMLResponse("<!doctype html><html lang='en'><meta name='viewport' content='width=device-width'>"
        "<title>CrewCrate Calendar</title><body><h1>CrewCrate</h1><p>" + message + "</p>"
        "<p>Return to CrewCrate and refresh Business integrations.</p></body></html>", status_code=code,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                 "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})


@router.get("/integrations/google-calendar/callback", response_class=HTMLResponse)
async def callback(session: Session, google: Provider, state: str = Query(min_length=20, max_length=200),
                   code: str | None = Query(default=None, max_length=4096),
                   error: str | None = Query(default=None, max_length=200)):
    # Lock order matches every business request. Serialize state consumption and reconnect/disconnect.
    pending = await session.get(CalendarOAuthState, digest(state))
    if not pending:
        return callback_page("This connection link is invalid or already used. Start again in Account.", 400)
    try:
        await business(pending.partner_id, Actor(pending.user_id), session, admin=True)
    except HTTPException:
        return callback_page("Business administrator access is no longer available. Start again in Account.", 403)
    pending = await session.scalar(select(CalendarOAuthState).where(CalendarOAuthState.state_hash == digest(state))
                                  .with_for_update().execution_options(populate_existing=True))
    if not pending or aware(pending.expires_at) <= now():
        return callback_page("This connection link expired or was already used. Start again in Account.", 400)
    partner_id = pending.partner_id
    verifier = pending.verifier
    await session.delete(pending)
    # Keep the partner lock until callback finishes; state is consumed even on provider failure.
    if error or not code:
        await session.commit()
        return callback_page("Google Calendar was not connected. You can try again in Account.", 400)
    try:
        settings, cipher = configuration()
        result = await google.token({"client_id": settings.google_calendar_client_id,
            "client_secret": settings.google_calendar_client_secret.get_secret_value(),
            "redirect_uri": settings.google_calendar_redirect_uri, "grant_type": "authorization_code",
            "code": code, "code_verifier": cipher.decrypt(verifier.encode()).decode()})
        if not SCOPES.issubset(result.get("scope", "").split()) or not result.get("refresh_token") or not result.get("access_token"):
            raise HTTPException(409, "Incomplete permissions")
        expires = now() + timedelta(seconds=int(result.get("expires_in", 3600)))
        refresh_cipher = cipher.encrypt(result["refresh_token"].encode()).decode()
        access_cipher = cipher.encrypt(result["access_token"].encode()).decode()
        row = await connection(partner_id, session)
        if row is None:
            row = CalendarConnection(partner_id=partner_id, revision=0)
            session.add(row)
        row.refresh_token = refresh_cipher
        row.access_token = access_cipher
        row.access_expires_at = expires
        row.calendar_id = None
        row.calendar_name = None
        row.time_zone = None
        row.revision += 1
        await session.commit()
    except (HTTPException, InvalidToken, ValueError, KeyError, TypeError):
        await session.commit()
        return callback_page("Google connection did not finish. Grant both calendar permissions and try again in Account.", 400)
    return callback_page("Google connected. Choose the business calendar in Account before your team can use it.")


async def connected(partner_id, session):
    row = await connection(partner_id, session)
    if not row or not row.refresh_token:
        raise HTTPException(409, "Google Calendar is not connected. Ask your business admin to connect it.")
    return row


@router.get("/partners/{partner_id}/google-calendar/calendars")
async def calendars(partner_id: uuid.UUID, actor: User, session: Session, google: Provider):
    await business(partner_id, actor, session, admin=True)
    row = await connected(partner_id, session)
    token = await access_token(row, session, google)
    items, page = [], None
    for _ in range(10):
        result = await google.request("GET", "users/me/calendarList", token,
            params={"minAccessRole": "writer", "maxResults": 250, **({"pageToken": page} if page else {})})
        items += [{"id": x["id"], "name": x.get("summaryOverride", x.get("summary", "Calendar")),
                   "time_zone": x.get("timeZone", "UTC")}
                  for x in result.get("items", []) if x.get("accessRole") in {"owner", "writer"}]
        page = result.get("nextPageToken")
        if not page:
            break
    if page:
        raise HTTPException(502, "Too many calendars to list. Use a Google account with fewer calendars.")
    await session.commit()
    return items


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    calendar_id: str = Field(min_length=1, max_length=1024)
    expected_revision: int = Field(ge=0)


def revision(row, expected):
    if expected != (row.revision if row else 0):
        raise HTTPException(409, "Calendar connection changed. Refresh before trying again.")


@router.put("/partners/{partner_id}/google-calendar/selection")
async def select_calendar(partner_id: uuid.UUID, body: Selection, actor: User, session: Session, google: Provider):
    await business(partner_id, actor, session, admin=True)
    row = await connected(partner_id, session)
    revision(row, body.expected_revision)
    token = await access_token(row, session, google)
    item = await google.request("GET", "users/me/calendarList/" + quote(body.calendar_id, safe=""), token)
    if item.get("accessRole") not in {"owner", "writer"}:
        raise HTTPException(422, "Choose a calendar with permission to edit events.")
    row.calendar_id = item["id"]
    row.calendar_name = item.get("summaryOverride", item.get("summary", "Calendar"))
    row.time_zone = item.get("timeZone", "UTC")
    row.revision += 1
    await session.execute(delete(CalendarOAuthState).where(CalendarOAuthState.partner_id == partner_id))
    await session.commit()
    return status(row)


@router.delete("/partners/{partner_id}/google-calendar")
async def disconnect(partner_id: uuid.UUID, actor: User, session: Session, expected_revision: int = Query(ge=0)):
    await business(partner_id, actor, session, admin=True)
    row = await connection(partner_id, session)
    revision(row, expected_revision)
    if row:
        row.refresh_token = ""
        row.access_token = ""
        row.access_expires_at = now()
        row.calendar_id = None
        row.calendar_name = None
        row.time_zone = None
        row.revision += 1
    await session.execute(delete(CalendarOAuthState).where(CalendarOAuthState.partner_id == partner_id))
    await session.commit()
    # Remove this business's tokens immediately. Account-wide Google revoke would affect other businesses.
    return {"ok": True}


async def event_access(partner_id, actor, session, google, expected_calendar_revision=None):
    await business(partner_id, actor, session)
    row = await connected(partner_id, session)
    if expected_calendar_revision is not None:
        revision(row, expected_calendar_revision)
    if not row.calendar_id:
        raise HTTPException(409, "Ask your business admin to choose a calendar in Account.")
    return row, await access_token(row, session, google)


def event_path(row, event_id=None):
    if event_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", event_id):
        raise HTTPException(422, "Invalid event identifier")
    return "calendars/" + quote(row.calendar_id, safe="") + "/events" + ("/" + event_id if event_id else "")


def event_read(item):
    return {"id": item["id"], "etag": item.get("etag", ""), "title": item.get("summary", "Untitled event"),
        "description": item.get("description", ""), "location": item.get("location", ""),
        "start": item.get("start", {}), "end": item.get("end", {}),
        "editable": item.get("eventType", "default") == "default" and not item.get("locked", False),
        "recurring": bool(item.get("recurringEventId"))}


@router.get("/partners/{partner_id}/google-calendar/events")
async def events(partner_id: uuid.UUID, actor: User, session: Session, google: Provider,
                 start: datetime, end: datetime):
    if not start.tzinfo or not end.tzinfo or not timedelta(0) < end - start <= timedelta(days=31):
        raise HTTPException(422, "Choose a time range up to 31 days with explicit time zones.")
    row, token = await event_access(partner_id, actor, session, google)
    items, page = [], None
    for _ in range(4):
        result = await google.request("GET", event_path(row), token, params={
            "timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
            "orderBy": "startTime", "maxResults": 250, **({"pageToken": page} if page else {})})
        items += [event_read(x) for x in result.get("items", []) if x.get("status") != "cancelled"]
        page = result.get("nextPageToken")
        if not page:
            break
    await session.commit()
    return {"events": items, "truncated": bool(page), "calendar": status(row)}


class EventTime(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: CalendarDate | None = None
    dateTime: datetime | None = None
    timeZone: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def valid(self):
        if (self.date is None) == (self.dateTime is None):
            raise ValueError("Supply one date or dateTime")
        if self.dateTime is not None and not self.dateTime.tzinfo:
            raise ValueError("Event dateTime needs an explicit offset")
        return self


class EventWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=10000)
    location: str = Field(default="", max_length=1000)
    start: EventTime
    end: EventTime
    calendar_revision: int = Field(ge=1)
    request_id: uuid.UUID
    etag: str | None = Field(default=None, max_length=256, pattern=r'^"[^"\r\n]+"$')

    @model_validator(mode="after")
    def valid(self):
        if not self.title.strip():
            raise ValueError("An event title is required")
        if (self.start.date is None) != (self.end.date is None):
            raise ValueError("Start and end must use the same date format")
        begin, finish = self.start.date or self.start.dateTime, self.end.date or self.end.dateTime
        if finish <= begin:
            raise ValueError("Event end must be after start; all-day end is exclusive")
        return self

    def google_body(self):
        return {"summary": self.title.strip(), "description": self.description, "location": self.location,
                "start": self.start.model_dump(mode="json", exclude_none=True),
                "end": self.end.model_dump(mode="json", exclude_none=True)}


async def editable(row, event_id, etag, token, google):
    if not etag:
        raise HTTPException(422, "Refresh the calendar before editing this event.")
    current = await google.request("GET", event_path(row, event_id), token)
    if current.get("etag") != etag:
        raise HTTPException(409, "This event changed in Google Calendar. Refresh before editing again.")
    if not event_read(current)["editable"]:
        raise HTTPException(422, "Manage this special event in Google Calendar.")


@router.post("/partners/{partner_id}/google-calendar/events", status_code=201)
async def create_event(partner_id: uuid.UUID, body: EventWrite, actor: User, session: Session, google: Provider):
    row, token = await event_access(partner_id, actor, session, google, body.calendar_revision)
    payload = body.google_body()
    # Stable, valid Google event ID: a retry after a lost response cannot create a duplicate.
    payload["id"] = hashlib.sha256((str(partner_id) + row.calendar_id + str(body.request_id)).encode()).hexdigest()
    result = await google.request("POST", event_path(row), token, params={"sendUpdates": "all"}, body=payload)
    await session.commit()
    return event_read(result)


@router.patch("/partners/{partner_id}/google-calendar/events/{event_id}")
async def update_event(partner_id: uuid.UUID, event_id: str, body: EventWrite, actor: User,
                       session: Session, google: Provider):
    row, token = await event_access(partner_id, actor, session, google, body.calendar_revision)
    await editable(row, event_id, body.etag, token, google)
    result = await google.request("PATCH", event_path(row, event_id), token, params={"sendUpdates": "all"},
        body=body.google_body(), etag=body.etag)
    await session.commit()
    return event_read(result)


@router.delete("/partners/{partner_id}/google-calendar/events/{event_id}")
async def delete_event(partner_id: uuid.UUID, event_id: str, actor: User, session: Session, google: Provider,
                       etag: str = Query(max_length=256, pattern=r'^"[^"\r\n]+"$'),
                       calendar_revision: int = Query(ge=1)):
    row, token = await event_access(partner_id, actor, session, google, calendar_revision)
    await editable(row, event_id, etag, token, google)
    await google.request("DELETE", event_path(row, event_id), token, params={"sendUpdates": "all"}, etag=etag)
    await session.commit()
    return {"ok": True}
