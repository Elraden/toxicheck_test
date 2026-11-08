CREATE SCHEMA IF NOT EXISTS orchestration_lab;
CREATE TABLE IF NOT EXISTS orchestration_lab.cases (
 id uuid PRIMARY KEY, product_label text NOT NULL DEFAULT '',
 expected_barcode text, label_layout text NOT NULL CHECK(label_layout IN ('same_side','different_side','unknown')),
 notes text NOT NULL DEFAULT '', created_by uuid NOT NULL REFERENCES public.users(id),
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS orchestration_lab.runs (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES orchestration_lab.cases(id),
 mode text NOT NULL CHECK(mode IN ('photo_sequential','photo_adaptive','video_sequential','video_parallel','video_adaptive')),
 status text NOT NULL DEFAULT 'running' CHECK(status IN ('running','finished','failed','interrupted')),
 outcome text, valid_result boolean NOT NULL DEFAULT false,
 protocol_version text NOT NULL, config jsonb NOT NULL, environment jsonb NOT NULL,
 summary jsonb NOT NULL DEFAULT '{}', needs_review boolean NOT NULL DEFAULT false,
 started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 heartbeat_at timestamptz NOT NULL DEFAULT now(), created_by uuid NOT NULL REFERENCES public.users(id)
);
CREATE INDEX IF NOT EXISTS orchestration_runs_history ON orchestration_lab.runs(created_by,started_at DESC,id);
CREATE TABLE IF NOT EXISTS orchestration_lab.events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id uuid NOT NULL REFERENCES orchestration_lab.runs(id) ON DELETE CASCADE,
 seq integer NOT NULL CHECK(seq>=0), event_name text NOT NULL,
 relative_ms double precision CHECK(relative_ms>=0 AND relative_ms<3600000),
 wall_at timestamptz NOT NULL DEFAULT now(), source text NOT NULL CHECK(source IN ('browser','server')),
 details jsonb NOT NULL DEFAULT '{}', UNIQUE(run_id,source,seq)
);
CREATE TABLE IF NOT EXISTS orchestration_lab.ocr_tasks (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES orchestration_lab.runs(id),
 kind text NOT NULL CHECK(kind IN ('ocr','lookup')),
 status text NOT NULL CHECK(status IN ('running','finished','failed','interrupted')),
 input_sha256 text NOT NULL, requested_at timestamptz NOT NULL DEFAULT now(),
 worker_started_at timestamptz, worker_finished_at timestamptz,
 cpu_time_ms double precision, rss_peak_bytes bigint,
 measurement_quality text NOT NULL DEFAULT 'not_measured',
 cancellation jsonb NOT NULL DEFAULT '{}', metrics jsonb NOT NULL DEFAULT '{}',
 result jsonb, sufficient boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS orchestration_tasks_run ON orchestration_lab.ocr_tasks(run_id,requested_at);
CREATE UNIQUE INDEX IF NOT EXISTS orchestration_one_ocr ON orchestration_lab.ocr_tasks((kind)) WHERE kind='ocr' AND status='running';
