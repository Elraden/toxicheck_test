"""Database storage contract; parser provenance remains in source JSON, not runtime SQL."""

CATALOG_DATA_TABLES = (
    'regulatory_sources', 'ingredients', 'ingredient_aliases', 'ingredient_rules',
    'rule_evidence', 'rule_transitions', 'ingredient_tags', 'ingredient_matches', 'ingredient_alias_review',
)
CATALOG_TABLES = ('schema_version', *CATALOG_DATA_TABLES, 'import_runs')
IDENTITY_TABLES = ('users', 'user_sessions', 'auth_rate_limits', 'user_preferences', 'user_ingredient_exclusions')
DECISION_TABLES = ('review_queue', 'training_data', 'user_corrections')
RESEARCH_TABLES = ('studies', 'participants', 'cases', 'runs', 'photos', 'attempts', 'validations', 'audit_events')

# Explicit allowlist. Never remove a table just because it is empty or unknown.
PARSER_TABLES = (
    'document_links', 'ingredient_references', 'ingredient_relations', 'ingredient_profiles',
    'documents', 'taxonomy_parents', 'taxonomy_properties', 'taxonomy_entries', 'taxonomy_sources',
)
REQUIRED_TABLES = ({f'catalog.{name}' for name in CATALOG_TABLES}
                   | {f'public.{name}' for name in IDENTITY_TABLES}
                   | {f'decision_tree.{name}' for name in DECISION_TABLES}
                   | {f'research.{name}' for name in RESEARCH_TABLES})
