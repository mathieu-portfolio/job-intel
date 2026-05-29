# Packaging smoke test

This is the first packaging-readiness layer, not the final installer.

Build a local desktop bundle from the repository root:

```bash
python -m pip install pyinstaller
pyinstaller packaging/pyinstaller/job_intel_desktop.spec
```

Then run:

```text
Windows: dist/JobIntel/JobIntel.exe
macOS/Linux: dist/JobIntel/JobIntel
```

The executable should start the local server, open the browser, use the desktop data folder, and expose diagnostics in Settings and `/runtime`.
