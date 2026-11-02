"""
TOD_Central — top-level multi-hub dashboard, runs on Render.

Hub backends (NUCs) register here on startup with their URL and access key.
TOD_Central polls each hub backend periodically for its node list, and
proxies management commands from the browser to the appropriate hub backend.
"""

import os
import pathlib
import threading
import time

import requests
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

import store

app = FastAPI()

_DASHBOARD_HTML = (pathlib.Path(__file__).parent / "templates" / "dashboard.html").read_text(encoding="utf-8")

_POLL_INTERVAL = 15   # seconds between TOD_Central → hub backend polls


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return _DASHBOARD_HTML


# ── Hub registration (called by hub backends on startup) ──────────────────────

@app.post("/api/hubs/register")
def register_hub(d: dict = Body(...)):
    hub_id     = d.get("id",         "").strip()
    name       = d.get("name",       hub_id)
    url        = d.get("url",        "").strip()
    access_key = d.get("access_key", "").strip()
    if not hub_id or not url:
        raise HTTPException(status_code=400, detail="id and url are required")
    store.register_hub(hub_id, name, url, access_key)
    print(f"Hub registered: {hub_id} ({name}) at {url}")
    return {"status": "registered"}


# ── Dashboard data ─────────────────────────────────────────────────────────────

@app.get("/api/hubs")
def list_hubs():
    return store.get_all_hubs()


@app.get("/api/hubs/{hub_id}")
def get_hub(hub_id: str):
    h = store.get_hub(hub_id)
    if not h:
        raise HTTPException(status_code=404)
    return {k: v for k, v in h.items() if k != "access_key"}


@app.get("/api/hubs/{hub_id}/units")
def hub_units(hub_id: str):
    """Returns cached node list (updated by background poller)."""
    hub = store.get_hub(hub_id)
    if not hub:
        raise HTTPException(status_code=404)
    return hub.get("units_cache", [])


# ── Proxy: forward all hub-specific requests to the hub backend ────────────────

@app.api_route("/api/hubs/{hub_id}/units/{rest:path}", methods=["GET", "POST", "DELETE"])
async def hub_proxy(hub_id: str, rest: str, request: Request):
    """Proxies all per-unit requests to the appropriate hub backend."""
    hub = store.get_hub(hub_id)
    if not hub:
        raise HTTPException(status_code=404)
    body    = await request.body()
    url     = f"{hub['url']}/api/units/{rest}"
    headers = {}
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
