# TOD Central Dashboard

The central management dashboard for TOD camera units. Hosted on Render at:

**https://tod-central-dashboard.onrender.com**

---

## What this is

This is a Flask web application that acts as the control plane for all camera units in the field. Camera units connect **out** to this server — they poll it every few seconds for commands. The dashboard has no way to reach camera units directly; everything flows through the polling mechanism.

Media (production captures) never passes through here. This server handles control only: status, config, snapshots for crop setup, commands, and WiFi management.

The dashboard UI is mobile-friendly — on small screens the sidebar and detail panel are shown one at a time, with a back button to navigate between them.

---

## How camera units interact with this server

Every camera unit runs a background thread (`render_client.py`) that:

1. **Registers** on startup — `POST /api/units/register` with its ID, location, and current config.
2. **Polls every 5 seconds** — `POST /api/units/<id>/poll` with its current config, health stats, and network info. The server returns any queued commands and clears the queue.
3. **Sends snapshots when requested** — `POST /api/units/<id>/snapshot` with a base64-encoded JPEG.
4. **Sends WiFi scan results** — `POST /api/units/<id>/wifi_scan` after completing a scan.
5. **Sends WiFi connect results** — `POST /api/units/<id>/wifi_connect_result` after a connection attempt.

A unit is considered **online** if its last poll was within 30 seconds. Offline units keep their last-known data in memory.

---

## Commands

Commands are queued by the dashboard and delivered to the unit on its next poll. Each command is a JSON object with a `type` field.

| Command type | Payload | Effect on unit |
|---|---|---|
| `request_snapshot` | — | Unit captures full frame (face-checked), sends to `/api/units/<id>/snapshot` |
| `set_crop` | `x, y, w, h` | Unit saves crop region to `captures/crop.json`, applied to all future captures |
| `clear_crop` | — | Unit removes crop region, reverts to full frame |
| `set_interval` | `interval` (`30s` / `1min` / `2min`) | Unit switches capture interval immediately |
| `set_location` | `location` | Unit updates location name, persists to `captures/location.json` |
| `snap` | — | Unit captures and uploads to local media server immediately |
| `set_standby` | — | Unit pauses all captures and uploads; continues polling |
| `resume` | — | Unit resumes normal capture and upload operations |
| `wifi_scan` | — | Unit scans WiFi networks (~10–30 s) and POSTs results back |
| `wifi_connect` | `ssid`, `password` | Unit connects to the specified WiFi network and POSTs the result back |

---

## Snapshot workflow (crop setup)

1. Open the dashboard, select a unit.
2. Click **Request Snapshot** — this queues a `request_snapshot` command.
3. The unit picks it up on its next poll (within 5 seconds), captures a full-frame JPEG (face-checked), and POSTs it back.
4. The dashboard polls for the snapshot every 2 seconds and displays it when it arrives (30-second timeout).
5. Click and drag on the image to draw a crop rectangle.
6. Click **Apply Crop** — this queues a `set_crop` command with the pixel coordinates.
7. The unit picks up `set_crop` on its next poll and saves `crop.json`.

Snapshots are rate-limited to **10 per minute per unit**.

---

## Network management workflow

The Network section is visible on the dashboard whenever a unit is reporting network data (every poll).

**Viewing current network and IP:**
- The unit reports its active WiFi SSID and all IPv4 addresses (per interface) on every poll.
- These are displayed in the Network section without any button press.
- Use this IP to SSH into the unit when it is on the same network as your laptop.

**Scanning for available networks:**
1. Click **Scan Networks** — queues a `wifi_scan` command.
2. The unit triggers `nmcli dev wifi list --rescan yes` in a background thread (takes 10–30 seconds).
3. Results are POSTed back to the dashboard and displayed as a list sorted by signal strength, with signal bars, SSID, and a lock icon for secured networks.

**Connecting to a network:**
1. Click **Connect** next to the target network.
2. Enter the password if the network is secured (the password field expands inline).
3. Click **Connect** — queues a `wifi_connect` command with the SSID and password.
4. The unit attempts the connection (`nmcli dev wifi connect … password …`) in a background thread.
5. The result (success or error message from nmcli) is POSTed back and shown in the dashboard.
6. The new IP address automatically appears in the Network section on the next poll.

The typical use case is switching the unit to your local WiFi so you can SSH into it for maintenance, then switching back to the USB modem when done.

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
    └── dashboard.html      # Single-page dashboard UI (mobile-friendly)
```

### `store.py`

Thread-safe in-memory dict keyed by `unit_id`. Stores per unit:
- Metadata (id, location, registered_at, last_seen, config, standby)
- System health stats (CPU, memory, disk, temperature, battery)
- Network info (WiFi SSID, IP addresses per interface)
- WiFi scan results (network list + timestamp)
- WiFi connect result (ssid, success, message, timestamp)
- Pending command queue
- Latest snapshot (base64 image + dimensions + timestamp)
- Snapshot request timestamps for rate limiting

### `app.py`

Three groups of routes:

**Camera unit endpoints** (called by camera units):
- `POST /api/units/register`
- `POST /api/units/<id>/poll`
- `POST /api/units/<id>/snapshot`
- `POST /api/units/<id>/wifi_scan`
- `POST /api/units/<id>/wifi_connect_result`

**Dashboard data endpoints** (called by the UI):
- `GET /api/units` — list all units
- `GET /api/units/<id>` — get unit detail (includes network, wifi_scan, wifi_connect)
- `GET /api/units/<id>/snapshot.jpg` — latest snapshot as JPEG

**Command endpoints** (called by the UI to queue commands):
- `POST /api/units/<id>/commands/snapshot`
- `POST /api/units/<id>/commands/crop` / `DELETE` to clear
- `POST /api/units/<id>/commands/interval`
- `POST /api/units/<id>/commands/location`
- `POST /api/units/<id>/commands/snap`
- `POST /api/units/<id>/commands/standby`
- `POST /api/units/<id>/commands/resume`
- `POST /api/units/<id>/commands/wifi/scan`
- `POST /api/units/<id>/commands/wifi/connect`

### `dashboard.html`

Single-page app. No framework — plain JavaScript with `fetch`. Mobile-friendly: on screens ≤ 640 px wide, the sidebar and detail panel are shown one at a time.

- **Left sidebar:** unit list, auto-refreshes every 15 seconds. Green/grey dot = online/offline.
- **Right panel:** unit detail — refreshes every 10 seconds.
  - **Info row:** status, last seen, registered, interval, crop, pending commands, snapshot info, mode.
  - **System Stats:** CPU, memory, disk, temperature, battery (hidden if not reported).
  - **Network:** current WiFi SSID and all IP addresses. Scan Networks button opens a list of visible networks with signal strength; each has a Connect button with an inline password field.
  - **Snapshot & Crop Setup:** full-frame image with canvas overlay for drawing crop rectangles; crop preview panel.
  - **Capture Interval:** 30s / 1min / 2min selector.
  - **Actions:** Take Capture Now, Standby, Resume.

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
