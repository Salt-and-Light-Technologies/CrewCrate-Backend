import uuid
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PartnerStatus(StrEnum):
    draft = "draft"
    submitted = "submitted"
    changes_requested = "changes_requested"
    pilot_approved = "pilot_approved"
    paused = "paused"


class Onboarding(BaseModel):
    model_config = ConfigDict(extra="forbid", str_max_length=10000)
    contact_name: str = ""
    email: str = ""
    team_emails: list[str] = Field(default_factory=list, max_length=50)
    business_name: str = ""
    website: str = ""
    industry: str = ""
    services: str = ""
    service_area: str = ""
    time_zone: str = ""
    audience: list[str] = Field(default_factory=list, max_length=3)
    dormant_days: int = Field(default=90, ge=1, le=3650)
    goal: str = "appointment"
    offer: str = ""
    exclusions: str = ""
    qualification: str = ""
    sales_contact: str = ""
    booking_url: str = ""
    handoff_email: str = ""
    response_hours: int = Field(default=24, ge=1, le=168)
    ai_boundaries: str = ""
    lead_source: str = ""
    eligibility_notes: str = ""
    evidence_source: str = "manual"
    reporting_system: str = ""
    compensation: str = "revenue"
    commercial_terms: str = ""
    attribution_days: int = Field(default=30, ge=1, le=365)
    reporting_days: int = Field(default=7, ge=1, le=90)
    accepts_visibility: bool = False
    confirms_accuracy: bool = False


class Readiness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility_reviewed: bool = False
    messaging_ready: bool = False
    reporting_ready: bool = False
    agreement_finalized: bool = False


class PartnerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160, pattern=r"\S")
    partner_user_id: uuid.UUID


class DraftUpdate(BaseModel):
    expected_revision: int = Field(ge=0)
    onboarding: Onboarding


class RevisionRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class ReadinessUpdate(RevisionRequest):
    readiness: Readiness
    reason: str = Field(min_length=5, max_length=2000, pattern=r"\S")


class DecisionAction(StrEnum):
    approve = "approve"
    request_changes = "request_changes"
    pause = "pause"
    resume = "resume"


class Decision(RevisionRequest):
    action: DecisionAction
    reason: str = Field(default="", max_length=2000)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PartnerRead(ORMModel):
    id: uuid.UUID
    name: str
    status: PartnerStatus
    revision: int
    onboarding: Onboarding
    readiness: Readiness
    updated_at: datetime
    paused_from: PartnerStatus | None
    missing_launch_requirements: list[str]


class EventRead(ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    previous_status: str
    resulting_status: str
    reason: str
    revision: int
    timestamp: datetime


class ImportRead(ORMModel):
    id: uuid.UUID
    file_name: str
    total_rows: int
    imported: int
    duplicates: int
    invalid: int
    timestamp: datetime


class LeadStatus(StrEnum):
    unreviewed = "unreviewed"
    eligible = "eligible"
    excluded = "excluded"


class LeadUpdate(RevisionRequest):
    status: LeadStatus


class LeadRead(ORMModel):
    id: uuid.UUID
    import_id: uuid.UUID
    sms_permission: str = "unknown"
    permission_evidence: str = ""
    opted_out: bool = False
    name: str
    phone: str
    email: str
    status: LeadStatus
    revision: int


class ImportIssue(BaseModel):
    row_number: int
    kind: str
    detail: str


class ImportPreview(BaseModel):
    partner_revision: int
    total_rows: int
    imported: int
    duplicates: int
    invalid: int
    issues: list[ImportIssue]
    issues_truncated: bool


class LeadWorkspaceRead(BaseModel):
    partner_id: uuid.UUID
    name: str
    status: PartnerStatus
    partner_revision: int
    total_leads: int
    import_count: int
    lead_counts: dict[str, int]


class PermissionUpdate(RevisionRequest):
    permission: Literal["recorded", "revoked"]
    evidence: str = Field(min_length=8, max_length=2000)


class CampaignConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=160, pattern=r"\S")
    offer: str = Field(default="", max_length=2000)
    message_template: str = Field(default="", max_length=480)
    qualification: str = Field(default="", max_length=2000)
    handoff_email: str = Field(default="", max_length=320)
    time_zone: str = Field(default="America/Chicago", max_length=100)
    start_hour: int = Field(default=9, ge=9, le=16)
    end_hour: int = Field(default=17, ge=10, le=17)
    daily_limit: int = Field(default=25, ge=1, le=100)
    follow_up_limit: int = Field(default=1, ge=0, le=3)
    lead_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)


class CampaignUpdate(RevisionRequest):
    config: CampaignConfig


class CampaignControl(RevisionRequest):
    action: Literal["pause", "resume", "archive"]
    reason: str = Field(min_length=5, max_length=2000)


class CampaignRead(ORMModel):
    owner_hold: bool = False
    id: uuid.UUID
    partner_id: uuid.UUID
    status: str
    revision: int
    config: CampaignConfig
    updated_at: datetime
    blockers: list[str]
    recipient_count: int
    messaging_connected: bool = False
    sending_enabled: bool = False
    needs_recheck: bool


class CampaignEventRead(ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    action: str
    detail: str
    revision: int
    timestamp: datetime


class ConversationCreate(BaseModel):
    lead_id: uuid.UUID
    note: str = Field(min_length=5, max_length=2000)


class ConversationUpdate(RevisionRequest):
    status: Literal["new", "interested", "appointment", "handoff", "closed"]
    note: str = Field(min_length=5, max_length=2000)


class ConversationRead(ORMModel):
    id: uuid.UUID
    campaign_id: uuid.UUID
    lead_id: uuid.UUID
    status: str
    revision: int
    updated_at: datetime
    source: str = "manual_tracking"


class ConversationEventRead(ORMModel):
    id: uuid.UUID
    actor_id: uuid.UUID
    status: str
    note: str
    revision: int
    timestamp: datetime
