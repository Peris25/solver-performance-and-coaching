# Developer Handoff — Solvit Performance Portal

This document tells you exactly what you need to take this code from "works on a laptop" to "live for the team to use."

You're inheriting a working, tested web application. The code base is small (~3,500 lines), all in one repo, with no microservices or build steps. The hard part is operational: hosting, credentials, monitoring, and the Zoho API integration when that time comes.

---

## 1. What you're inheriting

| Component | Status | Where it lives |
|---|---|---|
| FastAPI backend | ✅ Working | `app/` |
| PostgreSQL schema | ✅ Working | `app/models.py` (auto-creates on startup) |
| Admin login (single user) | ✅ Working | `app/auth.py` |
| Excel upload + analysis | ✅ Working | `app/analysis.py` + `app/routes.py` (`upload_workbook`) |
| Dashboard UI (red/white/black brand) | ✅ Working | `app/static/index.html` |
| Per-solver Word doc generation | ✅ Working | `app/reports.py` |
| Email send via SMTP (manual + bulk) | ✅ Built, ⚠️ needs SMTP creds | `app/emails.py` |
| Dockerfile + docker-compose | ✅ Working | `Dockerfile`, `docker-compose.yml` |
| Test against real data (April 2026 Zoho export) | ✅ Done | 87 solvers, 84.0% submission rate computed correctly |

---

## 2. What's pending (your work)

Pri 1 (before launch):
- [ ] Pick a host. Recommended: a managed Postgres + container platform like Render, Railway, or Fly.io. Self-hosting on a VPS also works — full instructions in README.md.
- [ ] Generate production secrets:
  ```bash
  python -c "import secrets; print(secrets.token_urlsafe(48))"      # SECRET_KEY
  python -c "from app.auth import hash_password; print(hash_password('STRONG-PASSWORD'))"  # ADMIN_PASSWORD_HASH
  ```
- [ ] Set up SMTP. Use the company's existing Office 365 or Gmail Workspace account; create a dedicated sender like `ops-noreply@solvit.co.ke`. Settings go in env vars (see `.env.example`).
- [ ] Put it behind HTTPS. Toggle `secure=False` → `secure=True` in `app/routes.py` (search for "session cookie"). Most managed platforms handle TLS automatically.
- [ ] Test the bulk-email flow with **one real solver** before sending to the whole team. Important.
- [ ] Decide how solver emails get into the system: enter them once via the "Manage solver emails" button, or seed the `solver_emails` table from a CSV.

Pri 2 (within 1-2 months):
- [ ] **Zoho API integration**: replace the manual upload with a scheduled pull. The endpoint to wire up is in `app/routes.py` (`upload_workbook`) — you'd replace the file-reading step with a Zoho API call but keep `analyse_workbook()` as-is.
- [ ] Scheduled bulk email: cron or APScheduler that calls `/api/periods/{latest}/send-coaching-emails` monthly after each Zoho pull.
- [ ] Set up monitoring: at minimum, a healthcheck ping every 5 min (e.g. UptimeRobot hitting `/api/health`).
- [ ] Backup strategy for Postgres. Most managed platforms include this; if self-hosting, set up `pg_dump` cron.

Pri 3 (nice to have):
- [ ] Pytest suite for the API endpoints. The end-to-end smoke test in README.md is the script to convert.
- [ ] In-app email preview endpoint (so you can see the email body without sending).
- [ ] Multi-user roles (team leads see only their team, solvers see only themselves).

---

## 3. The 30-minute deploy

If you want to get this live as fast as possible:

```bash
# 1. Clone / extract the code
tar -xzf solvit-webapp.tar.gz
cd webapp

# 2. Configure
cp .env.example .env
# Edit .env — fill in:
#   POSTGRES_PASSWORD=<strong-password>
#   SECRET_KEY=<generated above>
#   ADMIN_PASSWORD_HASH=<generated above>
#   SMTP_* (if you want emails right away — can be added later)

# 3. Build and run
docker compose up -d --build

# 4. Visit http://your-server:8000 and sign in
```

That's it. Health check at `/api/health`. Postgres data persists in a Docker volume (`pg_data`). Uploaded Excels persist in another volume (`uploads`).

For HTTPS, put Caddy or Nginx in front. Example Caddyfile:
```
portal.solvit.co.ke {
  reverse_proxy localhost:8000
}
```

---

## 4. Code architecture (15-minute orientation)

**Read in this order:**

1. `README.md` — the user-facing documentation. Read this first to understand what the system does.
2. `app/analysis.py` — the core business logic. All metric definitions live here in the `TARGETS` dict at the top. The `analyse_workbook()` function takes an Excel file path and returns a dict matching the database schema.
3. `app/routes.py` — every API endpoint, top to bottom, grouped by section. There are no service classes; routes call the analysis module and the database directly. Boring on purpose.
4. `app/static/index.html` — the entire frontend, ~1,400 lines of vanilla JS + CSS. No React, no build step.
5. `app/models.py` — four database tables. SQLAlchemy 2.0 declarative style.
6. `app/reports.py` — generates the Word `.docx`. The chart in the report is a matplotlib PNG embedded inline.
7. `app/emails.py` — SMTP send. Standard `smtplib` + `email.message.EmailMessage`.

**Key design decisions you should know:**

- **No microservices, no message queue**. One process serves the API and the static frontend. For one admin and 80-odd solvers, this is the right size.
- **Postgres for the DB, Docker volumes for uploads**. The Excel files themselves are kept (timestamped) so re-analysis is always possible without re-asking Zoho.
- **Auth is one cookie**. We don't have OAuth, JWT, or a refresh token dance because there's only one user. When you add multi-user support, the cleanest path is to add a `users` table and a `role` field; the cookie infrastructure stays the same.
- **All targets live in `app/analysis.py`**. Editing them requires a restart but no migration.
- **Re-upload semantics**: same label = replace existing snapshots. Different label = new period. This is how monthly uploads accumulate as history.

---

## 5. How the metrics are computed (read carefully)

This was the most-discussed area during development. The exact rules:

**Jobs assigned to a solver** = count of rows in TOTAL VALUED for that solver (uses the `Solver` column) + count of rows in PENDING REASONS for that solver (uses the `solver_name` column).

```python
assigned_count = valued_count + pending_count
```

**Jobs initiated by a solver** = count of rows in TOTAL VALUED where `Initiator_Source == "Solver"` AND `Initiated_by` matches the solver's name. This is the work they brought in themselves, vs work allocated to them by schedulers.

```python
# 40% of all assignments in April 2026 were self-sourced by solvers
# Top initiators: Samuel Home (247), josphat mwangi (101), Stephen Mwangi (88)
```

**Submission rate** = `valued_count / assigned_count`. Target ≥ 85%.

**Response TAT** = `Valuation_Date - Schedule_date` (falls back to `Requested_Date` if Schedule is null). Per solver, we use the **median** (robust to a few extreme outliers). Target ≤ 4 hours.

**On-site TAT** = `Valuation_Date - Valuation_Start`. Per solver, we use the **mean**. Target ≤ 30 minutes.

**Client rating** = mean of `rating` column in CLIENT RATING for that solver. Target ≥ 4.5/5. Solvers with fewer than 3 ratings are classified as "insufficient data."

**Stuck jobs** = jobs where response TAT > 72 hours. Reported but doesn't directly drive coaching (because the chart shows medians; stuck jobs are in the tail).

---

## 6. The email trigger logic

When the admin clicks **"Email coaching to solvers missing target"** (top right of the dashboard):

```python
for snap in period.snapshots:
    if not snap.focus_areas:                  # solver has no needs_work flags
        skip                                  # = "strong performer"
    elif snap.focus_areas == ["strong"]:      # same
        skip
    elif solver_emails.get(snap.name) is None:
        report "no email on file"
    else:
        # Generate their .docx
        # Send via SMTP with the doc attached
        # Log result to email_send_logs
```

A solver is in `focus_areas` if ANY classification = `needs_work`. Currently that means: response TAT > 6h (1.5× target), or on-site > 45min (1.5× target), or submission rate < 70%, or rating < 4.0 (or < team_avg - 0.5).

**To change who gets emailed**, edit `infer_focus_areas()` in `app/analysis.py` and re-upload the workbook (so the new flags are persisted to the snapshots).

---

## 7. SMTP setup gotchas

**Gmail**: requires an app password, not your account password. Steps:
1. Enable 2FA on the Google account
2. Visit https://myaccount.google.com/apppasswords
3. Generate a 16-character app password
4. Use that as `SMTP_PASSWORD`, with `SMTP_HOST=smtp.gmail.com` and `SMTP_PORT=587`

**Microsoft 365**: SMTP AUTH is often disabled by default at the tenant level. The IT admin needs to enable it for the specific sending account.

**Mailgun / SendGrid**: standard SMTP creds work; just use their `smtp.mailgun.org` / `smtp.sendgrid.net` host.

If SMTP is misconfigured, the portal won't crash — it returns clean error messages in the UI ("SMTP is not configured" or the actual SMTP error). Everything is logged to `email_send_logs` so you can debug.

---

## 8. Adding Zoho integration

When you're ready to replace the manual upload:

1. Get a Zoho API client_id / client_secret (Zoho Developer Console).
2. Add a new module `app/zoho.py` that pulls the three sheets' worth of data as DataFrames.
3. Refactor `analyse_workbook()` slightly so it can accept DataFrames directly (it currently reads from a file path; the change is trivial).
4. Add a scheduled endpoint or background task (APScheduler is easiest):
   ```python
   @scheduler.scheduled_job("cron", day=1, hour=8)
   async def monthly_pull():
       data = zoho.pull_last_month()
       result = analyse_dataframes(data)
       save_as_period(result, label=last_month_label())
       send_bulk_coaching_emails(latest_period_id)
   ```

The contract from `analyse_workbook()` is the stable interface — everything downstream works the same whether the data came from Excel or Zoho.

---

## 9. Database schema reference

```
periods
├── id (pk)
├── label (unique, e.g. "April 2026")
├── uploaded_at
├── uploaded_filename
├── total_solvers, total_valuations
├── total_jobs_assigned, total_jobs_pending
├── total_jobs_initiated_by_solvers
├── team_submission_rate, median_submission_rate
├── avg_/median_ response_tat_hrs
├── avg_/median_ onsite_tat_hrs
├── avg_rating, avg_volume, median_volume
└── pct_stuck_jobs_team

solver_snapshots
├── id (pk)
├── period_id (fk → periods)
├── name (indexed — joins to solver_emails by name)
├── volume, total_attempts, valued_count
├── assigned_count, pending_count, jobs_initiated
├── stuck_job_count, n_ratings
├── submission_rate, approval_rate
├── avg_/median_ response_tat_hrs
├── avg_/median_ onsite_tat_hrs
├── avg_rating, stuck_job_rate
├── classifications (json)
├── training_modules (json)
├── focus_areas (json)
└── extra (json — sub-ratings, top pending reasons)

solver_emails
├── id (pk)
├── name (unique)
├── email
└── updated_at

email_send_logs
├── id (pk)
├── period_id (fk → periods)
├── solver_name (indexed)
├── email_to
├── sent_at
├── status ("sent" | "failed")
├── error (text, nullable)
├── trigger ("manual" | "bulk_targets_missed")
└── focus_areas (json — snapshot at time of send)
```

---

## 10. Backups and disaster recovery

**What needs backup**:
- The Postgres database (`pg_data` volume in Docker, or your managed Postgres instance)
- The `uploads/` directory (raw Excel files for re-analysis if needed)

**What's recoverable without backup**:
- The analysis output. If you have the source Excels, you can re-upload them and the snapshots regenerate. The only things lost would be solver emails (`solver_emails` table) and the email send history (`email_send_logs`).

**Recommendation**: nightly `pg_dump` to S3 or equivalent. Most managed Postgres services include this. For the uploads volume, a weekly rsync to off-host storage is enough.

---

## 11. Contact for questions

This codebase was developed in collaboration with the Solvit operations lead. They know how the metrics map to the business and can answer:
- "What does this number mean?"
- "Why is this target set to X?"
- "What should the email say when a solver misses target Y?"

For code-level questions, the README and inline comments cover the architecture. The git history (when you put this in a repo) will be your friend.
