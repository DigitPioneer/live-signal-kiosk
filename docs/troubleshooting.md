# Troubleshooting

## No HDMI output at boot

If the TV shows nothing when the Pi boots (especially when the TV was
powered off or on a different input while the Pi booted), make sure
`hdmi_force_hotplug=1` is set in `config.txt`. `install.sh` sets this
automatically on Pi hardware via `scripts/setup-lite-kiosk.sh`. To confirm:

```bash
grep hdmi_force_hotplug /boot/firmware/config.txt
```

If missing, re-run `sudo /opt/live-signal-kiosk/scripts/setup-lite-kiosk.sh`
and reboot.

## No audio over HDMI

Force HDMI audio output explicitly:

```bash
sudo raspi-config
# System Options -> Audio -> HDMI
```

Or add `hdmi_drive=2` to `config.txt` if the TV isn't recognized as an audio
sink. mpv otherwise uses whatever the system's default ALSA/PulseAudio
output is.

## Black screen / kiosk not starting

Check the service status and logs first:

```bash
sudo systemctl status kiosk
sudo journalctl -u kiosk -e
```

Common causes:

- **`kiosk.service` isn't enabled/started.** `sudo systemctl enable --now kiosk`.
- **`kiosk.service`'s `[Install] WantedBy=` target doesn't match what this
  device actually boots to.** Raspberry Pi OS Lite (and a generic
  Debian/Ubuntu server install) boots to `multi-user.target` - there's no
  display manager, so a unit `WantedBy=graphical.target` would be enabled
  but never actually pulled in on a cold boot (`kiosk.service` correctly
  ships with `WantedBy=multi-user.target` for exactly this reason - if
  you've hand-edited the unit, check it hasn't drifted back). Confirm what
  your device boots to and that the unit targets it:
  ```bash
  systemctl get-default
  systemctl show kiosk.service -p WantedBy
  ```
- **X can't start on `tty1`** because a getty is still attached to it.
  `kiosk.service` declares `Conflicts=getty@tty1.service`, which should
  handle this automatically — but if you've customized `tty1` elsewhere,
  make sure nothing else is holding it.
- **Watcher crashed at startup** (bad config, missing binary). Check
  `/var/log/live-signal-kiosk/watcher.log` and the `journalctl` output above
  for a Python traceback.

> **Testing note:** `sudo systemctl start kiosk` succeeding only proves the
> service *can* run - it does not prove the service starts on its own.
> `systemctl start` bypasses the `[Install]`/`WantedBy=` wiring entirely, so
> a unit with a target mismatch (like the `graphical.target` bug above) can
> look completely fixed in manual testing while still never firing on a
> real cold boot. Always confirm boot-time behavior with an actual
> `sudo reboot` (or power-cycle) and watch it come up on its own - not just
> `systemctl start`/`enable` followed by a status check.

## Chromium won't launch

- Confirm a Chromium package is actually installed:
  ```bash
  which chromium-browser chromium
  ```
  If neither package installed cleanly during `install.sh` (this can happen
  on some distro/version combinations), install one manually and either set
  `CHROMIUM_BIN` in `/etc/live-signal-kiosk/config.env` to its path, or make
  sure it's on `PATH` under one of the two auto-detected names.
- Check `journalctl -u kiosk -f` while it should be launching — Chromium
  failures show up as repeated relaunch attempts in the watcher log.

## mpv won't play / stream detection not working

This is almost always **`yt-dlp` falling behind YouTube's changes** — YouTube
frequently makes changes that break extraction, and yt-dlp ships frequent
fixes. Update it:

```bash
sudo /usr/local/bin/yt-dlp -U
```

Or run the full updater, which does this and more:

```bash
sudo /opt/live-signal-kiosk/update.sh
```

If mpv launches but shows a black screen/no video, test stream resolution
manually:

```bash
/usr/local/bin/yt-dlp -j --skip-download "https://www.youtube.com/@yourchannel/live"
/usr/local/bin/yt-dlp -g "https://www.youtube.com/watch?v=<video-id>"
```

The second command should print a direct stream URL. If it errors, that's a
yt-dlp/YouTube issue, not a bug in the watcher — update yt-dlp and retry.

## Slides not updating

The watcher's local HTTP server serves `slides.json` fresh on every request
— there's no server-side caching. `waiting.html` also re-fetches
periodically on its own. If you don't see your edit:

- Confirm you edited the right file: `/opt/live-signal-kiosk/web/slides.json`
  (or wherever the repo was cloned).
- Confirm the JSON is valid — a syntax error will make the fetch fail
  silently in the browser console. `python3 -m json.tool web/slides.json`
  will tell you immediately if it's broken.
- Force it immediately instead of waiting for the periodic re-fetch:
  `sudo systemctl restart kiosk`.

## Ctrl+Alt+Escape (local admin breakout) doesn't do anything

- **Requires a keyboard physically attached to the device** — it's an
  Openbox keybinding, not something reachable over the network.
- Check the pidfile exists and matches a running watcher process:
  ```bash
  cat /run/live-signal-kiosk/watcher.pid
  ps -p "$(cat /run/live-signal-kiosk/watcher.pid)"
  ```
  If missing, `kiosk.service`'s `RuntimeDirectory=live-signal-kiosk` isn't
  taking effect (re-run `install.sh` to re-render the unit) or the watcher
  crashed before writing it — check `journalctl -u kiosk -e`.
- Confirm the keybinding actually got seeded: `cat ~kiosk/.config/openbox/rc.xml`
  should contain a `<keybind key="C-A-Escape">` block pointing at
  `scripts/toggle-admin-mode.sh`. This file is only written once (like
  `config.env`) — if you moved the install directory, delete it and let
  `xsession.sh` regenerate it on the next kiosk restart.
- If the screen does swap but reboot/restart/Wi-Fi-connect buttons in the
  admin panel fail: the sudoers grant may not have installed. Check for a
  warning in `install.sh`'s output, and confirm
  `/etc/sudoers.d/live-signal-kiosk` exists.

## Device seems stuck, behind, or recently rebooted unexpectedly (auto-update)

If `AUTOUPDATE_ENABLED=true`, check for these marker files under
`/etc/live-signal-kiosk/` — their presence tells you exactly where an
auto-update cycle is:

- **`pending-update-verification` exists, `rollback-attempted` doesn't:**
  the device just rebooted into a freshly-pulled update and
  `kiosk-healthcheck.service` hasn't resolved it yet (normally takes under
  90 seconds after `kiosk.service` starts). If this persists for several
  minutes, `kiosk-healthcheck.service` may not have run — check
  `systemctl status kiosk-healthcheck.service` and
  `journalctl -u kiosk-healthcheck -e`.
- **`rollback-attempted` exists:** an update failed to come up, and the
  device has already rolled back to `last-known-good-commit` and rebooted
  to verify that. This should resolve itself (both files get cleaned up)
  within about 90 seconds of that reboot. If both files are still present
  well after that, the rollback verification itself may not have
  completed — check the same service/journal commands above.
- **Neither file exists, but the device seems to be running old code:**
  auto-update may have found nothing to update (already up to date),
  be outside its maintenance window, or a `git fetch`/`pull` may have
  failed (e.g. no network access at check time). Check
  `journalctl -u kiosk-autoupdate -e` for the most recent cycle's log —
  it always logs a reason when it doesn't update, except for the
  "outside maintenance window" and "disabled" cases, which are silent by
  design (they fire on every timer tick and would otherwise spam the log).
- **The device rebooted once and is now on old code, with neither marker
  present:** this is the terminal "manual intervention required" state -
  automatic rollback is capped at exactly one attempt, and if that
  rollback itself didn't bring the kiosk up, `kiosk-healthcheck.service`
  logs a clear warning (`journalctl -u kiosk-healthcheck -e`) and cleans up
  both markers rather than rebooting again. See "Manual rollback" in
  README.md's Auto-update section.

To see exactly what auto-update decided on its last run:

```bash
journalctl -u kiosk-autoupdate -e
journalctl -u kiosk-healthcheck -e
cat /etc/live-signal-kiosk/last-known-good-commit
```

## Permissions issues with the kiosk user

The `kiosk` system user needs membership in `video`, `render`, `input`, and
`tty` groups for X/DRM access — `install.sh` adds these automatically. If
you see permission errors touching `/dev/dri/*` or input devices in the
logs:

```bash
groups kiosk
sudo usermod -aG video,render,input,tty kiosk
sudo systemctl restart kiosk
```

## Fully uninstalling

```bash
sudo systemctl stop kiosk
sudo systemctl disable kiosk
sudo rm /etc/systemd/system/kiosk.service
sudo systemctl daemon-reload
```

This leaves the cloned repo, `/etc/live-signal-kiosk/config.env`, and
`/var/log/live-signal-kiosk/` in place in case you want to reinstall later —
remove them manually if you want a completely clean system:

```bash
sudo rm -rf /etc/live-signal-kiosk /var/log/live-signal-kiosk
sudo userdel -r kiosk   # optional: removes the kiosk system user and its home dir
```
