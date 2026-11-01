"""
TOD_Central — top-level multi-hub dashboard, runs on Render.

Hub backends (NUCs) register here on startup with their URL and access key.
TOD_Central polls each hub backend periodically for its node list, and
proxies management commands from the browser to the appropriate hub backend.

Required environment variables:
  DASHBOARD_PASSWORD — password to log into TOD_Central
  SECRET_KEY         — session secret (random string)
"""

import os
import threading
import time

import requests
from fastapi import Body, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import store

app = FastAPI()

# ── DEMO CODE BLOCK — delete after demo ───────────────────────────────────────
# Hardcoded demo credentials so TOD_Central runs on Render without setting env vars.
_PASSWORD  = os.environ.get("DASHBOARD_PASSWORD", "demo123")
_SECRET    = os.environ.get("SECRET_KEY",         "demo-secret-key")
# ── END DEMO CODE BLOCK — production lines below ──────────────────────────────
# _PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
# _SECRET   = os.environ.get("SECRET_KEY", "dev-secret-change-me")

app.add_middleware(SessionMiddleware, secret_key=_SECRET)
templates = Jinja2Templates(directory="templates")

_POLL_INTERVAL = 15   # seconds between TOD_Central → hub backend polls


# ── Auth ───────────────────────────────────────────────────────────────────────

def _require_auth(request: Request):
    if not _PASSWORD:
        raise HTTPException(status_code=503, detail="DASHBOARD_PASSWORD is not set. Configure it on Render.")
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ── Login / logout ─────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if request.session.get("authenticated"):
        return RedirectResponse(url="/", status_code=302)
    error = request.query_params.get("error")
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not _PASSWORD:
        return RedirectResponse(url="/login?error=no_password", status_code=302)
    if password == _PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=302)
    return RedirectResponse(url="/login?error=wrong", status_code=302)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


# ── Hub registration (called by hub backends on startup) ──────────────────────

@app.post("/api/hubs/register")
def register_hub(d: dict = Body(...)):
    hub_id     = d.get("id",         "").strip()
    name       = d.get("name",       hub_id)
    url        = d.get("url",        "").strip()
    access_key = d.get("access_key", "").strip()
    if not hub_id or not url or not access_key:
        raise HTTPException(status_code=400, detail="id, url, and access_key are required")
    store.register_hub(hub_id, name, url, access_key)
    print(f"Hub registered: {hub_id} ({name}) at {url}")
    return {"status": "registered"}


# ── Dashboard data ─────────────────────────────────────────────────────────────

@app.get("/api/hubs")
def list_hubs(_=Depends(_require_auth)):
    return store.get_all_hubs()


@app.get("/api/hubs/{hub_id}")
def get_hub(hub_id: str, _=Depends(_require_auth)):
    h = store.get_hub(hub_id)
    if not h:
        raise HTTPException(status_code=404)
    return {k: v for k, v in h.items() if k != "access_key"}


@app.get("/api/hubs/{hub_id}/units")
def hub_units(hub_id: str, _=Depends(_require_auth)):
    """Returns cached node list (updated by background poller)."""
    hub = store.get_hub(hub_id)
    if not hub:
        raise HTTPException(status_code=404)
    return hub.get("units_cache", [])


# ── Proxy: forward all hub-specific requests to the hub backend ────────────────

def _hub_headers(hub):
    return {"Authorization": f"Bearer {hub['access_key']}"}


@app.api_route("/api/hubs/{hub_id}/units/{rest:path}", methods=["GET", "POST", "DELETE"])
async def hub_proxy(hub_id: str, rest: str, request: Request, _=Depends(_require_auth)):
    """Proxies all per-unit requests to the appropriate hub backend."""
    hub = store.get_hub(hub_id)
    if not hub:
        raise HTTPException(status_code=404)
    body    = await request.body()
    url     = f"{hub['url']}/api/units/{rest}"
    headers = {**_hub_headers(hub)}
    ct = request.headers.get("content-type")
    if ct:
        headers["Content-Type"] = ct
    try:
        r = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=body,
            timeout=30,
        )
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=502, detail=f"Cannot reach hub '{hub_id}'")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Hub '{hub_id}' timed out")

    excluded = {"transfer-encoding", "connection", "content-encoding"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    if not _PASSWORD:
        return Response(content="DASHBOARD_PASSWORD is not set. Configure it on Render.", status_code=503)
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse("dashboard.html", {"request": request})


# ── Background hub poller ──────────────────────────────────────────────────────

def _poll_hubs():
    """Background thread: polls every registered hub backend for its node list."""
    while True:
        for hub in store.get_all_hubs():
            hub_full = store.get_hub(hub["id"])
            if not hub_full:
                continue
            try:
                r = requests.get(
                    f"{hub_full['url']}/api/units",
                    headers=_hub_headers(hub_full),
                    timeout=10,
                )
                if r.ok:
                    store.update_hub_poll(hub["id"], r.json())
            except Exception:
                pass   # hub offline — last_seen won't update, shows as offline
        time.sleep(_POLL_INTERVAL)


threading.Thread(target=_poll_hubs, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
