import os

os.environ.update(DATABASE_URL="sqlite+aiosqlite://", SUPABASE_URL="https://example.supabase.co", SUPABASE_PUBLISHABLE_KEY="test-key")

import pytest

from app.ai_behavior import evaluate_signal
from app.ai_tests import AgentDecision, campaign_context
from app.schemas import CampaignConfig


def evaluate(qualified, signal, criterion="Needs work this month", evidence="I need work this month"):
    decision = AgentDecision(
        reply="Your request is ready for the team.", decision="continue",
        reason="Qualification evaluated", handoff_summary="Interested in a supported local service." if qualified else "",
        default_qualified=qualified, additional_signal=signal,
        signal_evidence=evidence, signal_reason="Lead states their desired timing.",
    )
    return evaluate_signal(decision, {"qualification": criterion}, [
        {"role": "user", "text": "I need work this month"},
    ])


@pytest.mark.parametrize("signal", ["unmet", "unknown"])
def test_extra_signal_never_blocks_default_handoff(signal):
    result = evaluate(True, signal)
    assert result.decision == "handoff"
    assert not result.strong_response
    assert result.signal_evidence == ""


def test_qualified_match_has_evidence():
    result = evaluate(True, "matched")
    assert result.decision == "handoff" and result.strong_response
    assert result.signal_evidence == "I need work this month"
    assert result.signal_reason


def test_extra_alone_does_not_qualify():
    result = evaluate(False, "matched")
    assert result.decision == "continue" and not result.strong_response
    assert not result.default_qualified


def test_blank_criterion_uses_defaults():
    result = evaluate(True, "matched", criterion="  ")
    assert result.decision == "handoff" and not result.strong_response
    assert result.additional_signal == "not_configured" and result.signal_evidence == ""


@pytest.mark.parametrize("quote", ["", "Fabricated evidence"])
def test_missing_or_invented_evidence_is_unknown(quote):
    result = evaluate(True, "matched", evidence=quote)
    assert result.decision == "handoff" and not result.strong_response
    assert result.additional_signal == "unknown"


def test_assistant_quote_cannot_support_signal():
    decision = AgentDecision(reply="OK", decision="handoff", reason="Human request",
                             handoff_summary="Requested contact", default_qualified=True,
                             additional_signal="matched", signal_evidence="This month",
                             signal_reason="Timing")
    result = evaluate_signal(decision, {"qualification": "This month"}, [
        {"role": "assistant", "text": "This month"},
    ])
    assert not result.strong_response


@pytest.mark.parametrize("outcome", ["opt_out", "end_outreach"])
def test_stop_outcomes_take_precedence(outcome):
    decision = AgentDecision(reply="Understood", decision=outcome, reason="Stop",
                             handoff_summary="", default_qualified=True)
    assert evaluate_signal(decision, {}, []).decision == outcome


def test_context_is_partner_scoped_and_unnamed_is_ai():
    class Partner:
        name = "Roof Business"
        onboarding = {"services": "Roof repair", "time_zone": "America/Denver"}
    context = campaign_context(Partner(), CampaignConfig(name="Repairs"))
    assert context["business_name"] == "Roof Business"
    assert context["ai_name"] == "AI Assistant"
    assert context["time_zone"] == "America/Denver"
    assert context["booking_details"] is None and context["lead_records"] == []


def test_extra_match_cannot_route_unqualified_handoff():
    decision = AgentDecision(reply="Thanks", decision="handoff", reason="Extra signal",
                             handoff_summary="Matched extra", additional_signal="matched",
                             signal_evidence="This month", signal_reason="Timing")
    result = evaluate_signal(decision, {"qualification": "This month"}, [
        {"role": "user", "text": "This month"},
    ])
    assert result.decision == "continue" and not result.strong_response
    assert result.handoff_summary == ""


def test_selected_identity_is_single_context_source():
    from app.ai_behavior import SHARED_RULES
    class Partner:
        name = "Roof Business"
        onboarding = {"ai_boundaries": "Use another old name"}
    plan = CampaignConfig(name="Campaign", ai_name="Olivia", ai_brief="Old custom name")
    assert campaign_context(Partner(), plan)["ai_name"] == "Olivia"
    assert "sole assistant name" in SHARED_RULES
    plan.ai_name = "  "
    assert campaign_context(Partner(), plan)["ai_name"] == "AI Assistant"
