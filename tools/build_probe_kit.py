"""Build a zip with the purifier probe tool for testers outside this repository.

The kit contains the probe, the parts of the integration it imports (the
Home Assistant-free LAN client and the DP model) and instructions, so a tester
on the purifier's network needs only Python and tinytuya.

Run from the repository root:

  python -m tools.build_probe_kit            # writes dist/birdie-purifier-probe-<version>.zip
"""

from __future__ import annotations

import argparse
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = Path("custom_components/birdie")
FILES = [
    Path("LICENSE"),
    Path("tools/purifier_probe.py"),
    INTEGRATION / "__init__.py",
    INTEGRATION / "const.py",
    INTEGRATION / "purifier/__init__.py",
    INTEGRATION / "purifier/model.py",
    *sorted(p.relative_to(ROOT) for p in (ROOT / INTEGRATION / "tuya_lan").glob("*.py")),
]

README = """\
Birdie Air Purifier probe {version}
==================================

This tool checks a Birdie air purifier over the local network and writes a
report for the developers. It does not need Home Assistant. Run it on a
computer connected to the same network (same Wi-Fi / subnet) as the purifier.

You need: Python 3.11 or newer, the purifier set up in the Birdie app, and
its device ID and local key.

Close other local controllers of the purifier first (for example a Homey
Tuya Local app): the purifier accepts only a few local connections.

1. Install
----------

macOS / Linux:

    cd birdie-purifier-probe-{version}
    python3 -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt

Windows (PowerShell):

    cd birdie-purifier-probe-{version}
    py -m venv .venv
    .venv\\Scripts\\Activate.ps1
    pip install -r requirements.txt

2. Find the purifier (about 12 seconds)
---------------------------------------

    python -m tools.purifier_probe discover

The purifier is listed as "Birdie purifier" with its protocol version. If
nothing is listed, allow Python through the computer's firewall (UDP ports
6666, 6667) or continue with --host <IP address of the purifier>.

3. Read-only check (about 2 minutes)
------------------------------------

    python -m tools.purifier_probe read --device-id <device ID> --watch 90 --report read.json

The tool asks for the local key (input hidden). While it watches, change
the mode and speed in the Birdie app, turn the purifier off and on in the
app, and open and close the filter cover if possible.

4. Control test (about 1 minute; changes the purifier's state)
--------------------------------------------------------------

    python -m tools.purifier_probe test --device-id <device ID> --yes --report test.json

The purifier turns off and on and runs through every mode and speed; power
and mode are restored at the end. Watch the purifier and note whether each
step happens. Add --include-filter-reset only if the filter counter may be
reset.

5. Send the results
-------------------

Send read.json, test.json and your notes to the developers. The reports
contain no local key and no IP address. Do not send the local key itself.

Options: --host <IP> skips the network search; --debug adds protocol logs
(the key is redacted); --help lists everything.
"""


def manifest() -> dict:
    """The integration manifest."""
    return json.loads((ROOT / INTEGRATION / "manifest.json").read_text())


def version() -> str:
    """Integration version plus the git commit, when available."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return manifest()["version"]
    return f"{manifest()['version']}+{commit}"


def build(out_dir: Path) -> Path:
    """Write the kit and return its path."""
    kit_version = version()
    name = f"birdie-purifier-probe-{kit_version}"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as kit:
        for path in FILES:
            kit.write(ROOT / path, f"{name}/{path.as_posix()}")
        kit.writestr(f"{name}/README.txt", README.format(version=kit_version))
        kit.writestr(f"{name}/requirements.txt", "\n".join(manifest()["requirements"]) + "\n")
    return target


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="output directory")
    args = parser.parse_args()
    print(build(args.out))


if __name__ == "__main__":
    main()
