-- Apply manually before deploying the permission-import preview changes.
-- Existing RLS and table grants remain unchanged. No source files are stored.
BEGIN;
ALTER TABLE cc_lead_imports ADD COLUMN raw_rows JSON NOT NULL DEFAULT '[]';
COMMIT;
