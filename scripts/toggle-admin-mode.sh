#!/bin/sh
# Bound to Ctrl+Alt+Escape in Openbox (see scripts/openbox-rc.xml). Sends
# SIGUSR1 to the watcher process, found via the pidfile it writes on
# startup (see src/watcher.py). The watcher's own SIGUSR1 handler does all
# the actual work of toggling admin mode - this script does nothing else,
# so there's no separate process racing the watcher's child-process
# management (see src/watcher.py's ChildProcessManager).

PIDFILE="/run/live-signal-kiosk/watcher.pid"

if [ ! -f "$PIDFILE" ]; then
  exit 0
fi

kill -USR1 "$(cat "$PIDFILE")" 2>/dev/null
