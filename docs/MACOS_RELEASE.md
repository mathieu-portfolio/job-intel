# macOS release build

The macOS release build creates a `.app` bundle and wraps it in a ZIP file for GitHub Releases. The first macOS artifact is an x64 build made on `macos-13`.

## Local build on macOS

From the repository root:

```bash
./scripts/build_macos.sh
```

This creates:

```text
dist/JobIntel.app
dist/releases/JobIntel-macOS-x64.zip
```

## Manual test

Open the generated app:

```bash
open dist/JobIntel.app
```

Expected behavior:

- The browser opens automatically.
- Job Intel loads at `http://127.0.0.1:8000`.
- Settings > Storage shows runtime mode `desktop · frozen`.
- The app uses `~/Library/Application Support/JobIntel` for small app data.
- The database path can still be moved from Settings > Storage.

## First-launch warning

The app is not signed or notarized yet. On a real Mac, Gatekeeper may block it on first launch.

For the first internal release, users may need to:

1. Right-click `JobIntel.app`.
2. Click **Open**.
3. Confirm the warning.

A later pass should add Apple Developer signing and notarization if this app is distributed beyond trusted testers.

## GitHub Actions

The release workflow builds `JobIntel-macOS-x64.zip` on `macos-13`.

Manual workflow runs upload the ZIP as a workflow artifact. Tag pushes like `v0.1.0` attach it to the GitHub Release.
