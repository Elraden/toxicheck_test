-- Additive migration in the existing catalog database, after identity migration.
CREATE SCHEMA IF NOT EXISTS research;
CREATE TABLE IF NOT EXISTS research.studies (
 id uuid PRIMARY KEY, title text NOT NULL, instruction text NOT NULL,
 scenario text NOT NULL CHECK (scenario IN ('comparison','ocr_quality')),
 protocol_version text NOT NULL DEFAULT '1', is_open boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research.participants (
 id uuid PRIMARY KEY, user_id uuid NOT NULL UNIQUE REFERENCES public.users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research.cases (
 id uuid PRIMARY KEY, title text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research.runs (
 id uuid PRIMARY KEY, study_id uuid NOT NULL REFERENCES research.studies(id),
 participant_id uuid NOT NULL REFERENCES research.participants(id),
 case_id uuid NOT NULL REFERENCES research.cases(id),
 scenario text NOT NULL CHECK (scenario IN ('comparison','ocr_quality')),
 protocol_version text NOT NULL, instruction text NOT NULL, consent_version text NOT NULL,
 consent_at timestamptz NOT NULL DEFAULT now(),
 exclusions jsonb NOT NULL, stage text NOT NULL CHECK (stage IN
 ('manual_ready','manual_active','app_ready','app_active','labeling','completed','aborted')),
 revision integer NOT NULL DEFAULT 0,
 manual_started_at timestamptz, manual_finished_at timestamptz, manual_duration_ms bigint,
 manual_answers jsonb NOT NULL DEFAULT '{}', external_search_used boolean,
 app_started_at timestamptz, app_finished_at timestamptz, app_duration_ms bigint,
 app_answers jsonb NOT NULL DEFAULT '{}', selected_attempt_id uuid,
 user_label_text text, labeling_status text NOT NULL DEFAULT 'pending' CHECK (labeling_status IN ('pending','done','skipped')),
 error_reason text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS research_runs_participant ON research.runs(participant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS research_runs_study ON research.runs(study_id, stage, created_at DESC);
CREATE TABLE IF NOT EXISTS research.photos (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research.runs(id),
 storage_key text NOT NULL UNIQUE, mime_type text NOT NULL, sha256 text NOT NULL,
 size_bytes integer NOT NULL, uploaded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS research_photos_run ON research.photos(run_id);
CREATE TABLE IF NOT EXISTS research.attempts (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES research.runs(id),
 photo_id uuid NOT NULL REFERENCES research.photos(id), input_method text NOT NULL DEFAULT 'ocr',
 status text NOT NULL CHECK (status IN ('processing','success','needs_retake','error','interrupted')),
 result jsonb, error text, started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 server_processing_ms bigint, app_e2e_ms bigint, displayed_at timestamptz,
 app_version text NOT NULL, pipeline_version text NOT NULL, catalog_version text NOT NULL,
 capture_source text NOT NULL
);
CREATE INDEX IF NOT EXISTS research_attempts_run ON research.attempts(run_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS research_one_processing ON research.attempts(run_id) WHERE status='processing';
CREATE TABLE IF NOT EXISTS research.validations (
 run_id uuid PRIMARY KEY REFERENCES research.runs(id), revision integer NOT NULL DEFAULT 0,
 status text NOT NULL DEFAULT 'unreviewed' CHECK(status IN ('unreviewed','confirmed','clarification','unverifiable')),
 verified_label_text text NOT NULL DEFAULT '', ground_truth_e_codes jsonb NOT NULL DEFAULT '[]',
 comment text NOT NULL DEFAULT '', reviewer_id uuid REFERENCES public.users(id),
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS research.audit_events (
 id bigserial PRIMARY KEY, run_id uuid REFERENCES research.runs(id),
 actor_id uuid NOT NULL REFERENCES public.users(id), event text NOT NULL,
 details jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS research_audit_run ON research.audit_events(run_id, created_at);
