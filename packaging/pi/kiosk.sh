#!/usr/bin/env bash
# Opens the NOTORYX Ops kiosk screen full screen every time the Pi's desktop starts.
# For a Raspberry Pi with a touchscreen. Run as the desktop user (not root), after install.sh:
#   ./kiosk.sh            set it up
#   ./kiosk.sh --remove   take it out again
set -euo pipefail

URL="${KIOSK_URL:-http://127.0.0.1:8484/kiosk}"
AUTOSTART="$HOME/.config/autostart/notoryx-ops-kiosk.desktop"
LABWC="$HOME/.config/labwc/autostart"
MARK="# notoryx-ops-kiosk"

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as the user who signs in to the desktop, not with sudo." >&2
  exit 1
fi

if [ "${1:-}" = "--remove" ]; then
  rm -f "$AUTOSTART"
  [ -f "$LABWC" ] && sed -i "/$MARK/d" "$LABWC"
  echo "Kiosk screen removed. It won't open at the next start."
  exit 0
fi

BROWSER=""
for b in chromium-browser chromium; do
  if command -v "$b" >/dev/null 2>&1; then BROWSER="$b"; break; fi
done
if [ -z "$BROWSER" ]; then
  echo "Chromium isn't installed. Install it with: sudo apt install chromium-browser" >&2
  exit 1
fi

CMD="$BROWSER --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --check-for-update-interval=31536000 $URL"

if [ -d "$HOME/.config/labwc" ] || [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  # Raspberry Pi OS Bookworm and later (labwc)
  mkdir -p "$(dirname "$LABWC")"
  touch "$LABWC"
  sed -i "/$MARK/d" "$LABWC"
  echo "(sleep 8; $CMD) & $MARK" >> "$LABWC"
else
  # Older desktops (LXDE / X11)
  mkdir -p "$(dirname "$AUTOSTART")"
  cat > "$AUTOSTART" <<DESKTOP
[Desktop Entry]
Type=Application
Name=NOTORYX Ops kiosk
Exec=sh -c "sleep 8; $CMD"
X-GNOME-Autostart-enabled=true
DESKTOP
fi

echo "Done. The kiosk screen opens at $URL each time the desktop starts."
echo "To stop the screen going blank, turn off Screen Blanking in Raspberry Pi Configuration > Display."
echo "The screen dims itself at night; tap it to wake it. Menu (top right) changes that."
