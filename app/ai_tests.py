"""Persisted practice conversations. No live lead mutations or delivery integrations."""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select

from .ai_behavior import SHARED_RULES, evaluate_signal
from .auth import accessible_partner
from .campaigns import Session, User, get_campaign
from .config import get_settings
from .models import AITestSession
from .schemas import CampaignConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/partners/{partner_id}/ai-tests", tags=["Test AI"])


class AgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=1600)
    decision: Literal["continue", "end_outreach", "opt_out", "handoff"]
    reason: str = Field(min_length=1, max_length=1000)
    handoff_summary: str = Field(max_length=2000)

    human_requested: bool = False
    default_qualified: bool = False
    additional_signal: Literal["matched", "unmet", "unknown", "not_configured"] = "unknown"
    signal_evidence: str = Field(default="", max_length=2000)
    signal_reason: str = Field(default="", max_length=1000)
    strong_response: bool = False

    @model_validator(mode="after")
    def valid_handoff(self):
        if self.decision == "handoff" and not self.handoff_summary.strip():
            raise ValueError("A handoff requires a factual summary")
        if self.decision != "handoff" and not self.default_qualified:
            self.handoff_summary = ""
        return self


class TestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: CampaignConfig | None = None
    campaign_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def one_source(self):
        if (self.config is None) == (self.campaign_id is None):
            raise ValueError("Provide either draft config or a saved campaign ID")
        return self


class TestTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_id: uuid.UUID
    text: str = Field(min_length=1, max_length=2000, pattern=r"\S")


class TestMessageRead(BaseModel):
    id: uuid.UUID
    role: Literal["assistant", "user"]
    text: str
    timestamp: str
    request_id: uuid.UUID | None = None


class TestDecisionRead(BaseModel):
    id: uuid.UUID
    decision: Literal["continue", "end_outreach", "opt_out", "handoff"]
    reason: str
    handoff_summary: str
    actor_id: uuid.UUID
    revision: int
    timestamp: str
    simulated: Literal[True]
    human_requested: bool = False
    default_qualified: bool = False
    additional_signal: str = "unknown"
    signal_evidence: str = ""
    signal_reason: str = ""
    strong_response: bool = False


class TestRead(BaseModel):
    id: uuid.UUID
    partner_id: uuid.UUID
    campaign_id: uuid.UUID | None
    revision: int
    status: Literal["active", "ended", "opted_out", "handoff"]
    ai_name: str
    messages: list[TestMessageRead]
    decisions: list[TestDecisionRead]
    source: Literal["ai_test"]
    simulated: Literal[True]
    sending_enabled: Literal[False]


INSTRUCTIONS = SHARED_RULES + """
This is a simulation. You cannot send SMS/email, book appointments or create sales.
Opening: introduce yourself as the partner's AI assistant, mention the approved offer,
include Reply STOP to opt out, and choose continue. Do not assume prior contact.
Choose handoff for default qualification or an explicit request for human contact;
include a factual summary. Choose continue for incomplete qualification/questions.
Choose end_outreach for disinterest or wrong numbers; opt_out for stop requests.
Acknowledge end_outreach/opt_out briefly without further questions. Never claim real
suppression, notification, deletion or booking occurred. Nonqualified continue/end/opt-out
uses an empty summary. A default-qualified continue must include a factual summary so
server routing can hand it off regardless of the additional signal.
Return only the specified JSON structure.
"""


class ResponsesAgent:
    async def generate(self, context: dict, messages: list) -> AgentDecision:
        settings = get_settings()
        if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
            raise HTTPException(503, "Test AI is not configured. Set OPENAI_API_KEY on the backend.")
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {
                "reply": {"type": "string"},
                "decision": {"type": "string", "enum": ["continue", "end_outreach", "opt_out", "handoff"]},
                "reason": {"type": "string"}, "handoff_summary": {"type": "string"},
                "human_requested": {"type": "boolean"},
                "default_qualified": {"type": "boolean"},
                "additional_signal": {"type": "string", "enum": ["matched", "unmet", "unknown", "not_configured"]},
                "signal_evidence": {"type": "string"}, "signal_reason": {"type": "string"},
            },
            "required": ["reply", "decision", "reason", "handoff_summary", "default_qualified",
                         "additional_signal", "signal_evidence", "signal_reason", "human_requested"],
        }
        history = [{"role": m["role"], "content": m["text"]} for m in messages]
        if not history:
            history = [{"role": "user", "content": "Write the initial outreach message. Choose continue."}]
        body = {
            "model": settings.openai_model, "store": False, "max_output_tokens": 1600,
            "instructions": INSTRUCTIONS + "\nCampaign context:\n" + json.dumps({**context, "current_time_utc": datetime.now(timezone.utc).isoformat()}),
            "input": history,
            "text": {"format": {"type": "json_schema", "name": "crewcrate_test_decision",
                                "strict": True, "schema": schema}},
        }
        failure = "provider_response"
        try:
            # Complete recovery within the iOS 75-second request timeout.
            async with asyncio.timeout(55), httpx.AsyncClient(timeout=40, follow_redirects=False) as client:
                for attempt in range(2):
                    response = await client.post(
                        "https://api.openai.com/v1/responses", json=body,
                        headers={"Authorization": "Bearer " + settings.openai_api_key.get_secret_value()},
                    )
                    if response.status_code == 429:
                        logger.warning("Test AI failure category=rate_limit")
                        raise HTTPException(503, "OpenAI usage or rate limit reached. Check API billing and try later.")
                    if response.status_code != 200:
                        logger.warning("Test AI failure category=http_status status=%s", response.status_code)
                        raise HTTPException(502, "OpenAI could not complete this test. Check the server API key/model settings.")
                    result = response.json()
                    parts = [part for item in result.get("output", []) if item.get("type") == "message"
                             for part in item.get("content", [])]
                    if any(part.get("type") == "refusal" for part in parts):
                        failure = "refusal"
                        raise ValueError("Refusal")
                    if result.get("status") != "completed":
                        reason = (result.get("incomplete_details") or {}).get("reason")
                        failure = "output_limit" if reason == "max_output_tokens" else "incomplete"
                        logger.warning("Test AI response category=%s attempt=%s", failure, attempt + 1)
                        if attempt == 0 and failure == "output_limit":
                            body["max_output_tokens"] = 3200
                            continue
                        raise ValueError("Incomplete response")
                    output = "".join(part["text"] for part in parts if part.get("type") == "output_text")
                    try:
                        return evaluate_signal(AgentDecision.model_validate_json(output), context, messages)
                    except ValidationError:
                        failure = "invalid_decision"
                        logger.warning("Test AI response category=invalid_decision attempt=%s", attempt + 1)
                        if attempt == 0:
                            body["max_output_tokens"] = 3200
                            body["instructions"] += (
                                "\nReturn a complete valid decision. Reply and reason must be nonempty, "
                                "within the character limits. A handoff must include a nonempty factual "
                                "summary; otherwise the summary must be empty."
                            )
                            continue
                        raise
        except (httpx.HTTPError, TimeoutError, ValueError, KeyError, TypeError, AttributeError) as error:
            category = "timeout" if isinstance(error, (TimeoutError, httpx.TimeoutException)) else failure
            # Never log lead text, campaign data, keys or raw provider responses.
            logger.warning("Test AI failed category=%s exception=%s", category, type(error).__name__)
            raise HTTPException(502, "The assistant couldn’t finish its reply. Your message is still here; please retry.") from None


def campaign_context(partner, config):
    business = partner.onboarding or {}
    return {
        "business_name": business.get("business_name") or partner.name,
        "services": business.get("services", ""),
        "service_area": business.get("service_area", ""),
        "business_boundaries": business.get("ai_boundaries", ""),
        "campaign_name": config.name,
        "offer": config.offer,
        "ai_name": (config.ai_name or "").strip() or "AI Assistant",
        "ai_instructions": config.ai_brief or "",
        "qualification": config.qualification,
        "handoff_email": config.handoff_email,
        "time_zone": business.get("time_zone") or config.time_zone,
        "business_hours": business.get("business_hours"),
        "booking_details": business.get("booking_details"),
        "follow_up_limit": config.follow_up_limit,
        "campaign_start_date": config.start_date.isoformat() if config.start_date else None,
        "campaign_end_date": config.end_date.isoformat() if config.end_date else None,
        "lead_records": [],  # Practice/opening generation has no verified individual record.
        "shared_rules_version": "2026-10-12",
    }


def get_agent():
    return ResponsesAgent()


Agent = Annotated[ResponsesAgent, Depends(get_agent)]


def read_test(test):
    return dict(id=test.id, partner_id=test.partner_id, campaign_id=test.campaign_id,
                revision=test.revision, status=test.status, ai_name=test.context["ai_name"],
                messages=test.messages, decisions=test.decisions, source="ai_test",
                simulated=True, sending_enabled=False)


def record(test, decision, actor_id):
    test.status = {"continue": "active", "end_outreach": "ended", "opt_out": "opted_out",
                   "handoff": "handoff"}[decision.decision]
    test.messages = [*test.messages, {"id": str(uuid.uuid4()), "role": "assistant",
                                    "text": decision.reply, "timestamp": datetime.now(timezone.utc).isoformat()}]
    test.decisions = [*test.decisions, {"id": str(uuid.uuid4()), "decision": decision.decision,
                                      "reason": decision.reason, "handoff_summary": decision.handoff_summary,
                                      "actor_id": str(actor_id), "revision": test.revision,
                                      "timestamp": datetime.now(timezone.utc).isoformat(), "simulated": True,
                                      "human_requested": decision.human_requested,
                                      "default_qualified": decision.default_qualified,
                                      "additional_signal": decision.additional_signal,
                                      "signal_evidence": decision.signal_evidence,
                                      "signal_reason": decision.signal_reason,
                                      "strong_response": decision.strong_response}]


@router.post("", status_code=201, response_model=TestRead)
async def start(partner_id: uuid.UUID, body: TestCreate, actor: User, session: Session, agent: Agent):
    # The partner lock serializes session creation and its per-user cost limit.
    partner = await accessible_partner(partner_id, actor, session, lock=True)
    recent = await session.scalar(select(func.count()).select_from(AITestSession).where(
        AITestSession.actor_id == actor.user_id,
        AITestSession.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    if recent >= 10:
        raise HTTPException(429, "Test limit reached (10 new tests per hour). Try again later.")
    if body.campaign_id:
        partner, campaign = await get_campaign(partner_id, body.campaign_id, actor, session)
        config = CampaignConfig.model_validate(campaign.config)
    else:
        config = body.config
    context = campaign_context(partner, config)
    test = AITestSession(partner_id=partner_id, campaign_id=body.campaign_id, actor_id=actor.user_id,
                         context=context, messages=[], decisions=[], revision=0)
    opening = await agent.generate(context, [])
    if opening.decision != "continue":
        raise HTTPException(502, "The agent did not generate a valid opening. Please retry.")
    record(test, opening, actor.user_id)
    session.add(test)
    await session.commit()
    return read_test(test)


async def find_test(partner_id, test_id, actor, session, lock=False):
    await accessible_partner(partner_id, actor, session)
    query = select(AITestSession).where(AITestSession.id == test_id,
                                        AITestSession.partner_id == partner_id,
                                        AITestSession.actor_id == actor.user_id)
    if lock:
        query = query.with_for_update()
    test = await session.scalar(query.execution_options(populate_existing=True))
    if not test:
        raise HTTPException(404, "Test conversation not found")
    return test


@router.get("/{test_id}", response_model=TestRead)
async def detail(partner_id: uuid.UUID, test_id: uuid.UUID, actor: User, session: Session):
    return read_test(await find_test(partner_id, test_id, actor, session))


@router.post("/{test_id}/messages", response_model=TestRead)
async def turn(partner_id: uuid.UUID, test_id: uuid.UUID, body: TestTurn,
               actor: User, session: Session, agent: Agent):
    test = await find_test(partner_id, test_id, actor, session, lock=True)
    for message in test.messages:
        if message.get("request_id") == str(body.request_id):
            if message["text"] != body.text:
                raise HTTPException(409, "This request ID was already used for a different message")
            return read_test(test)
    if test.revision != body.expected_revision:
        raise HTTPException(409, "Conversation changed; refresh before sending")
    if test.status != "active":
        raise HTTPException(409, "This test has ended. Start a new test to continue.")
    if test.revision >= 40:
        raise HTTPException(409, "Test limit reached. Start a new test.")
    incoming = {"id": str(uuid.uuid4()), "role": "user", "text": body.text,
                "request_id": str(body.request_id), "timestamp": datetime.now(timezone.utc).isoformat()}
    messages = [*test.messages, incoming]
    normalized = re.sub(r"[.!?]+$", "", body.text.strip().lower()).strip()
    if re.fullmatch(r"(?:stop|stopall|unsubscribe|cancel|end|quit|remove me|stop texting me|"
                    r"do not text me|don't text me|please stop(?: texting me)?)", normalized):
        decision = AgentDecision(reply="Understood. No more outreach in this test.", decision="opt_out",
                                 reason="Explicit request to stop texts (simulated opt-out).", handoff_summary="")
    elif normalized in {"no thanks", "no thank you", "not interested", "i'm not interested", "i am not interested"}:
        decision = AgentDecision(reply="Understood. Thank you for letting us know.", decision="end_outreach",
                                 reason="Lead declined (simulated campaign outreach ended).", handoff_summary="")
    else:
        decision = await agent.generate(test.context, messages)
    decision = evaluate_signal(decision, test.context, messages)
    test.messages = messages
    test.revision += 1
    record(test, decision, actor.user_id)
    await session.commit()
    return read_test(test)
