-- Additive CrewCrate rebuild. Apply once to development first. No legacy tables changed.
BEGIN;


CREATE TABLE cc_owners (
	user_id UUID NOT NULL, 
	PRIMARY KEY (user_id)
)

;


CREATE TABLE cc_partners (
	id UUID NOT NULL, 
	name VARCHAR(160) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	revision INTEGER NOT NULL, 
	onboarding JSON NOT NULL, 
	readiness JSON NOT NULL, 
	paused_from VARCHAR(30), 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
)

;


CREATE TABLE cc_lead_imports (
	id UUID NOT NULL, 
	partner_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	file_name VARCHAR(200) NOT NULL, 
	total_rows INTEGER NOT NULL, 
	imported INTEGER NOT NULL, 
	duplicates INTEGER NOT NULL, 
	invalid INTEGER NOT NULL, 
	timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(partner_id) REFERENCES cc_partners (id)
)

;

CREATE INDEX ix_cc_lead_imports_partner_id ON cc_lead_imports (partner_id);


CREATE TABLE cc_partner_members (
	partner_id UUID NOT NULL, 
	user_id UUID NOT NULL, 
	PRIMARY KEY (partner_id, user_id), 
	FOREIGN KEY(partner_id) REFERENCES cc_partners (id)
)

;


CREATE TABLE cc_review_events (
	id UUID NOT NULL, 
	partner_id UUID NOT NULL, 
	actor_id UUID NOT NULL, 
	action VARCHAR(40) NOT NULL, 
	previous_status VARCHAR(30) NOT NULL, 
	resulting_status VARCHAR(30) NOT NULL, 
	reason TEXT NOT NULL, 
	revision INTEGER NOT NULL, 
	timestamp TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (partner_id, revision), 
	FOREIGN KEY(partner_id) REFERENCES cc_partners (id)
)

;

CREATE INDEX ix_cc_review_events_partner_id ON cc_review_events (partner_id);


CREATE TABLE cc_leads (
	id UUID NOT NULL, 
	partner_id UUID NOT NULL, 
	import_id UUID NOT NULL, 
	name VARCHAR(200) NOT NULL, 
	phone VARCHAR(30) NOT NULL, 
	email VARCHAR(320) NOT NULL, 
	status VARCHAR(30) NOT NULL, 
	revision INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (partner_id, phone), 
	FOREIGN KEY(partner_id) REFERENCES cc_partners (id), 
	FOREIGN KEY(import_id) REFERENCES cc_lead_imports (id)
)

;

CREATE INDEX ix_cc_leads_partner_id ON cc_leads (partner_id);

ALTER TABLE cc_owners ADD CONSTRAINT cc_owner_auth_user FOREIGN KEY (user_id) REFERENCES auth.users(id);

ALTER TABLE cc_partner_members ADD CONSTRAINT cc_member_auth_user FOREIGN KEY (user_id) REFERENCES auth.users(id);

ALTER TABLE cc_partners ADD CONSTRAINT cc_partner_status CHECK (status IN ('draft','submitted','changes_requested','pilot_approved','paused'));

ALTER TABLE cc_partners ADD CONSTRAINT cc_partner_revision CHECK (revision >= 0);

ALTER TABLE cc_leads ADD CONSTRAINT cc_lead_status CHECK (status IN ('unreviewed','eligible','excluded'));

ALTER TABLE cc_leads ADD CONSTRAINT cc_lead_revision CHECK (revision >= 0);

ALTER TABLE cc_owners ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_owners FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_partners ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_partners FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_lead_imports ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_lead_imports FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_partner_members ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_partner_members FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_review_events ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_review_events FROM PUBLIC, anon, authenticated;

ALTER TABLE cc_leads ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE cc_leads FROM PUBLIC, anon, authenticated;

-- No browser/mobile direct-table policies: access goes through the authenticated API.

COMMIT;
