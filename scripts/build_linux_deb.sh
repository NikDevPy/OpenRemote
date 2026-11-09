#!/usr/bin/env bash
# Build the Linux amd64 preview .deb from the current verified source tree.
# This is a user-venv bootstrap package (first launch needs PyPI access).
# No personal config, tunnels, keys or machine-specific Python venvs included.
set -euo pipefail

repo="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
cd "$repo"
version="$(python3 - <<'PY'
import tomllib
with open("pyproject.toml","rb") as fp:
    print(tomllib.load(fp)["project"]["version"])
PY
)"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Invalid package version" >&2
  exit 2
fi
outdir="${1:-$repo/dist}"
mkdir -p "$outdir"
outdir="$(realpath "$outdir")"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
python_build="${OPENREMOTE_BUILD_PYTHON:-python3}"
"$python_build" -m build --wheel --outdir "$tmp/wheelhouse" >/dev/null
wheel="$tmp/wheelhouse/openremote_probe-$version-py3-none-any.whl"
if [[ ! -f "$wheel" ]]; then
  echo "Missing matching pure Python wheel" >&2
  exit 1
fi

stage="$tmp/package"
mkdir -p "$stage/DEBIAN" "$stage/usr/share/openremote" \
  "$stage/usr/share/applications" \
  "$stage/usr/share/doc/openremote-control-center" \
  "$stage/usr/share/icons/hicolor/scalable/apps" "$stage/usr/bin"
cp LICENSE "$stage/usr/share/doc/openremote-control-center/copyright"
cp "$wheel" "$stage/usr/share/openremote/"
cp openremote_gui/assets/openremote.svg \
  "$stage/usr/share/icons/hicolor/scalable/apps/openremote-control-center.svg"
cp scripts/openremote-bootstrap.sh "$stage/usr/bin/openremote-control-center"
chmod 0755 "$stage/usr/bin/openremote-control-center"
cat > "$stage/usr/share/applications/openremote-control-center.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Version=1.0
Name=OpenRemote
Name[ru]=OpenRemote — Центр управления
Comment=Private device control center
Comment[ru]=Подключите свои устройства к ChatGPT
Exec=/usr/bin/openremote-control-center
Icon=openremote-control-center
Terminal=false
Categories=Network;Utility;
StartupNotify=true
StartupWMClass=OpenRemote
X-OpenRemote-ControlCenter=true
DESKTOP
cat > "$stage/DEBIAN/control" <<EOF
Package: openremote-control-center
Version: $version~preview1
Section: net
Priority: optional
Architecture: amd64
Maintainer: NikDevPy <opensource@users.noreply.github.com>
Depends: python3 (>= 3.11), python3-venv, python3-pip, util-linux, ca-certificates, libegl1, libopengl0
Recommends: zenity
Description: OpenRemote private ChatGPT connection setup (Linux preview)
 An optional desktop/tray UI and first-run wizard that helps owners create
 a private Secure MCP Tunnel and link it to ChatGPT. Runtime Python and Qt
 dependencies are fetched into a per-user virtual environment on first
 launch. Requires internet for the initial dependency installation.
 This package does not include OpenAI credentials or tunnel-client.
EOF

dpkg-deb --build --root-owner-group "$stage" \
  "$outdir/openremote-control-center_${version}~preview1_amd64.deb" >/dev/null

echo "Built: $outdir/openremote-control-center_${version}~preview1_amd64.deb"
sha256sum "$outdir/openremote-control-center_${version}~preview1_amd64.deb"
dpkg-deb -I "$outdir/openremote-control-center_${version}~preview1_amd64.deb" | head -30
