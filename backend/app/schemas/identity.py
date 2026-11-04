from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionUser(BaseModel):
    id: UUID
    role: str
    account_kind: str
    display_name: str | None = None


class BrowserSession(BaseModel):
    user: SessionUser | None = None
    csrf: str | None = None
    admin: SessionUser | None = None
    admin_csrf: str | None = None


class AdminLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.@-]+$")
    password: str = Field(min_length=1, max_length=256, repr=False)


class PersonalPreferences(BaseModel):
    excludedIngredientIds: list[str]
    revision: int
    localImported: bool
    unresolvedIds: list[str] = Field(default_factory=list)


class SavePersonalPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excludedIngredientIds: list[UUID] = Field(max_length=2000)
    revision: int = Field(ge=0)


class ImportPersonalPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excludedIngredientIds: list[str] = Field(max_length=2000)
