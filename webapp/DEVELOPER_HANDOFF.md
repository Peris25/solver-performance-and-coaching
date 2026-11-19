# Developer Handoff — Solvit Performance Portal

This document tells you exactly what you need to take this code from "works on a laptop" to "live for the team to use."

You're inheriting a working, tested web application. The code base is small, all in one repo, with no microservices or build steps. The hard part is operational: hosting, credentials, monitoring, and the Zoho API integration when that time comes.

---

## 0. What changed in this delivery (read this first if you've deployed a previous version)

**Newest change — the "whole Solvers system" expansion: fleet bonus, complaints/accidents, broadcast messaging, dormancy, performance flags, recaps, regional TAT allowance, exports.**

This delivery is built on top of the most advanced version of the portal (the one with row-level job records, the continuous-range talent grid, and backlog tracking — see the entries below this one), **not** the smaller/older `solvit-webapp.tar.gz` that came back from your side this round. That file was missing the talent grid, regions, backlog, and the row-level tables entirely — it looks like an earlier snapshot got attached by mistake. Worth checking where that copy came from so it doesn't get redeployed over the current one by accident. Everything below is new on top of the full version.

New files: `app/extensions.py` (all the new routes), `app/settings_store.py` (tiny key/value store for the admin-editable settings below — shared by routes.py and extensions.py to avoid a circular import). Everything else is additions to the existing files, not rewrites.

1. **Fleet sourcing bonus** (`/api/fleet-bonus*`, new "Fleet bonus" tab). Mirrors `SOLVERS__Fleet_Bonus_Calculator.xlsx` exactly: `rate` = the flat bonus rate (default 3%) only when `fleet_size_booked ≥ minimum` (default 10) **and** the client has confirmed satisfied/closed, otherwise 0; `billed = price_per_vehicle × approved_count`; `bonus = billed × rate`. Add entries by hand or import a workbook laid out like the calculator (it skips the example/instruction rows automatically). Rate and minimum are admin-editable (`/api/settings/fleet_bonus`) without a redeploy. Export to CSV/XLSX from the tab.

2. **Complaints & accidents log** (`/api/complaints*`, new tab). Deliberately simple: kind (complaint/accident), solver, region, date, severity, free-text description, status workflow (open → investigating → resolved/closed), resolution notes. Exports to CSV/XLSX.

3. **Broadcast messaging with attachments** (`/api/broadcast*`, new "Broadcast" tab). Send a custom subject/message, with any number of file attachments, to all solvers with an email on file, a region, or an individually-picked list. Goes out through the **same SMTP relay** already configured for coaching emails — point `SMTP_HOST`/`SMTP_USERNAME`/etc. at the company's Office 365 account to send it "through Outlook" that way. Every send is logged (`EmailSendLog`, visible in the tab's Send log table) for an audit trail. **If you'd rather this go through the Microsoft Graph API directly** (true "send as" through a shared mailbox, no SMTP app password) instead of SMTP, that needs an Azure app registration (client ID/secret, `Mail.Send` application permission) — `app/emails.py`'s `send_broadcast_email()` is the one function to swap out; nothing else would need to change.

4. **Active/dormant solver monitoring** (`/api/solvers/activity`, banner on the Solvers tab). A registered solver with **zero valued jobs across the last 2 uploaded periods** (admin-editable via `/api/settings/dormancy`) is flagged dormant — this also catches a solver who was never in any upload at all. The Solvers tab shows a banner with the active/dormant counts and names the dormant ones.

5. **Performance-change flags** (`/api/solvers/performance-flags`, same banner). Auto-compares the two most recently uploaded periods for every solver and flags anyone who moved by more than a "worth noticing" threshold (±1h TAT, ±8pts submission rate, ±0.3 rating, ±25% volume — all in `TARGETS` in `analysis.py`, easy to retune). This sits on top of the comparison logic that already existed (`compare_snapshots`) — `flag_significant_changes()` just adds the threshold layer so routine noise doesn't get flagged.

6. **Nairobi/Mombasa TAT travel allowance in the 9-box grid** (`/api/settings/tat_allowance`). A solver registered outside Nairobi or Mombasa gets **+3 hours** added to the 10h TAT target *for talent-grid scoring only* — the raw TAT numbers shown everywhere else (KPIs, the solver table, coaching emails) are untouched; only where they land on the grid changes. Both the region list and the allowance are admin-editable. **The 3-hour figure is a starting default, not something Solvit specified — flag it to whoever owns this policy and adjust via the settings endpoint (or just change `tat_allowance_hours` in `TARGETS`) once there's a real number.**

7. **Annual / quarterly recap** (`/api/solvers/{name}/recap` for JSON, `/recap/report` for a Word doc; buttons in each solver's drawer). Rolls up a solver's snapshots across every period in the given year (and quarter, if given) into one journey summary: career totals, trend direction (first half of the window vs. second half, for TAT/submission/rating), best TAT and best-rated periods, and how many periods they were recognised as strong vs. flagged for coaching. The window is matched on each period's **upload date**, since periods are human labels ("April 2026") rather than tracked date ranges — if periods start getting uploaded well after the period they cover, this will drift; worth keeping an eye on once there's a year or two of history to check it against.

8. **CSV/XLSX export** for the solver table (per period), fleet bonus, and complaints — download buttons/links on each relevant tab (`/api/periods/{id}/export`, `/api/fleet-bonus/export`, `/api/complaints/export`, `?fmt=csv` or `?fmt=xlsx`).

9. **Solver details upload** (Manage solvers & regions → "Upload solver details"; `/api/registered-solvers/import-details`). Accepts .xlsx/.csv with a header row and columns Name, Email, Phone (or Mobile/Contact), Region, Location, Active/Status in any order; only Name is required. Matches existing solvers by name (ignoring case and extra spaces), updates only the cells that are filled (blanks never erase data), adds new solvers (which need a Region), and saves emails into the same list the coaching and broadcast emails use. Invalid emails and new solvers without a region are skipped and reported. "Download current list" exports the roster in the same layout to edit and re-upload. New `solvers.phone` column.

**Database upgrade:** nothing to run by hand. New tables (`fleet_entries`, `complaints`, `app_settings`) are created on startup, and `init_db()` now also adds `solvers.phone` and upgrades an existing `email_send_logs` table (adds `subject`/`attachments`, makes `period_id` nullable) without losing rows. Verified against an old-schema SQLite file; on Postgres it uses `ALTER TABLE`. Back up the production DB before the first start on the new version anyway.

**Known simplifications, by design, given the scope of this round:**
- The three new tabs only appear once at least one period has been uploaded (same as every other tab) — fine in practice since fleet bonus and broadcast both need the registered solver list, which is normally seeded alongside the first upload.
- Broadcast "send through Outlook" currently means SMTP via the company's Office 365 account (same mechanism as coaching emails already use), not a live Graph API integration — see point 3 above for what swapping that in would involve.
- Dormancy/flag thresholds and the TAT allowance are defaults I picked to be reasonable, not numbers Solvit confirmed — all four are one-line settings to change, not a code change.

---

**Previous change — TOTAL VALUED's export format changed, and the system now handles both.** A newer Zoho export layout showed up (first seen in a "13th July - 19th July 2026" weekly export) that renames most of TOTAL VALUED's columns: `Solver` → `Solver_name`, `Requested_Date` → `Requested_at`, `Schedule_date` → `Scheduled_at`, `Valuation_Start`/`Valuation_Date` → lowercase `Valuation_start`/`Valuation_date`, plus a new `Initiated_at`. SOLVERS BASKET and CLIENT RATING are unaffected — only TOTAL VALUED changed.

`analysis.normalize_total_valued_columns()` renames the new layout onto the original canonical names immediately after reading the sheet, so every downstream computation (TAT, jobs-initiated, backlog) is written once and works against either layout without knowing which one it got. Two things from the old layout have no equivalent in the new one and are deliberately not guessed at:
- **`Approval_Status`** doesn't exist in the new layout (only a bare 0/1 `Status` flag with no documented meaning) — `approval_rate` comes back as `None` for periods uploaded in the new layout rather than inventing a mapping.
- **`Initiator_Source`** doesn't exist either (replaced by a numeric `Initiated_by_type` code, also undocumented) — jobs-initiated-by-solver is computed a different way that doesn't need it: a job counts as self-initiated when `Initiated_by` names the same person as the assigned solver for that row, matched case/whitespace-insensitively. This works for both layouts and was verified against real data (584 self-initiated matches out of 1,481 rows).

**Also new — backlog tracking.** A "Backlog cleared" KPI card on the Overview tab (plus a sortable Backlog column in the Solvers table and a numbox in each solver's drawer) shows how many jobs were initiated *before* the period started but only finished *during* it — i.e. old work getting cleared, not fresh work. The period's own date boundaries aren't tracked as an explicit field anywhere (a Period is just a label) — they're derived from the data itself, the earliest and latest `Valuation_Date` in TOTAL VALUED, floored/ceiled to whole days. For a normal weekly export this reproduces the export's actual date range exactly. See `analysis.compute_backlog()`.

Verified end-to-end against the actual "13-19 July 2026" export: 451 backlog jobs, 30.5% of the week's total valuations, oldest one 521 days old (Request 326267 — worth a look, that's a genuine outlier, not typical backlog aging). Verified zero regression against the old-layout May 2026 file — every number matches exactly what it was before this change, and it now gets a backlog number too (588 jobs, 11.1%) automatically.

---

**Newest change — continuous date-range talent grid.** The 9-box grid is no longer locked to whichever workbook the sidebar has loaded. On the **Talent grid** tab there's now a "This period" / "Custom range" toggle — pick any start/end date (e.g. July 1–15, or any longer span) and the grid recomputes live from job-level data, correctly spanning across upload boundaries if the range crosses them (e.g. "April 25 – May 5" pulls from both the April and May uploads). Tested against real data: a full month split into two 15-day halves summed back to the exact full-month total, and a boundary-crossing range correctly combined rows from two separate uploads.

This required storing row-level data, not just period aggregates — two new tables, `JobRecord` (from TOTAL VALUED + SOLVERS BASKET) and `RatingRecord` (from CLIENT RATING), populated at upload time and deduplicated by a composite key so re-uploading an overlapping period never double-counts a job. See section 5a below for the mechanics, and note the backfill caveat: **periods uploaded before this feature shipped have no row-level data** — a date range that only overlaps an old upload will come back empty with an explanatory message; re-uploading that workbook (same label, so it overwrites cleanly) backfills it.

`analysis.py` was refactored so `analyse_workbook()` (Excel → result) and the new `analyse_date_range()` path (DB rows → result) share one computation function, `analyse_dataframes()`, instead of two parallel implementations that could drift apart.

---

This build supersedes any earlier one. The five changes below (previous delivery), all tested end-to-end against a real May 2026 export via FastAPI's in-process TestClient (login → seed regional list → upload → read every changed endpoint → generate coaching docs → delete period):

1. **TAT is now computed from the TOTAL VALUED sheet, not SOLVERS BASKET.**
   The main "average time per job" number (target ≤ 10h, shown on the Overview KPI row) is `Valuation_Date − Schedule_date`, falling back to `Valuation_Date − Requested_Date` when a job was never formally scheduled. A "Response TAT" sub-metric (`Valuation_Start − Schedule_date`, same fallback) now lives on its own **TAT tab** alongside On-site TAT (`Valuation_Date − Valuation_Start`). Because both sub-metrics share the `Valuation_Start` midpoint, they now cleanly explain where the total time goes — that wasn't true of the old basket-based definition. Submission rate is untouched and still comes from SOLVERS BASKET. See `analysis.py`, the block above `per_solver_tat = tat_sample.groupby(...)`.

2. **9-box talent grid: "jobs initiated" replaces "volume" on the quality axis, benchmarked per-region.**
   Instead of comparing every solver's initiated-jobs count (from `Initiated_by` in TOTAL VALUED) against one flat number, each solver is now scored against the **average for their own operating region** (matched via the registered solver roster / SOLVERS_REGIONAL_LIST). See `analysis.recompute_talent_grids()` — it's called twice: once in the upload handler (`routes.py`, right after solver→region matching, before snapshots are saved) and once in the `/regions?recompute=true` handler (so editing a solver's region in "Manage solvers" keeps their grid placement in sync). If you add a third place that touches solver→region matching, call `recompute_talent_grids()` there too or the grid will silently go stale.

3. **"Total Valued" is now a first-class KPI card** on the Overview tab (was previously folded into the "Total solvers" subtitle), and appears as the first line item in the strong-performer recognition email highlights.

4. **Admins can delete an uploaded period.** `DELETE /api/periods/{id}` removes the period row (cascades to solver snapshots), the region snapshots, the email send log, and the saved `.xlsx` off disk. Wired to a "🗑 Delete this period" button in the sidebar with a confirm dialog. This is destructive and not undoable — there's no soft-delete / trash.

5. **The dashboard is now tabbed** (Overview / Regions / TAT / Talent grid / Solvers) instead of one long scrolling page. Sections all live in the DOM together; `switchView()` toggles a `.active` class. **If you add a new Chart.js canvas to a tab, you must redraw it in `switchView()`** — Chart.js sizes off the container's rendered box, and a canvas drawn while its parent is `display:none` comes out 0×0. The pattern to copy is `renderOverviewDistributions()` / `renderTatDistributions()`, called both from `render()` (only for the currently-active tab) and from `switchView()` (so the first time a tab opens, its charts actually draw at the right size).

One bug fixed along the way, not requested but worth knowing about: `RegionSnapshot` was missing `avg_total_tat_hrs` and `avg_jobs_initiated` columns — both the model and **two separate insert call sites** were silently dropping that data. Both are fixed; if you're diffing against a much older copy of this code, that's why `models.py` and both region-insert blocks in `routes.py` changed even though it doesn't map to a specific ask.

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

This is the most-discussed area during development, and it has changed more than once — trust this section over any comment you find that contradicts it, and check the section 0 changelog above if something here looks surprising.

**Volume ("Total Valued")** = count of rows in **TOTAL VALUED** for that solver. This is what actually happened this period, regardless of approval outcome.

**Backlog cleared** = TOTAL VALUED rows where `Initiated_Date` is before the period's start AND `Valuation_Date` falls within the period. The period's start/end are derived from the data (min/max `Valuation_Date`, floored/ceiled to whole days) — not tracked as a separate field. See `analysis.compute_backlog()`.

**Jobs assigned / pending / submission rate** all come from **SOLVERS BASKET** — the set of jobs handed to a solver this period, whatever their outcome:
```python
assigned_count = count of SOLVERS BASKET rows for the solver
valued_count   = count where Request_Status == "Completed"
pending_count  = count where Request_Status == "Solver accept"
submission_rate = valued_count / assigned_count
```
Target ≥ 85%.

**Jobs initiated by a solver** = count of rows in TOTAL VALUED where `Initiator_Source == "Solver"` AND `Initiated_by` matches the solver's name. Work they brought in themselves vs work allocated to them by schedulers. This now also drives the quality axis of the 9-box talent grid (see below).

**Average TAT ("Average time per job")** — the PRIMARY time metric, shown on the Overview KPI row and used everywhere solvers are judged on speed. Sourced from **TOTAL VALUED**, not the basket:
```python
sched_or_req = Schedule_date, falling back to Requested_Date if null
total_tat_hrs    = Valuation_Date  - sched_or_req
response_tat_hrs = Valuation_Start - sched_or_req
onsite_tat_hrs   = Valuation_Date  - Valuation_Start
```
We use the **mean** (not median) per solver, per an explicit decision to reflect the full distribution rather than smoothing outliers away — except the mean itself excludes jobs where `total_tat_hrs > 72h` ("stuck" jobs; see below), since a handful of multi-day stragglers would otherwise dominate the average. Target ≤ 10h for `total_tat_hrs`.

`response_tat_hrs` and `onsite_tat_hrs` are sub-components shown on their own **TAT tab**, not the main KPI row. Because both segments share the `Valuation_Start` midpoint, they cleanly explain where the total time goes (response covers "how long to get there," onsite covers "how long once there").

**Client rating** = mean of `rating` column in CLIENT RATING for that solver, excluding zero/blank ratings. Target ≥ 4.5/5. Solvers with fewer than 3 ratings are classified as "insufficient data" and excluded from rating-based judgment.

**Stuck jobs** = TOTAL VALUED rows where `total_tat_hrs > 72 hours`. Reported as a count/rate but excluded from the average TAT calculation for the reason above.

**9-box talent grid** (`analysis.compute_talent_grid()`):
- Performance axis (x): average TAT (lower better, target 10h) + submission rate (higher better, target 85%) + reject rate if/when that data exists (target ≤5%, currently always "insufficient data" since no reject-rate column exists yet in either sheet).
- Quality axis (y): client rating (target 4.5) + **jobs initiated, benchmarked against the average for solvers in the same operating region** (not a flat global number — a solver in a dense area is expected to self-initiate more than one in a sparse rural region). See `recompute_talent_grids()` for the region-matching pass; it must run *after* solver→region matching, which is why it isn't inlined into `analyse_workbook()` itself.

---

## 5a. The date-range talent grid, mechanically

- **`JobRecord`** (`models.py`) — one row per TOTAL VALUED or SOLVERS BASKET row, tagged `sheet_source`. Dedup key is `f"{sheet_source}|{vehicle_reg}|{requested_date}|{solver}"` — good enough in practice since two genuinely different jobs sharing all four fields is essentially impossible, but it's a composite key, not a true row ID (the export doesn't have one).
- **`RatingRecord`** — one row per CLIENT RATING row, deduped on `Request_ID` (verified unique: 355/355 in the May export).
- **`_persist_job_and_rating_records()`** (`routes.py`, called at the end of the upload handler) — checks which dedup keys already exist in the DB before inserting, in chunks of 1000 to keep the `IN (...)` clause reasonable, then bulk-inserts only the new rows. Not its own transaction; rides along with the upload's existing `db.commit()`.
- **`GET /api/talent-grid/range?start=YYYY-MM-DD&end=YYYY-MM-DD`** — matches jobs by `valuation_date` (when the work was actually finished) if present, else falls back to `requested_date` for still-pending basket rows so an in-progress job still counts toward the submission-rate denominator for a range that includes "today." End date is treated as inclusive through 23:59:59.
- **`analysis.records_to_dataframes()`** — turns the queried ORM rows (converted to plain dicts first, so `analysis.py` stays free of any SQLAlchemy import) back into the same-shaped DataFrames `analyse_dataframes()` expects, so the exact same TAT/submission/rating/talent-grid math runs regardless of whether the data came from a fresh Excel file or a date-filtered DB query.
- **Region-aware jobs-initiated benchmark still applies in range mode** — `recompute_talent_grids()` runs against the *current* registered-solver roster (not a historical snapshot of it), using whichever solvers actually appear in the filtered window to compute each region's average.
- **Frontend**: `app/static/index.html`, search for `TALENT GRID — continuous date-range analysis`. `tgSolversSource` is the single variable the grid renders from; `tgMode` ("period" | "range") decides whether `renderTalentGrid()` refreshes it from `currentPeriod.solvers` or leaves it alone (already populated by `applyTalentGridRange()`). Switching the sidebar's period dropdown always resets back to "period" mode — a stale custom range surviving a period switch would be a confusing dangling state, not a feature.

**Known gap**: only workbooks uploaded *after* this feature shipped have row-level data. If IT is deploying this fresh, that's a non-issue — everything from day one is covered. If upgrading an existing deployment, the fix is simply re-uploading each historical period once (same label, so it overwrites in place) to backfill its `JobRecord`/`RatingRecord` rows.

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
