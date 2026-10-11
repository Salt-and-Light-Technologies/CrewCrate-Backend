-- Practice conversations only. Never modifies real leads or live messaging records.
BEGIN;
CREATE TABLE public.cc_ai_test_sessions (
    id uuid PRIMARY KEY,
    partner_id uuid NOT NULL REFERENCES public.cc_partners(id),
    campaign_id uuid REFERENCES public.cc_campaigns(id),
    actor_id uuid NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'active',
    revision integer NOT NULL DEFAULT 0,
    context jsonb NOT NULL,
    messages jsonb NOT NULL DEFAULT '[]'::jsonb,
    decisions jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT cc_ai_test_status CHECK (status IN ('active', 'ended', 'opted_out', 'handoff'))
);
CREATE INDEX ix_cc_ai_test_sessions_partner_id ON public.cc_ai_test_sessions(partner_id);
CREATE INDEX ix_cc_ai_test_sessions_actor_id ON public.cc_ai_test_sessions(actor_id);
ALTER TABLE public.cc_ai_test_sessions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cc_ai_test_sessions FROM PUBLIC, anon, authenticated;
COMMIT;
