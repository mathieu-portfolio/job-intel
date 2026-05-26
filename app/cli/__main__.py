from app.cli.shared import app

# Import command modules for Typer registration.
from app.cli import clear as _clear  # noqa: F401
from app.cli import fetch as _fetch  # noqa: F401
from app.cli import rank as _rank  # noqa: F401
from app.cli import ui as _ui  # noqa: F401

app()
