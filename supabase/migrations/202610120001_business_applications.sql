-- Apply explicitly after existing CrewCrate migrations; never at application startup.
BEGIN;
CREATE TABLE public.cc_business_applications (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,
    business_name varchar(160) NOT NULL,
    contact_name varchar(200) NOT NULL,
    email varchar(320) NOT NULL,
    phone varchar(40) NOT NULL,
    referral varchar(2000) NOT NULL DEFAULT '',
    status varchar(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'denied')),
    revision integer NOT NULL DEFAULT 0 CHECK (revision >= 0),
    partner_id uuid REFERENCES public.cc_partners(id),
    reviewed_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
    review_note text NOT NULL DEFAULT '',
    submitted_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);
CREATE INDEX ix_cc_business_applications_queue ON public.cc_business_applications(status, submitted_at, id);
ALTER TABLE public.cc_business_applications ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.cc_business_applications FROM PUBLIC, anon, authenticated;
COMMIT;
