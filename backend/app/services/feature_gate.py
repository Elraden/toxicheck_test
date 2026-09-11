from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureAccess:
    allowed: bool
    reason: str | None = None


class FeatureGate:
    def can_save_scan_history(
        self,
        *,
        is_authenticated: bool,
        subscription_status: str | None,
    ) -> FeatureAccess:
        if not is_authenticated:
            return FeatureAccess(False, "Authentication is required.")

        if subscription_status != "active":
            return FeatureAccess(False, "Premium subscription is required.")

        return FeatureAccess(True)

    def can_compare_products(
        self,
        *,
        is_authenticated: bool,
        subscription_status: str | None,
    ) -> FeatureAccess:
        return self.can_save_scan_history(
            is_authenticated=is_authenticated,
            subscription_status=subscription_status,
        )

    def can_use_anonymous_preferences(self) -> FeatureAccess:
        return FeatureAccess(True)

