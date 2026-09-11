from sqlalchemy import Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidMixin


class User(UuidMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str | None] = mapped_column(Text, unique=True)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="user")

    auth_identities = relationship("AuthIdentity", back_populates="user")
    subscriptions = relationship("Subscription", back_populates="user")
    preferences = relationship("UserPreference", back_populates="user")
    scan_results = relationship("ScanResult", back_populates="user")

