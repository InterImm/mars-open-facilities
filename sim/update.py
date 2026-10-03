"""Advance every facility to the current time and publish its new data.

Each facility is a Homeostat scenario (facilities/<id>/scenario.yaml) plus its description
(facilities/<id>/facility.yaml). The plants run "pseudo-live": the simulation clock starts at
the facility's epoch, and every run of this script advances it to the last whole 10 minutes
before now. The engine is saved between runs, so the history is one continuous run; if the
saved engine cannot be used (first run, a changed scenario, other library versions), the
plant is replayed from its epoch, which gives the same history for the same versions.

Usage: python sim/update.py [--data DIR] [--now ISO8601]
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

import homeostat

ROOT = Path(__file__).resolve().parent.parent
FACILITIES = ROOT / "facilities"
STEP = 600  # published resolution, seconds (the scenarios record every 10 min)
RECENT_DAYS = 14  # length of recent.csv, the file the dashboard charts
KEEP_DAYS = 60  # daily raw files kept
LOG_KEEP = 300  # operations log entries kept
LOOKAHEAD = 30 * 86400  # planned work published ahead

# Mars Sol Date (Allison and McEwen 2000, as in Mars24); TT - UTC = 69.184 s since 2017
SOL = 88775.244147


def msd(ts: pd.Series | datetime) -> float | pd.Series:
    if isinstance(ts, datetime):
        jd_ut = ts.timestamp() / 86400 + 2440587.5
    else:
        jd_ut = (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds() / 86400 + 2440587.5
    jd_tt = jd_ut + 69.184 / 86400
    return (jd_tt - 2405522.0028779) / 1.0274912517


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt(v: float) -> str:
    if v is None or not math.isfinite(v):
        return ""
    return f"{v:.5g}"


def read_day(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


class Facility:
    def __init__(self, folder: Path, data: Path):
        self.folder = folder
        self.meta = yaml.safe_load((folder / "facility.yaml").read_text())
        self.id = self.meta["id"]
        self.scenario_text = (folder / "scenario.yaml").read_text()
        self.config = yaml.safe_load(self.scenario_text)
        self.epoch = parse_time(self.meta["epoch"])
        self.out = data / self.id
        self.tags = [t["tag"] for t in self.meta["tags"]]
        for k in self.meta.get("kpis", []):
            if k["tag"] not in self.tags:
                self.tags.append(k["tag"])
        versions = f"{homeostat.__version__}/{np.__version__}"
        self.hash = hashlib.sha256((self.scenario_text + versions).encode()).hexdigest()[:16]
        self.prepared = homeostat.prepare(self.config)

    # --- the engine -------------------------------------------------------------------
    def engine_at(self, cursor: dict):
        """An engine at the cursor's time, restored or replayed."""
        state = self.out / "state" / "engine.bin"
        t = cursor.get("t", 0)
        if t and cursor.get("hash") == self.hash and state.exists():
            try:
                return homeostat.Engine.restore(state.read_bytes()), "restored"
            except Exception as err:  # noqa: BLE001 - any failure means replay
                print(f"  {self.id}: cannot restore ({err}); replaying", file=sys.stderr)
        engine = self.prepared.engine()
        engine.initialize(self.config.get("init", "steady"))
        if t:
            engine.run(t)
            engine.result(clear=True)
        return engine, "replayed" if t else "started"

    # --- one update -------------------------------------------------------------------
    def update(self, now: datetime) -> dict:
        cursor = read_json(self.out / "state" / "cursor.json", {})
        horizon = float(self.prepared.scenario.duration)
        target = math.floor((now - self.epoch).total_seconds() / STEP) * STEP
        target = min(target, horizon)
        t0 = cursor.get("t", 0)
        how = "up to date"
        frame = None
        if target > t0:
            engine, how = self.engine_at(cursor)
            engine.run(target)
            run = engine.result(clear=True)
            frame = self.published(run)
            self.write_rows(frame)
            self.write_log(run)
            (self.out / "state").mkdir(parents=True, exist_ok=True)
            (self.out / "state" / "engine.bin").write_bytes(engine.snapshot())
            cursor = {"t": target, "hash": self.hash, "homeostat": homeostat.__version__,
                      "numpy": np.__version__, "updated": iso(now)}
            write_json(self.out / "state" / "cursor.json", cursor)
        recent = self.write_recent(now)
        summary = self.write_meta(cursor.get("t", 0), recent)
        print(f"  {self.id}: {how}, sim t = {cursor.get('t', 0) / 86400:.2f} d"
              + (f", {len(frame)} new rows" if frame is not None else ""))
        return summary

    def published(self, run) -> pd.DataFrame:
        cols = {}
        for tag in self.tags:
            for src in (run.observed, run.measured, run.truth):
                if tag in src.columns:
                    cols[tag] = src[tag].to_numpy(dtype=float)
                    break
            else:
                raise KeyError(f"{self.id}: tag {tag} is not a signal of the scenario")
        seconds = run.truth.index.total_seconds().to_numpy()
        frame = pd.DataFrame(cols)
        frame.insert(0, "time", pd.to_datetime(self.epoch) + pd.to_timedelta(seconds, unit="s"))
        return frame

    def write_rows(self, frame: pd.DataFrame) -> None:
        days = self.out / "days"
        days.mkdir(parents=True, exist_ok=True)
        for day, rows in frame.groupby(frame["time"].dt.strftime("%Y-%m-%d")):
            path = days / f"{day}.csv"
            new = not path.exists()
            with path.open("a", newline="") as f:
                w = csv.writer(f, lineterminator="\n")
                if new:
                    w.writerow(["time", *self.tags])
                for row in rows.itertuples(index=False):
                    w.writerow([iso(row[0]), *(fmt(v) for v in row[1:])])
        files = sorted(days.glob("*.csv"))
        for old in files[:-KEEP_DAYS]:
            self.archive(old)
            old.unlink()
        self.write_sols()

    def sol_means(self, df: pd.DataFrame) -> pd.DataFrame:
        """Mean of every tag per Mars Sol Date, with the number of rows behind it."""
        df = df.copy()
        df["msd"] = np.floor(msd(df["time"])).astype(int)
        df["n"] = 1
        return df.drop(columns="time").groupby("msd").agg({**{t: "mean" for t in self.tags}, "n": "sum"}).reset_index()

    @staticmethod
    def merge_means(parts: list[pd.DataFrame], tags: list[str]) -> pd.DataFrame:
        parts = [p for p in parts if not p.empty]
        if not parts:
            return pd.DataFrame(columns=["msd", *tags, "n"])
        both = pd.concat(parts)
        sums = both[tags].mul(both["n"], axis=0)
        sums["msd"], sums["n"] = both["msd"], both["n"]
        g = sums.groupby("msd").sum(min_count=1)
        out = g[tags].div(g["n"], axis=0)
        out["n"] = g["n"]
        return out.reset_index().sort_values("msd")

    def write_sol_csv(self, path: Path, df: pd.DataFrame) -> None:
        with path.open("w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["msd", *self.tags, "n"])
            for row in df.itertuples(index=False):
                w.writerow([int(row[0]), *(fmt(v) for v in row[1:-1]), int(row[-1])])

    def archive(self, old: Path) -> None:
        """Fold an expiring daily file into sols-archive.csv before it is deleted."""
        path = self.out / "sols-archive.csv"
        prev = pd.read_csv(path) if path.exists() else pd.DataFrame()
        self.write_sol_csv(path, self.merge_means([prev, self.sol_means(read_day(old))], self.tags))

    def write_sols(self) -> None:
        """sols.csv: one row per sol over the whole history (archive plus the daily files)."""
        path = self.out / "sols-archive.csv"
        parts = [pd.read_csv(path)] if path.exists() else []
        days = sorted((self.out / "days").glob("*.csv"))
        if days:
            parts.append(self.sol_means(pd.concat([read_day(p) for p in days])))
        self.write_sol_csv(self.out / "sols.csv", self.merge_means(parts, self.tags))

    def write_recent(self, now: datetime) -> pd.DataFrame:
        days = sorted((self.out / "days").glob("*.csv"))
        start = now - timedelta(days=RECENT_DAYS)
        parts = [read_day(p) for p in days if p.stem >= start.strftime("%Y-%m-%d")]
        if not parts:
            return pd.DataFrame()
        df = pd.concat(parts)
        df = df[df["time"] >= pd.Timestamp(start)]
        with (self.out / "recent.csv").open("w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["time", *self.tags])
            for row in df.itertuples(index=False):
                w.writerow([iso(row[0].to_pydatetime()), *(fmt(v) for v in row[1:])])
        return df

    # --- operations log and plan ------------------------------------------------------
    def write_log(self, run) -> None:
        ev = run.meta["events"]
        log = read_json(self.out / "log.json", [])
        if ev is not None and len(ev):
            planned = ev[ev["origin"].astype(str).str.startswith("plan.")]
            if not self.meta.get("log_transitions", True):  # a day/night cycle would flood the log
                planned = planned[~planned["label"].astype(str).str.startswith("transition:")]
            seen = set()
            for row in planned.itertuples():
                key = (row.label, round(float(row.at)))
                if key in seen:
                    continue
                seen.add(key)
                targets = sorted(set(planned[(planned["label"] == row.label) & (planned["at"].round() == round(float(row.at)))]["target"]))
                log.append({"time": iso(self.epoch + timedelta(seconds=float(row.at))),
                            "event": self.describe(str(row.label)), "targets": targets})
        write_json(self.out / "log.json", log[-LOG_KEEP:])

    @staticmethod
    def describe(label: str) -> str:
        kind, _, what = label.partition(":")
        if kind == "transition":
            a, _, b = what.partition("->")
            return f"Operating mode changed from {a.replace('_', ' ')} to {b.replace('_', ' ')}"
        if kind == "maintenance":
            names = {"stop": "Unit shut down for maintenance", "clean": "Heat-transfer surfaces cleaned",
                     "replace_catalyst": "Catalyst (or stack) replaced", "calibrate": "Instruments calibrated",
                     "replace_valve": "Control valve replaced", "replace_sensor": "Transmitter replaced"}
            return names.get(what, f"Maintenance: {what}")
        return label

    def plan_state(self, t: float) -> tuple[str | None, list[dict]]:
        plan = self.prepared.scenario.plan
        if plan is None:
            return None, []
        regime = plan.start
        for tr in plan.production or []:
            if tr.at <= t:
                regime = tr.to
        upcoming = []
        for task in plan.maintenance or []:
            end = task.at + (task.duration or 0)
            if task.at <= t < end:
                upcoming.append({"time": iso(self.epoch + timedelta(seconds=task.at)), "task": task.task,
                                 "target": task.target, "until": iso(self.epoch + timedelta(seconds=end)), "active": True})
            elif t <= task.at < t + LOOKAHEAD:
                upcoming.append({"time": iso(self.epoch + timedelta(seconds=task.at)), "task": task.task,
                                 "target": task.target, "duration_h": (task.duration or 0) / 3600})
        upcoming.sort(key=lambda u: u["time"])
        return regime, upcoming

    def write_meta(self, t: float, recent: pd.DataFrame) -> dict:
        regime, upcoming = self.plan_state(t)
        latest = {}
        if not recent.empty:
            last = recent.iloc[-1]
            latest = {tag: (None if pd.isna(last[tag]) else float(f"{last[tag]:.5g}")) for tag in self.tags}
        kpis = []
        for k in self.meta.get("kpis", []):
            v = latest.get(k["tag"])
            avg = None
            if not recent.empty:
                window = recent[recent["time"] >= recent["time"].iloc[-1] - pd.Timedelta(seconds=SOL)][k["tag"]]
                avg = float(f"{window.mean():.5g}") if window.notna().any() else None
            kpis.append({**k, "value": v, "sol_mean": avg})
        status = self.status(kpis, upcoming)
        summary = {
            "id": self.id, "name": self.meta["name"], "name_zh": self.meta.get("name_zh"),
            "city": self.meta["city"], "city_zh": self.meta.get("city_zh"),
            "region": self.meta["region"], "region_zh": self.meta.get("region_zh"),
            "lat": self.meta["lat"], "lon": self.meta["lon"], "operator": self.meta.get("operator"),
            "summary": self.meta.get("summary"), "status": status, "mode": regime,
            "time": iso(self.epoch + timedelta(seconds=t - STEP)) if t else None,
            "kpis": kpis,
        }
        write_json(self.out / "meta.json", {
            **summary, "story": self.meta.get("story"), "book": self.meta.get("book"),
            "process": self.meta.get("process", []), "tags": self.meta["tags"],
            "sources": self.meta.get("sources", []), "epoch": iso(self.epoch),
            "latest": latest, "upcoming": upcoming, "scenario": f"facilities/{self.id}/scenario.yaml",
            "homeostat": homeostat.__version__,
        })
        return summary

    @staticmethod
    def status(kpis: list[dict], upcoming: list[dict]) -> str:
        if any(u.get("active") and u["task"] == "stop" for u in upcoming):
            return "maintenance"
        # judged on the mean of the last sol, so a day/night cycle is not read as an outage
        output = [k for k in kpis if k.get("kind", "output") == "output" and k.get("sol_mean") is not None]
        if not output:
            return "operating"
        ratio = output[0]["sol_mean"] / output[0]["design"]
        if ratio >= 0.85:
            return "operating"
        if ratio >= 0.25:
            return "reduced"
        return "offline"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data"))
    ap.add_argument("--now", default=None, help="pretend it is this time (ISO 8601, UTC)")
    ap.add_argument("--only", default=None, help="comma-separated facility ids")
    args = ap.parse_args()
    now = parse_time(args.now) if args.now else datetime.now(timezone.utc)
    data = Path(args.data)
    only = set(args.only.split(",")) if args.only else None
    print(f"Advancing facilities to {iso(now)} (homeostat {homeostat.__version__})")
    summaries = []
    for folder in sorted(p for p in FACILITIES.iterdir() if (p / "scenario.yaml").exists()):
        if only and folder.name not in only:
            continue
        summaries.append(Facility(folder, data).update(now))
    if not only:
        write_json(data / "index.json", {"generated": iso(now), "msd": round(msd(now), 4),
                                         "homeostat": homeostat.__version__, "facilities": summaries})


if __name__ == "__main__":
    main()
