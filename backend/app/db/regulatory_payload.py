"""Validate archived source bundles without maintaining a second SQL importer."""
from app.db.build_regulatory_data import validate_bundle


def validate_import_payload(payload: dict) -> dict:
    dataset = payload.get('metadata', {}).get('dataset')
    if dataset == 'toxicheck_proe_regulatory':
        from app.db.merge_proe_regulatory import validate_bundle as validate_merged
        return validate_merged(payload)
    if dataset == 'toxicheck_proe':
        from app.db.scrape_proe import validate_bundle as validate_proe
        return validate_proe(payload)
    return validate_bundle(payload)
