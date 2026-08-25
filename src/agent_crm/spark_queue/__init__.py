"""OpenAI-compatible proxy that rate-limits Spark SGLang concurrency."""

from .app import app

__all__ = ["app"]
