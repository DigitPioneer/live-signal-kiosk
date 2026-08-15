#!/usr/bin/env python3
"""LiveSignal Kiosk slide editor.

A separate, optional network-facing service for editing web/slides.json and
uploading slide/logo images from a browser on the LAN. Runs as its own
systemd unit (kiosk-editor.service), independent of kiosk.service/watcher.py
- a bug or crash here must never be able to take the kiosk display down.
"""

import base64
import hmac
import http.server
import json
import logging
import logging.handlers
import mimetypes
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

from envfile import parse_env_file

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SRC_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(SRC_DIR)
WEB_DIR = os.path.join(APP_DIR, "web")
ASSETS_DIR = os.path.join(WEB_DIR, "assets")
WEB_ADMIN_DIR = os.path.join(APP_DIR, "web-admin")
INDEX_HTML_PATH = os.path.join(WEB_ADMIN_DIR, "index.html")
SLIDES_PATH = os.path.join(WEB_DIR, "slides.json")
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")
SYSTEM_HELPER_PATH = os.path.join(SCRIPTS_DIR, "system-helper.sh")

INSTALLED_CONFIG_PATH = "/etc/live-signal-kiosk/config.env"
FALLBACK_CONFIG_PATH = os.path.join(APP_DIR, "config.example.env")

LOG_DIR = "/var/log/live-signal-kiosk"
LOG_FILE = os.path.join(LOG_DIR, "editor.log")

# Also used by src/watcher.py when it runs a local-only instance of this
# server for the admin-mode breakout (see RUN_DIR/watcher.pid there).
RUN_DIR = "/run/live-signal-kiosk"
EXIT_ADMIN_SENTINEL_PATH = os.path.join(RUN_DIR, "exit-admin-requested")

# Chromium profile dirs created by watcher.py - cleared by the "clear
# Chromium cache" system action. Kept in sync with watcher.py's own
# CHROMIUM_PROFILE_DIR / ADMIN_CHROMIUM_PROFILE_DIR paths.
CHROMIUM_PROFILE_DIRS = [
    os.path.expanduser("~/.cache/live-signal-kiosk/chromium-profile"),
    os.path.expanduser("~/.cache/live-signal-kiosk/chromium-admin-profile"),
]

LOGO_FILENAME = "src-logo.png"

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # a few MB, per spec
MAX_SLIDES_BODY_BYTES = 2 * 1024 * 1024
MAX_SYSTEM_BODY_BYTES = 4 * 1024
# Upper bound on how much of an oversized request body we'll drain before
# responding - keeps a well-behaved client's connection clean without
# fully buffering an absurdly large (many-GB) body.
HARD_DRAIN_LIMIT_BYTES = 20 * 1024 * 1024

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
        "EDITOR_PORT": get_int("EDITOR_PORT", 8766),
        "EDITOR_USERNAME": get("EDITOR_USERNAME", "admin"),
        "EDITOR_PASSWORD": get("EDITOR_PASSWORD", "changeme"),
        "LOG_LEVEL": get("LOG_LEVEL", "INFO"),
    }
    return config


CONFIG = load_config()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging():
    level_name = CONFIG.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("livesignal.editor")
    logger.setLevel(level)
    # "livesignal.editor" is a child of watcher.py's "livesignal" logger in
    # Python's dotted logger hierarchy. When watcher.py imports this module
    # for the local-admin breakout, records would otherwise propagate up
    # and get handled a second time by watcher's own handlers (duplicate
    # lines in stdout/watcher.log). This logger's own handlers below are
    # everything it needs.
    logger.propagate = False

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
# Errors
# ---------------------------------------------------------------------------


class EditorError(Exception):
    """Raised for any request-level failure that should become an HTTP error
    response instead of a crashed handler thread."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------------------
# Slide document validation
# ---------------------------------------------------------------------------

_SAFE_IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_slides_doc(data):
    """Validates the slides.json schema already used by waiting.html.
    Raises EditorError(400, ...) with a human-readable reason on failure.
    Returns the (possibly normalized) document on success.
    """
    if not isinstance(data, dict):
        raise EditorError(400, "top-level JSON must be an object")

    default_duration = data.get("default_duration_seconds", 10)
    if not isinstance(default_duration, (int, float)) or isinstance(default_duration, bool):
        raise EditorError(400, "default_duration_seconds must be a number")
    if default_duration <= 0:
        raise EditorError(400, "default_duration_seconds must be positive")

    slides = data.get("slides")
    if not isinstance(slides, list):
        raise EditorError(400, "slides must be an array")

    normalized_slides = []
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            raise EditorError(400, "slide {} must be an object".format(i))

        title = slide.get("title")
        if not isinstance(title, str) or not title.strip():
            raise EditorError(400, "slide {} is missing a required title".format(i))

        normalized = {"title": title}

        for key in ("subtitle", "message", "image"):
            value = slide.get(key)
            if value is None or value == "":
                continue
            if not isinstance(value, str):
                raise EditorError(400, "slide {} field '{}' must be a string".format(i, key))
            normalized[key] = value

        if "image" in normalized and not _SAFE_IMAGE_NAME_RE.match(normalized["image"]):
            raise EditorError(
                400, "slide {} has an invalid image filename".format(i)
            )

        full_image = slide.get("full_image")
        if full_image is not None:
            if not isinstance(full_image, bool):
                raise EditorError(400, "slide {} full_image must be true or false".format(i))
            if full_image:
                if not normalized.get("image"):
                    raise EditorError(
                        400,
                        "slide {} is a full-screen image slide but has no image".format(i),
                    )
                normalized["full_image"] = True

        duration = slide.get("duration")
        if duration is not None:
            if not isinstance(duration, (int, float)) or isinstance(duration, bool):
                raise EditorError(400, "slide {} duration must be a number".format(i))
            if duration <= 0:
                raise EditorError(400, "slide {} duration must be positive".format(i))
            normalized["duration"] = duration

        normalized_slides.append(normalized)

    return {
        "default_duration_seconds": default_duration,
        "slides": normalized_slides,
    }


def atomic_write_json(path, data):
    """Writes JSON to a temp file in the same directory, then renames it
    over the target. os.replace() is atomic on POSIX, so a reader (the
    kiosk watcher's HTTP server) never sees a partially-written file."""
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".slides-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_bytes(path, data):
    directory = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(prefix=".upload-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Image upload handling
# ---------------------------------------------------------------------------


def sniff_image_type(data):
    """Checks file-signature magic bytes - never trusts a filename
    extension or a client-supplied Content-Type."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def validate_upload_filename(raw_name):
    """Rejects (rather than silently rewrites) anything that looks like a
    path-traversal or absolute-path attempt, then reduces what's left to a
    safe charset."""
    if not raw_name or "\x00" in raw_name:
        raise EditorError(400, "invalid filename")

    normalized = raw_name.replace("\\", "/")
    if "/" in normalized or normalized in ("..", "."):
        raise EditorError(400, "invalid filename")
    if os.path.isabs(raw_name):
        raise EditorError(400, "invalid filename")

    base = os.path.basename(raw_name)
    if base != raw_name or not base:
        raise EditorError(400, "invalid filename")

    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base).lstrip(".")
    if not safe:
        raise EditorError(400, "invalid filename")
    return safe


def resolve_asset_path(filename):
    """Resolves filename under ASSETS_DIR and verifies the result cannot
    escape that directory (defense in depth alongside filename validation)."""
    candidate = os.path.join(ASSETS_DIR, filename)
    real_assets_dir = os.path.realpath(ASSETS_DIR)
    real_candidate_dir = os.path.realpath(os.path.dirname(candidate))
    if real_candidate_dir != real_assets_dir:
        raise EditorError(400, "invalid filename")
    return candidate


def parse_multipart(body, boundary):
    """Minimal multipart/form-data parser - stdlib only, no `cgi` module
    (deprecated/removed in newer Python). Returns a list of parts, each a
    dict with 'name', 'filename' (may be None), and 'data' (bytes)."""
    delimiter = b"--" + boundary
    raw_parts = body.split(delimiter)
    parts = []

    for raw_part in raw_parts[1:-1]:
        if raw_part.startswith(b"\r\n"):
            raw_part = raw_part[2:]
        if raw_part.endswith(b"\r\n"):
            raw_part = raw_part[:-2]

        header_blob, sep, data = raw_part.partition(b"\r\n\r\n")
        if not sep:
            continue

        name = None
        filename = None
        for header_line in header_blob.split(b"\r\n"):
            try:
                header_text = header_line.decode("utf-8", errors="replace")
            except Exception:
                continue
            if not header_text.lower().startswith("content-disposition"):
                continue
            for match in re.finditer(r'(\w+)="([^"]*)"', header_text):
                key, value = match.group(1), match.group(2)
                if key == "name":
                    name = value
                elif key == "filename":
                    filename = value

        parts.append({"name": name, "filename": filename, "data": data})

    return parts


def get_multipart_boundary(content_type_header):
    if not content_type_header:
        return None
    fields = content_type_header.split(";")
    if fields[0].strip().lower() != "multipart/form-data":
        return None
    for field in fields[1:]:
        field = field.strip()
        if field.lower().startswith("boundary="):
            boundary = field[len("boundary="):]
            if boundary.startswith('"') and boundary.endswith('"'):
                boundary = boundary[1:-1]
            return boundary.encode("utf-8")
    return None


def save_uploaded_image(file_bytes, requested_filename, fixed_filename=None):
    """Validates image bytes and saves them into ASSETS_DIR. Returns the
    filename actually used. Raises EditorError on any validation failure."""
    if len(file_bytes) == 0:
        raise EditorError(400, "uploaded file is empty")

    image_type = sniff_image_type(file_bytes)
    if image_type is None:
        raise EditorError(415, "file is not a recognized image (png/jpeg/gif/webp)")

    if fixed_filename is not None:
        filename = fixed_filename
    else:
        safe_name = validate_upload_filename(requested_filename)
        # Prefix with a short random token so distinct uploads never collide
        # even if two slides pick the same original filename.
        filename = "{}-{}".format(secrets.token_hex(4), safe_name)

    target_path = resolve_asset_path(filename)
    os.makedirs(ASSETS_DIR, exist_ok=True)
    atomic_write_bytes(target_path, file_bytes)
    return filename


# ---------------------------------------------------------------------------
# System actions (reboot, restart, Wi-Fi, cache cleanup)
# ---------------------------------------------------------------------------
#
# reboot / restart-kiosk / wifi-connect need root, which this process (run
# as the unprivileged kiosk user, same as watcher.py) doesn't have. Those
# go through scripts/system-helper.sh via a narrow passwordless-sudo grant
# installed by install.sh (see scripts/system-helper.sudoers) - that script
# is the only thing sudo trusts, and it validates its own arguments.
# Cache/asset cleanup needs no privilege since the kiosk user already owns
# those directories.


def run_system_helper(args, timeout=20):
    """Runs scripts/system-helper.sh via sudo. Returns the completed
    subprocess.CompletedProcess. `-n` (non-interactive) means this fails
    fast with a clear error instead of hanging if the sudoers grant is
    missing or broken, rather than waiting on a password prompt nothing
    can ever answer."""
    cmd = ["sudo", "-n", SYSTEM_HELPER_PATH] + list(args)
    try:
        return subprocess.run(cmd, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        raise EditorError(504, "system command timed out")
    except OSError as exc:
        raise EditorError(500, "failed to run system helper: {}".format(exc))


def parse_nmcli_terse_line(line):
    """Splits one line of `nmcli -t` output on unescaped colons. nmcli
    escapes literal ':' within a field as '\\:' in terse mode; this handles
    that but not every edge case (e.g. an SSID containing a literal
    backslash), which is an acceptable simplification for realistic Wi-Fi
    SSIDs."""
    fields = []
    current = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            current.append(line[i + 1])
            i += 2
            continue
        if ch == ":":
            fields.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    fields.append("".join(current))
    return fields


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class EditorRequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "LiveSignalEditor/1.0"

    def log_message(self, format_str, *args):
        log.info("%s - %s", self.address_string(), format_str % args)

    # -- auth -----------------------------------------------------------

    def _check_auth(self):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(header[len("Basic "):]).decode("utf-8")
        except Exception:
            return False
        username, _, password = decoded.partition(":")
        expected_user = CONFIG["EDITOR_USERNAME"]
        expected_pass = CONFIG["EDITOR_PASSWORD"]
        return hmac.compare_digest(username, expected_user) and hmac.compare_digest(
            password, expected_pass
        )

    def _require_auth(self):
        if self._check_auth():
            return True
        log.warning("Rejected request from %s: missing/invalid credentials", self.address_string())
        body = b"401 Unauthorized\n"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="LiveSignal Kiosk Editor"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    # -- response helpers -------------------------------------------------

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, message):
        self._send_json({"error": message}, status=status)

    def _send_bytes(self, data, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _drain(self, n):
        """Reads and discards up to n bytes. Used before responding to a
        rejected request so the client can finish sending and still
        cleanly receive our response, instead of the connection resetting
        mid-upload."""
        remaining = n
        chunk_size = 65536
        while remaining > 0:
            chunk = self.rfile.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_body(self, max_bytes):
        length = self.headers.get("Content-Length")
        try:
            length = int(length)
        except (TypeError, ValueError):
            raise EditorError(400, "missing or invalid Content-Length")
        if length <= 0:
            raise EditorError(400, "empty request body")
        if length > max_bytes:
            self._drain(min(length, HARD_DRAIN_LIMIT_BYTES))
            raise EditorError(413, "request body too large")
        return self.rfile.read(length)

    # -- routing ------------------------------------------------------------

    def do_GET(self):
        if not self._require_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._serve_index()
            elif path == "/api/slides":
                self._serve_slides()
            elif path == "/api/mode":
                self._serve_mode()
            elif path == "/api/system/wifi/status":
                self._handle_wifi_status()
            elif path == "/api/system/wifi/scan":
                self._handle_wifi_scan()
            elif path.startswith("/assets/"):
                self._serve_asset(path[len("/assets/"):])
            else:
                self._send_error_json(404, "not found")
        except EditorError as exc:
            self._send_error_json(exc.status, exc.message)
        except Exception:
            log.exception("Unhandled error handling GET %s", self.path)
            self._send_error_json(500, "internal server error")

    def do_POST(self):
        if not self._require_auth():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        try:
            if path == "/api/slides":
                self._handle_save_slides()
            elif path == "/api/upload":
                self._handle_upload(fixed_filename=None)
            elif path == "/api/logo":
                self._handle_upload(fixed_filename=LOGO_FILENAME)
            elif path == "/api/system/reboot":
                self._handle_reboot()
            elif path == "/api/system/restart-kiosk":
                self._handle_restart_kiosk()
            elif path == "/api/system/wifi/connect":
                self._handle_wifi_connect()
            elif path == "/api/system/clear-chromium-cache":
                self._handle_clear_chromium_cache()
            elif path == "/api/system/clear-unused-images":
                self._handle_clear_unused_images()
            elif path == "/api/local-admin/exit":
                self._handle_local_admin_exit()
            else:
                self._send_error_json(404, "not found")
        except EditorError as exc:
            self._send_error_json(exc.status, exc.message)
        except Exception:
            log.exception("Unhandled error handling POST %s", self.path)
            self._send_error_json(500, "internal server error")

    # -- handlers ------------------------------------------------------------

    def _serve_index(self):
        try:
            with open(INDEX_HTML_PATH, "rb") as f:
                data = f.read()
        except OSError:
            raise EditorError(500, "editor UI is missing")
        self._send_bytes(data, "text/html; charset=utf-8")

    def _serve_slides(self):
        try:
            with open(SLIDES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("Could not read %s: %s", SLIDES_PATH, exc)
            raise EditorError(500, "could not read slides.json")
        self._send_json(data)

    def _serve_asset(self, rel_path):
        filename = validate_upload_filename(urllib.parse.unquote(rel_path))
        full_path = resolve_asset_path(filename)
        if not os.path.isfile(full_path):
            raise EditorError(404, "not found")
        content_type, _ = mimetypes.guess_type(full_path)
        with open(full_path, "rb") as f:
            data = f.read()
        self._send_bytes(data, content_type or "application/octet-stream")

    def _handle_save_slides(self):
        raw_body = self._read_body(MAX_SLIDES_BODY_BYTES)
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EditorError(400, "invalid JSON: {}".format(exc))

        normalized = validate_slides_doc(data)

        try:
            atomic_write_json(SLIDES_PATH, normalized)
        except OSError as exc:
            log.error("Failed to write %s: %s", SLIDES_PATH, exc)
            raise EditorError(500, "failed to save slides.json")

        log.info(
            "slides.json updated by %s (%d slides)",
            self.address_string(),
            len(normalized["slides"]),
        )
        self._send_json({"ok": True, "slides": normalized})

    def _handle_upload(self, fixed_filename):
        content_type_header = self.headers.get("Content-Type", "")
        boundary = get_multipart_boundary(content_type_header)
        if boundary is None:
            raise EditorError(400, "expected multipart/form-data")

        raw_body = self._read_body(MAX_UPLOAD_BYTES)
        parts = parse_multipart(raw_body, boundary)

        file_part = None
        for part in parts:
            if part.get("filename"):
                file_part = part
                break

        if file_part is None:
            raise EditorError(400, "no file uploaded")

        filename = save_uploaded_image(
            file_part["data"], file_part.get("filename"), fixed_filename=fixed_filename
        )

        log.info(
            "%s uploaded by %s (%d bytes) -> %s",
            "logo" if fixed_filename else "image",
            self.address_string(),
            len(file_part["data"]),
            filename,
        )
        self._send_json({"ok": True, "filename": filename})

    # -- mode / local-admin breakout ------------------------------------

    def _serve_mode(self):
        self._send_json({"local_admin": bool(getattr(self.server, "is_local_admin", False))})

    def _handle_local_admin_exit(self):
        if not getattr(self.server, "is_local_admin", False):
            # This route only means something on the local-only instance
            # the watcher starts for admin mode - reject it on the normal
            # LAN-facing kiosk-editor.service instance rather than quietly
            # no-op'ing, so a misconfigured client fails loudly.
            raise EditorError(404, "not found")

        try:
            os.makedirs(RUN_DIR, exist_ok=True)
            with open(EXIT_ADMIN_SENTINEL_PATH, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except OSError as exc:
            log.error("Failed to write exit-admin sentinel: %s", exc)
            raise EditorError(500, "failed to request exit")

        log.info("Local admin exit requested by %s", self.address_string())
        self._send_json({"ok": True})

    # -- system actions ---------------------------------------------------

    def _handle_reboot(self):
        log.warning("Reboot requested by %s", self.address_string())
        result = run_system_helper(["reboot"])
        if result.returncode != 0:
            raise EditorError(
                500, "reboot failed: {}".format((result.stderr or "").strip()[:300])
            )
        self._send_json({"ok": True})

    def _handle_restart_kiosk(self):
        log.warning("Kiosk service restart requested by %s", self.address_string())
        result = run_system_helper(["restart-kiosk"])
        if result.returncode != 0:
            raise EditorError(
                500, "restart failed: {}".format((result.stderr or "").strip()[:300])
            )
        self._send_json({"ok": True})

    def _handle_wifi_status(self):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "dev", "wifi"],
                timeout=15,
                capture_output=True,
                text=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise EditorError(500, "could not query Wi-Fi status: {}".format(exc))

        connected_ssid = None
        for line in (result.stdout or "").splitlines():
            fields = parse_nmcli_terse_line(line)
            if len(fields) >= 2 and fields[0] == "yes":
                connected_ssid = fields[1]
                break

        self._send_json({"connected_ssid": connected_ssid})

    def _handle_wifi_scan(self):
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"],
                timeout=20,
                capture_output=True,
                text=True,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise EditorError(500, "could not scan Wi-Fi networks: {}".format(exc))

        networks = []
        seen = set()
        for line in (result.stdout or "").splitlines():
            fields = parse_nmcli_terse_line(line)
            if len(fields) < 3:
                continue
            ssid, signal_str, security = fields[0], fields[1], fields[2]
            if not ssid or ssid in seen:
                continue
            seen.add(ssid)
            try:
                signal_val = int(signal_str)
            except ValueError:
                signal_val = None
            networks.append({"ssid": ssid, "signal": signal_val, "security": security or None})

        networks.sort(key=lambda n: n["signal"] if n["signal"] is not None else -1, reverse=True)
        self._send_json({"networks": networks})

    def _handle_wifi_connect(self):
        raw_body = self._read_body(MAX_SYSTEM_BODY_BYTES)
        try:
            data = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EditorError(400, "invalid JSON: {}".format(exc))

        ssid = data.get("ssid")
        password = data.get("password", "")
        if not isinstance(ssid, str) or not ssid.strip():
            raise EditorError(400, "ssid is required")
        if not isinstance(password, str):
            raise EditorError(400, "password must be a string")

        log.info("Wi-Fi connect requested by %s (ssid=%s)", self.address_string(), ssid)
        args = ["wifi-connect", ssid] + ([password] if password else [])
        result = run_system_helper(args, timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:300]
            raise EditorError(502, "failed to connect: {}".format(detail))
        self._send_json({"ok": True})

    def _handle_clear_chromium_cache(self):
        cleared = []
        for profile_dir in CHROMIUM_PROFILE_DIRS:
            if not os.path.isdir(profile_dir):
                continue
            try:
                shutil.rmtree(profile_dir)
            except OSError as exc:
                log.error("Failed to clear %s: %s", profile_dir, exc)
                raise EditorError(500, "failed to clear cache: {}".format(exc))
            cleared.append(profile_dir)

        log.info("Chromium cache cleared by %s: %s", self.address_string(), cleared)
        self._send_json({"ok": True, "cleared": cleared})

    def _handle_clear_unused_images(self):
        try:
            with open(SLIDES_PATH, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            raise EditorError(500, "could not read slides.json: {}".format(exc))

        referenced = {LOGO_FILENAME}
        for slide in doc.get("slides", []):
            if isinstance(slide, dict) and slide.get("image"):
                referenced.add(slide["image"])

        removed = []
        try:
            for name in os.listdir(ASSETS_DIR):
                if name in referenced:
                    continue
                full_path = os.path.join(ASSETS_DIR, name)
                if os.path.isfile(full_path):
                    os.remove(full_path)
                    removed.append(name)
        except OSError as exc:
            raise EditorError(500, "failed to clean up assets: {}".format(exc))

        log.info("Unused images cleared by %s: %s", self.address_string(), removed)
        self._send_json({"ok": True, "removed": removed})


def start_editor_server(port, bind_host="0.0.0.0", local_admin=False):
    server = http.server.ThreadingHTTPServer((bind_host, port), EditorRequestHandler)
    server.is_local_admin = local_admin

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info(
        "Slide editor listening on %s:%s%s",
        bind_host,
        port,
        " (local admin mode)" if local_admin else "",
    )
    return server


def main():
    log.info("LiveSignal Kiosk editor starting (config: %s)", CONFIG["_source_path"])
    log.info("APP_DIR=%s WEB_DIR=%s WEB_ADMIN_DIR=%s", APP_DIR, WEB_DIR, WEB_ADMIN_DIR)

    if CONFIG["EDITOR_PASSWORD"] == "changeme":
        log.warning(
            "EDITOR_PASSWORD is still the default 'changeme' - change it in %s",
            CONFIG["_source_path"],
        )

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        log.info("Received signal %s, shutting down", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # start_editor_server() already runs its own background thread, so
    # this just blocks the main thread until a shutdown signal arrives.
    server = start_editor_server(CONFIG["EDITOR_PORT"])
    try:
        stop_event.wait()
    finally:
        log.info("Slide editor shutting down")
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
