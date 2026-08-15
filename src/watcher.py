#!/usr/bin/env python3
"""LiveSignal Kiosk watcher.

Runs as the X session's client process. Serves the local waiting-screen web
page, polls a YouTube channel for live status, and manages exactly one of
Chromium (waiting screen) or mpv (live stream) as a child process at a time.
"""

import functools
import http.server
import json
import logging
import logging.handlers
import os
import shlex
import signal
import subprocess
import sys
import threading
import time

from envfile import parse_env_file
import editor

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(SRC_DIR)
WEB_DIR = os.path.join(APP_DIR, "web")

INSTALLED_CONFIG_PATH = "/etc/live-signal-kiosk/config.env"
FALLBACK_CONFIG_PATH = os.path.join(APP_DIR, "config.example.env")

LOG_DIR = "/var/log/live-signal-kiosk"
LOG_FILE = os.path.join(LOG_DIR, "watcher.log")

CHROMIUM_PROFILE_DIR = os.path.expanduser(
    "~/.cache/live-signal-kiosk/chromium-profile"
)
# Separate profile for the windowed admin-mode browser, so it never shares
# state (cookies, autofill, crash-restore prompts) with the kiosk waiting
# screen's Chromium instance.
ADMIN_CHROMIUM_PROFILE_DIR = os.path.expanduser(
    "~/.cache/live-signal-kiosk/chromium-admin-profile"
)

# Runtime dir for the watcher's pidfile (read by scripts/toggle-admin-mode.sh
# to send it SIGUSR1) and the exit-admin sentinel file (written by the local
# admin editor's POST /api/local-admin/exit - see src/editor.py). Provided
# by kiosk.service's RuntimeDirectory= on a real install; created here too
# for local/dev runs where that isn't set up.
RUN_DIR = "/run/live-signal-kiosk"
PID_FILE = os.path.join(RUN_DIR, "watcher.pid")
EXIT_ADMIN_SENTINEL_PATH = editor.EXIT_ADMIN_SENTINEL_PATH

STATE_WAITING = "WAITING"
STATE_LIVE = "LIVE"

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def load_config():
    if os.path.isfile(INSTALLED_CONFIG_PATH):
        path = INSTALLED_CONFIG_PATH
    else:
        path = FALLBACK_CONFIG_PATH

    raw = parse_env_file(path)

    def get(key, default=""):
        return raw.get(key, default)

    def get_int(key, default):
        try:
            return int(get(key, str(default)))
        except (TypeError, ValueError):
            return default

    config = {
        "_source_path": path,
        "CHANNEL_LIVE_URL": get("CHANNEL_LIVE_URL"),
        "CHECK_INTERVAL_SECONDS": get_int("CHECK_INTERVAL_SECONDS", 30),
        "OFFLINE_CONFIRM_CHECKS": get_int("OFFLINE_CONFIRM_CHECKS", 2),
        "YOUTUBE_EMBED_PARAMS": get("YOUTUBE_EMBED_PARAMS"),
        "LOCAL_SERVER_PORT": get_int("LOCAL_SERVER_PORT", 8765),
        "CHROMIUM_BIN": get("CHROMIUM_BIN"),
        "MPV_BIN": get("MPV_BIN"),
        "YTDLP_BIN": get("YTDLP_BIN"),
        "KIOSK_USER": get("KIOSK_USER", "kiosk"),
        "LOG_LEVEL": get("LOG_LEVEL", "INFO"),
        "LOCAL_ADMIN_PORT": get_int("LOCAL_ADMIN_PORT", 8767),
    }
    return config


CONFIG = load_config()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging():
    level_name = CONFIG.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("livesignal")
    logger.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning(
            "Could not open log file %s (%s) - logging to stdout only",
            LOG_FILE,
            exc,
        )

    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Local HTTP server
# ---------------------------------------------------------------------------


def start_http_server(port):
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=WEB_DIR
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Local HTTP server serving %s on 127.0.0.1:%s", WEB_DIR, port)
    return server


# ---------------------------------------------------------------------------
# Pidfile (read by scripts/toggle-admin-mode.sh to signal this process)
# ---------------------------------------------------------------------------


def write_pid_file():
    try:
        os.makedirs(RUN_DIR, exist_ok=True)
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except OSError as exc:
        log.warning(
            "Could not write pidfile %s (%s) - the admin-mode hotkey won't "
            "be able to find this process",
            PID_FILE,
            exc,
        )


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def clear_exit_admin_sentinel():
    try:
        os.remove(EXIT_ADMIN_SENTINEL_PATH)
    except OSError:
        pass


def pause_cursor_hiding():
    """unclutter (started in xsession.sh) hides the mouse cursor after a
    short idle period. Admin mode needs the cursor visible/usable, so pause
    unclutter with SIGSTOP rather than killing it - SIGCONT on exit just
    resumes its normal idle-hiding behavior with no relaunch needed. A
    nonzero exit here just means unclutter isn't running (e.g. not
    installed), which is fine and not logged as an error."""
    try:
        subprocess.run(["pkill", "-STOP", "-x", "unclutter"], timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not pause unclutter: %s", exc)


def resume_cursor_hiding():
    try:
        subprocess.run(["pkill", "-CONT", "-x", "unclutter"], timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not resume unclutter: %s", exc)


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def which(name):
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve_chromium_bin():
    configured = CONFIG.get("CHROMIUM_BIN")
    if configured:
        return configured
    for name in ("chromium-browser", "chromium"):
        found = which(name)
        if found:
            return found
    return "chromium-browser"


def resolve_mpv_bin():
    configured = CONFIG.get("MPV_BIN")
    if configured:
        return configured
    return which("mpv") or "mpv"


def resolve_ytdlp_bin():
    configured = CONFIG.get("YTDLP_BIN")
    if configured:
        return configured
    found = which("yt-dlp")
    if found:
        return found
    if os.path.isfile("/usr/local/bin/yt-dlp"):
        return "/usr/local/bin/yt-dlp"
    return "yt-dlp"


CHROMIUM_BIN = resolve_chromium_bin()
MPV_BIN = resolve_mpv_bin()
YTDLP_BIN = resolve_ytdlp_bin()

# ---------------------------------------------------------------------------
# YouTube live check
# ---------------------------------------------------------------------------


def check_live_status(channel_live_url):
    """Returns (is_live, video_id). video_id is None when not live or unknown."""
    cmd = [
        YTDLP_BIN,
        "--no-warnings",
        "--skip-download",
        "-j",
        "--socket-timeout",
        "15",
        channel_live_url,
    ]
    try:
        result = subprocess.run(
            cmd, timeout=25, capture_output=True, text=True
        )
    except subprocess.TimeoutExpired:
        log.warning("yt-dlp timed out while checking live status")
        return False, None
    except OSError as exc:
        log.warning("Failed to run yt-dlp (%s): %s", YTDLP_BIN, exc)
        return False, None

    if result.returncode != 0:
        log.debug(
            "yt-dlp exited %s while checking live status (likely not live): %s",
            result.returncode,
            (result.stderr or "").strip()[:300],
        )
        return False, None

    stdout = (result.stdout or "").strip()
    if not stdout:
        log.warning("yt-dlp returned no output while checking live status")
        return False, None

    # -j can print multiple JSON lines in some edge cases; use the first
    # line that parses.
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        live_status = data.get("live_status")
        video_id = data.get("id")

        if live_status == "is_live":
            return True, video_id

        log.debug("Channel not live (live_status=%s)", live_status)
        return False, None

    log.warning("Could not parse yt-dlp JSON output while checking live status")
    return False, None


def resolve_stream_url(video_id):
    watch_url = "https://www.youtube.com/watch?v={}".format(video_id)
    cmd = [YTDLP_BIN, "--no-warnings", "-g", "--socket-timeout", "15", watch_url]
    try:
        result = subprocess.run(
            cmd, timeout=25, capture_output=True, text=True
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Failed to resolve stream URL for %s: %s", video_id, exc)
        return None

    if result.returncode != 0:
        log.warning(
            "yt-dlp failed to resolve stream URL for %s: %s",
            video_id,
            (result.stderr or "").strip()[:300],
        )
        return None

    stream_url = (result.stdout or "").strip().splitlines()
    if not stream_url:
        log.warning("yt-dlp returned no stream URL for %s", video_id)
        return None

    return stream_url[0].strip()


# ---------------------------------------------------------------------------
# Child process management
# ---------------------------------------------------------------------------


class ChildProcessManager:
    """Owns exactly one running child process (Chromium or mpv) at a time."""

    def __init__(self):
        self.process = None
        self.kind = None  # "chromium" or "mpv"
        self.launch_args = None
        self._last_launch_attempt = 0.0
        self._backoff_seconds = 2

    def _terminate_current(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            log.info("Stopping current %s child (pid %s)", self.kind, self.process.pid)
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("%s did not exit cleanly, killing", self.kind)
                self.process.kill()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None
        self.kind = None
        self.launch_args = None

    def switch_to(self, kind, args):
        """Kill whatever is running and launch the given process."""
        if self.kind == kind and self.launch_args == args and self.is_running():
            return
        self._terminate_current()
        self._launch(kind, args)

    def _launch(self, kind, args):
        log.info("Launching %s: %s", kind, " ".join(shlex.quote(a) for a in args))
        try:
            self.process = subprocess.Popen(args)
        except OSError as exc:
            log.error("Failed to launch %s (%s): %s", kind, args[0], exc)
            self.process = None
            self.kind = None
            self.launch_args = None
            return
        self.kind = kind
        self.launch_args = args
        self._last_launch_attempt = time.time()

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def check_and_relaunch(self):
        """If the expected child died unexpectedly, relaunch it with backoff."""
        if self.process is None or self.launch_args is None:
            return
        if self.process.poll() is None:
            return  # still running

        exit_code = self.process.returncode
        log.warning(
            "%s exited unexpectedly (code %s), relaunching",
            self.kind,
            exit_code,
        )
        elapsed = time.time() - self._last_launch_attempt
        if elapsed < self._backoff_seconds:
            time.sleep(self._backoff_seconds - elapsed)
        self._launch(self.kind, self.launch_args)

    def stop(self):
        self._terminate_current()


def build_chromium_args(port):
    os.makedirs(CHROMIUM_PROFILE_DIR, exist_ok=True)
    url = "http://127.0.0.1:{}/waiting.html".format(port)
    return [
        CHROMIUM_BIN,
        "--kiosk",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--no-first-run",
        "--no-default-browser-check",
        "--check-for-update-interval=31536000",
        "--autoplay-policy=no-user-gesture-required",
        "--overscroll-history-navigation=0",
        "--disable-pinch",
        "--user-data-dir={}".format(CHROMIUM_PROFILE_DIR),
        url,
    ]


def build_mpv_args(stream_url):
    return [
        MPV_BIN,
        "--fullscreen",
        "--no-osc",
        "--no-osd-bar",
        "--osd-level=0",
        "--no-border",
        "--cursor-autohide=always",
        "--really-quiet",
        stream_url,
    ]


def build_admin_chromium_args(port):
    """Windowed (non-kiosk) Chromium for admin mode - normal window
    decorations, resizable/closable, not fullscreen, mouse-usable."""
    os.makedirs(ADMIN_CHROMIUM_PROFILE_DIR, exist_ok=True)
    url = "http://127.0.0.1:{}/".format(port)
    return [
        CHROMIUM_BIN,
        "--new-window",
        "--window-size=1200,800",
        "--noerrdialogs",
        "--disable-infobars",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--no-first-run",
        "--no-default-browser-check",
        "--check-for-update-interval=31536000",
        "--user-data-dir={}".format(ADMIN_CHROMIUM_PROFILE_DIR),
        url,
    ]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


class Watcher:
    def __init__(self, config):
        self.config = config
        self.state = STATE_WAITING
        self.current_video_id = None
        self.consecutive_not_live = 0
        self.children = ChildProcessManager()
        self.http_server = None
        self._stop = threading.Event()
        self._last_check = 0.0

        # Admin mode: entered/exited via SIGUSR1 (see scripts/toggle-admin-
        # mode.sh, bound to Ctrl+Alt+Escape in Openbox). While active, the
        # main loop skips the normal live-check/relaunch state machine -
        # see _run_loop().
        self.admin_mode = False
        self.admin_editor_server = None
        self._admin_toggle_requested = threading.Event()

    def start(self):
        clear_exit_admin_sentinel()
        write_pid_file()

        self.http_server = start_http_server(self.config["LOCAL_SERVER_PORT"])
        self.children.switch_to(
            "chromium", build_chromium_args(self.config["LOCAL_SERVER_PORT"])
        )

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGUSR1, self._handle_sigusr1)

        self._run_loop()

    def _handle_signal(self, signum, _frame):
        log.info("Received signal %s, shutting down", signum)
        self._stop.set()

    def _handle_sigusr1(self, _signum, _frame):
        # Keep the handler itself trivial (just flag a request) - the main
        # loop does the actual work of entering/exiting admin mode, same as
        # everything else it does.
        self._admin_toggle_requested.set()

    def _run_loop(self):
        interval = self.config["CHECK_INTERVAL_SECONDS"]

        while not self._stop.is_set():
            try:
                if self._admin_toggle_requested.is_set():
                    self._admin_toggle_requested.clear()
                    self._toggle_admin_mode()

                self.children.check_and_relaunch()

                if self.admin_mode:
                    if os.path.exists(EXIT_ADMIN_SENTINEL_PATH):
                        log.info("Exit-admin sentinel detected")
                        self._exit_admin_mode()
                else:
                    now = time.time()
                    if now - self._last_check >= interval:
                        self._last_check = now
                        self._poll_and_transition()
            except Exception:
                log.exception("Unhandled error in main loop, continuing")

            self._stop.wait(1)

        self._shutdown()

    def _toggle_admin_mode(self):
        if self.admin_mode:
            self._exit_admin_mode()
        else:
            self._enter_admin_mode()

    def _enter_admin_mode(self):
        log.info("Entering admin mode")

        pause_cursor_hiding()

        port = self.config["LOCAL_ADMIN_PORT"]
        try:
            self.admin_editor_server = editor.start_editor_server(
                port, bind_host="127.0.0.1", local_admin=True
            )
        except OSError as exc:
            log.error(
                "Failed to start local admin editor server on 127.0.0.1:%s: %s",
                port,
                exc,
            )
            resume_cursor_hiding()
            return

        # switch_to() atomically kills whatever's currently on the display
        # and updates ChildProcessManager's own idea of what should be
        # running, in one call - there's no window where check_and_relaunch()
        # could see the old (kiosk) process gone and relaunch it before the
        # new one is recorded. That's what keeps this safe from the exact
        # race this design exists to avoid.
        self.children.switch_to("admin-chromium", build_admin_chromium_args(port))

        self.admin_mode = True
        clear_exit_admin_sentinel()

    def _exit_admin_mode(self):
        log.info("Exiting admin mode")

        if self.admin_editor_server is not None:
            try:
                self.admin_editor_server.shutdown()
                self.admin_editor_server.server_close()
            except Exception:
                log.exception("Error stopping local admin editor server")
            self.admin_editor_server = None

        resume_cursor_hiding()

        # Don't trust anything cached from before admin mode: show the
        # waiting screen immediately (same as a normal Watcher.start()
        # bootstrap), then force a fresh live check on the very next loop
        # iteration rather than waiting up to CHECK_INTERVAL_SECONDS.
        self.state = STATE_WAITING
        self.current_video_id = None
        self.consecutive_not_live = 0
        self.children.switch_to(
            "chromium", build_chromium_args(self.config["LOCAL_SERVER_PORT"])
        )
        self._last_check = 0.0

        self.admin_mode = False
        clear_exit_admin_sentinel()

    def _poll_and_transition(self):
        channel_url = self.config["CHANNEL_LIVE_URL"]
        if not channel_url:
            log.warning("CHANNEL_LIVE_URL is not configured, skipping live check")
            return

        is_live, video_id = check_live_status(channel_url)

        if is_live:
            self.consecutive_not_live = 0
            if self.state != STATE_LIVE or video_id != self.current_video_id:
                self._go_live(video_id)
        else:
            if self.state == STATE_LIVE:
                self.consecutive_not_live += 1
                log.info(
                    "Channel reported not-live (%s/%s consecutive checks)",
                    self.consecutive_not_live,
                    self.config["OFFLINE_CONFIRM_CHECKS"],
                )
                if self.consecutive_not_live >= self.config["OFFLINE_CONFIRM_CHECKS"]:
                    self._go_waiting()

    def _go_live(self, video_id):
        if not video_id:
            log.warning("Channel is live but no video id was returned, skipping")
            return

        stream_url = resolve_stream_url(video_id)
        if not stream_url:
            log.warning("Could not resolve stream URL for %s, staying WAITING", video_id)
            return

        log.info("Channel went LIVE (video_id=%s)", video_id)
        self.state = STATE_LIVE
        self.current_video_id = video_id
        self.children.switch_to("mpv", build_mpv_args(stream_url))

    def _go_waiting(self):
        log.info("Channel is no longer live, returning to WAITING")
        self.state = STATE_WAITING
        self.current_video_id = None
        self.consecutive_not_live = 0
        self.children.switch_to(
            "chromium", build_chromium_args(self.config["LOCAL_SERVER_PORT"])
        )

    def _shutdown(self):
        log.info("Stopping child process")
        self.children.stop()
        if self.http_server is not None:
            log.info("Stopping local HTTP server")
            self.http_server.shutdown()
        if self.admin_editor_server is not None:
            log.info("Stopping local admin editor server")
            try:
                self.admin_editor_server.shutdown()
                self.admin_editor_server.server_close()
            except Exception:
                log.exception("Error stopping local admin editor server")
            self.admin_editor_server = None
        if self.admin_mode:
            resume_cursor_hiding()
        remove_pid_file()
        clear_exit_admin_sentinel()
        log.info("Watcher exited cleanly")


def main():
    log.info("LiveSignal Kiosk watcher starting (config: %s)", CONFIG["_source_path"])
    log.info("APP_DIR=%s WEB_DIR=%s", APP_DIR, WEB_DIR)
    log.info(
        "Resolved binaries: chromium=%s mpv=%s yt-dlp=%s",
        CHROMIUM_BIN,
        MPV_BIN,
        YTDLP_BIN,
    )
    watcher = Watcher(CONFIG)
    watcher.start()


if __name__ == "__main__":
    main()
