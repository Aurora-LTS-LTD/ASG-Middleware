"""
Aurora LTS — Migrations Package (Sprint 8.3+)
==============================================

New-style migrations live under this package. Older migrations
remain at `app/migrate_phaseXX.py` for backward compatibility; new
features should add a module here and wire it from `main.py`.

Each module exposes a `run_<name>_migrations()` callable that is
idempotent and safe to invoke on every Cloud Run boot.
"""
