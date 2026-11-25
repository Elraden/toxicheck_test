-- Additive: existing participants, runs and photos retain their identifiers.
ALTER TABLE research.participants ALTER COLUMN user_id DROP NOT NULL;
ALTER TABLE research.participants ADD COLUMN IF NOT EXISTS participant_number bigserial;
CREATE UNIQUE INDEX IF NOT EXISTS research_participant_number ON research.participants(participant_number);
CREATE TABLE IF NOT EXISTS research.enrollments (
 id uuid PRIMARY KEY, participant_id uuid NOT NULL REFERENCES research.participants(id),
 study_id uuid NOT NULL REFERENCES research.studies(id), exclusions jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(participant_id,study_id)
);
CREATE TABLE IF NOT EXISTS research.assignments (
 id uuid PRIMARY KEY, enrollment_id uuid NOT NULL REFERENCES research.enrollments(id),
 case_id uuid NOT NULL REFERENCES research.cases(id), position integer NOT NULL CHECK(position>0),
 run_id uuid UNIQUE REFERENCES research.runs(id), UNIQUE(enrollment_id,position)
);
CREATE TABLE IF NOT EXISTS research.invitations (
 id uuid PRIMARY KEY, enrollment_id uuid NOT NULL REFERENCES research.enrollments(id),
 token_hash text NOT NULL UNIQUE, expires_at timestamptz NOT NULL DEFAULT now()+interval '90 days',
 revoked_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research.access_grants (
 invitation_id uuid NOT NULL REFERENCES research.invitations(id),
 user_id uuid NOT NULL REFERENCES public.users(id), created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(invitation_id,user_id)
);
CREATE TABLE IF NOT EXISTS research.satisfaction (
 participant_id uuid PRIMARY KEY REFERENCES research.participants(id),
 enrollment_id uuid NOT NULL REFERENCES research.enrollments(id),
 score smallint NOT NULL CHECK(score BETWEEN 0 AND 10),
 question_version text NOT NULL DEFAULT 'satisfaction-v1',
 created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE research.runs ADD COLUMN IF NOT EXISTS barcode_required boolean NOT NULL DEFAULT false;
ALTER TABLE research.runs ADD COLUMN IF NOT EXISTS barcode_started_at timestamptz;
ALTER TABLE research.runs ADD COLUMN IF NOT EXISTS barcode_finished_at timestamptz;
ALTER TABLE research.runs ADD COLUMN IF NOT EXISTS barcode_duration_ms bigint;
ALTER TABLE research.runs DROP CONSTRAINT IF EXISTS runs_stage_check;
ALTER TABLE research.runs ADD CONSTRAINT runs_stage_check CHECK(stage IN
 ('manual_ready','manual_active','app_ready','app_active','barcode_ready','barcode_active','labeling','completed','aborted'));
CREATE TABLE IF NOT EXISTS research.barcode_attempts (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research.runs(id),
 status text NOT NULL DEFAULT 'scanning' CHECK(status IN
 ('scanning','lookup','success','product_not_found','composition_missing','no_code','camera_denied','camera_error','network_error','service_error','timeout','interrupted')),
 barcode text, result jsonb, error text,
 started_at timestamptz NOT NULL DEFAULT now(), detected_at timestamptz,
 finished_at timestamptz, detection_ms bigint, lookup_ms bigint, total_ms bigint,
 displayed_at timestamptz, client_e2e_ms bigint,
 source text NOT NULL DEFAULT 'open_food_facts'
);
CREATE INDEX IF NOT EXISTS research_barcode_run ON research.barcode_attempts(run_id,started_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_barcode_one_active ON research.barcode_attempts(run_id)
 WHERE status IN ('scanning','lookup');
