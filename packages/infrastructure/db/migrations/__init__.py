"""Alembic migration scripts for the platform.

`env.py` is configured separately; for this first slice we ship a
hand-written initial revision (`0001_initial.py`) that creates every
core table. The file is meant to be the single source of truth for the
day-one schema — any later change goes through a new revision.
"""
