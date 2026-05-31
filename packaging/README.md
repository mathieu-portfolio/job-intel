# Packaging smoke test

This is the packaging-readiness layer, not the final installer.

## Recommended Windows build

From PowerShell:

```powershell
.\scripts\build_windows.ps1
```

This creates:

```text
dist/JobIntel/
dist/releases/JobIntel-Windows.zip
```

## Recommended macOS build

From a macOS terminal:

```bash
./scripts/build_macos.sh
```

This creates:

```text
dist/JobIntel.app
dist/releases/JobIntel-macOS-x64.zip
```

## Manual build

Build a local desktop bundle from the repository root:

```bash
python -m pip install -e .
python -m pip install pyinstaller
python -m PyInstaller packaging/pyinstaller/job_intel_desktop.spec --clean --noconfirm
```

Then run:

```text
Windows: dist/JobIntel/JobIntel.exe
macOS/Linux: dist/JobIntel/JobIntel
```

The executable should start the local server, open the browser, use the desktop data folder, and expose diagnostics in Settings and `/runtime`.

See `docs/PACKAGING.md` for the fuller checklist.


## GitHub Actions

Release artifacts are built by `.github/workflows/release.yml`.

- Manual runs upload `JobIntel-Windows.zip` and `JobIntel-macOS-x64.zip` as workflow artifacts.
- Pushing a tag like `v0.1.0` also attaches both ZIP files to the GitHub Release.
