from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_path(path: Path) -> None:
    """Ask the operating system to reveal/open a folder or file location."""

    target = path if path.is_dir() else path.parent
    target.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(target)])
        return

    subprocess.Popen(["xdg-open", str(target)])
