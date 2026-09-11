from pydantic import BaseModel


class AuthFeatureState(BaseModel):
    status: str = "planned"
    message: str = "Registration and authorization will be added later."

