#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "$(uname -s)/$(uname -m)" != Darwin/arm64 ]]; then
    echo "Build this release on Apple Silicon macOS." >&2
    exit 1
fi
version=${1:?Usage: packaging/build-macos.sh VERSION}
if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc[0-9]+)?$ ]]; then
    echo "Use a tag such as v0.1.0-rc2." >&2
    exit 1
fi
export MACOSX_DEPLOYMENT_TARGET=15.0
export HOMEBREW_NO_AUTO_UPDATE=1

work="$PWD/build/macos/$version"
out="$PWD/dist/$version"
mkdir -p "$work" "$out"
python="$(brew --prefix python@3.13)/bin/python3.13"
"$python" -c 'import sys, tkinter; assert sys.version_info[:2] == (3, 13) and 9 <= tkinter.TkVersion < 10'
"$python" -m venv "$work/venv"
python="$work/venv/bin/python"
"$python" -m pip install -r packaging/requirements.txt

usb_version=1.0.29
usb_source="libusb-$usb_version.tar.bz2"
usb_sha=5977fc950f8d1395ccea9bd48c06b3f808fd3c2c961b44b0c2e6e29fc3a70a85
if [ ! -f "$work/$usb_source" ]; then
    curl -fsSL --retry 3 "https://github.com/libusb/libusb/releases/download/v$usb_version/$usb_source" \
        -o "$work/$usb_source.part"
    mv "$work/$usb_source.part" "$work/$usb_source"
fi
printf '%s  %s\n' "$usb_sha" "$work/$usb_source" | shasum -a 256 -c
tar -xf "$work/$usb_source" -C "$work"
(
    cd "$work/libusb-$usb_version"
    ./configure --prefix="$work/libusb" --disable-static --enable-shared
    make -j "$(sysctl -n hw.logicalcpu)"
    make install
)
cp "$work/$usb_source" "$out/$usb_source"

app_name="Hantek 6254BD"
payload="$work/payload"
mkdir -p "$payload/Licenses"
cp LICENSE THIRD_PARTY_NOTICES.md "$payload/Licenses/"
cp "$(brew --prefix python@3.13)/LICENSE" "$payload/Licenses/Python.txt"
cp "$(brew --prefix tcl-tk)/license.terms" "$payload/Licenses/Tcl-Tk.txt"
cp "$work/libusb-$usb_version/COPYING" "$payload/Licenses/libusb.txt"
"$python" - "$payload/Licenses" <<'PY'
from importlib.metadata import distribution
from pathlib import Path
import shutil
import sys

for name in ("pyusb", "pyinstaller"):
    package = distribution(name)
    files = [f for f in package.files if f.name.lower().startswith(("license", "copying"))]
    if not files:
        raise SystemExit(f"No license found for {name}")
    for f in files:
        shutil.copyfile(package.locate_file(f), Path(sys.argv[1]) / f"{name}-{f.name}")
PY
# Homebrew's Python/Tk frameworks can pull in additional dylibs.
for formula in $(brew deps --installed --union python@3.13 python-tk@3.13); do
    prefix="$(brew --prefix "$formula")"
    find -H "$prefix" -maxdepth 1 -type f \( -iname 'license*' -o -iname 'copying*' -o -iname 'copyright*' \) |
        while IFS= read -r file; do
            cp "$file" "$payload/Licenses/${formula//\//_}-$(basename "$file")"
        done
done
"$python" --version > "$payload/Build-versions.txt"
"$python" -c 'import tkinter; print("Tk", tkinter.Tcl().eval("info patchlevel"))' >> "$payload/Build-versions.txt"
"$python" -m pip freeze >> "$payload/Build-versions.txt"
printf 'libusb %s\n' "$usb_version" >> "$payload/Build-versions.txt"

"$python" -m PyInstaller --noconfirm --onedir --windowed --target-arch arm64 \
    --name "$app_name" --osx-bundle-identifier io.github.ftjn740.hantek6254bd \
    --specpath "$work" --workpath "$work/pyinstaller" --distpath "$work/app" \
    --add-binary "$work/libusb/lib/libusb-1.0.0.dylib:." \
    --add-data "$PWD/SAMPLE-CH1-CH2.hk6254:." \
    --add-data "$payload/Licenses:Licenses" \
    --add-data "$payload/Build-versions.txt:." scope.py

archive="hantek6254bd-$version-macos-arm64.zip"
ditto -c -k --keepParent "$work/app/$app_name.app" "$out/$archive"
unpacked="$(mktemp -d "$work/verify.XXXXXX")"
ditto -x -k "$out/$archive" "$unpacked"
app="$unpacked/$app_name.app"
executable="$app/Contents/MacOS/$app_name"
test "$(lipo -archs "$executable")" = arm64
codesign --verify --deep --strict "$app"

# Inspect every frozen Mach-O file, rather than only the launcher.
while IFS= read -r -d '' binary; do
    if file -b "$binary" | grep -q Mach-O; then
        deps="$(otool -L "$binary" | tail -n +2)"
        if grep -Eq '/(opt/homebrew|usr/local|private/tmp|Users|Volumes)/' <<< "$deps"; then
            printf 'Nonportable dependency in %s\n%s\n' "$binary" "$deps" >&2
            exit 1
        fi
    fi
done < <(find "$app/Contents" -type f -print0)
(
    cd "$unpacked"
    env -i HOME="$HOME" PATH=/usr/bin:/bin:/usr/sbin:/sbin "$executable" --smoke-test
)
source="hantek6254bd-$version-source.tar.gz"
git archive --format=tar.gz --prefix="hantek6254bd-$version/" -o "$out/$source" HEAD
(
    cd "$out"
    shasum -a 256 "$archive" "$source" "$usb_source" > SHA256SUMS
    shasum -c SHA256SUMS
)
