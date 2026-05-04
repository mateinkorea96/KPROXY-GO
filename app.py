"""starphotocard-go — Group Order management web app.

Public side (English): buyers browse active GOs and submit orders.
Admin side (password-gated): manage GO campaigns, view buyer orders, import xlsx.
On every new order: a Telegram message is pushed to the admin chat (if env vars set).
"""
from __future__ import annotations

import io
import os
import re
import secrets
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

import httpx
from fastapi import FastAPI, Request, Form, Cookie, HTTPException, UploadFile, File, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from supabase import create_client, Client

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "24"))
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

if not SUPABASE_URL or not SUPABASE_KEY:
    # Allow boot without env so the user can see /health while configuring; routes that touch DB will 500.
    print("[WARN] SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY missing — DB calls will fail.")

sb: Optional[Client] = None
if SUPABASE_URL and SUPABASE_KEY:
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI(title="starphotocard-go", version="0.1.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ----------------------------------------------------------------------------
# Sessions (in-memory; same pattern as ImageAutoUploader). For multi-instance
# deploys swap to Redis or Supabase, but Railway free tier runs single instance.
# ----------------------------------------------------------------------------

_sessions: Dict[str, datetime] = {}

def _now() -> datetime:
    return datetime.utcnow()

def _make_session() -> str:
    sid = secrets.token_urlsafe(32)
    _sessions[sid] = _now() + timedelta(hours=SESSION_HOURS)
    return sid

def _is_logged_in(session_id: Optional[str]) -> bool:
    if not session_id or session_id not in _sessions:
        return False
    if _sessions[session_id] < _now():
        _sessions.pop(session_id, None)
        return False
    return True

def require_admin(session_id: Optional[str] = Cookie(None)):
    """Dependency for /admin/api/* JSON endpoints — returns 401 JSON if not logged in.
    HTML pages handle auth inline by returning RedirectResponse."""
    if not _is_logged_in(session_id):
        raise HTTPException(status_code=401, detail="Login required")
    return session_id

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

GO_CODE_RE = re.compile(r"^(GO|ALBUM|RESTOCK)-\d{3,}$")

CATEGORY_PREFIX = {"POCA_SET": "GO", "ALBUM": "ALBUM", "RESTOCK": "RESTOCK"}

def category_for_code(code: str) -> str:
    if code.startswith("GO-"): return "POCA_SET"
    if code.startswith("ALBUM-"): return "ALBUM"
    if code.startswith("RESTOCK-"): return "RESTOCK"
    return "POCA_SET"

def next_go_code(category: str) -> str:
    """Auto-generate the next GO code per category."""
    prefix = CATEGORY_PREFIX.get(category, "GO")
    res = sb.table("go_campaigns").select("go_code").like("go_code", f"{prefix}-%").execute()
    nums: List[int] = []
    for r in (res.data or []):
        m = re.match(rf"^{prefix}-(\d+)$", r["go_code"])
        if m: nums.append(int(m.group(1)))
    nxt = (max(nums) + 1) if nums else 1
    return f"{prefix}-{nxt:03d}"

def derived_status(deadline: Optional[date], is_closed: bool) -> str:
    if is_closed: return "closed"
    if deadline is None: return "closed"
    delta = (deadline - date.today()).days
    if delta < 0: return "closed"
    if delta <= 3: return "closing_soon"
    return "open"

def parse_date(value: Any) -> Optional[date]:
    if not value: return None
    if isinstance(value, date) and not isinstance(value, datetime): return value
    if isinstance(value, datetime): return value.date()
    s = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d", "%m/%d/%Y"):
        try: return datetime.strptime(s, fmt).date()
        except ValueError: continue
    return None

def parse_num(value: Any) -> Optional[float]:
    if value in (None, "", "X", "-"): return None
    try: return float(str(value).replace(",", "").strip())
    except ValueError: return None

async def telegram_notify(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json={"chat_id": TG_CHAT, "text": text, "disable_web_page_preview": True})
            return r.status_code == 200
    except Exception as e:
        print(f"[telegram_notify] {e}")
        return False

def _enrich_status(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add `status` and `d_day` to a list of campaign rows."""
    today = date.today()
    out = []
    for r in rows:
        dl = r.get("deadline")
        if isinstance(dl, str):
            try: dl_date = datetime.strptime(dl, "%Y-%m-%d").date()
            except ValueError: dl_date = None
        elif isinstance(dl, date):
            dl_date = dl
        else:
            dl_date = None
        status = derived_status(dl_date, bool(r.get("is_closed")))
        d_day = (dl_date - today).days if dl_date else None
        out.append({**r, "status": status, "d_day": d_day})
    return out

# ----------------------------------------------------------------------------
# Public routes
# ----------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok", "service": "starphotocard-go", "version": "0.1.0",
            "db": bool(sb), "telegram": bool(TG_TOKEN and TG_CHAT)}

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    by_cat: Dict[str, List[Dict[str, Any]]] = {"POCA_SET": [], "ALBUM": [], "RESTOCK": []}
    error = None
    if not sb:
        error = "Service is being configured. Please check back shortly."
        return templates.TemplateResponse("home.html", {"request": request, "by_cat": by_cat, "active_count": 0, "error": error})
    res = sb.table("go_campaigns").select("*").order("category").order("deadline", desc=False).execute()
    enriched = _enrich_status(res.data or [])
    active = [c for c in enriched if c["status"] in ("open", "closing_soon")]
    for c in active:
        by_cat[c["category"]].append(c)
    return templates.TemplateResponse("home.html", {"request": request, "by_cat": by_cat, "active_count": len(active), "error": None})

@app.get("/order", response_class=HTMLResponse)
async def order_form(request: Request):
    if not sb:
        return templates.TemplateResponse("order.html", {"request": request, "options": [], "error": "Service is being configured. Please check back shortly."})
    res = sb.table("go_campaigns").select("go_code, category, idol_name, album, version, retail_store, set_price_krw, set_detail").execute()
    enriched = _enrich_status(res.data or [])
    options = [c for c in enriched if c["status"] in ("open", "closing_soon")]
    return templates.TemplateResponse("order.html", {"request": request, "options": options, "error": None})

@app.post("/order")
async def order_submit(request: Request):
    if not sb:
        raise HTTPException(status_code=503, detail="Service unavailable")
    form = await request.form()
    alibaba_username = (form.get("alibaba_username") or "").strip()
    contact_email = (form.get("contact_email") or "").strip() or None
    contact_note = (form.get("contact_note") or "").strip() or None
    if not alibaba_username:
        raise HTTPException(status_code=400, detail="Alibaba username is required.")
    # Multi-row items: go_code[] + qty[]
    codes = form.getlist("go_code[]")
    qtys = form.getlist("qty[]")
    items: List[Dict[str, Any]] = []
    total_krw = 0.0
    # Pre-fetch prices
    if codes:
        price_res = sb.table("go_campaigns").select("go_code, set_price_krw").in_("go_code", codes).execute()
        price_map = {r["go_code"]: r.get("set_price_krw") for r in (price_res.data or [])}
    else:
        price_map = {}
    for code, qty in zip(codes, qtys):
        code = (code or "").strip()
        try: q = int(qty)
        except (TypeError, ValueError): continue
        if not code or q <= 0: continue
        unit = float(price_map.get(code) or 0)
        line_total = unit * q
        total_krw += line_total
        items.append({"go_code": code, "qty": q, "unit_price_krw": unit or None, "line_total_krw": line_total or None})
    if not items:
        raise HTTPException(status_code=400, detail="At least one valid order item (GO code + qty) is required.")
    # Insert order
    order_res = sb.table("go_buyer_orders").insert({
        "alibaba_username": alibaba_username,
        "contact_email": contact_email,
        "contact_note": contact_note,
        "total_krw": total_krw,
    }).execute()
    order_id = order_res.data[0]["id"]
    for it in items:
        it["order_id"] = order_id
    sb.table("go_buyer_order_items").insert(items).execute()
    # Notify admin via Telegram (fire-and-forget; doesn't block submit if Telegram is down)
    summary = "\n".join([f"  {it['go_code']} × {it['qty']}" for it in items])
    msg = (f"🎀 New GO order #{order_id}\n"
           f"Buyer: {alibaba_username}\n"
           f"Items:\n{summary}\n"
           f"Total: ₩{int(total_krw):,}")
    notified = await telegram_notify(msg)
    if notified:
        sb.table("go_buyer_orders").update({"notified_at": _now().isoformat()}).eq("id", order_id).execute()
    return RedirectResponse(f"/order/success?id={order_id}", status_code=302)

@app.get("/order/success", response_class=HTMLResponse)
async def order_success(request: Request, id: int):
    return templates.TemplateResponse("order_success.html", {"request": request, "order_id": id})

# ----------------------------------------------------------------------------
# Admin — login
# ----------------------------------------------------------------------------

@app.get("/admin", response_class=HTMLResponse)
async def admin_redirect(request: Request, session_id: Optional[str] = Cookie(None)):
    if _is_logged_in(session_id):
        return RedirectResponse("/admin/dashboard", status_code=302)
    return RedirectResponse("/admin/login", status_code=302)

@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.post("/admin/login")
async def admin_login_submit(request: Request, password: str = Form(...)):
    if not APP_PASSWORD:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Server APP_PASSWORD not configured."})
    if password != APP_PASSWORD:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Wrong password."})
    sid = _make_session()
    resp = RedirectResponse("/admin/dashboard", status_code=302)
    resp.set_cookie("session_id", sid, max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", secure=True)
    return resp

@app.get("/admin/logout")
async def admin_logout(session_id: Optional[str] = Cookie(None)):
    if session_id: _sessions.pop(session_id, None)
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("session_id")
    return resp

# ----------------------------------------------------------------------------
# Admin — pages (HTML, redirect to login if not authed)
# ----------------------------------------------------------------------------

def _admin_or_redirect(session_id: Optional[str]):
    if not _is_logged_in(session_id):
        return RedirectResponse("/admin/login", status_code=302)
    return None

@app.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(request: Request, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    counts = sb.table("go_campaigns").select("category", count="exact").execute()
    cnt_total = sb.table("go_campaigns").select("id", count="exact").execute().count or 0
    open_q = sb.table("go_campaigns").select("id", count="exact").eq("is_closed", False).gte("deadline", date.today().isoformat()).execute()
    cnt_open = open_q.count or 0
    orders_q = sb.table("go_buyer_orders").select("id", count="exact").execute()
    cnt_orders = orders_q.count or 0
    # Recent orders
    recent = sb.table("go_buyer_orders").select("*").order("created_at", desc=True).limit(10).execute()
    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request, "cnt_total": cnt_total, "cnt_open": cnt_open, "cnt_orders": cnt_orders,
        "recent_orders": recent.data or [],
    })

@app.get("/admin/campaigns", response_class=HTMLResponse)
async def admin_campaigns(request: Request, session_id: Optional[str] = Cookie(None), q: str = "", cat: str = ""):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    query = sb.table("go_campaigns").select("*").order("category").order("go_code")
    if cat in ("POCA_SET", "ALBUM", "RESTOCK"):
        query = query.eq("category", cat)
    res = query.execute()
    rows = _enrich_status(res.data or [])
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in (r.get("idol_name","").lower() + " " + r.get("album","").lower() + " " + r.get("retail_store","").lower())]
    return templates.TemplateResponse("admin/campaigns.html", {"request": request, "rows": rows, "q": q, "cat": cat})

@app.get("/admin/campaign/new", response_class=HTMLResponse)
async def admin_campaign_new(request: Request, session_id: Optional[str] = Cookie(None), category: str = "POCA_SET"):
    if (r := _admin_or_redirect(session_id)): return r
    return templates.TemplateResponse("admin/campaign_form.html", {
        "request": request, "row": None, "category": category, "is_new": True,
    })

@app.post("/admin/campaign/new")
async def admin_campaign_create(request: Request, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    form = await request.form()
    category = form.get("category", "POCA_SET")
    payload = _campaign_payload_from_form(form)
    payload["category"] = category
    payload["go_code"] = (form.get("go_code") or "").strip() or next_go_code(category)
    sb.table("go_campaigns").insert(payload).execute()
    return RedirectResponse("/admin/campaigns", status_code=302)

@app.get("/admin/campaign/{code}/edit", response_class=HTMLResponse)
async def admin_campaign_edit(request: Request, code: str, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    res = sb.table("go_campaigns").select("*").eq("go_code", code).single().execute()
    if not res.data: raise HTTPException(404, "Campaign not found")
    return templates.TemplateResponse("admin/campaign_form.html", {
        "request": request, "row": res.data, "category": res.data["category"], "is_new": False,
    })

@app.post("/admin/campaign/{code}/edit")
async def admin_campaign_update(request: Request, code: str, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    form = await request.form()
    payload = _campaign_payload_from_form(form)
    payload["updated_at"] = _now().isoformat()
    sb.table("go_campaigns").update(payload).eq("go_code", code).execute()
    return RedirectResponse("/admin/campaigns", status_code=302)

@app.post("/admin/campaign/{code}/delete")
async def admin_campaign_delete(request: Request, code: str, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    sb.table("go_campaigns").delete().eq("go_code", code).execute()
    return RedirectResponse("/admin/campaigns", status_code=302)

@app.post("/admin/campaign/{code}/toggle-close")
async def admin_campaign_toggle_close(request: Request, code: str, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    cur = sb.table("go_campaigns").select("is_closed").eq("go_code", code).single().execute()
    new_val = not bool(cur.data["is_closed"])
    sb.table("go_campaigns").update({"is_closed": new_val, "updated_at": _now().isoformat()}).eq("go_code", code).execute()
    return RedirectResponse("/admin/campaigns", status_code=302)

def _campaign_payload_from_form(form) -> Dict[str, Any]:
    return {
        "idol_name": (form.get("idol_name") or "").strip(),
        "album": (form.get("album") or "").strip(),
        "version": (form.get("version") or "").strip() or None,
        "retail_store": (form.get("retail_store") or "").strip() or None,
        "url": (form.get("url") or "").strip() or None,
        "set_price_krw": parse_num(form.get("set_price_krw")),
        "retail_price_krw": parse_num(form.get("retail_price_krw")),
        "discount_pct": parse_num(form.get("discount_pct")),
        "pob_store": (form.get("pob_store") or "").strip() or None,
        "pob_detail": (form.get("pob_detail") or "").strip() or None,
        "set_detail": (form.get("set_detail") or "").strip() or None,
        "detail": (form.get("detail") or "").strip() or None,
        "deadline": parse_date(form.get("deadline")) and parse_date(form.get("deadline")).isoformat(),
        "is_closed": form.get("is_closed") == "on",
        "note": (form.get("note") or "").strip() or None,
    }

# ----------------------------------------------------------------------------
# Admin — buyer orders + xlsx import
# ----------------------------------------------------------------------------

@app.get("/admin/orders", response_class=HTMLResponse)
async def admin_orders(request: Request, session_id: Optional[str] = Cookie(None), status: str = ""):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    q = sb.table("go_buyer_orders").select("*").order("created_at", desc=True).limit(200)
    if status: q = q.eq("status", status)
    res = q.execute()
    orders = res.data or []
    # Fetch all items for these orders
    if orders:
        ids = [o["id"] for o in orders]
        items_res = sb.table("go_buyer_order_items").select("*").in_("order_id", ids).execute()
        by_order: Dict[int, List[Dict[str, Any]]] = {}
        for it in (items_res.data or []):
            by_order.setdefault(it["order_id"], []).append(it)
        for o in orders:
            o["items"] = by_order.get(o["id"], [])
    return templates.TemplateResponse("admin/orders.html", {"request": request, "orders": orders, "status": status})

@app.post("/admin/order/{order_id}/status")
async def admin_order_status(order_id: int, request: Request, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    form = await request.form()
    new_status = form.get("status")
    if new_status not in ("submitted", "confirmed", "shipped", "cancelled"):
        raise HTTPException(400, "Invalid status")
    sb.table("go_buyer_orders").update({"status": new_status, "updated_at": _now().isoformat()}).eq("id", order_id).execute()
    return RedirectResponse("/admin/orders", status_code=302)

@app.get("/admin/aggregate", response_class=HTMLResponse)
async def admin_aggregate(request: Request, session_id: Optional[str] = Cookie(None)):
    """Per-GO total quantity for Alibaba bulk-buy planning."""
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    res = sb.table("go_campaign_aggregate").select("*").order("status").order("deadline").execute()
    return templates.TemplateResponse("admin/aggregate.html", {"request": request, "rows": res.data or []})

@app.get("/admin/import", response_class=HTMLResponse)
async def admin_import_page(request: Request, session_id: Optional[str] = Cookie(None)):
    if (r := _admin_or_redirect(session_id)): return r
    return templates.TemplateResponse("admin/import.html", {"request": request, "result": None})

@app.post("/admin/import", response_class=HTMLResponse)
async def admin_import_submit(request: Request, session_id: Optional[str] = Cookie(None), file: UploadFile = File(...)):
    if (r := _admin_or_redirect(session_id)): return r
    if not sb: return PlainTextResponse("DB not configured", status_code=503)
    import openpyxl
    contents = await file.read()
    wb = openpyxl.load_workbook(io.BytesIO(contents), data_only=True)
    summary = {"POCA_SET": 0, "ALBUM": 0, "RESTOCK": 0, "errors": []}

    def upsert_rows(rows: List[Dict[str, Any]]):
        if not rows: return
        for row in rows:
            try:
                # Use go_code as conflict key
                existing = sb.table("go_campaigns").select("id").eq("go_code", row["go_code"]).execute()
                if existing.data:
                    sb.table("go_campaigns").update(row).eq("go_code", row["go_code"]).execute()
                else:
                    sb.table("go_campaigns").insert(row).execute()
            except Exception as e:
                summary["errors"].append(f"{row.get('go_code')}: {e}")

    # POCA SET
    if "POCA SET" in wb.sheetnames:
        ws = wb["POCA SET"]
        rows = []
        for r in ws.iter_rows(min_row=5, values_only=True):
            code = r[1]
            if not code or not str(code).startswith("GO-"): continue
            if not (r[2] and r[3]): continue  # need idol + album to be meaningful
            dl = parse_date(r[8])
            rows.append({
                "go_code": str(code).strip(), "category": "POCA_SET",
                "idol_name": str(r[2]).strip(), "album": str(r[3]).strip(),
                "retail_store": (str(r[4]).strip() if r[4] else None),
                "url": (str(r[5]).strip() if r[5] else None),
                "set_price_krw": parse_num(r[6]),
                "set_detail": (str(r[7]).strip() if r[7] else None),
                "deadline": dl.isoformat() if dl else None,
                "note": (str(r[11]).strip() if len(r) > 11 and r[11] else None),
            })
            summary["POCA_SET"] += 1
        upsert_rows(rows)

    # ALBUM
    if "ALBUM" in wb.sheetnames:
        ws = wb["ALBUM"]
        rows = []
        for r in ws.iter_rows(min_row=5, values_only=True):
            code = r[1]
            if not code or not str(code).startswith("ALBUM-"): continue
            if not (r[2] and r[3]): continue
            dl = parse_date(r[9])
            rows.append({
                "go_code": str(code).strip(), "category": "ALBUM",
                "idol_name": str(r[2]).strip(), "album": str(r[3]).strip(),
                "version": (str(r[4]).strip() if r[4] else None),
                "url": (str(r[5]).strip() if r[5] else None),
                "set_price_krw": parse_num(r[6]),
                "pob_store": (str(r[7]).strip() if r[7] else None),
                "pob_detail": (str(r[8]).strip() if r[8] else None),
                "deadline": dl.isoformat() if dl else None,
                "retail_price_krw": parse_num(r[12]) if len(r) > 12 else None,
                "discount_pct": parse_num(r[13]) if len(r) > 13 else None,
                "note": (str(r[14]).strip() if len(r) > 14 and r[14] else None),
            })
            summary["ALBUM"] += 1
        upsert_rows(rows)

    # ALBUM RESTOCK
    if "ALBUM RESTOCK" in wb.sheetnames:
        ws = wb["ALBUM RESTOCK"]
        rows = []
        for r in ws.iter_rows(min_row=5, values_only=True):
            code = r[1]
            if not code or not str(code).startswith("RESTOCK-"): continue
            if not (r[2] and r[3]): continue
            rows.append({
                "go_code": str(code).strip(), "category": "RESTOCK",
                "idol_name": str(r[2]).strip(), "album": str(r[3]).strip(),
                "version": (str(r[4]).strip() if r[4] else None),
                "url": (str(r[5]).strip() if r[5] else None),
                "set_price_krw": parse_num(r[6]),
                "detail": (str(r[7]).strip() if r[7] else None),
                "note": (str(r[8]).strip() if len(r) > 8 and r[8] else None),
            })
            summary["RESTOCK"] += 1
        upsert_rows(rows)

    return templates.TemplateResponse("admin/import.html", {"request": request, "result": summary})
