from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class EnvError(RuntimeError):
    pass


@dataclass(frozen=True)
class Env:
    values: dict[str, str]

    def get(self, key: str, default: str | None = None) -> str | None:
        if key in os.environ:
            return os.environ.get(key) or default
        return self.values.get(key, default)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def load_env(path: Path | None = None) -> Env:
    """
    Load a simple .env file from cwd by default.
    Supported:
      - KEY=VALUE
      - export KEY=VALUE
      - quoted values with '...' or "..."
      - comments starting with #
    """
    if path is None:
        path = Path.cwd() / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return Env(values=values)

    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = _strip_quotes(v.strip())
        if k:
            values[k] = v
    return Env(values=values)

