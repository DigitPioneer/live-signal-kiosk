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
| `EDITOR_USERNAME`          | `admin`                                                | HTTP Basic Auth username for the slide editor. Also used by the local admin breakout (see below). |
| `EDITOR_PASSWORD`          | `changeme`                                             | HTTP Basic Auth password for the slide editor. **Change this before enabling the editor.** |
| `LOCAL_ADMIN_PORT`         | `8767`                                                 | Port the local admin breakout binds to on `127.0.0.1` only (never the LAN) - see "Local admin breakout" below. |
| `AUTOUPDATE_ENABLED`       | `false`                                                | Turns unattended auto-update on/off. **Off by default** - see "Auto-update" below before enabling. |
| `AUTOUPDATE_CHECK_INTERVAL_MINUTES` | `60`                                          | How often `kiosk-autoupdate.timer` checks for new commits. Baked into the timer at install time. |
| `AUTOUPDATE_WINDOW_START`  | `2`                                                    | Maintenance window start hour (0-23, local time). Updates/reboots only happen inside the window. |
| `AUTOUPDATE_WINDOW_END`    | `4`                                                    | Maintenance window end hour (0-23, local time). `START > END` wraps midnight. |

## Editing slides

Slides live in `web/slides.json` on the device and are picked up
automatically (the waiting page re-fetches them periodically — no restart
needed, though a hard refresh forces it immediately).

**Not tracked in git, by design.** `web/slides.json` and everything under
`web/assets/` (the logo, uploaded slide images) are excluded from version
control (see `.gitignore`) because they're live content, edited on disk
through the slide editor — not something meant to go through commits and
pull requests. This also means [Auto-update](#auto-update)'s rollback
(`git reset --hard`) can never discard a slide edit or a re-uploaded logo
along with reverting broken code: it only ever touches tracked code, never
these files. `install.sh` seeds both from the tracked templates,
[web/slides.example.json](web/slides.example.json) and
[web/assets/src-logo.example.png](web/assets/src-logo.example.png), the
first time it runs — same never-overwrite-an-existing-install pattern as
`config.env` — so a fresh clone still comes up with sensible defaults even
though the live files themselves aren't in the repository.

Schema:

```json
{
  "default_duration_seconds": 10,
  "slides": [
    {
      "title": "string, required",
      "subtitle": "string, optional",
      "message": "string, optional, may contain \n for line breaks",
      "image": "string, optional, relative path under assets/",
      "duration": "number, optional, seconds — falls back to default_duration_seconds",
      "full_image": "boolean, optional, default false — see below"
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

**Full-screen image slides:** set `"full_image": true` on a slide to show
its `image` filling the whole screen — no logo header, no title/subtitle/
message, letterboxed (not cropped) if its aspect ratio doesn't match the
TV. `image` is required when `full_image` is `true`; `title` is still
required by the schema but isn't shown for this slide type. Omit
`full_image` entirely (the default) for the normal logo-header + text
layout — existing slides with no `full_image` field are unaffected.

```json
{
  "title": "This Week's Flyer",
  "image": "flyer-2026-08-16.png",
  "full_image": true,
  "duration": 10
}
```

The church logo at `web/assets/src-logo.png` starts out as a placeholder
(seeded from [web/assets/src-logo.example.png](web/assets/src-logo.example.png)
on install — see above). Replace it with your church's real logo —
**keep the same filename** so `waiting.html` doesn't need editing.
Replacing the logo or any slide image (by hand or through the editor)
shows up on the waiting screen on its own within the next periodic slide
refresh — no `systemctl restart kiosk` needed.

You can also edit slides through the browser-based slide editor (see below)
instead of hand-editing the JSON, including toggling a slide between the
normal text layout and a full-screen image.

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

### System panel

The editor also has a **System** panel for the small set of device-level
actions you'd otherwise need SSH for:

- **Wi-Fi** — see current connection, scan for nearby networks, connect to
  one (password prompted only for secured networks).
- **Restart Kiosk Display** — restarts `kiosk.service` (briefly interrupts
  the screen, same as `sudo systemctl restart kiosk`).
- **Reboot Device** — reboots the whole machine.
- **Clear Chromium Cache** — wipes the kiosk's and admin browser's Chromium
  profile directories (cookies, cached pages, etc.) if either starts
  behaving oddly.
- **Clear Unused Images** — deletes any file under `web/assets/` that isn't
  the logo or referenced by a current slide's `image` field, to keep old
  uploads from accumulating.

Reboot/restart/Wi-Fi-connect need root, which the editor process doesn't
have (it runs as the unprivileged kiosk user, same as the watcher). Those
three go through `scripts/system-helper.sh` via a narrow passwordless-sudo
grant `install.sh` installs to `/etc/sudoers.d/live-signal-kiosk` — that
script is the only thing sudo trusts, it validates its own arguments, and
`install.sh` validates the generated sudoers file with `visudo -c` before
installing it (never installs anything unvalidated). Cache/image cleanup
needs no elevated privilege since the kiosk user already owns those files.

## Local admin breakout

For on-site setup — especially configuring Wi-Fi before the device has any
working network connection — press **Ctrl+Alt+Escape** on a keyboard
plugged directly into the device. This works even with no network at all,
since it's a physical hotkey handled entirely by the watcher process
running on the device, not a network request.

What it does:

1. Kills whatever's currently on screen (the waiting slides or a live
   stream) and pauses the watcher's normal YouTube-checking/relaunch loop,
   so nothing fights you for the display while you're using it.
2. Starts a local-only instance of the same editor (same
   `EDITOR_USERNAME`/`EDITOR_PASSWORD` login, same System panel) bound
   strictly to `127.0.0.1:8767` — this is never reachable from the network,
   regardless of any setting, and runs on a different port than the
   LAN-facing `kiosk-editor.service` so both can coexist if that happens to
   be running too.
3. Opens a normal windowed (not fullscreen) Chromium pointed at that local
   admin session, so you can use a mouse normally — the default `-nocursor`
   the X server used to start with has been removed for exactly this reason
   (it disabled the cursor at the X server level for everything, not just
   the kiosk display).

Press **Ctrl+Alt+Escape** again, or click **Return to Kiosk Display** in the
admin page, to exit: it closes the admin browser, stops the local admin
server, and returns to the waiting screen with a freshly-checked live
status (never anything cached from before you entered admin mode).

Under the hood: the hotkey (bound in Openbox's config, see
`scripts/openbox-rc.xml`) just runs `scripts/toggle-admin-mode.sh`, a tiny
script that sends `SIGUSR1` to the watcher process via its pidfile at
`/run/live-signal-kiosk/watcher.pid`. The watcher's own signal handler does
all the actual work — this keeps a single process in charge of what's on
the display at all times, so there's no separate script racing the
watcher's own crash-relaunch logic.

## Updating

- **Software update** (pulls the latest code, updates `yt-dlp`, restarts the
  service — briefly interrupts the display):
  ```bash
  sudo /opt/live-signal-kiosk/update.sh
  ```
- **Slide content update:** just edit `web/slides.json` and save. The waiting
  page re-fetches it on its own; no restart required.

## Auto-update

An optional unattended alternative to running `update.sh` by hand:
`kiosk-autoupdate.timer` periodically checks the git repo for new commits
and, if it finds one, updates and reboots on its own — but only with real
safety rails, since this runs on a device nobody's actively watching:

1. **Maintenance window.** Nothing happens outside
   `AUTOUPDATE_WINDOW_START`–`AUTOUPDATE_WINDOW_END` (local time, default
   2 AM–4 AM). A church kiosk must not go dark mid-service because a
   commit happened to land at a bad time.
2. **Sanity checks before ever rebooting.** After pulling, it runs
   `python3 -m py_compile` on every `.py` file, `bash -n` on every `.sh`
   file, and validates `web/slides.json` if it changed. Any failure resets
   the working tree back to the commit it was on before the pull and stops
   — the device keeps running its current, still-working code. No reboot.
3. **Automatic rollback for the failures sanity checks can't catch.** Code
   that's syntactically fine but still broken at runtime (the exact
   `WantedBy=graphical.target` class of bug noted in
   [docs/troubleshooting.md](docs/troubleshooting.md) — passes every static
   check, only fails on an actual boot) is caught differently: right
   before rebooting into an update, the pre-update commit is recorded as
   `/etc/live-signal-kiosk/last-known-good-commit`. On the next boot,
   `kiosk-healthcheck.service` waits up to 90 seconds for `kiosk.service`
   to actually become active. If it does, that commit becomes the new
   known-good baseline. If it doesn't, the device automatically rolls back
   to the last known-good commit and reboots once more to verify *that*.
4. **Rollback is capped at exactly one attempt.** If the rollback target
   itself doesn't come up cleanly either, the device stops trying and
   waits for a human — it will not reboot-loop.

**Off by default.** This device can be showing a live church service, and
an unattended reboot — even with the safety rails above — is a real risk
worth opting into deliberately rather than something that starts happening
silently after a routine `install.sh` run. Turn it on with:

```bash
sudo nano /etc/live-signal-kiosk/config.env   # set AUTOUPDATE_ENABLED=true
sudo /opt/live-signal-kiosk/install.sh
```

Re-running `install.sh` is also how you change
`AUTOUPDATE_CHECK_INTERVAL_MINUTES` or turn it back off
(`AUTOUPDATE_ENABLED=false` + re-run) — the timer's schedule is baked into
the systemd unit at install time, since timers can't read `config.env` at
runtime.

**Manual rollback**, if you ever need to do it yourself instead of waiting
on the automatic recovery (or after the one-attempt cap has been hit):

```bash
cat /etc/live-signal-kiosk/last-known-good-commit   # the commit to roll back to
cd /opt/live-signal-kiosk
sudo git reset --hard <commit>
sudo ./install.sh
sudo reboot
```

**Rollback only ever touches code, never slide content.** `web/slides.json`
and everything under `web/assets/` (logo, uploaded slide images) are
deliberately excluded from version control — see
[Editing slides](#editing-slides) — specifically so that both `git pull`
and, more importantly, `git reset --hard` during a rollback are scoped to
tracked application code only. Auto-update and its rollback path can never
discard a slide edit or a re-uploaded logo, however many update or
rollback cycles run in between. There's nothing to configure for this —
it's just how the files are tracked.

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
    slides.example.json     # tracked template - install.sh seeds slides.json from this on first install
    slides.json              # NOT tracked in git (see .gitignore) - live announcement slides, edited on disk
    assets/
      src-logo.example.png   # tracked template - install.sh seeds src-logo.png from this on first install
      src-logo.png            # NOT tracked in git - the live logo (and any uploaded slide images land here too)
  web-admin/
    index.html              # slide editor UI (served by editor.py, not by the kiosk's own server)
  scripts/
    start-kiosk.sh           # systemd ExecStart entrypoint
    xsession.sh               # X client script: screen-blanking off, Openbox, execs watcher.py
    openbox-rc.xml              # Openbox config template: admin-mode hotkey (Ctrl+Alt+Escape)
    toggle-admin-mode.sh          # tiny script the hotkey runs: sends SIGUSR1 to the watcher
    kiosk.service                  # systemd unit template
    kiosk-editor.service             # systemd unit template for the optional slide editor
    system-helper.sh                  # root-privileged helper for reboot/restart/Wi-Fi (see System panel)
    system-helper.sudoers              # sudoers template granting NOPASSWD access to system-helper.sh
    autoupdate.sh                       # unattended check-and-update, run by kiosk-autoupdate.timer
    healthcheck.sh                       # post-update boot verification/rollback, run by kiosk-healthcheck.service
    kiosk-autoupdate.service              # systemd unit template: runs autoupdate.sh (triggered by the timer)
    kiosk-autoupdate.timer                 # systemd timer template: how often autoupdate.sh runs
    kiosk-healthcheck.service               # systemd unit template: runs healthcheck.sh once per boot
    setup-lite-kiosk.sh                       # Raspberry Pi firmware/config.txt tweaks (Pi only, run by install.sh)
    silent-boot.sh                             # optional: hide most boot text (not run automatically)
  docs/
    raspberry-pi-lite-setup.md
    troubleshooting.md
```

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md).