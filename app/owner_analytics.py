"""Read-only, stored-record business analytics for the verified platform owner."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import Actor, accessible_partner, require_owner
from .database import get_session
from .models import Campaign, Conversation, Lead, LeadImport

router = APIRouter(prefix="/v1/owner")
Owner = Annotated[Actor, Depends(require_owner)]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/partners/{partner_id}/analytics")
async def analytics(partner_id: uuid.UUID, actor: Owner, session: Session):
    partner = await accessible_partner(partner_id, actor, session)
    lead_counts = dict((await session.execute(select(Lead.status, func.count())
        .where(Lead.partner_id == partner_id).group_by(Lead.status))).all())
    campaign_counts = dict((await session.execute(select(Campaign.status, func.count())
        .where(Campaign.partner_id == partner_id).group_by(Campaign.status))).all())
    manual_counts = dict((await session.execute(select(Conversation.status, func.count())
        .join(Campaign, Campaign.id == Conversation.campaign_id)
        .where(Campaign.partner_id == partner_id).group_by(Conversation.status))).all())
    imports = await session.scalar(select(func.count()).select_from(LeadImport)
                                   .where(LeadImport.partner_id == partner_id))
    return {
        "partner_id": partner.id, "name": partner.name, "access_approved": partner.access_approved,
        "total_contacts": sum(lead_counts.values()), "import_count": imports,
        "campaign_count": sum(campaign_counts.values()), "campaign_status_counts": campaign_counts,
        "lead_status_counts": lead_counts, "manual_conversation_count": sum(manual_counts.values()),
        "manual_status_counts": manual_counts, "sms_connected": False,
        "collected_revenue": None, "direct_job_costs": None, "commission_due": None,
        "measurement_scope": "Stored contacts, campaign plans and manual follow-up records. Financial and provider evidence unavailable.",
    }
