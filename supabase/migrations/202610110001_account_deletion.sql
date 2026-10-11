BEGIN;
-- Delete login access atomically, preserving business records and anonymized history.
ALTER TABLE cc_owners DROP CONSTRAINT cc_owner_auth_user;
ALTER TABLE cc_owners ADD CONSTRAINT cc_owner_auth_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE cc_partner_members DROP CONSTRAINT cc_member_auth_user;
ALTER TABLE cc_partner_members ADD CONSTRAINT cc_member_auth_user FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE;
ALTER TABLE cc_campaign_events ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE cc_campaign_events DROP CONSTRAINT cc_campaign_actor;
ALTER TABLE cc_campaign_events ADD CONSTRAINT cc_campaign_actor FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE SET NULL;
ALTER TABLE cc_conversation_events ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE cc_conversation_events DROP CONSTRAINT cc_conversation_actor;
ALTER TABLE cc_conversation_events ADD CONSTRAINT cc_conversation_actor FOREIGN KEY (actor_id) REFERENCES auth.users(id) ON DELETE SET NULL;

ALTER TABLE cc_review_events ALTER COLUMN actor_id DROP NOT NULL;
ALTER TABLE cc_lead_imports ALTER COLUMN actor_id DROP NOT NULL;

COMMIT;
