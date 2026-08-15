# LiveSignal Kiosk

A lightweight Linux kiosk for a church TV. It shows a full-screen "Waiting for
Signal" screen with rotating announcement slides, watches a YouTube channel,
and automatically switches to a full-screen player of the stream when the
channel goes live — then switches back when the stream ends.

- **Primary target:** Raspberry Pi OS Lite 64-bit (no desktop environment), Pi
  connected to a TV via HDMI.
- **Secondary target:** any Debian/Ubuntu-based device (mini PC, old laptop) —
  the same scripts work there too; nothing outside `scripts/setup-lite-kiosk.sh`
  and the Pi-specific docs assumes Pi hardware.

## Architecture, briefly

Two layers of crash supervision, so the display always recovers on its own:

1. **Outer layer — systemd.** `kiosk.service` takes over `tty1` directly (no
   getty, no display manager) and runs `startx`, which starts Openbox and then
   the watcher. `Restart=always` means if anything in the X session dies
   unexpectedly, systemd relaunches the whole session after a few seconds.
2. **Inner layer — `src/watcher.py`.** A single Python process, `exec`'d as
   the X session's client. It runs a small local HTTP server for the waiting
   screen, polls the configured YouTube channel with `yt-dlp`, and manages
   exactly one child process at a time: **Chromium** (kiosk mode, waiting
   screen) when not live, or **mpv** (fullscreen stream playback) when live.
   It relaunches either one if it crashes, and handles its own errors so
   transient network/yt-dlp hiccups don't take the display down.

Chromium never does the live video decode — mpv does, on every target, not
just the Pi. Chromium's HTML5 video path is the most common source of
stutter/crashes on devices without a desktop compositor or a weak GPU, and
that includes the "old laptop" secondary target, not only the Pi. `mpv` uses
hardware decoding properly and sidesteps YouTube embed-page quirks (consent
dialogs, ad slates, autoplay policy fights). Using it everywhere keeps the
watcher logic and install steps identical across hardware.

See [docs/raspberry-pi-lite-setup.md](docs/raspberry-pi-lite-setup.md) and
[docs/troubleshooting.md](docs/troubleshooting.md) for more detail.

## Quick start — Raspberry Pi OS Lite 64-bit

```bash
git clone <this-repo-url> /opt/live-signal-kiosk
cd /opt/live-signal-kiosk
sudo ./install.sh
sudo nano /etc/live-signal-kiosk/config.env   # set CHANNEL_LIVE_URL
sudo reboot
```

See [docs/raspberry-pi-lite-setup.md](docs/raspberry-pi-lite-setup.md) for the
full walkthrough from a blank SD card, including headless first-boot setup.

## Quick start — generic Debian/Ubuntu

Same steps as above work on any Debian/Ubuntu-based machine with a display
attached — `install.sh` detects Pi hardware and only runs the Pi-specific
firmware setup when it finds it.

```bash
git clone <this-repo-url> /opt/live-signal-kiosk
cd /opt/live-signal-kiosk
sudo ./install.sh
sudo nano /etc/live-signal-kiosk/config.env
sudo systemctl start kiosk
```

## Config reference

Installed to `/etc/live-signal-kiosk/config.env` on first install only.
Neither `install.sh` nor `update.sh` will overwrite it on re-run, so your
edits persist across updates.

| Key                       | Default                                              | Meaning |
|---------------------------|-------------------------------------------------------|---------|
| `CHANNEL_LIVE_URL`        | `https://www.youtube.com/@SRCMinistry/live`            | Channel `/live` URL to poll. |
| `CHECK_INTERVAL_SECONDS`  | `30`                                                   | Seconds between live-status checks. |
| `OFFLINE_CONFIRM_CHECKS`  | `2`                                                     | Consecutive "not live" checks required before switching LIVE → WAITING. Going TO live is immediate. |
| `YOUTUBE_EMBED_PARAMS`    | `autoplay=1&controls=1&rel=0&modestbranding=1`          | Documented for reference / a future Chromium-embed fallback. Not used by mpv. |
| `LOCAL_SERVER_PORT`       | `8765`                                                  | Port the watcher's local HTTP server binds to (`127.0.0.1` only). |
| `CHROMIUM_BIN`            | *(blank = auto-detect)*                                 | Override Chromium binary path. Auto-detect tries `chromium-browser`, then `chromium`. |
| `MPV_BIN`                 | *(blank = auto-detect)*                                 | Override `mpv` binary path. |
| `YTDLP_BIN`                | *(blank = auto-detect)*                                | Override `yt-dlp` binary path. Auto-detect tries `PATH`, then `/usr/local/bin/yt-dlp`. |
| `KIOSK_USER`               | `kiosk`                                                | System user the kiosk session runs as. |
| `LOG_LEVEL`                | `INFO`                                                 | Python logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `EDITOR_PORT`              | `8766`                                                 | Port the optional slide editor binds to on the LAN interface. |
| `EDITOR_USERNAME`          | `admin`                                                | HTTP Basic Auth username for the slide editor. |
| `EDITOR_PASSWORD`          | `changeme`                                             | HTTP Basic Auth password for the slide editor. **Change this before enabling the editor.** |

## Editing slides

Slides live in [web/slides.json](web/slides.json) and are picked up
automatically (the waiting page re-fetches them periodically — no restart
needed, though a hard refresh forces it immediately). Schema:

```json
{
  "default_duration_seconds": 10,
  "slides": [
    {
      "title": "string, required",
      "subtitle": "string, optional",
      "message": "string, optional, may contain \n for line breaks",
      "image": "string, optional, relative path under assets/",
      "duration": "number, optional, seconds — falls back to default_duration_seconds"
    }
  ]
}
```

Example:

```json
{
  "default_duration_seconds": 10,
  "slides": [
    {
      "title": "Potluck This Friday",
      "subtitle": "6:00 PM in the Fellowship Hall",
      "message": "Bring a dish to share!\nSign up at the welcome desk.",
      "duration": 12
    }
  ]
}
```

The church logo at [web/assets/src-logo.png](web/assets/src-logo.png) ships
as a placeholder. Replace it with your church's real logo — **keep the same
filename** so `waiting.html` doesn't need editing.

You can also edit slides through the browser-based slide editor (see below)
instead of hand-editing the JSON.

## Slide editor (optional)

A small browser-based editor lets you add/edit/reorder/delete slides and
upload slide images or a new logo from a laptop on the same network —
no SSH or hand-editing JSON required. It runs as its own systemd service,
`kiosk-editor.service`, completely independent of `kiosk.service`: it's a
plain network service (not part of the X session), so a bug or crash in it
can never take the kiosk display down.

It's **off by default** — it's only needed while actively editing slides,
so there's no reason to leave an extra LAN-facing port open the rest of the
time. Turn it on when you need it:

```bash
sudo systemctl enable --now kiosk-editor
```

Then browse to `http://<device-ip>:8766` from any device on the same
network and log in with `EDITOR_USERNAME`/`EDITOR_PASSWORD` from
`/etc/live-signal-kiosk/config.env` (HTTP Basic Auth). **Change
`EDITOR_PASSWORD` from its default (`changeme`) before enabling this** —
edit the config file, then restart the editor for it to take effect:

```bash
sudo nano /etc/live-signal-kiosk/config.env
sudo systemctl restart kiosk-editor
```

Turn it back off when you're done:

```bash
sudo systemctl disable --now kiosk-editor
```

Saves write straight to `web/slides.json` (validated and written
atomically, so the kiosk display never sees a partially-written file) and
uploaded images go into `web/assets/`, so nothing about the waiting page
changes — the editor is just a friendlier way to produce the same files you
could otherwise edit by hand.

## Updating

- **Software update** (pulls the latest code, updates `yt-dlp`, restarts the
  service — briefly interrupts the display):
  ```bash
  sudo /opt/live-signal-kiosk/update.sh
  ```
- **Slide content update:** just edit `web/slides.json` and save. The waiting
  page re-fetches it on its own; no restart required.

## Day-to-day service management

```bash
sudo systemctl status kiosk      # is it running?
sudo systemctl restart kiosk     # restart the whole kiosk session
sudo systemctl stop kiosk        # stop it
journalctl -u kiosk -f           # follow live logs
```

Watcher logs also go to `/var/log/live-signal-kiosk/watcher.log` (rotated).

## Repository structure

```
live-signal-kiosk/
  README.md
  config.example.env
  install.sh
  update.sh
  src/
    watcher.py            # the watcher: HTTP server + YouTube polling + child process management
    editor.py              # optional slide editor: LAN HTTP server for slides.json + image uploads
    envfile.py               # shared KEY=VALUE config parser used by both
  web/
    waiting.html           # self-contained waiting/slides page
    slides.json             # editable announcement slides
    assets/
      src-logo.png          # placeholder church logo — replace with the real one
  web-admin/
    index.html              # slide editor UI (served by editor.py, not by the kiosk's own server)
  scripts/
    start-kiosk.sh           # systemd ExecStart entrypoint
    xsession.sh               # X client script: screen-blanking off, Openbox, execs watcher.py
    kiosk.service              # systemd unit template
    kiosk-editor.service        # systemd unit template for the optional slide editor
    setup-lite-kiosk.sh        # Raspberry Pi firmware/config.txt tweaks (Pi only, run by install.sh)
    silent-boot.sh              # optional: hide most boot text (not run automatically)
  docs/
    raspberry-pi-lite-setup.md
    troubleshooting.md
```

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md).
