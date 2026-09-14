BEGIN;

-- ТР ТС 022/2011 и ТР ТС 029/2012 содержат нормы, которые относятся
-- ко всей маркировке или категории продукции, а не к одному ингредиенту.
-- NULL означает глобальное правило; существующие связи не меняются.
ALTER TABLE ingredient_rules
  ALTER COLUMN ingredient_id DROP NOT NULL;

CREATE INDEX IF NOT EXISTS ingredient_rules_global_lookup_idx
  ON ingredient_rules (source_id, rule_type, effective_from, effective_to)
  WHERE ingredient_id IS NULL;

COMMIT;
