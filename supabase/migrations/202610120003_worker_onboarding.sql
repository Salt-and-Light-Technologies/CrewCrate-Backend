-- Apply after business access gate. Additive; legacy tables unchanged.
BEGIN;
ALTER TABLE cc_partner_members ADD COLUMN role varchar(20) NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'worker'));
ALTER TABLE cc_partner_members ADD COLUMN active boolean NOT NULL DEFAULT true;
ALTER TABLE cc_partner_members ADD COLUMN display_name varchar(200) NOT NULL DEFAULT '';
CREATE TABLE cc_worker_invitations (
 id uuid PRIMARY KEY, partner_id uuid NOT NULL REFERENCES cc_partners(id), email varchar(320) NOT NULL,
 token_hash varchar(64) NOT NULL UNIQUE, created_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 expires_at timestamptz NOT NULL, accepted_by uuid REFERENCES auth.users(id) ON DELETE SET NULL,
 consumed boolean NOT NULL DEFAULT false, revoked boolean NOT NULL DEFAULT false
);
CREATE INDEX cc_worker_invites_partner ON cc_worker_invitations(partner_id);
CREATE TABLE cc_worker_notifications (
 id uuid PRIMARY KEY, partner_id uuid NOT NULL REFERENCES cc_partners(id),
 user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 conversation_id uuid NOT NULL REFERENCES cc_conversations(id) ON DELETE CASCADE,
 event_revision integer NOT NULL, read boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id, conversation_id, event_revision)
);
CREATE INDEX cc_worker_notification_inbox ON cc_worker_notifications(user_id, created_at);
CREATE TABLE cc_worker_devices (
 id uuid PRIMARY KEY, user_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
 token varchar(200) NOT NULL UNIQUE, environment varchar(20) NOT NULL CHECK (environment IN ('sandbox','production')),
 active boolean NOT NULL DEFAULT true
);
CREATE TABLE cc_worker_push_jobs (
 id uuid PRIMARY KEY, notification_id uuid NOT NULL REFERENCES cc_worker_notifications(id) ON DELETE CASCADE,
 device_id uuid NOT NULL REFERENCES cc_worker_devices(id) ON DELETE CASCADE,
 status varchar(20) NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
 next_attempt_at timestamptz NOT NULL DEFAULT now(), UNIQUE(notification_id, device_id)
);
CREATE INDEX cc_worker_push_pending ON cc_worker_push_jobs(status, next_attempt_at);
ALTER TABLE cc_worker_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE cc_worker_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE cc_worker_devices ENABLE ROW LEVEL SECURITY;
ALTER TABLE cc_worker_push_jobs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON cc_worker_invitations, cc_worker_notifications, cc_worker_devices, cc_worker_push_jobs FROM PUBLIC, anon, authenticated;
COMMIT;
