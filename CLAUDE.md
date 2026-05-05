# starphotocard-go

**Live:** https://starphotocard-go.up.railway.app
**Repo:** https://github.com/stevemoon6522/starphotocard-go
**Local:** `C:\dev\starphotocard-go\app.py` (single FastAPI file)

## Stack
FastAPI + Supabase Postgres + Jinja2 + plain JS. English UI. Light theme tuned to brand logo (pink #ff2d8f → purple #6610d4 gradient). Logo at `static/img/logo.png`.

## Deploy
Railway auto-deploy from `main`. NIXPACKS via `railway.toml`. Healthcheck `/health`.

## Notification
Kakao "memo to self" is the primary admin alert channel (replaces Telegram). Telegram is fallback. `notify_admin()` wraps both.

## Cron
External scheduler (cron-job.org) hits `GET /cron/deadline-check?secret=$CRON_SECRET` daily 00:00 UTC = 09:00 KST. Idempotent via `cron_runs` UNIQUE(job, scope). Soft rate-limit returns 200+skipped (not 429) to keep cron-job.org happy. Heartbeat sends even on no-items days.

## Env vars (Railway)
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`
- `APP_PASSWORD` (admin login)
- `KAKAO_REST_API_KEY`, `KAKAO_REDIRECT_URI`
- `KAKAO_CLIENT_SECRET` (only if Kakao app has Client Secret enabled)
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` (fallback)
- `CRON_SECRET`

## DB tables (in shared `bpdafetvjyvvwbksvowu`)
- `go_campaigns` (POCA_SET / ALBUM / RESTOCK unified, `is_closed` manual override)
- `go_buyer_orders`, `go_buyer_order_items`
- `go_campaign_aggregate` view (buyer_count + total_qty + total_krw per GO)
- `cron_runs` (idempotency)
- `kakao_token` (single-row, OAuth tokens)
- function `go_campaign_status(deadline, is_closed)` — returns 'open' / 'closing_soon' / 'closed'

## Key flows
- Public `/` lists active GOs grouped by category, with buyer-count badges (gated >=3)
- `/order` multi-line buyer form → on submit, inserts to DB + fires `notify_admin()`
- `/admin/import` xlsx upload (POCA SET / ALBUM / ALBUM RESTOCK sheets)
- `/admin/buyers.csv?closing_within=N` bulk export for Alibaba bulk-buy this week
- `/admin/campaign/{code}/buyers.csv` per-GO export
- `/admin/kakao/connect` once for OAuth, then auto-refresh

## Plan / handoff
- `WORK_LOG.md` in repo root — cross-project session log including this app's birth on 2026-05-04
- `C:\Users\STEVE\.claude\plans\delightful-seeking-moonbeam.md` — pre-launch plan that birthed this state

## Pending
- Bundle / SET decomposition (see `C:\dev\shopee-dashboard\TODO.md` §1.1 — same design discussion gates this app's bundle support)
- Data quality cleanup post-import: 19 missing prices on POCA_SET, 5 missing deadlines on ALBUM, 3 RESTOCK policy decision
