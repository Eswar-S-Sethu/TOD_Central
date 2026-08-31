"""
In-memory state store for the Render dashboard.
All data is lost on server restart — camera units repopulate it within
one poll cycle (≤ POLL_INTERVAL seconds) after reconnecting.
"""

import threading
from datetime import datetime, timedelta

_lock  = threading.Lock()
_units = {}   # unit_id → unit record


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now():
    return datetime.utcnow()


def _is_online(unit):
    last = datetime.fromisoformat(unit["last_seen"])
    return _now() - last < timedelta(seconds=30)


# ── Camera unit API ────────────────────────────────────────────────────────────

def register_unit(unit_id, location, config):
    with _lock:
        existing = _units.get(unit_id, {})
        _units[unit_id] = {
            "id":               unit_id,
            "location":         location,
            "registered_at":    existing.get("registered_at", _now().isoformat()),
            "last_seen":        _now().isoformat(),
            "config":           config,
            "health":           existing.get("health"),
            "pending_commands": existing.get("pending_commands", []),
            "snapshot":         existing.get("snapshot"),
            "_snap_times":      existing.get("_snap_times", []),
            "dashboard_crop":   existing.get("dashboard_crop"),
        }


def poll_unit(unit_id, config, location=None, health=None):
    """Updates last_seen, config, health, and optionally location; returns and clears pending commands."""
    with _lock:
        if unit_id not in _units:
            return []
        unit = _units[unit_id]
        unit["last_seen"] = _now().isoformat()
        unit["config"]    = config
        if location:
            unit["location"] = location
        if health is not None:
            unit["health"] = health
        commands = list(unit["pending_commands"])
        unit["pending_commands"] = []
        return commands


def store_snapshot(unit_id, image_base64, width, height, timestamp):
    with _lock:
        if unit_id not in _units:
            return
        _units[unit_id]["snapshot"] = {
            "data":      image_base64,
            "width":     width,
            "height":    height,
            "timestamp": timestamp,
        }


# ── Dashboard API ──────────────────────────────────────────────────────────────

def get_all_units():
    with _lock:
        result = []
        for u in _units.values():
            result.append({
                "id":               u["id"],
                "location":         u["location"],
                "online":           _is_online(u),
                "last_seen":        u["last_seen"],
                "config":           u["config"],
                "has_snapshot":     u["snapshot"] is not None,
                "snapshot_timestamp": u["snapshot"]["timestamp"] if u["snapshot"] else None,
            })
        return result


def get_unit(unit_id):
    with _lock:
        u = _units.get(unit_id)
        if not u:
            return None
        snap = u["snapshot"]
        return {
            "id":                    u["id"],
            "location":              u["location"],
            "online":                _is_online(u),
            "registered_at":         u["registered_at"],
            "last_seen":             u["last_seen"],
            "config":                u["config"],
            "has_snapshot":          snap is not None,
            "snapshot_timestamp":    snap["timestamp"] if snap else None,
            "snapshot_width":        snap["width"]     if snap else None,
            "snapshot_height":       snap["height"]    if snap else None,
            "pending_commands_count": len(u["pending_commands"]),
            "crop":                  u.get("dashboard_crop"),
            "health":                u.get("health"),
        }


def get_snapshot(unit_id):
    with _lock:
        u = _units.get(unit_id)
        return dict(u["snapshot"]) if u and u.get("snapshot") else None


def set_unit_location(unit_id, location):
    with _lock:
        if unit_id not in _units:
            return False
        _units[unit_id]["location"] = location
        return True


def set_unit_crop(unit_id, crop):
    with _lock:
        if unit_id not in _units:
            return False
        _units[unit_id]["dashboard_crop"] = crop
        return True


def clear_unit_crop(unit_id):
    with _lock:
        if unit_id not in _units:
            return False
        _units[unit_id]["dashboard_crop"] = None
        return True


def queue_command(unit_id, command):
    with _lock:
        if unit_id not in _units:
            return False
        _units[unit_id]["pending_commands"].append(command)
        return True


def can_request_snapshot(unit_id):
    """Rate limit: max 10 snapshot requests per minute per unit."""
    with _lock:
        u = _units.get(unit_id)
        if not u:
            return False
        cutoff = _now() - timedelta(minutes=1)
        recent = [t for t in u["_snap_times"] if datetime.fromisoformat(t) > cutoff]
        if len(recent) >= 10:
            return False
        recent.append(_now().isoformat())
        u["_snap_times"] = recent
        return True


def snapshot_requests_remaining(unit_id):
    """Returns how many snapshot requests are left in the current minute."""
    with _lock:
        u = _units.get(unit_id)
        if not u:
            return 0
        cutoff = _now() - timedelta(minutes=1)
        recent = [t for t in u["_snap_times"] if datetime.fromisoformat(t) > cutoff]
        return max(0, 10 - len(recent))
