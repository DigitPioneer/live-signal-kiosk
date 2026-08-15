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
- **X can't start on `tty1`** because a getty is still attached to it.
  `kiosk.service` declares `Conflicts=getty@tty1.service`, which should
  handle this automatically — but if you've customized `tty1` elsewhere,
  make sure nothing else is holding it.
- **Watcher crashed at startup** (bad config, missing binary). Check
  `/var/log/live-signal-kiosk/watcher.log` and the `journalctl` output above
  for a Python traceback.

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
