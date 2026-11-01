"""
In-memory hub registry for TOD_Central.

Hubs register themselves on startup (providing their URL and access key).
TOD_Central polls each hub backend periodically and caches the result here.
All data is lost on restart — hubs re-register on their own schedule.
"""

import threading
from datetime import datetime, timedelta

_lock = threading.Lock()
_hubs = {}   # hub_id → hub record


def _now():
    return datetime.utcnow()


def _is_online(hub):
    """Hub is online if it was polled successfully within the last 60 seconds."""
    last = hub.get("last_seen")
    if not last:
        return False
    return _now() - datetime.fromisoformat(last) < timedelta(seconds=60)


# ── Hub registry ───────────────────────────────────────────────────────────────

def register_hub(hub_id, name, url, access_key):
    """Called when a hub backend POSTs to /api/hubs/register."""
    with _lock:
        existing = _hubs.get(hub_id, {})
        _hubs[hub_id] = {
            "id":            hub_id,
            "name":          name,
            "url":           url.rstrip("/"),
            "access_key":    access_key,
            "registered_at": existing.get("registered_at", _now().isoformat()),
            "last_seen":     existing.get("last_seen"),
            "units_cache":   existing.get("units_cache", []),
        }


def update_hub_poll(hub_id, units_list):
    """Called after a successful poll of a hub backend. Updates node cache."""
    with _lock:
        if hub_id not in _hubs:
            return
        _hubs[hub_id]["last_seen"]   = _now().isoformat()
        _hubs[hub_id]["units_cache"] = units_list


def get_all_hubs():
    """Returns summary of all registered hubs (with cached node summaries)."""
    with _lock:
        result = []
        for h in _hubs.values():
            units  = h.get("units_cache", [])
            online = sum(1 for u in units if u.get("online"))
            result.append({
                "id":            h["id"],
                "name":          h["name"],
                "online":        _is_online(h),
                "last_seen":     h.get("last_seen"),
                "registered_at": h.get("registered_at"),
                "unit_count":    len(units),
                "online_count":  online,
                "units":         units,
            })
        return result


def get_hub(hub_id):
    """Returns the full hub record (includes url + access_key for proxying)."""
    with _lock:
        return dict(_hubs[hub_id]) if hub_id in _hubs else None


def hub_exists(hub_id):
    with _lock:
        return hub_id in _hubs
