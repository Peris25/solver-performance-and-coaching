# Solvit Performance & Coaching Portal

Web app for monitoring solver performance, generating coaching reports, and emailing them to solvers who didn't meet targets.

- **Stack**: FastAPI + SQLAlchemy + PostgreSQL (SQLite for local dev). Vanilla JS + Chart.js frontend.
- **Brand**: red / white / black (Solvit corporate colors).
- **Auth**: single admin user, bcrypt password, signed session cookies.
- **Deploy**: Docker-compose (web + Postgres), or push the image to Render / Railway / Fly.io.

## Quick start (Docker)

```bash
cp .env.example .env
# edit .env — set POSTGRES_PASSWORD, SECRET_KEY, ADMIN_PASSWORD_HASH (and optionally SMTP_*)
docker compose up -d --build
```

Visit **http://localhost:8000** and sign in. Default credentials: `admin` / `changeme` (change before deploying anywhere public — instructions below).

## What's in the dashboard

**Six KPI cards across the top** showing team-level numbers:
- Total solvers
- **Jobs assigned** (sum of all rows in TOTAL VALUED + PENDING REASONS, per the `Solver` column)
- **Jobs initiated by solvers** (rows where `Initiator_Source` = "Solver")
- Team submission rate (valued ÷ assigned)
- Median response TAT across solvers
- Team mean client rating

**Distribution histograms** showing how the team is spread across each metric, with target zones in black and out-of-target in red.

**Sortable, searchable solver table** with one row per solver. Click any row to open the detail drawer.

**Solver drawer** — for each solver:
- Scorecard bars: their value vs target vs team marker for the four headline metrics
- All numbers in detail (valued, assigned, initiated, submission rate, both TATs, rating, stuck jobs, approval rate)
- History trend charts (need ≥ 2 uploaded periods to populate)
- Coaching focus content — the same text that appears in their Word doc
- Top pending reasons
- **Email address management inline** + buttons to download the doc, email it to them, or copy a personalized intro

**Email triggers**:
- Each solver row gets an email input in their drawer
- "Email coaching to solver" — manual send for one solver
- "Email coaching to solvers missing target" — page-level button at the top, sends to everyone with a `needs_work` classification, skips strong performers and anyone without an email on file

## Metric definitions

These are computed exactly the same way as the standalone Python skill (`app/analysis.py` is the source of truth):

| Metric | Definition | Target |
|---|---|---|
| **Jobs assigned** | Count of rows in TOTAL VALUED + count of rows in PENDING REASONS for that solver (uses the `Solver` column in TV and `solver_name` in PR) | n/a (info only) |
| **Jobs initiated by solver** | Count of rows where `Initiator_Source` == "Solver" AND `Initiated_by` matches the solver name | n/a (info only) |
| **Volume / Valued** | Count of rows in TOTAL VALUED for the solver (every TV row has a `Valuation_Date`) | ≥ 60 / month |
| **Submission rate** | valued ÷ assigned | ≥ 85% |
| **Response TAT** (median per solver) | `Valuation_Date` − `Schedule_date` (falls back to `Requested_Date`) | ≤ 4 hours |
| **On-site TAT** (mean per solver) | `Valuation_Date` − `Valuation_Start` | ≤ 30 minutes |
| **Client rating** (mean of `rating` in CLIENT RATING) | average of provided ratings | ≥ 4.5 / 5 |
| **Approval rate** | Approved ÷ all attempts in TOTAL VALUED | ≥ 95% (info only, doesn't drive coaching) |
| **Stuck jobs** | Count of rows where response TAT > 72 hours | report only |

All targets are editable in `app/analysis.py` (the `TARGETS` dict). Restart the service after changing.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  Browser (vanilla JS + Chart.js + custom CSS)            │
│  /              dashboard (red/white/black brand)        │
│  /login         admin login                              │
└──────────────────────┬───────────────────────────────────┘
                       │ HTTPS, session cookie
                       ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI                                                 │
│   /api/auth/login, /logout, /me                          │
│   /api/uploads          (multipart: file + label)        │
│   /api/periods, /periods/{id}                            │
│   /api/solvers/{name}/history                            │
│   /api/periods/{id}/solvers/{name}/report  (.docx)       │
│   /api/periods/{id}/solvers/{name}/email                 │
│   /api/periods/{id}/send-coaching-emails  (bulk)         │
│   /api/periods/{id}/email-log                            │
│   /api/solver-emails    (CRUD for name->email)           │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────┐
│  PostgreSQL (or SQLite in dev) — 4 tables                │
│  - periods             one row per uploaded workbook     │
│  - solver_snapshots    one row per solver per period     │
│  - solver_emails       name -> email mapping             │
│  - email_send_logs     audit of every coaching email     │
└──────────────────────────────────────────────────────────┘
```

## Project layout

```
webapp/
├── app/
│   ├── main.py           FastAPI app factory, lifespan, static mount
│   ├── config.py         Settings from env (SECRET_KEY, DATABASE_URL, SMTP_*, ADMIN_*)
│   ├── database.py       SQLAlchemy engine + session
│   ├── models.py         Period, SolverSnapshot, SolverEmail, EmailSendLog
│   ├── analysis.py       Excel -> metrics (port of skill's analyse.py)
│   ├── reports.py        metrics -> .docx (single page, scorecard + coaching)
│   ├── auth.py           bcrypt + signed cookies
│   ├── emails.py         SMTP send with .docx attached
│   ├── routes.py         All API endpoints in one file
│   └── static/
│       ├── index.html    Dashboard (single-page app)
│       └── login.html    Login page
├── Dockerfile            multi-stage, non-root, healthcheck
├── docker-compose.yml    web + postgres
├── .env.example          all environment variables documented
├── .dockerignore
├── requirements.txt
└── README.md             this file
```

## API reference

All endpoints under `/api/*`. Everything except `/api/health` and `/api/auth/login` requires the `solvit_session` cookie.

### Auth
- `POST /api/auth/login` — `{username, password}` → sets `solvit_session` cookie (HttpOnly, signed)
- `POST /api/auth/logout` — clears the cookie
- `GET /api/auth/me` — `{user: "admin"}` if logged in

### Periods (uploads)
- `POST /api/uploads` — multipart form: `file` (.xlsx) + `label` (string). Parses, computes all metrics, persists as a Period + N snapshots. Replaces if a Period with the same label exists.
- `GET /api/periods` — list of all periods (id, label, uploaded_at, counts)
- `GET /api/periods/{id}` — full period with team stats and all solver snapshots
- `DELETE /api/periods/{id}` — removes the period and its snapshots

### Solvers
- `GET /api/solvers/{name}/history` — all snapshots for one solver across periods, oldest first (for trend charts)

### Reports
- `POST /api/periods/{id}/solvers/{name}/report` — returns the `.docx` as a download. Body: `{intro?: string}` to override the auto-generated intro.

### Solver emails
- `GET /api/solver-emails` — list all stored name → email mappings
- `PUT /api/solver-emails/{name}` — body `{email: string}`, upserts
- `DELETE /api/solver-emails/{name}` — removes one

### Email send
- `POST /api/periods/{id}/solvers/{name}/email` — send one coaching email. 400 if no email on file; 500 if SMTP not configured.
- `POST /api/periods/{id}/send-coaching-emails` — bulk send. Skips strong performers and anyone without an email on file. Returns a summary `{sent, failed, skipped_no_email, skipped_strong}` and a per-solver result list.
- `GET /api/periods/{id}/email-log` — audit log of every email attempt for this period.

FastAPI also auto-generates an interactive API explorer at `/docs` while running.

## Environment variables

See `.env.example` for the canonical list. The critical ones to set before production:

| Variable | Required | What it does |
|---|---|---|
| `DATABASE_URL` | Yes (in prod) | Postgres connection string. Falls back to SQLite if unset. |
| `SECRET_KEY` | Yes | Signs the session cookie. Generate with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `ADMIN_USERNAME` | No (default `admin`) | Login username. |
| `ADMIN_PASSWORD_HASH` | Yes | Bcrypt hash. Generate with `python -c "from app.auth import hash_password; print(hash_password('your-password'))"`. |
| `SESSION_HOURS` | No (default 24) | Session lifetime. |
| `SMTP_HOST` | No (disables email if blank) | e.g. `smtp.gmail.com`, `smtp.office365.com` |
| `SMTP_PORT` | No (default 587) | |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | If SMTP_HOST set | Gmail requires an [app password](https://support.google.com/accounts/answer/185833). |
| `SMTP_FROM_EMAIL` | If SMTP_HOST set | The from address. Must match the authenticated user for most providers. |
| `SMTP_FROM_NAME` | No | Display name, e.g. "Solvit Operations". |

## Production checklist

Before exposing the portal to the internet:

1. **Set a strong `SECRET_KEY`** (48 random bytes) and `ADMIN_PASSWORD_HASH`.
2. **Set `POSTGRES_PASSWORD`** in `.env`.
3. Put it **behind HTTPS** (Caddy / Nginx / a managed platform). Then change `secure=False` to `secure=True` in `app/routes.py` (the `login` handler's `response.set_cookie` call) so the session cookie is HTTPS-only.
4. **Configure SMTP** so coaching emails can actually send.
5. **Add solver email addresses** via the "Manage solver emails" button in the sidebar (or seed the `solver_emails` table directly).
6. **Test the bulk send** with one or two real solvers first, then expand.

## Email behavior

When the admin clicks "Email coaching to solvers missing target":

1. The portal iterates every solver snapshot in the current period.
2. **Skipped: strong performers** — anyone whose only `focus_area` is "strong" (no `needs_work` classifications). They don't need a coaching email.
3. **Skipped: no email on file** — reported in the result so the admin can add them. The portal auto-opens the Emails modal if there are any.
4. **Sent** — for everyone else: generates their `.docx` server-side, attaches it to a personalized email (subject and body name their actual focus areas), and sends via SMTP. Every attempt is logged.

The email body is built from each solver's metrics — it names specific numbers and the focus area, with text/HTML versions. See `app/emails.py` for the templates.

## Re-uploads

Uploading a workbook with the **same period label** as an existing period **replaces** that period's solver snapshots. The Period id stays the same (so links and email log rows aren't orphaned). The Excel file itself is also saved to `./uploads/` with a timestamped filename for audit.

Different labels (e.g. "April 2026" vs "May 2026") create separate periods, which is what enables the history trend charts.

## Running locally without Docker (dev)

```bash
pip install -r requirements.txt
export DATABASE_URL="sqlite:///./data/portal.db"
export SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
# Default ADMIN_PASSWORD_HASH = "changeme" — fine for dev
uvicorn app.main:app --reload
```

`--reload` watches files. Visit http://localhost:8000.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "401 Not authenticated" right after a successful upload | Session expired (default 24h). Sign in again, or bump `SESSION_HOURS`. |
| "Workbook missing required sheets" on upload | Excel needs exact sheet names: `TOTAL VALUED`, `CLIENT RATING`, `PENDING REASONS` (case-sensitive). |
| "SMTP is not configured" when emailing | Set the `SMTP_*` env vars (see `.env.example` for Gmail/M365 examples). |
| "Authentication failed" from SMTP | For Gmail: use a 16-char app password, not your account password. For M365: make sure SMTP AUTH is enabled for the account. |
| Postgres container won't start | `POSTGRES_PASSWORD` not set in `.env`. The compose file refuses to start without it (intentional). |
| Numbers don't match the standalone skill | They should — same logic. If they differ, check the workbook is the same. |

## What's NOT in this version (future enhancements)

The architecture supports these without restructuring, but they're not built yet:

- **Multi-user roles** — currently admin-only. Adding a `User` table + role check on routes would be a couple-hour addition.
- **Zoho API direct pull** — replace the manual upload with a scheduled task. `analyse_workbook()` is the stable interface; you'd feed it a DataFrame instead of an Excel file.
- **Scheduled email sends** — currently triggered manually. Adding APScheduler or a cron job to call the bulk-send endpoint nightly/monthly is straightforward.
- **In-app email preview** — currently you have to trust the template. A "preview" endpoint that returns the HTML body without sending would help testing.

---

## For the IT developer

This codebase is small (~3,500 lines) and intentionally boring. If you've used FastAPI before, you'll be at home in 30 minutes.

**Where to look first:**
- `app/routes.py` — every endpoint, in order. Read top to bottom.
- `app/analysis.py` — the core business logic. All metric definitions live here.
- `app/static/index.html` — the entire frontend. No build step; edit and reload.

**How to add a new metric:**
1. Compute it in `app/analysis.py` (add to the per-solver loop and team stats).
2. Add a column to `models.SolverSnapshot` (and to `Period` if it's team-level).
3. Persist it in the upload handler (`routes.upload_workbook`).
4. Return it from `routes.snapshot_to_dict` and `routes.get_period`.
5. Display it in `app/static/index.html` (KPI card, table column, drawer box).

For schema changes, the cleanest approach is to add an Alembic migration. As a quick alternative for a small system, you can drop and recreate the DB during development (data is reloadable from the source Excel files).

**Running tests** — there isn't a test suite yet. The endpoint-level verification done during development is in this README's smoke-test commands; converting those to pytest would be the obvious next step (`pytest` + `httpx.AsyncClient` against the FastAPI app).
