-- Independent ingredient catalog, PostgreSQL 16+. No JSON/JSONB columns.
-- Apply through app.db.import_catalog in a dedicated database.
CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE IF NOT EXISTS catalog.schema_version (
  version integer PRIMARY KEY
);
ALTER TABLE catalog.schema_version DROP CONSTRAINT IF EXISTS schema_version_version_check;
UPDATE catalog.schema_version SET version = 2 WHERE version = 1;
INSERT INTO catalog.schema_version (version) VALUES (2) ON CONFLICT DO NOTHING;
ALTER TABLE catalog.schema_version ADD CONSTRAINT schema_version_version_check CHECK (version = 2);

CREATE TABLE IF NOT EXISTS catalog.regulatory_sources (
  id uuid PRIMARY KEY,
  code text NOT NULL UNIQUE,
  title text NOT NULL,
  url text,
  version_date date,
  effective_from date,
  effective_to date,
  CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

CREATE TABLE IF NOT EXISTS catalog.ingredients (
  id uuid PRIMARY KEY,
  kind text NOT NULL,
  e_code text,
  canonical_name_ru text NOT NULL,
  canonical_name_en text,
  category text,
  description text,
  full_description text,
  is_active boolean NOT NULL DEFAULT true,
  CHECK (e_code ~* '^E[0-9]{3,4}[a-z]?(\([ivx]+\))?$')
);
CREATE UNIQUE INDEX IF NOT EXISTS catalog_ingredients_code_idx ON catalog.ingredients (lower(e_code));
-- Version 1 catalogs contain additives only; ordinary foods have no E-code.
ALTER TABLE catalog.ingredients ALTER COLUMN e_code DROP NOT NULL;

CREATE TABLE IF NOT EXISTS catalog.ingredient_aliases (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  language text,
  source text,
  confidence numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1)
);
CREATE INDEX IF NOT EXISTS catalog_alias_lookup_idx ON catalog.ingredient_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS catalog_alias_ingredient_idx ON catalog.ingredient_aliases(ingredient_id);

CREATE TABLE IF NOT EXISTS catalog.ingredient_rules (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  source_id uuid NOT NULL REFERENCES catalog.regulatory_sources(id),
  regulatory_status text NOT NULL CHECK (regulatory_status IN ('PERMITTED_WITH_CONDITIONS', 'BANNED', 'PHASE_OUT')),
  title text NOT NULL,
  explanation text,
  citation text,
  scope text,
  jurisdiction text,
  verification_status text NOT NULL,
  evaluation text NOT NULL CHECK (evaluation IN ('reference_only', 'notice')),
  match_policy text CHECK (match_policy IS NULL OR match_policy = 'exact_only'),
  primary_basis_required boolean,
  production_ready boolean,
  product_compliance_assessed boolean NOT NULL DEFAULT false CHECK (NOT product_compliance_assessed),
  effective_from date,
  effective_to date,
  CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);
CREATE INDEX IF NOT EXISTS catalog_rules_ingredient_idx ON catalog.ingredient_rules(ingredient_id, regulatory_status);

CREATE TABLE IF NOT EXISTS catalog.rule_evidence (
  id uuid PRIMARY KEY,
  rule_id uuid NOT NULL REFERENCES catalog.ingredient_rules(id) ON DELETE CASCADE,
  source_id uuid NOT NULL REFERENCES catalog.regulatory_sources(id),
  url text NOT NULL,
  locator text NOT NULL,
  document_role text NOT NULL,
  verification_status text NOT NULL,
  pdf_page integer CHECK (pdf_page > 0),
  appendix text,
  table_label text
);
CREATE INDEX IF NOT EXISTS catalog_evidence_rule_idx ON catalog.rule_evidence(rule_id);

CREATE TABLE IF NOT EXISTS catalog.rule_transitions (
  rule_id uuid PRIMARY KEY REFERENCES catalog.ingredient_rules(id) ON DELETE CASCADE,
  effective_from date NOT NULL,
  months integer NOT NULL CHECK (months > 0),
  last_production_date date,
  transition_end date NOT NULL,
  boundary_basis text,
  requires_preexisting_conformity_documents boolean NOT NULL,
  legacy_circulation_until_expiry boolean NOT NULL,
  CHECK (transition_end >= effective_from),
  CHECK (last_production_date IS NULL OR last_production_date < transition_end)
);

CREATE TABLE IF NOT EXISTS catalog.ingredient_profiles (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL UNIQUE REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  source_id uuid NOT NULL REFERENCES catalog.regulatory_sources(id),
  source_url text NOT NULL,
  fetched_at timestamptz,
  has_article boolean NOT NULL,
  full_description_status text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.ingredient_tags (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  tag_type text NOT NULL CHECK (tag_type IN ('category', 'origin')),
  name_ru text NOT NULL,
  url text
);
CREATE INDEX IF NOT EXISTS catalog_tags_ingredient_idx ON catalog.ingredient_tags(ingredient_id);

CREATE TABLE IF NOT EXISTS catalog.documents (
  id uuid PRIMARY KEY,
  source_id uuid NOT NULL REFERENCES catalog.regulatory_sources(id),
  title text NOT NULL,
  url text NOT NULL UNIQUE,
  verification_status text NOT NULL,
  fetched_at timestamptz
);
CREATE TABLE IF NOT EXISTS catalog.document_links (
  id uuid PRIMARY KEY,
  document_id uuid NOT NULL REFERENCES catalog.documents(id) ON DELETE CASCADE,
  relation text NOT NULL CHECK (relation IN ('external', 'file')),
  url text NOT NULL,
  UNIQUE(document_id, relation, url)
);
CREATE TABLE IF NOT EXISTS catalog.ingredient_references (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  document_id uuid REFERENCES catalog.documents(id),
  title text NOT NULL,
  url text NOT NULL,
  relation text NOT NULL,
  organization text
);
CREATE INDEX IF NOT EXISTS catalog_references_ingredient_idx ON catalog.ingredient_references(ingredient_id);

CREATE TABLE IF NOT EXISTS catalog.ingredient_relations (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  related_ingredient_id uuid REFERENCES catalog.ingredients(id),
  related_url text NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_relations_ingredient_idx ON catalog.ingredient_relations(ingredient_id);

-- Missing direct attribution stays unknown instead of naming an aggregator.
ALTER TABLE catalog.ingredient_profiles ALTER COLUMN source_id DROP NOT NULL;
ALTER TABLE catalog.ingredient_profiles ALTER COLUMN source_url DROP NOT NULL;
ALTER TABLE catalog.documents ALTER COLUMN source_id DROP NOT NULL;
ALTER TABLE catalog.documents ALTER COLUMN url DROP NOT NULL;
ALTER TABLE catalog.ingredient_references ALTER COLUMN url DROP NOT NULL;
ALTER TABLE catalog.ingredient_relations ALTER COLUMN related_url DROP NOT NULL;

CREATE TABLE IF NOT EXISTS catalog.ingredient_matches (
  ingredient_id uuid PRIMARY KEY REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  legacy_ingredient_id uuid NOT NULL UNIQUE,
  match_method text NOT NULL
);

-- Taxonomy provenance is separate from regulatory evidence and hazard rules.
CREATE TABLE IF NOT EXISTS catalog.taxonomy_sources (
  id uuid PRIMARY KEY,
  code text NOT NULL UNIQUE,
  title text NOT NULL,
  url text NOT NULL,
  file_sha256 text NOT NULL CHECK (length(file_sha256) = 64)
);
CREATE TABLE IF NOT EXISTS catalog.taxonomy_entries (
  id uuid PRIMARY KEY,
  taxonomy_source_id uuid NOT NULL REFERENCES catalog.taxonomy_sources(id),
  taxonomy_key text NOT NULL,
  canonical_name_ru text,
  canonical_name_en text,
  ingredient_id uuid REFERENCES catalog.ingredients(id),
  match_method text NOT NULL CHECK (match_method IN
    ('exact_e_code', 'new_ordinary_ingredient', 'untranslated', 'code_review_required')),
  source_line integer NOT NULL CHECK (source_line > 0),
  UNIQUE(taxonomy_source_id, taxonomy_key)
);
CREATE INDEX IF NOT EXISTS catalog_taxonomy_ingredient_idx ON catalog.taxonomy_entries(ingredient_id);
CREATE TABLE IF NOT EXISTS catalog.taxonomy_parents (
  id uuid PRIMARY KEY,
  entry_id uuid NOT NULL REFERENCES catalog.taxonomy_entries(id) ON DELETE CASCADE,
  parent_entry_id uuid REFERENCES catalog.taxonomy_entries(id),
  parent_reference text NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_taxonomy_parents_idx ON catalog.taxonomy_parents(entry_id);
CREATE TABLE IF NOT EXISTS catalog.taxonomy_properties (
  id uuid PRIMARY KEY,
  entry_id uuid NOT NULL REFERENCES catalog.taxonomy_entries(id) ON DELETE CASCADE,
  property text NOT NULL,
  language text NOT NULL,
  value text NOT NULL,
  verification_status text NOT NULL CHECK (verification_status = 'reference_only')
);
CREATE INDEX IF NOT EXISTS catalog_taxonomy_properties_idx ON catalog.taxonomy_properties(entry_id, property);
-- Never use this table in automatic ingredient matching.
CREATE TABLE IF NOT EXISTS catalog.ingredient_alias_review (
  id uuid PRIMARY KEY,
  ingredient_id uuid NOT NULL REFERENCES catalog.ingredients(id) ON DELETE CASCADE,
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  language text,
  source text,
  confidence numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  reason text NOT NULL CHECK (reason IN ('ambiguous_name', 'e_code_mismatch', 'empty_normalized_alias', 'name_fragment'))
);
CREATE INDEX IF NOT EXISTS catalog_alias_review_idx ON catalog.ingredient_alias_review(normalized_alias);

CREATE TABLE IF NOT EXISTS catalog.import_runs (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  imported_at timestamptz NOT NULL DEFAULT now(),
  input_filename text NOT NULL,
  input_sha256 text NOT NULL CHECK (length(input_sha256) = 64),
  ingredient_count integer NOT NULL,
  rule_count integer NOT NULL
);
