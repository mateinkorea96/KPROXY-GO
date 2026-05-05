# Work Log — 2026-05-04 ~ 2026-05-05 Session

This file documents the major changes made across the user's repos in this work session, plus pending items and open decisions, so the next session can resume cleanly.

> Apps touched (in order of session): kpop-wms → shopee-dashboard → ImageAutoUploader (Railway) → starphotocard-go (this repo, brand new)

---

## TL;DR — What's live right now

| App | Live URL | Deploy | Today's biggest change |
|---|---|---|---|
| **starphotocard-go** | https://starphotocard-go.up.railway.app | Railway auto-deploy from main | Brand new app, 100% built today |
| **kpop-wms** | https://stevemoon6522.github.io/kpop-wms/ | GitHub Pages | Joom combined-shipping + auto stock decrement on arrange |
| **shopee-dashboard** | https://shopee-dashboard-kohl.vercel.app | Vercel manual `vercel deploy --prod --yes` | 6-region price update + multi-model SKU sync |
| **imageautouploader-web** | https://web-production-99792.up.railway.app | Railway auto-deploy | Auth guard redirects HTML pages to /login (no longer 401 JSON) |

All Supabase Edge Functions deployed to project `bpdafetvjyvvwbksvowu`:
- `shopee-orders` v34 (banned-shop guard, ship_order retry, auto stock decrement on arrange)
- `shopee-bridge` v19 (multi-model SKU expansion, /list_items, /update_price model_id-aware)
- `joom-orders` v9 (combined shipping, auto stock decrement on arrange)

---

## 1. starphotocard-go (this repo) — built from scratch today

### Status: live + verified end-to-end, awaiting first real buyer order

**What's deployed:**
- FastAPI + Supabase + Jinja2 + plain JS, single `app.py`
- Public pages: `/` (active GOs grouped by category, with buyer-count badges gated >=3), `/order` (multi-line buyer form), `/order/success`, `/faq`
- Admin pages (password-gated, redirect to /admin/login on miss): `/admin/dashboard`, `/admin/campaigns` (CRUD list), `/admin/campaign/new`, `/admin/campaign/{code}/edit|delete|toggle-close`, `/admin/orders` (status updates), `/admin/aggregate` (bulk-buy view), `/admin/import` (xlsx upload)
- Per-GO buyers CSV: `GET /admin/campaign/{code}/buyers.csv`
- Bulk buyers CSV (this week's Alibaba order): `GET /admin/buyers.csv?closing_within=7`
- Cron: `GET /cron/deadline-check?secret=$CRON_SECRET` — D-day {0,1,3,5,7} digest with idempotency + heartbeat + soft rate-limit
- Kakao OAuth flow: `/admin/kakao/connect` → `/admin/kakao/callback` → tokens persisted in `kakao_token` table (auto-refresh every 6h via refresh_token, 60d life)
- Kakao test: `/admin/kakao/test` (rich diagnostics on failure), `/admin/kakao/status` (JSON)
- Light theme matching brand logo (pink #ff2d8f → purple #6610d4 gradient), responsive

**DB schema (all in same Supabase project `bpdafetvjyvvwbksvowu`):**
- `go_campaigns` — POCA_SET / ALBUM / RESTOCK unified, `is_closed` manual override + `go_campaign_status(deadline, is_closed)` function
- `go_buyer_orders` (alibaba_username, total_krw, status, notified_at)
- `go_buyer_order_items` (go_code, qty, unit_price_krw, line_total_krw)
- `go_campaign_aggregate` view — buyer_count + total_qty + total_krw per GO
- `cron_runs` — UNIQUE (job, scope) for idempotent daily digest
- `kakao_token` — single row, OAuth tokens

**Imported data (5/5 STEP C done):**
- POCA_SET: 37 (29 active / 8 closed / 18 with price / 19 missing price)
- ALBUM: 23 (18 active / 18 with price / 5 missing deadline)
- RESTOCK: 3 (all 3 missing deadline)

**Notification:**
- Kakao "memo to self" (KakaoTalk) is the primary admin alert channel
- Telegram is fallback (kept for redundancy)
- `notify_admin()` wraps both — tries Kakao first, falls back to Telegram

### Env vars set on Railway
- `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `APP_PASSWORD`
- `KAKAO_REST_API_KEY`, `KAKAO_REDIRECT_URI`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` (fallback)
- `CRON_SECRET`

### External services
- cron-job.org — daily 00:00 UTC (= 09:00 KST) hits `/cron/deadline-check?secret=$CRON_SECRET`. Notify on failure ON.

### Pending operational tasks (user)
- **STEP E** (manual, just messaging): announce URL to 2-3 trusted Alibaba buyers via 1:1 chat
- **STEP F**: monitor first 24-48h after launch
- Data quality cleanup (admin → Edit on each):
  - Fill 19 missing prices on POCA_SET (LE SSERAFIM PURELOW series)
  - Fill 5 missing deadlines on ALBUM (ALBUM-001~005)
  - Decide RESTOCK policy (always-open vs deadline)

### Pending feature work (not started)
- Bundle / SET decomposition (3 VER SET components — see this repo's history; user said "검토 후 알려줄께")
- POB comparison matrix (per album, multiple stores side-by-side)
- Refund / cancellation flow automation (manual via admin status change for now)
- Public branding / Instagram cross-posting

### Open decisions (waiting on user)
- Pending design review: `C:\dev\shopee-dashboard\TODO.md` §1.1 — SET bundle data model. Awaiting answers to Q1-Q5 (region-specific components? cost override? nested? auto stock push? label format?)
- REST API key rotation recommended (sent in chat earlier)

### Plan file (from this session)
`C:\Users\STEVE\.claude\plans\delightful-seeking-moonbeam.md` — pre-launch feature boost plan, includes Plan-agent review feedback. Already executed; kept as record.

---

## 2. kpop-wms

### Today's changes
- **Joom combined shipping** (commit f32cfa6) — same buyer + same address orders consolidate to one shipment + one tracking number + one PDF label. New "🔗 합배송 (수령인 자동 그룹)" button auto-detects groups by recipient signature. Backend: `joom-orders` v8/v9 added `/arrange-combined` and `/label-combined`.
- **shipping success-rate hardening** (shopee-orders v31): pre-check live order_status before ship_order in bulk arrange; transient-error retry with exp backoff; concurrency reduced 5→3. Required by Shopee Open Platform 90% success rate compliance.
- **Token management overhaul** (shopee-orders v30→v33): merchant_id-based refresh keeps tokens merchant-scoped indefinitely (was being downgraded to shop-scope on every /poll); auto-sync to `shopee_tokens` mirror table; banned-shop guard prevents BR shop 1002269093 (영구정지) from being used; sync prefers shops with most recent `last_polled_at`.
- **Auto stock decrement on arrange** (shopee-orders v34, joom-orders v9): SKU column on `inventory` (computed from idol/album/version/member dimensions, indexed). On arrange button click, matching inventory rows decrement; `marketplace_orders.stock_decremented_at` ensures idempotency; `stock_decrements` audit table records every change. 발주 필요 list auto-refreshes via existing realtime channel — no frontend changes needed.

### Pending
- (none active — both ship_order success-rate compliance and combined shipping are operational)

---

## 3. shopee-dashboard

### Today's changes
- **6-region multi-region price update** (commit 9a39960): `SHOPEE_REGIONS` expanded SG/TW/TH → SG/TW/TH/MY/PH/BR (VN/MX explicitly excluded per user). Parallel push via Promise.all instead of serial loop.
- **SKU mapping sync UI** (commit b59b54f): "🔗 Shopee 매핑 동기화" button fetches `/list_items` for all 6 regions in parallel, auto-matches dashboard SKUs against returned items by SKU exact then name exact (unique-only); preview table shows matched/dup/none/err per region; bulk apply writes to `product_shopee_listings`.
- **Multi-model SKU support** (commit baa76b4): shopee-bridge v19 expands has_model items via get_model_list, returning per-model rows with `model_id`. Dashboard saves `shop_model_id` (new column) alongside `shop_item_id`. `/update_price` sends `model_id` for variant-level pricing.

### Pending
- See `C:\dev\shopee-dashboard\TODO.md` for full backlog. Highest priority: §1.1 SET bundle data model design (awaiting user Q1-Q5 answers).

---

## 4. imageautouploader-web (Railway)

### Today's changes
- **Auth guard redirect fix** (commit cd14583): HTML page routes (/, /workflow, /alibaba, /settings) now redirect to /login (302) on no session, instead of returning 401 JSON. API routes (/api/*) keep clean 401 JSON. Fixed indentation bug in `require_login`.

### Pending
- (none — user-reported issue resolved)

---

## How to resume

If a future session needs to continue work:

1. **Read this file first** (in starphotocard-go repo root) for full session context.
2. **Memory files** auto-load each session — see `C:\Users\STEVE\.claude\projects\C--dev\memory\MEMORY.md` for index. Key files for these projects:
   - `reference_starphotocard_go.md`
   - `reference_shopee_dashboard.md`
   - `reference_repo_supabase.md`
   - `reference_shopee_open_platform.md`
   - `reference_kse_api.md`
3. **Pending design discussions:**
   - `C:\dev\shopee-dashboard\TODO.md` §1.1 — SET bundle Q1-Q5
4. **Plan file from this session:** `C:\Users\STEVE\.claude\plans\delightful-seeking-moonbeam.md`

### To resume a specific track
- "starphotocard-go data quality cleanup" → admin UI, fill missing prices/deadlines on existing GOs
- "starphotocard-go bundle support" → answer SET bundle Q1-Q5 first, then implement
- "shopee-dashboard manual mapping UI" → for SKUs unmatched by auto-sync (TODO §2)
- "ship_order success-rate monitoring" → add a panel showing daily success % (TODO §2)

---

Last updated: 2026-05-05 by Claude Opus 4.7
