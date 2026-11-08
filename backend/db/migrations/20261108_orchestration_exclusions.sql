-- Additive and repeatable; preserves runs, tasks, events and decisions.
ALTER TABLE orchestration_lab.runs
 ADD COLUMN IF NOT EXISTS excluded_from_analysis boolean NOT NULL DEFAULT false,
 ADD COLUMN IF NOT EXISTS exclusion_reason text,
 ADD COLUMN IF NOT EXISTS exclusion_comment text,
 ADD COLUMN IF NOT EXISTS excluded_at timestamptz,
 ADD COLUMN IF NOT EXISTS excluded_by uuid REFERENCES public.users(id);
CREATE TABLE IF NOT EXISTS orchestration_lab.exclusion_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id uuid NOT NULL REFERENCES orchestration_lab.runs(id),
 actor_id uuid NOT NULL REFERENCES public.users(id),
 action text NOT NULL CHECK (action IN ('exclude','restore')),
 reason text NOT NULL CHECK (reason IN ('wrong_product','bad_setup','operator_mistake','duplicate_trial','instrumentation_error','other')),
 comment text,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS exclusion_audit_run_idx ON orchestration_lab.exclusion_audit(run_id,id);
