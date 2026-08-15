"""Minimal KEY=VALUE config file parser shared by watcher.py and editor.py."""


def parse_env_file(path):
    """Minimal KEY=VALUE parser. Strips quotes and trailing comments."""
    values = {}
    with open(path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()

            # Strip an inline comment (only outside of quotes).
            if value and value[0] not in ("'", '"'):
                hash_idx = value.find("#")
                if hash_idx != -1:
                    value = value[:hash_idx].strip()

            # Strip surrounding quotes.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]

            values[key] = value
    return values
