-- Apply once after 202610120003_worker_onboarding.sql. Tokens are backend-only and encrypted.
BEGIN;
CREATE TABLE public.cc_google_calendar_connections (
 partner_id uuid PRIMARY KEY REFERENCES public.cc_partners(id) ON DELETE CASCADE,
 refresh_token text NOT NULL, access_token text NOT NULL, access_expires_at timestamptz NOT NULL,
 calendar_id text, calendar_name text, time_zone varchar(100), revision integer NOT NULL DEFAULT 1
);
CREATE TABLE public.cc_google_calendar_oauth_states (
 state_hash varchar(64) PRIMARY KEY,
 partner_id uuid NOT NULL UNIQUE REFERENCES public.cc_partners(id) ON DELETE CASCADE,
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 verifier text NOT NULL, expires_at timestamptz NOT NULL
);
ALTER TABLE public.cc_google_calendar_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cc_google_calendar_oauth_states ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cc_google_calendar_connections, public.cc_google_calendar_oauth_states FROM PUBLIC, anon, authenticated;
COMMIT;
