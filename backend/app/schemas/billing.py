from pydantic import BaseModel


class BillingFeatureState(BaseModel):
    status: str = "planned"
    message: str = "Premium subscriptions and payment webhooks will be added later."

