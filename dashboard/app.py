import base64
import os
import re
from functools import wraps

from flask import (
    Flask, Response, abort, jsonify, redirect,
    render_template, request, session, url_for,
)

import store

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-before-deploying")

# Set DASHBOARD_PASSWORD as an environment variable on Render.
# If not set, the dashboard is inaccessible (returns 503) so you can't
# accidentally leave it open in production.
_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")


# ── Auth helpers ───────────────────────────────────────────────────────────────

def login_required(f):
    """Protects dashboard-facing routes. Camera unit routes are intentionally excluded."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _PASSWORD:
            return Response(
                "DASHBOARD_PASSWORD environment variable is not set. "
                "Configure it on Render before using the dashboard.",
                status=503, mimetype="text/plain",
            )
        if not session.get("authenticated"):
            # API calls from JS get a 401; browser navigations get a redirect.
            if request.accept_mimetypes.best == "application/json" \
                    or request.path.startswith("/api/"):
                return jsonify({"error": "Unauthorized"}), 401
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


# ── Login / logout ─────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET"])
def login_page():
    if session.get("authenticated"):
        return redirect(url_for("dashboard"))
    error = request.args.get("error")
    return render_template("login.html", error=error)


@app.route("/login", methods=["POST"])
def login():
    if not _PASSWORD:
        return redirect(url_for("login_page", error="no_password"))
    if request.form.get("password") == _PASSWORD:
        session["authenticated"] = True
        session.permanent = True
        return redirect(url_for("dashboard"))
    return redirect(url_for("login_page", error="wrong"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login_page"))


# ── Camera unit endpoints (open — no auth) ────────────────────────────────────

@app.route("/api/units/register", methods=["POST"])
def register():
    d = request.get_json()
    store.register_unit(d["id"], d["location"], d.get("config", {}))
    return jsonify({"status": "registered"})


@app.route("/api/units/<unit_id>/poll", methods=["POST"])
def poll(unit_id):
    d = request.get_json()
    commands = store.poll_unit(
        unit_id,
        d.get("config", {}),
        d.get("location"),
        d.get("standby"),
        d.get("health"),
        d.get("network"),
        d.get("op_hours"),
    )
    return jsonify({"commands": commands})


@app.route("/api/units/<unit_id>/snapshot", methods=["POST"])
def receive_snapshot(unit_id):
    d = request.get_json()
    store.store_snapshot(unit_id, d["image_base64"], d.get("width"), d.get("height"), d.get("timestamp"))
    return jsonify({"status": "received"})


@app.route("/api/units/<unit_id>/wifi_scan", methods=["POST"])
def receive_wifi_scan(unit_id):
    d = request.get_json()
    store.store_wifi_scan(unit_id, d.get("networks", []))
    return jsonify({"status": "received"})


@app.route("/api/units/<unit_id>/wifi_connect_result", methods=["POST"])
def receive_wifi_connect_result(unit_id):
    d = request.get_json()
    store.store_wifi_connect_result(unit_id, d.get("ssid"), d.get("success"), d.get("message"))
    return jsonify({"status": "received"})


@app.route("/api/units/<unit_id>/debug_capture", methods=["POST"])
def receive_debug_capture(unit_id):
    d = request.get_json()
    store.store_debug_capture(
        unit_id,
        d.get("image_base64"),
        d.get("trolley_grid", []),
        d.get("detection_count", 0),
        d.get("width"),
        d.get("height"),
    )
    return jsonify({"status": "received"})


# ── Dashboard data endpoints (auth required) ───────────────────────────────────

@app.route("/api/units", methods=["GET"])
@login_required
def list_units():
    return jsonify(store.get_all_units())


@app.route("/api/units/<unit_id>", methods=["GET"])
@login_required
def get_unit(unit_id):
    u = store.get_unit(unit_id)
    if not u:
        abort(404)
    u["snapshot_requests_remaining"] = store.snapshot_requests_remaining(unit_id)
    return jsonify(u)


@app.route("/api/units/<unit_id>/snapshot.jpg", methods=["GET"])
@login_required
def get_snapshot_image(unit_id):
    snap = store.get_snapshot(unit_id)
    if not snap:
        abort(404)
    return Response(base64.b64decode(snap["data"]), mimetype="image/jpeg")


# ── Dashboard command endpoints (auth required) ────────────────────────────────

@app.route("/api/units/<unit_id>/commands/snapshot", methods=["POST"])
@login_required
def request_snapshot(unit_id):
    if not store.can_request_snapshot(unit_id):
        return jsonify({"error": "Rate limit: max 10 snapshots per minute"}), 429
    if not store.queue_command(unit_id, {"type": "request_snapshot"}):
        abort(404)
    return jsonify({"status": "queued", "remaining": store.snapshot_requests_remaining(unit_id)})


@app.route("/api/units/<unit_id>/commands/crop", methods=["POST"])
@login_required
def set_crop(unit_id):
    d = request.get_json()
    crop = {"x": d["x"], "y": d["y"], "w": d["w"], "h": d["h"]}
    if not store.queue_command(unit_id, {"type": "set_crop", "data": crop}):
        abort(404)
    store.set_unit_crop(unit_id, crop)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/crop", methods=["DELETE"])
@login_required
def clear_crop(unit_id):
    if not store.queue_command(unit_id, {"type": "clear_crop"}):
        abort(404)
    store.clear_unit_crop(unit_id)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/grid", methods=["POST"])
@login_required
def set_grid(unit_id):
    d = request.get_json()
    grid = {
        "rows":        d["rows"],
        "cols":        d["cols"],
        "col_widths":  d["col_widths"],
        "row_heights": d["row_heights"],
    }
    if d.get("corners"):
        grid["corners"] = d["corners"]
    if not store.queue_command(unit_id, {"type": "set_grid", "data": grid}):
        abort(404)
    store.set_unit_grid(unit_id, grid)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/grid", methods=["DELETE"])
@login_required
def clear_grid(unit_id):
    if not store.queue_command(unit_id, {"type": "clear_grid"}):
        abort(404)
    store.clear_unit_grid(unit_id)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/interval", methods=["POST"])
@login_required
def set_interval(unit_id):
    d = request.get_json()
    interval = d.get("interval")
    if interval not in ("30s", "1min", "2min"):
        return jsonify({"error": "interval must be 30s, 1min, or 2min"}), 400
    if not store.queue_command(unit_id, {"type": "set_interval", "interval": interval}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/location", methods=["POST"])
@login_required
def set_location(unit_id):
    d = request.get_json()
    location = (d.get("location") or "").strip()
    if not location:
        return jsonify({"error": "location must not be empty"}), 400
    if not store.queue_command(unit_id, {"type": "set_location", "location": location}):
        abort(404)
    store.set_unit_location(unit_id, location)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/operational-hours", methods=["POST"])
@login_required
def set_operational_hours(unit_id):
    d     = request.get_json()
    start = (d.get("start") or "").strip()
    end   = (d.get("end")   or "").strip()
    if not (re.match(r'^\d{2}:\d{2}$', start) and re.match(r'^\d{2}:\d{2}$', end)):
        return jsonify({"error": "start and end must be HH:MM format"}), 400
    if not store.queue_command(unit_id, {"type": "set_operational_hours", "start": start, "end": end}):
        abort(404)
    store.set_unit_op_hours(unit_id, start, end)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/debug-copy", methods=["POST"])
@login_required
def set_debug_copy(unit_id):
    d = request.get_json()
    enabled = bool(d.get("enabled", False))
    if not store.queue_command(unit_id, {"type": "set_debug_copy", "enabled": enabled}):
        abort(404)
    store.set_unit_debug_copy(unit_id, enabled)
    return jsonify({"status": "queued", "enabled": enabled})


@app.route("/api/units/<unit_id>/commands/debug-capture", methods=["POST"])
@login_required
def request_debug_capture(unit_id):
    if not store.queue_command(unit_id, {"type": "request_debug_capture"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/snap", methods=["POST"])
@login_required
def trigger_snap(unit_id):
    if not store.queue_command(unit_id, {"type": "snap"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/standby", methods=["POST"])
@login_required
def standby_unit(unit_id):
    if not store.queue_command(unit_id, {"type": "set_standby"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/resume", methods=["POST"])
@login_required
def resume_unit(unit_id):
    if not store.queue_command(unit_id, {"type": "resume"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/wifi/scan", methods=["POST"])
@login_required
def request_wifi_scan(unit_id):
    if not store.queue_command(unit_id, {"type": "wifi_scan"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/wifi/connect", methods=["POST"])
@login_required
def request_wifi_connect(unit_id):
    d = request.get_json()
    ssid = (d.get("ssid") or "").strip()
    if not ssid:
        return jsonify({"error": "ssid required"}), 400
    if not store.queue_command(unit_id, {"type": "wifi_connect", "ssid": ssid, "password": d.get("password", "")}):
        abort(404)
    return jsonify({"status": "queued"})


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)
