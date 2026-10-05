#!/usr/bin/env bash
# Install Tunebox in Termux (Android).   Run it from this folder:   bash termux-install.sh
set -e
cd "$(dirname "$0")"

if [ -z "$TERMUX_VERSION" ] && [[ "${PREFIX:-}" != *com.termux* ]]; then
    echo "This script is for Termux (Android). On a computer use:  pip install -r requirements.txt"
    exit 1
fi

echo "==> Installing Termux packages (python, ffmpeg, mpv, numpy, pillow, termux-api)"
pkg update -y
pkg install -y python ffmpeg mpv python-numpy python-pillow termux-api git

echo "==> Installing Python packages"
pip install -r requirements-termux.txt

echo "==> Creating the 'tunebox' command"
mkdir -p "$PREFIX/bin"
printf '#!/usr/bin/env bash\nexec python "%s/run.py" "$@"\n' "$PWD" > "$PREFIX/bin/tunebox"
chmod +x "$PREFIX/bin/tunebox"

cat <<'MSG'

Done. Start it with:  tunebox

Do these once, for the best experience:
  1. Downloads in your phone's Music folder:  run  termux-setup-storage  and allow access.
  2. Music that keeps playing with the screen off: install the "Termux:API" app (F-Droid), and in Android's
     battery settings set Termux to "Unrestricted" / "Don't optimize".
  3. Hold your phone upright for the one-column layout, or sideways for the two-pane one.
MSG
