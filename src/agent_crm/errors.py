"""Domain errors raised by the tooling layer.

Agents catch these instead of SQLAlchemy internals, so the store can change
backends without agents changing their error handling.
"""

from __future__ import annotations


class CRMError(Exception):
    """Base class for all CRM tooling errors."""


class NotFoundError(CRMError):
    """A referenced record does not exist."""


class InvalidStageTransition(CRMError):
    """A requested pipeline stage change is not allowed from the current stage."""
