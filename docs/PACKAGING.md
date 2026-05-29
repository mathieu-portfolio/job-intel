# Packaging

Job Intel can be packaged as a local desktop bundle. The packaged app starts the local FastAPI server, opens the browser, and stores runtime data in the desktop data directory.

## Windows build

From a PowerShell terminal in the repository, with the virtual environment activated:

```powershell
pip install -e .
.\scripts\build_windows.ps1
```

The script:

1. Installs or updates PyInstaller.
2. Runs the PyInstaller spec in clean mode.
3. Builds the desktop bundle in `dist/JobIntel`.
4. Creates a distributable zip at `dist/releases/JobIntel-Windows.zip`.

To skip reinstalling PyInstaller:

```powershell
.\scripts\build_windows.ps1 -SkipInstall
```

To skip PyInstaller clean mode:

```powershell
.\scripts\build_windows.ps1 -NoClean
```

## Manual PyInstaller build

```powershell
python -m PyInstaller packaging/pyinstaller/job_intel_desktop.spec --clean --noconfirm
```

Then run:

```powershell
.\dist\JobIntel\JobIntel.exe
```

## Smoke test checklist

After launching the packaged app:

- The browser opens automatically.
- The UI loads at `http://127.0.0.1:8000`.
- Settings > Storage shows `desktop · frozen`.
- The executable path points inside `dist\JobIntel`.
- The database path is correct.
- Open data/database folder buttons work.
- Fetching, ranking, and reviewing still work.

HTTP `304 Not Modified` entries in the console are normal browser-cache responses, not errors.

## Current release format

The current release artifact is a zip:

```text
dist/releases/JobIntel-Windows.zip
```

Users can unzip it and run:

```text
JobIntel/JobIntel.exe
```

A proper installer can be added later with Inno Setup, NSIS, or WiX.
