# Raspberry Pi OS Lite setup, step by step

This walks through setting up a LiveSignal Kiosk from a blank SD card,
assuming no monitor or keyboard is attached to the Pi until it's already
running on the TV.

## 1. Flash Raspberry Pi OS Lite 64-bit

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
   on another computer.
2. Insert the SD card, open Raspberry Pi Imager.
3. Choose **Device**: your Pi model.
4. Choose **Operating System**: `Raspberry Pi OS Lite (64-bit)` (under
   "Raspberry Pi OS (other)"). The Lite image has no desktop environment —
   the kiosk provides its own minimal X session, so a full desktop image is
   unnecessary overhead.
5. Choose **Storage**: your SD card.
6. Click the gear icon / **Edit Settings** (Imager's "advanced options") and
   set:
   - Hostname (e.g. `livesignal-kiosk`)
   - Enable SSH, with password auth or your public key
   - Username/password (this becomes your admin login, separate from the
     `kiosk` service user `install.sh` creates)
   - Wi-Fi SSID/password (if not using Ethernet) and your country code
   - Locale/timezone
7. Write the image. This lets you do everything below over SSH — no monitor
   or keyboard needed until the TV is the display.

## 2. First boot

1. Insert the SD card into the Pi, connect Ethernet (or rely on the Wi-Fi you
   configured), connect the Pi to the TV's HDMI port, and power it on.
2. From another machine on the same network:
   ```bash
   ssh <your-username>@<hostname-or-ip>.local
   ```
3. Update the base OS:
   ```bash
   sudo apt-get update && sudo apt-get full-upgrade -y
   sudo reboot
   ```

## 3. Clone the repo and install

```bash
sudo git clone <this-repo-url> /opt/live-signal-kiosk
cd /opt/live-signal-kiosk
sudo ./install.sh
```

`install.sh` installs all required packages (X11, Openbox, Chromium, mpv,
yt-dlp), creates the `kiosk` system user, installs the systemd service, and
— because it detects Pi hardware automatically — runs
`scripts/setup-lite-kiosk.sh` to apply Pi firmware tweaks (HDMI hotplug
forcing so the display works even if the TV was off at boot, GPU memory
split, disabling the boot splash).

## 4. Configure your channel

```bash
sudo nano /etc/live-signal-kiosk/config.env
```

At minimum, set `CHANNEL_LIVE_URL` to your church's YouTube channel `/live`
URL. See the [config reference in the README](../README.md#config-reference)
for every option.

## 5. Replace the logo

```bash
scp your-logo.png <your-username>@<hostname>.local:/tmp/src-logo.png
ssh <your-username>@<hostname>.local sudo mv /tmp/src-logo.png /opt/live-signal-kiosk/web/assets/src-logo.png
```

Keep the filename `src-logo.png` — `waiting.html` references it directly.

## 6. Edit the announcement slides

```bash
sudo nano /opt/live-signal-kiosk/web/slides.json
```

See the [slide schema and example in the README](../README.md#editing-slides).

## 7. Reboot into the kiosk

```bash
sudo reboot
```

The Pi will boot straight into the kiosk display — no login prompt on
`tty1`. Give it a minute after boot; the waiting screen and slides should
appear, and the display will switch automatically when your channel goes
live.

## 8. (Optional) Hide most of the boot text

If you want a cleaner boot (less scrolling text before the kiosk appears):

```bash
sudo /opt/live-signal-kiosk/scripts/silent-boot.sh
```

This is a separate, optional, purely cosmetic step — it's not run by
`install.sh` automatically. It backs up the files it edits before changing
them, so it's easy to revert if needed. See the script's own output for
details.
