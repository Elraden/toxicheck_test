-- Bounded OCR candidate retrieval; no catalogue or user data replaced.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS catalog_alias_trgm_idx
 ON catalog.ingredient_aliases USING gin (normalized_alias gin_trgm_ops);
