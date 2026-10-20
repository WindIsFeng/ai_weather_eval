"""Reproducible best-track/native-forecast maps for randomly sampled storms.

Run in ai-weather-eval. Sampling is uniform by SID, without filtering forecast
quality or availability; each storm uses its earliest coastal event. All five
nominal initialization leads are shown, including no-seed and stopped tracks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from cartopy.io.shapereader import Reader
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.ticker import MaxNLocator
from PIL import Image

from ai_weather_eval.catalog.ibtracs import load_ibtracs_csv

ROOT = Path(__file__).resolve().parents[1]
LEADS = (24, 48, 72, 96, 120)
MODELS = {
    "pangu": ("Pangu", "#0072B2", "-", "o"),
    "fengwu": ("FengWu", "#CC79A7", "--", "s"),
    "fuxi": ("FuXi", "#708238", "-.", "^"),
    "graphcast": ("GraphCast", "#E69F00", ":", "D"),
    "aurora": ("Aurora", "#D55E00", (0, (5, 2, 1, 2)), "v"),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure_font(font_path: Path | None) -> tuple[str, str]:
    for family in ("Arial", "Liberation Sans"):
        try:
            path = font_manager.findfont(family, fallback_to_default=False)
            return family, path
        except ValueError:
            continue
    candidates = [font_path] if font_path else [
        ROOT / "data/cache/fonts/liberation2/usr/share/fonts/truetype/liberation2"
        / "LiberationSans-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            font_manager.fontManager.addfont(str(candidate))
            family = font_manager.FontProperties(fname=str(candidate)).get_name()
            if family not in {"Arial", "Liberation Sans"}:
                raise ValueError("Use Arial or the project-approved Liberation Sans fallback")
            return family, str(candidate)
    raise ValueError("Arial/Liberation Sans unavailable; supply --font-path to its regular TTF")


def wrap(longitude, center):
    return center + (np.asarray(longitude, dtype=float) - center + 180) % 360 - 180


def plot_observations(axis, observed, center):
    """Do not draw a connector across an observed gap longer than six hours."""
    groups = observed.ISO_TIME.diff().gt(pd.Timedelta(hours=6)).cumsum()
    for _, group in observed.groupby(groups):
        axis.plot(wrap(group.LON, center), group.LAT, color="#202124", linewidth=2.8,
                  transform=ccrs.PlateCarree(), zorder=4)


def render_storm(storm, cases, tracks, truth, event, geometries, args):
    event_cases = cases.loc[cases.case_id.eq(event.case_id)].copy()
    if len(event_cases) != len(MODELS) * len(LEADS):
        raise ValueError(f"Expected all five models and initializations for {event.case_id}")
    selected_tracks = tracks.loc[tracks.case_id.eq(event.case_id)].copy()
    start = event_cases.init_time.min()
    end = (event_cases.init_time + pd.to_timedelta(event_cases.forecast_hours, unit="h")).max()
    observed = truth.loc[truth.SID.eq(storm) & truth.ISO_TIME.between(start, end)].copy()
    if observed.empty:
        raise ValueError(f"No observed path in the selected forecast window: {storm}")
    good = selected_tracks.loc[selected_tracks.status.eq("ok")]
    all_lon = np.r_[observed.LON.to_numpy(float), good.longitude.to_numpy(float)]
    all_lat = np.r_[observed.LAT.to_numpy(float), good.latitude.to_numpy(float)]
    center = float(np.degrees(np.angle(np.mean(np.exp(1j * np.radians(all_lon))))))
    unwrapped = wrap(all_lon, center)
    xmid = float((unwrapped.min() + unwrapped.max()) / 2)
    ymid = float((all_lat.min() + all_lat.max()) / 2)
    xspan = max(9.0, float(np.ptp(unwrapped)) * 1.22 + 2.0)
    yspan = max(7.0, float(np.ptp(all_lat)) * 1.22 + 2.0)
    extent = [xmid - xspan / 2, xmid + xspan / 2,
              max(-89.0, ymid - yspan / 2), min(89.0, ymid + yspan / 2)]
    projection = ccrs.PlateCarree(central_longitude=center)
    fig = plt.figure(figsize=(18, 10.5), facecolor="white")
    grid = fig.add_gridspec(2, 3, left=0.055, right=0.99, bottom=0.09, top=0.985,
                           wspace=0.20, hspace=0.17)
    panels, observation_rows = [], []
    for number, nominal in enumerate(LEADS):
        axis = fig.add_subplot(grid[number // 3, number % 3], projection=projection)
        axis.set_extent(extent, crs=ccrs.PlateCarree())
        axis.set_facecolor("#f7fafc")
        axis.add_geometries(geometries, crs=ccrs.PlateCarree(), facecolor="#efeee8",
                            edgecolor="#88867f", linewidth=0.5, zorder=1)
        labels = axis.gridlines(draw_labels=True, linewidth=0.6, color="#c8ced3",
                                alpha=0.75, linestyle=":", x_inline=False, y_inline=False)
        labels.top_labels = labels.right_labels = False
        labels.rotate_labels = False
        labels.xlocator = MaxNLocator(nbins=3)
        labels.ylocator = MaxNLocator(nbins=3)
        labels.xlabel_style = labels.ylabel_style = {"size": 16}
        panel_cases = event_cases.loc[event_cases.nominal_lead_hours.eq(nominal)]
        if set(panel_cases.model) != set(MODELS) or panel_cases.init_time.nunique() != 1:
            raise ValueError("Five-model initialization mismatch")
        first = panel_cases.iloc[0]
        init = first.init_time
        horizon = int(first.forecast_hours)
        valid_end = init + pd.Timedelta(hours=horizon)
        panel_truth = observed.loc[observed.ISO_TIME.between(init, valid_end)]
        plot_observations(axis, panel_truth, center)
        for row in panel_truth.itertuples(index=False):
            observation_rows.append({"storm_id": storm, "case_id": event.case_id,
                                     "forecast_case_id": first.forecast_case_id,
                                     "init_time": init, "valid_time": row.ISO_TIME,
                                     "latitude": row.LAT, "longitude": row.LON})
        if pd.notna(first.seed_latitude):
            axis.scatter(wrap([first.seed_longitude], center), [first.seed_latitude],
                         marker="*", s=160, facecolor="white", edgecolor="#202124",
                         linewidth=1.3, transform=ccrs.PlateCarree(), zorder=8)
        status = {}
        for model, (label, color, linestyle, marker) in MODELS.items():
            record = panel_cases.loc[panel_cases.model.eq(model)].iloc[0]
            points = selected_tracks.loc[
                selected_tracks.forecast_case_id.eq(record.forecast_case_id)
                & selected_tracks.model.eq(model)
            ].sort_values("lead_hours")
            if len(points) != horizon // 6:
                raise ValueError("Missing planned forecast rows")
            status[model] = str(record.status)
            valid = points.status.eq("ok")
            if not valid.any():
                continue
            marker_indices = np.flatnonzero(valid & points.lead_hours.mod(24).eq(0)).tolist()
            marker_indices.append(int(np.flatnonzero(valid)[-1]))
            axis.plot(wrap(points.longitude, center), points.latitude, color=color,
                      linestyle=linestyle, linewidth=2.0, marker=marker, markersize=5,
                      markevery=sorted(set(marker_indices)), label=label,
                      transform=ccrs.PlateCarree(), zorder=5)
            if record.status != "complete":
                last = points.loc[valid].iloc[-1]
                axis.scatter(wrap([last.longitude], center), [last.latitude], marker="x",
                             s=85, linewidth=2.0, color=color,
                             transform=ccrs.PlateCarree(), zorder=9)
        annotation = (f"({chr(97 + number)}) L{nominal:03d} | "
                      f"Init {init.strftime('%m-%d %HZ')} | +{horizon} h")
        # Initialization metadata belongs outside the map: a box inside the
        # upper-left corner can hide the landfall/end of a northward track.
        axis.text(0.0, 1.04, annotation, transform=axis.transAxes, va="bottom", fontsize=16)
        unavailable = [MODELS[m][0] for m, state in status.items() if state == "no_seed"]
        other_failure = [f"{MODELS[m][0]}: {state}" for m, state in status.items()
                         if state not in {"no_seed", "complete"}]
        if len(unavailable) == len(MODELS):
            note = "no_seed: all five model tracks unavailable"
        else:
            note = "\n".join((["no_seed: " + ", ".join(unavailable)] if unavailable else [])
                             + other_failure)
        if note:
            axis.text(0.5, -0.20, note, transform=axis.transAxes, va="top", ha="center",
                      fontsize=16, color="#4c5054")
        panels.append({"nominal_lead_hours": nominal, "init_time": init.isoformat(),
                       "forecast_hours": horizon, "best_track_points": len(panel_truth),
                       "forecast_case_id": first.forecast_case_id, "model_statuses": status})
    info = fig.add_subplot(grid[1, 2])
    info.axis("off")
    handles = [Line2D([], [], color="#202124", linewidth=2.8, label="IBTrACS best track")]
    handles += [Line2D([], [], color=color, linestyle=style, marker=marker, linewidth=2,
                       markersize=6, label=name) for name, color, style, marker in MODELS.values()]
    handles += [Line2D([], [], color="#202124", marker="*", markerfacecolor="white",
                       linestyle="none", markersize=11, label="Observed initialization"),
                Line2D([], [], color="#555555", marker="x", linestyle="none", markersize=8,
                       label="Last point before tracking stopped")]
    info.legend(handles=handles, loc="upper left", frameon=False, fontsize=16,
                handlelength=2.8, borderaxespad=0, labelspacing=0.65)
    info.text(0, 0.28, f"Storm: {event['name']} ({pd.Timestamp(event.reference_time).year})\n"
              f"SID: {storm}\nEvent: {event.case_id.rsplit('-', 1)[-1]} ({event.reference_event})\n"
              f"Reference: {pd.Timestamp(event.reference_time).strftime('%m-%d %H:%MZ')}\n"
              f"Random seed: {args.seed}", fontsize=16, va="top", transform=info.transAxes)
    fig.text(0.055, 0.035, "Model markers: every 24 h and final tracked point. "
             "Predictions start at +6 h. All times UTC.", fontsize=16)
    fig.text(0.055, 0.01, "no_seed: no observed initialization position; later best-track "
             "positions are not used to start tracking.", fontsize=16)
    stem = f"{pd.Timestamp(event.reference_time).year}_{event['name'].lower()}_{storm}"
    tif = args.output_dir / f"{stem}.tif"
    png = args.output_dir / f"{stem}_preview.png"
    fig.savefig(tif, dpi=600, pil_kwargs={"compression": "tiff_lzw"})
    fig.savefig(png, dpi=120)
    plt.close(fig)
    with Image.open(tif) as exported:
        if any(abs(value - 600) > 0.01 for value in exported.info["dpi"]):
            raise ValueError("TIFF export does not have 600 DPI")
        size = exported.size
    return ({"storm_id": storm, "name": event["name"], "basin": event.basin,
             "event_case_id": event.case_id, "reference_time": str(event.reference_time),
             "extent": extent, "shared_extent_across_initializations": True, "panels": panels,
             "tif": str(tif), "preview": str(png), "tif_dimensions": size},
            event_cases, selected_tracks, observation_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261021)
    parser.add_argument("--sample-size", type=int, default=3)
    parser.add_argument("--storm-ids", nargs="+", help="Optional explicit SID selection")
    parser.add_argument("--tracks-dir", type=Path, default=ROOT / "outputs/typhoon_tracks")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "outputs/typhoon_track_samples_2026-10-21")
    parser.add_argument("--event-catalog", type=Path, default=ROOT
                        / "data/catalogs/coastal_impact_events_2022_2024_v2026-09-22.csv")
    parser.add_argument("--ibtracs", type=Path, default=ROOT
                        / "data/frozen_sources/ibtracs.since1980.2026-09-22.study_range.csv.gz")
    parser.add_argument("--coastline", type=Path, default=ROOT
                        / "data/raw/coastline/gshhg/2.3.7/GSHHS_shp/i/GSHHS_i_L1.shp")
    parser.add_argument("--font-path", type=Path)
    args = parser.parse_args()
    font_family, font_file = configure_font(args.font_path)
    plt.rcParams.update({"font.family": font_family, "font.size": 16, "axes.labelsize": 16,
                         "axes.titlesize": 16, "xtick.labelsize": 16, "ytick.labelsize": 16,
                         "legend.fontsize": 16, "figure.titlesize": 16, "axes.linewidth": 0.8})
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = {"cases": args.tracks_dir / "cases.csv", "tracks": args.tracks_dir / "tracks.csv",
             "events": args.event_catalog, "ibtracs": args.ibtracs, "coastline": args.coastline}
    cases = pd.read_csv(paths["cases"], keep_default_na=False, na_values=[""])
    tracks = pd.read_csv(paths["tracks"], keep_default_na=False, na_values=[""])
    events = pd.read_csv(paths["events"], keep_default_na=False, na_values=[""])
    for frame, columns in ((cases, ["init_time"]), (tracks, ["init_time", "valid_time"]),
                           (events, ["reference_time"])):
        for column in columns:
            frame[column] = pd.to_datetime(frame[column], utc=True, format="mixed")
    if cases.duplicated(["model", "forecast_case_id"]).any() or tracks.duplicated(
        ["model", "forecast_case_id", "lead_hours"]
    ).any():
        raise ValueError("Duplicate forecast identities")
    population = sorted(cases.storm_id.unique())
    selected = args.storm_ids or np.random.default_rng(args.seed).choice(
        population, size=args.sample_size, replace=False
    ).tolist()
    if len(set(selected)) != len(selected) or set(selected).difference(population):
        raise ValueError("Requested storm IDs must be distinct members of the forecast cohort")
    truth = load_ibtracs_csv(paths["ibtracs"])
    truth = truth.loc[truth.TRACK_TYPE_NORMALIZED.eq("main")]
    reader = Reader(args.coastline)
    try:
        geometries = list(reader.geometries())
    finally:
        reader.close()
    records, sampled_cases, sampled_tracks, observations = [], [], [], []
    for storm in selected:
        event = events.loc[events.storm_id.eq(storm)].sort_values(
            ["reference_time", "case_id"]
        ).iloc[0]
        record, one_cases, one_tracks, observed = render_storm(
            storm, cases, tracks, truth, event, geometries, args
        )
        records.append(record)
        sampled_cases.append(one_cases)
        sampled_tracks.append(one_tracks)
        observations.extend(observed)
        print(f"PLOT {record['name']}: {record['tif']}", flush=True)
    pd.concat(sampled_cases).to_csv(args.output_dir / "sampled_cases.csv", index=False)
    pd.concat(sampled_tracks).to_csv(args.output_dir / "sampled_tracks.csv", index=False)
    pd.DataFrame(observations).to_csv(args.output_dir / "plotted_best_track.csv", index=False)
    manifest = {
        "sampling": ("explicit storm IDs" if args.storm_ids else
                     "uniform without replacement over sorted storm IDs; "
                     "no forecast quality filter"),
        "event_selection": "earliest coastal event per sampled storm; no availability filter",
        "seed": args.seed, "population_size": len(population), "population": population,
        "selected_storm_ids": selected, "font": font_family, "font_file": font_file,
        "font_sha256": sha256(Path(font_file)), "font_size_pt": 16, "titles": False,
        "sampled_case_count": sum(len(frame) for frame in sampled_cases),
        "sampled_planned_points": sum(len(frame) for frame in sampled_tracks),
        "tif_dpi": 600, "preview_dpi": 120, "best_track": "frozen IBTrACS main LAT/LON",
        "source_sha256": {name: sha256(path) for name, path in paths.items()},
        "script_sha256": sha256(Path(__file__)), "figures": records,
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output_dir / "manifest.json", flush=True)


if __name__ == "__main__":
    main()
