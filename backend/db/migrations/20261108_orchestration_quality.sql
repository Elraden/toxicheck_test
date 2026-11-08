-- Additive, repeatable research instrumentation; preserve all v3.0 records.
ALTER TABLE orchestration_lab.cases
  ADD COLUMN IF NOT EXISTS reference_ingredients jsonb NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE orchestration_lab.ocr_tasks
  ADD COLUMN IF NOT EXISTS quality_review jsonb;
