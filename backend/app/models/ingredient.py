from sqlalchemy import Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidMixin


class Ingredient(UuidMixin, TimestampMixin, Base):
    __tablename__ = "ingredients"

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    e_code: Mapped[str | None] = mapped_column(Text, unique=True)
    canonical_name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_name_en: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    aliases = relationship("IngredientAlias", back_populates="ingredient")
    rules = relationship("IngredientRule", back_populates="ingredient")

