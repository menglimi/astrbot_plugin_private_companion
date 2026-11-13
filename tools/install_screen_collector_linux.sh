#!/bin/sh
# Run as the desktop user, from the logged-in graphical session.
set -eu
if [ "$(id -u)" = 0 ]; then
    echo 'Run this installer as the logged-in desktop user.' >&2
    exit 1
fi
python3 -c 'from PIL import ImageGrab' || {
    echo 'Install Python 3.10+ and Pillow for the system Python using your distribution package manager.' >&2
    exit 1
}
source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
install -d -m 700 "$HOME/.local/share/companion-screen" "$HOME/.config/companion-screen"
install -d -m 755 "$HOME/.config/systemd/user"
install -m 644 "$source_dir/remote_screen_collector.py" "$HOME/.local/share/companion-screen/remote_screen_collector.py"
if [ ! -f "$HOME/.config/companion-screen/collector.json" ]; then
    python3 "$HOME/.local/share/companion-screen/remote_screen_collector.py" \
        --config "$HOME/.config/companion-screen/collector.json" --init --device-id "${1:-linux-desktop}"
fi
install -m 644 "$source_dir/companion-screen-collector.service" "$HOME/.config/systemd/user/companion-screen-collector.service"
systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP
if [ -n "${NIRI_SOCKET:-}" ]; then
    systemctl --user import-environment NIRI_SOCKET
fi
systemctl --user daemon-reload
systemctl --user enable --now companion-screen-collector.service
echo 'Collector installed. Private configuration: ~/.config/companion-screen/collector.json'
echo 'It listens on 127.0.0.1:6191; use an SSH tunnel or an authenticated private network to connect AstrBot.'
