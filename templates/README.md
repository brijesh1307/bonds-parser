# Approved templates

One JSON file per approved slip layout (`<template_id>.json`), written by the API when a mapping is
approved (`POST /api/v1/templates`) and read on every parse. Each file holds **labels and mapping
rules only** — never PDFs, deal values or PANs — so it is safe to review and version in git.

- Location: `BONDS_TEMPLATES_DIR` (default `./templates`).
- Every version is kept inside the file; a change always adds a new version.
- Move templates between environments with `GET /api/v1/templates/{id}` → `POST /api/v1/templates/import`.
- Format: `docs/00_design_baseline.md` §6.
