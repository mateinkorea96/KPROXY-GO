# mateinkorea-go

K-pop **Group Order** management web app.
Public buyer-facing order form + password-gated admin for GO campaign CRUD,
buyer-order tracking, Alibaba bulk-buy aggregate, and xlsx import.

Powered by FastAPI + Supabase Postgres + Jinja2 + plain JS.
On every new buyer order, a Telegram message is pushed to the admin chat.

## Routes

Public (English):
- `GET /` — active GOs grouped by category
- `GET /order` — multi-line order form
- `POST /order` — submit + Telegram notify
- `GET /order/success` — confirmation page

Admin (password-gated):
- `GET /admin/login` + `POST /admin/login`
- `GET /admin/dashboard`
- `GET /admin/campaigns` (filter/search)
- `GET /admin/campaign/new` + `POST` — create
- `GET /admin/campaign/{code}/edit` + `POST` — update
- `POST /admin/campaign/{code}/toggle-close` — soft close/reopen
- `POST /admin/campaign/{code}/delete`
- `GET /admin/orders` (filter by status) + `POST /admin/order/{id}/status`
- `GET /admin/aggregate` — total qty per GO across submitted/confirmed orders
- `GET /admin/import` + `POST /admin/import` — xlsx upload

Health: `GET /health`

## Env vars (Railway)

- `SUPABASE_URL` — your Supabase project URL
- `SUPABASE_SERVICE_ROLE_KEY` — service-role JWT (DB writes)
- `APP_PASSWORD` — admin login password
- `SESSION_HOURS` — session duration, default 24
- `TELEGRAM_BOT_TOKEN` — for buyer-order notifications (optional; leave empty to disable)
- `TELEGRAM_ADMIN_CHAT_ID` — chat to receive notifications

## Local dev

```bash
python -m venv .venv
.venv\Scripts\activate     # Windows
pip install -r requirements.txt
cp .env.example .env       # then edit .env
uvicorn app:app --reload --port 8000
```

## DB schema

Applied via Supabase migration `create_go_grouporder_tables`:
- `go_campaigns` — POCA SET / ALBUM / RESTOCK unified
- `go_buyer_orders` + `go_buyer_order_items`
- View `go_campaign_aggregate` for bulk-buy planning
- Function `go_campaign_status(deadline, is_closed)` for derived status

## Deploy

Auto-deploy from `main` via Railway GitHub integration.
