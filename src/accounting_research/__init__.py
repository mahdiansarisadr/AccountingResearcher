"""Accounting Research Assistant — Phase 1.

A text-to-SQL agent that answers accounting questions over a seeded Postgres
store, selecting relevant tables via a pgvector-backed schema catalog and
returning cited, confidence-scored answers.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
