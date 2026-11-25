"""Версия агента для паспорта прогона: тег или коммит git, иначе версия пакета."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__

_ROOT = Path(__file__).resolve().parent.parent


def agent_version() -> str:
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return f"{__version__} (git {out.stdout.strip()})"
    except (OSError, subprocess.SubprocessError):
        pass
    return f"{__version__} (вне git)"
