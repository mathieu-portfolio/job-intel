# PyInstaller spec for the macOS desktop app bundle.
# Build from the repository root with:
#   python -m PyInstaller packaging/pyinstaller/job_intel_desktop_macos.spec --clean --noconfirm

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

SPEC_DIR = Path(globals().get("SPECPATH", Path.cwd() / "packaging" / "pyinstaller")).resolve()
PROJECT_ROOT = SPEC_DIR.parents[1]

ENTRYPOINT = PROJECT_ROOT / "app" / "desktop" / "__main__.py"


def data_dir(source: Path, destination: str) -> tuple[str, str] | None:
    """Return a PyInstaller data tuple only when the source exists."""

    if not source.exists():
        return None
    return (str(source), destination)


hiddenimports = []
for package_name in [
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "uvicorn",
    "anyio",
    "jinja2",
    "typer",
    "rich",
    "requests",
    "openai",
    "dotenv",
]:
    hiddenimports += collect_submodules(package_name)


datas = [
    data_dir(PROJECT_ROOT / "app" / "ui" / "templates", "app/ui/templates"),
    data_dir(PROJECT_ROOT / "app" / "ui" / "static", "app/ui/static"),
    data_dir(PROJECT_ROOT / "app" / "sql", "app/sql"),
    data_dir(PROJECT_ROOT / "profiles", "profiles"),
    data_dir(PROJECT_ROOT / "config" / "scoring_presets", "config/scoring_presets"),
]
datas = [item for item in datas if item is not None]


a = Analysis(
    [str(ENTRYPOINT)],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JobIntel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="JobIntel",
)
app = BUNDLE(
    coll,
    name="JobIntel.app",
    icon=None,
    bundle_identifier="com.mathieuportfolio.jobintel",
    info_plist={
        "CFBundleName": "Job Intel",
        "CFBundleDisplayName": "Job Intel",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": "True",
    },
)
