"""Probe the authorized Android phone without starting AstrBot.

Examples:
  python -X utf8 tools/phone_probe.py status
  python -X utf8 tools/phone_probe.py --connect 192.168.1.23:37123 status
  python -X utf8 tools/phone_probe.py observe --json
  python -X utf8 tools/phone_probe.py tap-text "签到"
  python -X utf8 tools/phone_probe.py launch com.example.app
  python -X utf8 tools/phone_probe.py screenshot .tmp/phone.png

The probe is read-mostly.  ``tap-text``/``launch`` are the same bounded
operations the runtime uses; it never opens a general shell and never installs
anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from companion_v2.device.adb import ADB_KEYBOARD_IME, AdbClient, AdbError, discover_adb  # noqa: E402


def _client(arguments):
    executable = discover_adb(arguments.adb or None)
    if arguments.connect:
        AdbClient(executable).connect(arguments.connect)
    return AdbClient(executable, serial=arguments.serial or None)


def _status(client: AdbClient) -> dict:
    devices = [{"serial": item.serial, "state": item.state, "model": item.model}
               for item in client.devices()]
    return {"executable": str(client.executable), "devices": devices,
            "selected": client.serial}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded ADB probe for the companion device layer")
    parser.add_argument("command", choices=("status", "observe", "tap-text", "launch", "screenshot", "ime"))
    parser.add_argument("value", nargs="?", default="", help="text, package or output path")
    parser.add_argument("--adb", default="", help="explicit adb executable path")
    parser.add_argument("--serial", default="", help="device serial when several are attached")
    parser.add_argument("--connect", default="", help="wireless debugging host:port to connect first")
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    arguments = parser.parse_args(argv)
    try:
        client = _client(arguments)
        if arguments.command == "status":
            payload = _status(client)
        elif arguments.command == "observe":
            payload = client.snapshot()
        elif arguments.command == "tap-text":
            element = client.tap_text(arguments.value)
            payload = {"tapped": element.document()}
        elif arguments.command == "launch":
            payload = {"output": client.launch(arguments.value)}
        elif arguments.command == "ime":
            installed = client.ime_list()
            payload = {"current": client.ime_current(), "adbkeyboard": ADB_KEYBOARD_IME in installed,
                       "imes": list(installed)}
        else:
            target = Path(arguments.value or "phone.png").expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            image = client.screenshot()
            target.write_bytes(image)
            payload = {"path": str(target), "bytes": len(image)}
    except AdbError as error:
        payload = {"state": "failed", "code": error.code, "message": str(error)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if arguments.json else
              f"{error.code}: {error}", file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif arguments.command == "observe":
        print(f"screen {payload['width']}x{payload['height']} "
              f"elements={len(payload['elements'])} sha={payload['screenshot_sha256'][:12]}")
        for element in payload["elements"][:20]:
            label = element["text"] or element["description"]
            if label:
                print(f"  [{element['index']}] {'可点' if element['clickable'] else '只读'} "
                      f"{label!r} @{tuple(element['center'])}")
    else:
        print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
