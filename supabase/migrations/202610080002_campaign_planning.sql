-- Apply after 202610080001. Adds planning/manual tracking only; no delivery jobs.

BEGIN;

ALTER TABLE cc_leads ADD COLUMN sms_permission varchar(20) NOT NULL DEFAULT 'unknown';

ALTER TABLE cc_leads ADD COLUMN permission_evidence text NOT NULL DEFAULT '';

ALTER TABLE cc_leads ADD COLUMN opted_out boolean NOT NULL DEFAULT false;

ALTER TABLE cc_leads ADD CONSTRAINT cc_sms_permission CHECK (sms_permission IN ('unknown','recorded','revoked'));

ALTER TABLE cc_leads ADD CONSTRAINT cc_optout_permission CHECK (NOT opted_out OR sms_permission = 'revoked');


CREATE TABLE cc_campaigns (
	owner_hold BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	partner_id UUID NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	revision INTEGER NOT NULL, 
	config JSON NOT NULL, 
	prepared_partner_revision INTEGER, 
	prepared_lead_revisions JSON NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(partner_id) REFERENCES cc_partners (id)
)

;

CREATE INDEX ix_cc_campaigns_partner_id ON cc_campaigns (partner_id);

ALTER TABLE cc_campaigns ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_campaigns FROM PUBLIC, anon, authenticated;


CREATE TABLE cc_campaign_events (
	id UUID NOT NULL, 
	campaign_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	action VARCHAR(40) NOT NULL, 
	detail TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (campaign_id, revision), 
	FOREIGN KEY(campaign_id) REFERENCES cc_campaigns (id)
)

;

CREATE INDEX ix_cc_campaign_events_campaign_id ON cc_campaign_events (campaign_id);

ALTER TABLE cc_campaign_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_campaign_events FROM PUBLIC, anon, authenticated;


CREATE TABLE cc_conversations (
	id UUID NOT NULL, 
	campaign_id UUID NOT NULL, 
	lead_id UUID NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	revision INTEGER NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (campaign_id, lead_id), 
	FOREIGN KEY(campaign_id) REFERENCES cc_campaigns (id), 
	FOREIGN KEY(lead_id) REFERENCES cc_leads (id)
)

;

CREATE INDEX ix_cc_conversations_campaign_id ON cc_conversations (campaign_id);

ALTER TABLE cc_conversations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_conversations FROM PUBLIC, anon, authenticated;


CREATE TABLE cc_conversation_events (
	id UUID NOT NULL, 
	conversation_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	note TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (conversation_id, revision), 
	FOREIGN KEY(conversation_id) REFERENCES cc_conversations (id)
)

;

CREATE INDEX ix_cc_conversation_events_conversation_id ON cc_conversation_events (conversation_id);

ALTER TABLE cc_conversation_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_conversation_events FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_campaigns ADD CONSTRAINT cc_campaign_status CHECK (status IN ('draft','ready_to_connect','paused','archived'));

ALTER TABLE cc_conversations ADD CONSTRAINT cc_conversation_status CHECK (status IN ('new','interested','appointment','handoff','closed'));

ALTER TABLE cc_campaign_events ADD CONSTRAINT cc_campaign_actor FOREIGN KEY (actor_id) REFERENCES auth.users(id);

ALTER TABLE cc_conversation_events ADD CONSTRAINT cc_conversation_actor FOREIGN KEY (actor_id) REFERENCES auth.users(id);

COMMIT;
