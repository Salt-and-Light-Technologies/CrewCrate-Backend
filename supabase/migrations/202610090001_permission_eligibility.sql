-- Align existing contacts with the permission-driven eligibility rule.
-- Unknown permission remains unreviewed or retains its manual classification.
BEGIN;
UPDATE public.cc_leads
SET status = CASE WHEN sms_permission = 'recorded' AND NOT opted_out
                  THEN 'eligible' ELSE 'excluded' END,
    revision = revision + 1
WHERE (sms_permission IN ('recorded', 'revoked') OR opted_out)
  AND status <> CASE WHEN sms_permission = 'recorded' AND NOT opted_out
                     THEN 'eligible' ELSE 'excluded' END;
COMMIT;
