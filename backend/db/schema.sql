CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS ingredients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind text NOT NULL,
  e_code text UNIQUE,
  canonical_name_ru text NOT NULL,
  canonical_name_en text,
  category text,
  description text,
  is_active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingredient_aliases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ingredient_id uuid NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  language text,
  source text,
  confidence numeric(3, 2) NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS regulatory_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text NOT NULL UNIQUE,
  title text NOT NULL,
  url text,
  version_date date,
  effective_from date,
  effective_to date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingredient_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  ingredient_id uuid NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
  source_id uuid NOT NULL REFERENCES regulatory_sources(id) ON DELETE RESTRICT,
  rule_type text NOT NULL,
  severity text NOT NULL,
  title text NOT NULL,
  explanation text,
  conditions jsonb NOT NULL DEFAULT '{}'::jsonb,
  citation text,
  effective_from date,
  effective_to date,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ingredients_e_code_idx
  ON ingredients (e_code)
  WHERE e_code IS NOT NULL;

CREATE INDEX IF NOT EXISTS ingredient_aliases_normalized_idx
  ON ingredient_aliases (normalized_alias);

CREATE INDEX IF NOT EXISTS ingredient_aliases_trgm_idx
  ON ingredient_aliases
  USING gin (normalized_alias gin_trgm_ops);

CREATE INDEX IF NOT EXISTS ingredient_rules_lookup_idx
  ON ingredient_rules (ingredient_id, severity, effective_from, effective_to);

CREATE INDEX IF NOT EXISTS ingredient_rules_conditions_idx
  ON ingredient_rules
  USING gin (conditions);

CREATE TABLE IF NOT EXISTS users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text UNIQUE,
  display_name text,
  role text NOT NULL DEFAULT 'user',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth_identities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  provider text NOT NULL,
  provider_subject text NOT NULL,
  email text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_subject)
);

CREATE TABLE IF NOT EXISTS subscriptions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan text NOT NULL,
  status text NOT NULL,
  provider text,
  provider_subscription_id text,
  current_period_end timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_preferences (
  user_id uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  diet_type text,
  options jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_ingredient_exclusions (
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ingredient_id uuid NOT NULL REFERENCES ingredients(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, ingredient_id)
);

CREATE TABLE IF NOT EXISTS ocr_jobs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  status text NOT NULL,
  image_object_key text,
  recognized_text text,
  error_message text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);

CREATE TABLE IF NOT EXISTS scan_results (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  source text NOT NULL,
  barcode text,
  product_name text,
  brand text,
  verdict_level text NOT NULL,
  risk_score integer NOT NULL,
  raw_ingredients_text text,
  is_saved boolean NOT NULL DEFAULT false,
  source_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scan_result_ingredients (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_result_id uuid NOT NULL REFERENCES scan_results(id) ON DELETE CASCADE,
  ingredient_id uuid REFERENCES ingredients(id) ON DELETE SET NULL,
  raw_text text NOT NULL,
  matched_name text,
  e_code text,
  severity text NOT NULL,
  match_score numeric(4, 3) NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS compare_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid REFERENCES users(id) ON DELETE SET NULL,
  winner_scan_result_id uuid REFERENCES scan_results(id) ON DELETE SET NULL,
  summary text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS compare_session_items (
  compare_session_id uuid NOT NULL REFERENCES compare_sessions(id) ON DELETE CASCADE,
  scan_result_id uuid NOT NULL REFERENCES scan_results(id) ON DELETE CASCADE,
  position integer NOT NULL,
  PRIMARY KEY (compare_session_id, scan_result_id)
);

CREATE TABLE IF NOT EXISTS billing_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  provider text NOT NULL,
  event_type text NOT NULL,
  provider_event_id text UNIQUE,
  payload jsonb NOT NULL,
  processed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS subscriptions_user_status_idx
  ON subscriptions (user_id, status);

CREATE INDEX IF NOT EXISTS ocr_jobs_status_created_idx
  ON ocr_jobs (status, created_at);

CREATE INDEX IF NOT EXISTS scan_results_user_created_idx
  ON scan_results (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS scan_results_barcode_idx
  ON scan_results (barcode)
  WHERE barcode IS NOT NULL;

CREATE INDEX IF NOT EXISTS scan_results_payload_idx
  ON scan_results
  USING gin (source_payload);
