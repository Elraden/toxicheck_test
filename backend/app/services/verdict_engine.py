from app.schemas.analysis import (
    AnalysisPreferences,
    ProductVerdict,
    VerdictReason,
)
from app.schemas.ingredients import MatchedIngredientOut
from app.services.normalization import normalize_text


class VerdictEngine:
    def build_verdict(
        self,
        matched: list[MatchedIngredientOut],
        unmatched: list[str],
        preferences: AnalysisPreferences,
    ) -> ProductVerdict:
        reasons = self._collect_reasons(matched, preferences)
        highest_severity = self._highest_severity(reasons)
        risk_score = self._risk_score(highest_severity, len(reasons), len(unmatched))

        if highest_severity == "neutral":
            return ProductVerdict(
                level="unknown", risk_score=0,
                title="Недостаточно данных для оценки" if unmatched else "Предупреждений не найдено",
                description=("Часть ингредиентов не найдена в справочнике. Это не подтверждает безопасность продукта."
                             if unmatched else "По найденным правилам предупреждений нет. Допустимость применения зависит от количества и категории продукта."),
                reasons=reasons,
            )

        return ProductVerdict(
            level=self._level(highest_severity),
            risk_score=risk_score,
            title=self._title(highest_severity),
            description=self._description(highest_severity, len(unmatched)),
            reasons=reasons,
        )

    def _collect_reasons(
        self,
        matched: list[MatchedIngredientOut],
        preferences: AnalysisPreferences,
    ) -> list[VerdictReason]:
        excluded_ids = set(preferences.excluded_ingredient_ids)
        excluded_names = {
            normalize_text(name)
            for name in preferences.excluded_names
        }
        reasons: list[VerdictReason] = []

        for ingredient in matched:
            if ingredient.severity != "neutral":
                reasons.append(
                    VerdictReason(
                        severity=ingredient.severity,
                        title=ingredient.name,
                        explanation=self._rule_explanation(ingredient),
                        ingredient_id=ingredient.ingredient_id,
                        raw_text=ingredient.raw_text,
                    )
                )

            if (
                ingredient.ingredient_id in excluded_ids
                or normalize_text(ingredient.name) in excluded_names
            ):
                reasons.append(
                    VerdictReason(
                        severity="personal",
                        title=f"Персональное исключение: {ingredient.name}",
                        explanation="Ингредиент отмечен пользователем как нежелательный.",
                        ingredient_id=ingredient.ingredient_id,
                        raw_text=ingredient.raw_text,
                    )
                )

        return reasons

    @staticmethod
    def _rule_explanation(ingredient: MatchedIngredientOut) -> str | None:
        if not ingredient.rules:
            return None

        return (ingredient.rules[0].assessment_note
                or ingredient.rules[0].explanation or ingredient.rules[0].title)

    @staticmethod
    def _highest_severity(reasons: list[VerdictReason]) -> str:
        weights = {
            "neutral": 0,
            "attention": 1,
            "personal": 2,
            "avoid": 3,
            "forbidden": 4,
        }

        return max(
            (reason.severity for reason in reasons),
            key=lambda severity: weights.get(severity, 0),
            default="neutral",
        )

    @staticmethod
    def _risk_score(severity: str, reasons_count: int, unmatched_count: int) -> int:
        base_score = {
            "neutral": 10,
            "attention": 45,
            "personal": 65,
            "avoid": 80,
            "forbidden": 95,
        }.get(severity, 10)

        return min(100, base_score + max(0, reasons_count - 1) * 5 + unmatched_count)

    @staticmethod
    def _level(severity: str) -> str:
        if severity in {"forbidden", "avoid"}:
            return "dangerous"

        if severity == "personal":
            return "risky_for_user"

        if severity == "attention":
            return "attention"

        return "safe"

    @staticmethod
    def _title(severity: str) -> str:
        return {
            "forbidden": "Есть запрещенные ингредиенты",
            "avoid": "Лучше избегать",
            "personal": "Не подходит под ваши предпочтения",
            "attention": "С осторожностью",
            "neutral": "Без явных рисков",
        }.get(severity, "Без явных рисков")

    @staticmethod
    def _description(severity: str, unmatched_count: int) -> str:
        if severity == "neutral" and unmatched_count:
            return "Опасных совпадений не найдено, но часть состава не распознана."

        return {
            "forbidden": "В составе есть ингредиенты с наиболее строгими ограничениями.",
            "avoid": "В составе есть ингредиенты, которые лучше исключить или ограничить.",
            "personal": "В составе есть ингредиенты из вашего персонального списка.",
            "attention": "В составе есть ингредиенты, требующие внимания.",
            "neutral": "В составе не найдено ингредиентов из списка повышенного риска.",
        }.get(severity, "Результат проверки сформирован.")
