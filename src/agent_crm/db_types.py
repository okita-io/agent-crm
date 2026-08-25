"""Database type helpers for SQLAlchemy enums."""

from __future__ import annotations

from enum import Enum

from sqlalchemy import Enum as SAEnum


def str_enum(enum_class: type[Enum]) -> SAEnum:
    """Map a str enum to Postgres/SQLite using lowercase ``.value`` labels.

    Use this for *new* enum columns only. Existing enums (Brand, LeadSource, …)
    keep the default name-bound mapping.
    """

    return SAEnum(enum_class, values_callable=lambda obj: [member.value for member in obj])
