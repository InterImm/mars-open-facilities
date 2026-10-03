"""Bundle each finished month of 10-minute data into a zip for a GitHub Release.

    python sim/archive.py --data data --out _release           # list and zip the months not yet released
    python sim/archive.py --data data --mark 2026-09            # record a month as released

A month is finished once its last UTC day is complete. The zip holds every facility's daily
files for the month (`<id>/YYYY-MM-DD.csv`) and its meta.json. Released months are listed in
data/releases.json; update.py deletes daily files older than 60 days only once their month
is released, so nothing is lost.
"""

from __future__ import annotations

import argparse
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

README = """Mars Open Facilities, {month}: 10-minute readings of every facility.

{facilities}

Each <facility>/YYYY-MM-DD.csv has one row every 10 minutes (UTC) and one column per tag; the
tags, their units and descriptions are in <facility>/meta.json. The data is simulated with
Homeostat from the scenarios in https://github.com/InterImm/mars-open-facilities and is
deterministic: the same scenario and versions give the same numbers.
"""


def released(data: Path) -> list[str]:
    try:
        return json.loads((data / "releases.json").read_text())
    except (OSError, ValueError):
        return []


def finished_months(data: Path, now: datetime) -> list[str]:
    months = set()
    for f in data.glob("*/days/*.csv"):
        months.add(f.stem[:7])
    current = now.strftime("%Y-%m")
    return sorted(m for m in months if m < current)


def bundle(data: Path, month: str, out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"mars-open-facilities-{month}.zip"
    names = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for folder in sorted(p for p in data.iterdir() if (p / "days").is_dir()):
            days = sorted((folder / "days").glob(f"{month}-*.csv"))
            if not days:
                continue
            meta = json.loads((folder / "meta.json").read_text())
            names.append(f"- {folder.name}: {meta['name']} ({meta['city']}), {len(days)} days")
            for d in days:
                z.write(d, f"{folder.name}/{d.name}")
            z.write(folder / "meta.json", f"{folder.name}/meta.json")
        z.writestr("README.txt", README.format(month=month, facilities="\n".join(names)))
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--out", default="_release")
    ap.add_argument("--mark", default=None, help="record this month (YYYY-MM) as released")
    args = ap.parse_args()
    data = Path(args.data)
    done = released(data)
    if args.mark:
        if args.mark not in done:
            (data / "releases.json").write_text(json.dumps(sorted(done + [args.mark])) + "\n")
        return
    for month in finished_months(data, datetime.now(timezone.utc)):
        if month not in done:
            print(month, bundle(data, month, Path(args.out)))


if __name__ == "__main__":
    main()
