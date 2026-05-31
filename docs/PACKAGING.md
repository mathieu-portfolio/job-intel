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

## macOS build

From a macOS terminal in the repository:

```bash
./scripts/build_macos.sh
```

The script:

1. Installs or updates PyInstaller.
2. Runs the macOS PyInstaller spec in clean mode.
3. Builds `dist/JobIntel.app`.
4. Creates `dist/releases/JobIntel-macOS-x64.zip`.

Manual test:

```bash
open dist/JobIntel.app
```

## Manual PyInstaller build

Windows:

```powershell
python -m PyInstaller packaging/pyinstaller/job_intel_desktop.spec --clean --noconfirm
```

macOS:

```bash
python -m PyInstaller packaging/pyinstaller/job_intel_desktop_macos.spec --clean --noconfirm
```

Then run:

```powershell
.\dist\JobIntel\JobIntel.exe
```

Or on macOS:

```bash
open dist/JobIntel.app
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


## GitHub release workflow

The repository includes a GitHub Actions workflow that builds the Windows and macOS ZIP files automatically.

Manual build from GitHub:

1. Open the repository on GitHub.
2. Go to **Actions**.
3. Select **Build release artifacts**.
4. Click **Run workflow**.
5. Download the `JobIntel-Windows` or `JobIntel-macOS-x64` artifact.

Tagged release build:

```powershell
git tag v0.1.0
git push origin v0.1.0
```

When a `v*` tag is pushed, GitHub Actions builds `JobIntel-Windows.zip` and `JobIntel-macOS-x64.zip`, then attaches both to the GitHub Release for that tag.

## Current release format

The current release artifacts are ZIP files:

```text
dist/releases/JobIntel-Windows.zip
dist/releases/JobIntel-macOS-x64.zip
```

Windows users can unzip it and run:

```text
JobIntel/JobIntel.exe
```

macOS users can unzip it and open:

```text
JobIntel.app
```

The macOS app is not signed or notarized yet, so first-launch Gatekeeper warnings are expected. Proper installers can be added later.
