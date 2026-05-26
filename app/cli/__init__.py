from __future__ import annotations

from app.cli.shared import app, console
from app.cli.clear import clear
from app.cli.fetch import fetch
from app.cli.rank import rank
from app.cli.ui import ui


@app.callback()
def main() -> None:
    """Job Intel command-line tools."""


# Register split command functions explicitly.
app.command(name="clear")(clear)
app.command(name="fetch")(fetch)
app.command(name="rank")(rank)
app.command(name="ui")(ui)


if __name__ == "__main__":
    app()
