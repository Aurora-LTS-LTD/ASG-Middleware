"""
Aurora LTS — Configuration package (Sprint 5, Appendix M).

Single home for cross-cutting Aurora configuration that is too small
for a full service module but too important to scatter across env
reads in random files. Modules:

  • feature_flags.py — Pre-Armed feature gating + Growth Milestones

This package is intentionally thin. App-startup code reads from here;
nothing here reads from the app — strict one-way dependency.
"""
