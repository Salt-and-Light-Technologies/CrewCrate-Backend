-- Apply after 202610120001_business_applications.sql. Membership alone grants no access.
BEGIN;
ALTER TABLE public.cc_partners ADD COLUMN access_approved boolean NOT NULL DEFAULT false;
-- Preserve only explicit application approvals. Legacy memberships stay blocked until reviewed.
UPDATE public.cc_partners AS partner
SET access_approved = true
FROM public.cc_business_applications AS application
WHERE application.partner_id = partner.id AND application.status = 'approved'
  AND application.reviewed_by IS NOT NULL;
COMMIT;
