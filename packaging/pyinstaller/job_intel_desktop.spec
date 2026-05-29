# Minimal PyInstaller spec for the desktop launcher smoke test.
# Build from the repository root with:
#   pyinstaller packaging/pyinstaller/job_intel_desktop.spec

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

app_datas = collect_data_files("app", includes=[
    "ui/templates/*.html",
    "ui/static/*",
    "sql/**/*.sql",
])


a = Analysis(
    ["app/desktop/__main__.py"],
    pathex=[],
    binaries=[],
    datas=app_datas + [
        ("profiles", "profiles"),
        ("config/scoring_presets", "config/scoring_presets"),
    ],
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
