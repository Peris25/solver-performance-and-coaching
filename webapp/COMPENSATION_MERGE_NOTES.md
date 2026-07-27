# Compensation Engine — merge notes

The standalone **Solvit Compensation Engine v5** has been folded into this
Performance & Coaching portal as a self-contained module. The portal remains
the host (it owns auth, the deploy, and the URL); the engine plugs in behind
the same login.

## What was added

| File | Purpose |
|------|---------|
| `app/compensation_engine.py` | The v5 calculation logic — **copied verbatim**, pure Python, no DB. Single source of truth for T1/T2 thresholds and pay. |
| `app/compensation_seed.py` | The 116-solver seed roster with historical averages (2024–2026). |
| `app/compensation_routes.py` | The engine's REST API, ported from async→sync SQLAlchemy, mounted under `/api/comp/*`, auth-protected. |
| `app/static/compensation.html` | The engine's frontend, served at `/compensation` and embedded in the portal via an iframe. |

Modified: `app/models.py` (3 new tables), `app/main.py` (router include + seed + `/compensation` route), `app/static/index.html` (sidebar button + iframe overlay). `requirements.txt` needed **no new dependencies**.

## The five collisions and how they were resolved

1. **Table name clash** — the engine's `solvers`/`period_entries`/`audit_log`
   were renamed to **`solver_compensation`** / **`comp_period_entries`** /
   **`comp_audit_log`** so they never touch the portal's own `solvers` roster.
2. **Route clash** — engine routes moved from `/api/*` to **`/api/comp/*`**.
3. **Async vs sync** — the engine was rewritten to use the portal's synchronous
   `Session` (`app.database.get_db`); its async engine/asyncpg were dropped.
4. **No auth** — every `/api/comp/*` route now depends on `auth.require_admin`.
5. **Two entrypoints / roots** — only the portal's `app.main:app` and `/` remain.

## Design decisions

- **Separate roster, matched by name.** `solver_compensation` is its own table,
  not merged into the portal's `Solver` roster. Zero risk to existing data; it
  carries fields the roster doesn't (historical averages, manual override). The
  two are keyed by solver name and can be unified later if desired.
- **Iframe embed.** The compensation UI is served as its own page and embedded,
  so the engine's ~980 lines of JS/CSS can't collide with the portal's ~3,500.

## How it behaves at runtime

- On first boot, `lifespan` in `main.py` creates the new tables and seeds the
  116 solvers (idempotent — skipped once rows exist).
- Sidebar → **💰 Compensation engine** opens the module full-screen; **← Back
  to Performance portal** returns. A `401` from any `/api/comp` call bounces the
  user to `/login`.

## Excel export

`POST /api/comp/export/xlsx` builds a styled workbook **server-side** with
openpyxl (already a portal dependency) — authoritative and identical across
browsers. Three sheets: **Billing Schedule** (active solvers + totals row,
formatted for invoicing), **Full Breakdown** (all 116 solvers, every threshold
and pay column), **Summary** (period headline metrics). The dashboard's
"Download This Period (Excel)" and the billing tab's Excel button both call it.
The client-side SheetJS is now used only for parsing *uploaded* files and CSV
templates.

## Verified

An isolated functional test (auth gate, seeding, threshold math, compute+save,
period history, upsert dedup, override set/clear, audit log, CSV export, 404s)
passes 20/20, and the embedded UI was confirmed loading all 116 solvers live
through `/api/comp`. Full end-to-end boot still requires the portal's existing
deps (pandas, matplotlib, openpyxl, python-docx, psycopg2), unchanged by this merge.
