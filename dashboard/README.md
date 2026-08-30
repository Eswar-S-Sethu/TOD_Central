# TOD Central Dashboard

The central management dashboard for TOD camera units. Hosted on Render at:

**https://tod-central-dashboard.onrender.com**

---

## What this is

This is a Flask web application that acts as the control plane for all camera units in the field. Camera units connect **out** to this server — they poll it every few seconds for commands. The dashboard has no way to reach camera units directly; everything flows through the polling mechanism.

Media (production captures) never passes through here. This server handles control only: status, config, snapshots for crop setup, and commands.

---

## How camera units interact with this server

Every camera unit runs a background thread (`render_client.py`) that:

1. **Registers** on startup — `POST /api/units/register` with its ID, location, and current config.
2. **Polls every 5 seconds** — `POST /api/units/<id>/poll` with its current config. The server returns any queued commands and clears the queue.
3. **Sends snapshots when requested** — `POST /api/units/<id>/snapshot` with a base64-encoded JPEG.

A unit is considered **online** if its last poll was within 30 seconds. Offline units keep their last-known data in memory.

---

## Commands

Commands are queued by the dashboard and delivered to the unit on its next poll. Each command is a JSON object with a `type` field.

| Command type | Payload | Effect on unit |
|---|---|---|
| `request_snapshot` | — | Unit captures full frame (no crop), sends to `/api/units/<id>/snapshot` |
| `set_crop` | `x, y, w, h` | Unit saves crop region to `captures/crop.json`, applied to all future captures |
| `clear_crop` | — | Unit removes crop region, reverts to full frame |
| `set_interval` | `interval` (30s / 1min / 2min) | Unit switches capture interval immediately |
| `snap` | — | Unit captures and uploads to local media server immediately |
| `stop` | — | Unit shuts down cleanly |

---

## Snapshot workflow (crop setup)

1. Open the dashboard, select a unit.
2. Click **Request Snapshot** — this queues a `request_snapshot` command.
3. The unit picks it up on its next poll (within 5 seconds), captures a full-frame JPEG, and POSTs it back.
4. The dashboard polls for the snapshot every 2 seconds and displays it when it arrives (30-second timeout).
5. Click and drag on the image to draw a crop rectangle.
6. Click **Apply Crop** — this queues a `set_crop` command with the pixel coordinates.
7. The unit picks up `set_crop` on its next poll and saves `crop.json`.

Snapshots are rate-limited to **10 per minute per unit**.

---

## In-memory state

All data is stored in memory (`store.py`). There is no database. On server restart, all unit data is lost — but camera units re-register and repopulate within one poll cycle (≤ 5 seconds).

The Render starter plan may spin the service down after inactivity. Camera units will reconnect and re-register automatically on the next poll attempt.

---

## File structure

```
dashboard/
├── app.py                  # Flask app and all route definitions
├── store.py                # In-memory state store (units, snapshots, commands)
├── render.yaml             # Render deployment config
├── requirements.txt        # flask, gunicorn
└── templates/
    └── dashboard.html      # Single-page dashboard UI
```

### `store.py`

Thread-safe in-memory dict keyed by `unit_id`. Stores:
- Unit metadata (id, location, registered_at, last_seen, config)
- Pending command queue
- Latest snapshot (base64 image + dimensions + timestamp)
- Snapshot request timestamps for rate limiting

### `app.py`

Three groups of routes:

**Camera unit endpoints** (called by camera units):
- `POST /api/units/register`
- `POST /api/units/<id>/poll`
- `POST /api/units/<id>/snapshot`

**Dashboard data endpoints** (called by the UI):
- `GET /api/units` — list all units
- `GET /api/units/<id>` — get unit detail
- `GET /api/units/<id>/snapshot.jpg` — latest snapshot as JPEG

**Command endpoints** (called by the UI to queue commands):
- `POST /api/units/<id>/commands/snapshot`
- `POST /api/units/<id>/commands/crop` / `DELETE` to clear
- `POST /api/units/<id>/commands/interval`
- `POST /api/units/<id>/commands/snap`
- `POST /api/units/<id>/commands/stop`

### `dashboard.html`

Single-page app. No framework — plain JavaScript with `fetch`.

- **Left sidebar:** unit list, auto-refreshes every 15 seconds. Green/grey dot = online/offline.
- **Right panel:** unit detail — refreshes every 10 seconds.
  - Snapshot section with canvas overlay for drawing crop rectangles.
  - Interval selector (30s / 1min / 2min) with active state.
  - Action buttons: Take Capture Now, Stop Unit.

---

## Deployment

Deployed via `render.yaml`:

```yaml
services:
  - type: web
    name: tod-camunit-dashboard
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app
    plan: starter
```

Render runs `gunicorn` from inside the `dashboard/` root directory. The `render.yaml` root directory is set to `dashboard/` in the Render service settings.
