from datetime import date

from app.schemas.ingredients import IngredientRuleOut


def assess_rule(rule: IngredientRuleOut, *, matched_by: str, today: date | None = None) -> IngredientRuleOut:
    """Separate a regulatory statement from a context-dependent product verdict."""
    today = today or date.today()
    conditions = rule.conditions
    severity = rule.severity
    note = rule.explanation
    if conditions.get("evaluation") == "reference_only" or severity == "regulatory":
        severity = "neutral"
    if conditions.get("match_policy") == "exact_only" and matched_by == "similarity":
        severity = "neutral"
        note = "Недостаточно точное совпадение для применения нормативного статуса."
    elif conditions.get("regulatory_status") == "BANNED" and conditions.get("evaluation") != "reference_only":
        # Restriction status and the verification of its legal basis are separate.
        severity = "forbidden"
    elif conditions.get("regulatory_status") == "PHASE_OUT":
        transition = conditions.get("transition", {})
        end = transition.get("transition_end")
        severity = "attention"
        if end and today >= date.fromisoformat(end):
            note = ("Переходный период завершен. Проверьте дату изготовления и документы: "
                    "законно выпущенная ранее продукция может обращаться до окончания срока годности. "
                    "Наличие ингредиента само по себе не устанавливает незаконность товара.")
    return rule.model_copy(update={"assessment_severity": severity, "assessment_note": note})
