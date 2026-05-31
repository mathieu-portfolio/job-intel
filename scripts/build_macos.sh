#!/usr/bin/env bash
# Build the macOS desktop distribution bundle for Job Intel.
# Run from anywhere inside the repository:
#   ./scripts/build_macos.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SPEC_PATH="$PROJECT_ROOT/packaging/pyinstaller/job_intel_desktop_macos.spec"
DIST_DIR="$PROJECT_ROOT/dist"
RELEASE_DIR="$DIST_DIR/releases"
APP_PATH="$DIST_DIR/JobIntel.app"
RELEASE_ZIP="$RELEASE_DIR/JobIntel-macOS-x64.zip"

SKIP_INSTALL=0
NO_CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --skip-install) SKIP_INSTALL=1 ;;
    --no-clean) NO_CLEAN=1 ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$SPEC_PATH" ]]; then
  echo "PyInstaller spec not found: $SPEC_PATH" >&2
  exit 1
fi

cd "$PROJECT_ROOT"

PYTHON_EXE="python3"
if [[ -x "$PROJECT_ROOT/.venv/bin/python" ]]; then
  PYTHON_EXE="$PROJECT_ROOT/.venv/bin/python"
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
  echo "Installing/updating project dependencies..."
  "$PYTHON_EXE" -m pip install -e .
  echo "Installing/updating PyInstaller..."
  "$PYTHON_EXE" -m pip install pyinstaller
fi

PYINSTALLER_ARGS=("-m" "PyInstaller" "$SPEC_PATH" "--noconfirm")
if [[ "$NO_CLEAN" -eq 0 ]]; then
  PYINSTALLER_ARGS+=("--clean")
fi

echo "Building JobIntel macOS app bundle..."
"$PYTHON_EXE" "${PYINSTALLER_ARGS[@]}"

if [[ ! -d "$APP_PATH" ]]; then
  echo "Expected app bundle was not generated: $APP_PATH" >&2
  exit 1
fi

mkdir -p "$RELEASE_DIR"
rm -f "$RELEASE_ZIP"

echo "Creating release archive..."
/usr/bin/ditto -c -k --keepParent "$APP_PATH" "$RELEASE_ZIP"

echo ""
echo "Build complete."
echo "App:     $APP_PATH"
echo "Release: $RELEASE_ZIP"
