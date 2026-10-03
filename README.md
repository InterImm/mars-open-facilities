# Mars Open Facilities

The key facilities of the Mars colonies in the [InterImm book](https://book.interimm.org/history/mars_immigration/), running live: propellant, power, air, iron, water and food. Each facility is a process model written for [Homeostat](https://homeostat.kausalflow.com/docs/), sized from published engineering numbers. A GitHub Actions workflow advances every plant to the present each hour and publishes its readings, and a static dashboard on GitHub Pages shows the status and the time series. Nothing runs on a server and nothing costs money.

## The facilities

| Facility | City | What it does | Design output |
|---|---|---|---|
| Procyon Propellant Works · 南河城推进剂工厂 | Procyon, Isidis | Sabatier reactor and water electrolysis make CH4 and O2 for the shuttles | 250 kg/h CH4, 1 t/h O2, 7.9 MW |
| Procyon Energy Station · 南河城能源站 | Procyon, Isidis | Four 5 MWe heat-pipe fission modules and a 12 MWp solar field | about 24 MW on a sol average |
| Sirius Air Works · 天狼城空气工厂 | Sirius, Isidis | Solid-oxide CO2 electrolysis (a scaled MOXIE) and the dome atmosphere loop | 400 kg/h O2 |
| Atlantis Ironworks · 亚特兰蒂斯城钢铁厂 | Atlantis, Amazonis | Hydrogen direct reduction of hematite (as in HYBRIT) and an electric arc furnace | 13 t/h steel |
| Horizon Starport Propellant Depot · 视界星港推进剂库 | Horizon, Meridiani | LOX and liquid methane tank farms that load ships | 4,100 t LOX, 1,140 t CH4 |
| Trantor Ice Mine · 川陀城冰矿 | Trantor, Hellas | Rodriguez wells melt buried glacial ice with hot water | 21 m3/h water |
| Daoya Farm No. 3 · 稻芽三号农场 | Terminus, Hellas | A 2 ha LED growth hall with CO2 enrichment | 26 kg/h dry biomass while lit |

Every facility page on the dashboard lists the sources behind its numbers; they are also in `facilities/<id>/facility.yaml`.

## How it works

```
facilities/<id>/scenario.yaml   the Homeostat model: loops, instruments, plan, wear, maintenance, faults
facilities/<id>/facility.yaml   name, city, story, published tags, KPIs and sources
sim/update.py                   advances every plant to now and writes the data
sim/archive.py                  zips each finished month for a GitHub Release
web/                            the dashboard (plain HTML, CSS and JS; charts with uPlot)
.github/workflows/update.yml    runs sim/update.py hourly, publishes data, deploys Pages
```

- **Pseudo-live.** Each plant's clock starts at its `epoch` (1 September 2026). A run advances it to the last whole 10 minutes before now. The engine is saved between runs (`Engine.snapshot()`), so the history is one continuous run.
- **Deterministic.** The same scenario and the same Homeostat and NumPy versions always give the same data, so if a saved engine cannot be used (a changed scenario, other versions), the plant is replayed from its epoch. `requirements.txt` pins the versions for that reason.
- **Realistic, not real.** Control loops, instrument lag, noise, quantization and drift, production plans, catalyst decay, fouling, valve wear, scheduled maintenance and random instrument faults all come from Homeostat. Sizes, yields and energy use come from the sources listed per facility.
- **Operators' view.** The data is what a plant historian would record: measured values, setpoints and controller outputs. Planned work is published 30 days ahead; faults are not announced, they only show in the data.

## The data

The workflow keeps the current data on the `data` branch (one commit, replaced each run) and serves it with the site under `data/`:

| File | Content |
|---|---|
| `index.json` | Every facility's status, mode and KPIs |
| `<id>/meta.json` | Description, tags, latest values, planned work, sources |
| `<id>/recent.csv` | The last 14 days, every 10 minutes |
| `<id>/days/YYYY-MM-DD.csv` | One file per UTC day, at least the last 60 days |
| `<id>/sols.csv` | One row per Mars Sol Date over the whole history (means, with `n` rows behind each) |
| `<id>/log.json` | Operations log: mode changes and maintenance |
| `<id>/state/` | The saved engine (not published on the site) |
| `releases.json` | Months already bundled into releases |

Every finished month of 10-minute data is also bundled into a zip and published as a [GitHub Release](https://github.com/InterImm/mars-open-facilities/releases) tagged `data-YYYY-MM` (`sim/archive.py`). Daily files leave the `data` branch only after their month is released, so the full-resolution history is always in one place or the other.

## Run it locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python sim/update.py --data data            # advance to now (the first run compiles each plant, a few minutes)
python sim/update.py --data data --now 2026-09-10T00:00:00Z --only sirius-air
mkdir -p _site && cp -r web/. _site/ && cp -r data _site/data && python -m http.server -d _site
```

## Adding a facility

Write `facilities/<id>/scenario.yaml` (see the [Homeostat docs](https://homeostat.kausalflow.com/docs/) and the existing plants), check it with `homeostat validate` or a short run, then add `facility.yaml` with the tags to publish and the KPIs. The pull-request check runs every facility for two days.

## Setup

GitHub Pages must use **GitHub Actions** as its source (Settings → Pages). The workflow needs no secrets.
