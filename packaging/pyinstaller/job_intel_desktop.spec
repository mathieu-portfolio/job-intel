# Minimal PyInstaller spec for the desktop launcher smoke test.
# Build from the repository root with:
#   pyinstaller packaging/pyinstaller/job_intel_desktop.spec

from pathlib import Path

block_cipher = None

SPEC_DIR = Path(globals().get("SPECPATH", Path.cwd() / "packaging" / "pyinstaller")).resolve()
PROJECT_ROOT = SPEC_DIR.parents[1]

ENTRYPOINT = PROJECT_ROOT / "app" / "desktop" / "__main__.py"


def data_dir(source: Path, destination: str) -> tuple[str, str] | None:
    """Return a PyInstaller data tuple only when the source exists."""

    if not source.exists():
        return None
    return (str(source), destination)


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
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
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
    console=True,
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
