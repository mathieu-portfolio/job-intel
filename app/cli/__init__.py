from __future__ import annotations

from app.cli.shared import app, console
from app.cli.fetch import fetch as fetch
from app.cli.rank import rank as rank
from app.cli.clear import clear as clear
from app.cli.ui import ui as ui


@app.callback()
def main() -> None:
    """Job Intel command-line tools."""


if __name__ == "__main__":
    app()
