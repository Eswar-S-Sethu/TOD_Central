import base64

from flask import Flask, Response, abort, jsonify, render_template, request

import store

app = Flask(__name__)


# ── Camera unit endpoints ──────────────────────────────────────────────────────

@app.route("/api/units/register", methods=["POST"])
def register():
    d = request.get_json()
    store.register_unit(d["id"], d["location"], d.get("config", {}))
    return jsonify({"status": "registered"})


@app.route("/api/units/<unit_id>/poll", methods=["POST"])
def poll(unit_id):
    d = request.get_json()
    commands = store.poll_unit(unit_id, d.get("config", {}))
    return jsonify({"commands": commands})


@app.route("/api/units/<unit_id>/snapshot", methods=["POST"])
def receive_snapshot(unit_id):
    d = request.get_json()
    store.store_snapshot(unit_id, d["image_base64"], d.get("width"), d.get("height"), d.get("timestamp"))
    return jsonify({"status": "received"})


# ── Dashboard data endpoints ───────────────────────────────────────────────────

@app.route("/api/units", methods=["GET"])
def list_units():
    return jsonify(store.get_all_units())


@app.route("/api/units/<unit_id>", methods=["GET"])
def get_unit(unit_id):
    u = store.get_unit(unit_id)
    if not u:
        abort(404)
    u["snapshot_requests_remaining"] = store.snapshot_requests_remaining(unit_id)
    return jsonify(u)


@app.route("/api/units/<unit_id>/snapshot.jpg", methods=["GET"])
def get_snapshot_image(unit_id):
    snap = store.get_snapshot(unit_id)
    if not snap:
        abort(404)
    return Response(base64.b64decode(snap["data"]), mimetype="image/jpeg")


# ── Dashboard command endpoints ────────────────────────────────────────────────

@app.route("/api/units/<unit_id>/commands/snapshot", methods=["POST"])
def request_snapshot(unit_id):
    if not store.can_request_snapshot(unit_id):
        return jsonify({"error": "Rate limit: max 10 snapshots per minute"}), 429
    if not store.queue_command(unit_id, {"type": "request_snapshot"}):
        abort(404)
    return jsonify({"status": "queued", "remaining": store.snapshot_requests_remaining(unit_id)})


@app.route("/api/units/<unit_id>/commands/crop", methods=["POST"])
def set_crop(unit_id):
    d = request.get_json()
    if not store.queue_command(unit_id, {"type": "set_crop", "data": {
        "x": d["x"], "y": d["y"], "w": d["w"], "h": d["h"]
    }}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/crop", methods=["DELETE"])
def clear_crop(unit_id):
    if not store.queue_command(unit_id, {"type": "clear_crop"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/interval", methods=["POST"])
def set_interval(unit_id):
    d = request.get_json()
    interval = d.get("interval")
    if interval not in ("30s", "1min", "2min"):
        return jsonify({"error": "interval must be 30s, 1min, or 2min"}), 400
    if not store.queue_command(unit_id, {"type": "set_interval", "interval": interval}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/snap", methods=["POST"])
def trigger_snap(unit_id):
    if not store.queue_command(unit_id, {"type": "snap"}):
        abort(404)
    return jsonify({"status": "queued"})


@app.route("/api/units/<unit_id>/commands/stop", methods=["POST"])
def stop_unit(unit_id):
    if not store.queue_command(unit_id, {"type": "stop"}):
        abort(404)
    return jsonify({"status": "queued"})


# ── Dashboard UI ───────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    return render_template("dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)
