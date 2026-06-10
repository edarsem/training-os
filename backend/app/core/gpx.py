"""GPX processing: parse, resample on a fixed distance grid, smooth elevation, slope stats."""

from __future__ import annotations

import math
from typing import Any

import gpxpy

GRID_INTERVAL_M = 20.0
SMOOTHING_WINDOW_POINTS = 9  # centered rolling mean ~180 m
SLOPE_CLAMP_PCT = 50.0

def _build_slope_brackets() -> list[tuple[float | None, float | None, str]]:
    boundaries = [-40.0, -35.0, -30.0, -25.0, -20.0, -15.0, -10.0, -5.0, -2.0,
                  2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0]
    brackets: list[tuple[float | None, float | None, str]] = [(None, boundaries[0], f"<{boundaries[0]:.0f}%")]
    for lo, hi in zip(boundaries, boundaries[1:]):
        brackets.append((lo, hi, f"{lo:.0f}..{hi:.0f}%"))
    brackets.append((boundaries[-1], None, f">{boundaries[-1]:.0f}%"))
    return brackets


SLOPE_BRACKETS = _build_slope_brackets()


class GPXProcessingError(ValueError):
    pass


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _extract_points(xml_text: str) -> list[tuple[float, float, float | None]]:
    try:
        gpx = gpxpy.parse(xml_text)
    except Exception as exc:
        raise GPXProcessingError(f"Could not parse GPX file: {exc}") from exc

    points: list[tuple[float, float, float | None]] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                points.append((p.latitude, p.longitude, p.elevation))
    if not points:
        for route in gpx.routes:
            for p in route.points:
                points.append((p.latitude, p.longitude, p.elevation))

    if len(points) < 2:
        raise GPXProcessingError("GPX file contains fewer than 2 track points.")
    return points


def _extract_name(xml_text: str) -> str | None:
    try:
        gpx = gpxpy.parse(xml_text)
    except Exception:
        return None
    if gpx.name and gpx.name.strip():
        return gpx.name.strip()
    for track in gpx.tracks:
        if track.name and track.name.strip():
            return track.name.strip()
    for route in gpx.routes:
        if route.name and route.name.strip():
            return route.name.strip()
    return None


def _interp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    if x1 <= x0:
        return y0
    t = (x - x0) / (x1 - x0)
    return y0 + t * (y1 - y0)


def _rolling_mean(values: list[float], window: int) -> list[float]:
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(sum(values[lo:hi]) / (hi - lo))
    return out


def process_gpx(xml_text: str) -> dict[str, Any]:
    """Parse GPX and return processed track + summary stats.

    Returns dict with keys: name, track (column-oriented dict), distance_km,
    elevation_gain_m, elevation_loss_m, min_elevation_m, max_elevation_m, has_elevation.
    """
    points = _extract_points(xml_text)
    has_elevation = all(p[2] is not None for p in points)

    # cumulative distance along raw points
    cum_m = [0.0]
    for i in range(1, len(points)):
        d = _haversine_m(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        cum_m.append(cum_m[-1] + d)

    total_m = cum_m[-1]
    if total_m <= 0:
        raise GPXProcessingError("GPX track has zero total distance.")

    n_grid = int(total_m // GRID_INTERVAL_M) + 1
    grid_m = [i * GRID_INTERVAL_M for i in range(n_grid)]
    if grid_m[-1] < total_m:
        grid_m.append(total_m)
    n = len(grid_m)

    lat: list[float] = []
    lng: list[float] = []
    ele: list[float] = []
    j = 0
    for gm in grid_m:
        while j < len(cum_m) - 2 and cum_m[j + 1] < gm:
            j += 1
        x0, x1 = cum_m[j], cum_m[j + 1]
        p0, p1 = points[j], points[j + 1]
        lat.append(round(_interp(gm, x0, x1, p0[0], p1[0]), 6))
        lng.append(round(_interp(gm, x0, x1, p0[1], p1[1]), 6))
        if has_elevation:
            ele.append(_interp(gm, x0, x1, float(p0[2]), float(p1[2])))

    dist_km = [round(gm / 1000.0, 3) for gm in grid_m]

    ele_smooth: list[float] | None = None
    slope_pct: list[float] | None = None
    gain = loss = None
    min_ele = max_ele = None

    if has_elevation:
        ele_smooth = _rolling_mean(ele, SMOOTHING_WINDOW_POINTS)

        slope_pct = []
        for i in range(n):
            lo = max(0, i - 1)
            hi = min(n - 1, i + 1)
            dd = grid_m[hi] - grid_m[lo]
            if dd <= 0:
                slope_pct.append(0.0)
                continue
            s = (ele_smooth[hi] - ele_smooth[lo]) / dd * 100.0
            slope_pct.append(round(max(-SLOPE_CLAMP_PCT, min(SLOPE_CLAMP_PCT, s)), 2))

        gain = loss = 0.0
        for i in range(1, n):
            delta = ele_smooth[i] - ele_smooth[i - 1]
            if delta > 0:
                gain += delta
            else:
                loss -= delta
        gain = round(gain, 1)
        loss = round(loss, 1)
        min_ele = round(min(ele_smooth), 1)
        max_ele = round(max(ele_smooth), 1)
        ele_smooth = [round(v, 1) for v in ele_smooth]

    track = {
        "interval_m": GRID_INTERVAL_M,
        "n": n,
        "lat": lat,
        "lng": lng,
        "dist_km": dist_km,
        "ele_m": ele_smooth,
        "slope_pct": slope_pct,
    }

    return {
        "name": _extract_name(xml_text),
        "track": track,
        "distance_km": round(total_m / 1000.0, 3),
        "elevation_gain_m": gain,
        "elevation_loss_m": loss,
        "min_elevation_m": min_ele,
        "max_elevation_m": max_ele,
        "has_elevation": has_elevation,
    }


def compute_slope_histogram(slope_pct: list[float] | None, interval_m: float) -> list[dict[str, Any]]:
    """Distance per gradient bracket. Returns list of {label, min_pct, max_pct, km, pct_of_route}."""
    if not slope_pct:
        return []
    counts = [0] * len(SLOPE_BRACKETS)
    for s in slope_pct:
        for idx, (lo, hi, _label) in enumerate(SLOPE_BRACKETS):
            if (lo is None or s >= lo) and (hi is None or s < hi):
                counts[idx] += 1
                break
    total = len(slope_pct)
    out = []
    for (lo, hi, label), count in zip(SLOPE_BRACKETS, counts):
        out.append(
            {
                "label": label,
                "min_pct": lo,
                "max_pct": hi,
                "km": round(count * interval_m / 1000.0, 2),
                "pct_of_route": round(count / total * 100.0, 1),
            }
        )
    return out


def interpolate_point_at_distance(track: dict[str, Any], distance_km: float) -> dict[str, Any]:
    """Interpolate lat/lng/elevation at a given distance along the track."""
    dist = track["dist_km"]
    n = len(dist)
    d = max(dist[0], min(dist[-1], float(distance_km)))

    interval_km = float(track.get("interval_m", GRID_INTERVAL_M)) / 1000.0
    i = min(n - 2, max(0, int(d / interval_km))) if interval_km > 0 else 0
    while i < n - 2 and dist[i + 1] < d:
        i += 1
    while i > 0 and dist[i] > d:
        i -= 1

    x0, x1 = dist[i], dist[i + 1]
    lat = _interp(d, x0, x1, track["lat"][i], track["lat"][i + 1])
    lng = _interp(d, x0, x1, track["lng"][i], track["lng"][i + 1])
    ele = None
    if track.get("ele_m"):
        ele = round(_interp(d, x0, x1, track["ele_m"][i], track["ele_m"][i + 1]), 1)
    return {"distance_km": d, "lat": round(lat, 6), "lng": round(lng, 6), "elevation_m": ele}


def compare_route_with_activity(track: dict[str, Any], streams: dict[str, Any]) -> dict[str, Any]:
    """Compare a planned route with an actual activity, aligned by cumulative distance.

    The activity is resampled onto the route's distance grid via its own distance
    stream (no map-matching). Returns aligned pace/HR arrays, per-km splits,
    per-slope-bracket stats, the actual polyline, and a distance-mismatch warning.
    """

    def _stream_data(key: str) -> list[Any] | None:
        entry = streams.get(key) if isinstance(streams, dict) else None
        data = entry.get("data") if isinstance(entry, dict) else None
        return data if isinstance(data, list) and data else None

    dist_m = _stream_data("distance")
    time_s = _stream_data("time")
    if not dist_m or not time_s or len(dist_m) != len(time_s):
        raise GPXProcessingError("Activity streams are missing distance or time data.")

    latlng = _stream_data("latlng")
    hr = _stream_data("heartrate")

    route_dist_km = track["dist_km"]
    n = len(route_dist_km)
    route_total_km = route_dist_km[-1]
    activity_total_km = float(dist_m[-1]) / 1000.0

    mismatch_pct = abs(activity_total_km - route_total_km) / route_total_km * 100.0 if route_total_km > 0 else 0.0

    # time (and HR) at each route grid distance, interpolated over the activity distance stream
    grid_time: list[float | None] = []
    grid_hr: list[float | None] = []
    j = 0
    for dk in route_dist_km:
        target_m = dk * 1000.0
        if target_m > dist_m[-1]:
            grid_time.append(None)
            grid_hr.append(None)
            continue
        while j < len(dist_m) - 2 and dist_m[j + 1] < target_m:
            j += 1
        x0, x1 = float(dist_m[j]), float(dist_m[j + 1])
        grid_time.append(_interp(target_m, x0, x1, float(time_s[j]), float(time_s[j + 1])))
        if hr and len(hr) == len(dist_m):
            try:
                grid_hr.append(_interp(target_m, x0, x1, float(hr[j]), float(hr[j + 1])))
            except (TypeError, ValueError):
                grid_hr.append(None)
        else:
            grid_hr.append(None)

    # pace per grid segment (min/km), lightly smoothed over ~9 points like elevation
    seg_pace: list[float | None] = [None] * n
    for i in range(1, n):
        t0, t1 = grid_time[i - 1], grid_time[i]
        dd_km = route_dist_km[i] - route_dist_km[i - 1]
        if t0 is None or t1 is None or dd_km <= 0:
            continue
        dt_min = (t1 - t0) / 60.0
        if dt_min <= 0:
            continue
        seg_pace[i] = dt_min / dd_km
    valid = [p for p in seg_pace if p is not None]
    if valid:
        smoothed: list[float | None] = []
        half = SMOOTHING_WINDOW_POINTS // 2
        for i in range(n):
            window = [p for p in seg_pace[max(0, i - half):min(n, i + half + 1)] if p is not None]
            smoothed.append(round(sum(window) / len(window), 3) if window else None)
        seg_pace = smoothed

    # per-km splits
    splits: list[dict[str, Any]] = []
    km = 1
    prev_time = grid_time[0]
    prev_idx = 0
    for i in range(1, n):
        if route_dist_km[i] >= km or i == n - 1:
            t = grid_time[i]
            if prev_time is not None and t is not None and route_dist_km[i] > route_dist_km[prev_idx]:
                dur_min = (t - prev_time) / 60.0
                d_km = route_dist_km[i] - route_dist_km[prev_idx]
                hr_window = [h for h in grid_hr[prev_idx:i + 1] if h is not None]
                splits.append(
                    {
                        "km": km if route_dist_km[i] >= km else round(route_dist_km[i], 2),
                        "pace_min_per_km": round(dur_min / d_km, 2),
                        "avg_hr_bpm": round(sum(hr_window) / len(hr_window), 0) if hr_window else None,
                    }
                )
            prev_time = grid_time[i]
            prev_idx = i
            km += 1

    # per-slope-bracket pace/HR (uses the route's slope at each grid point)
    bracket_stats: list[dict[str, Any]] = []
    slope_pct = track.get("slope_pct")
    if slope_pct:
        interval_km = float(track.get("interval_m", GRID_INTERVAL_M)) / 1000.0
        for lo, hi, label in SLOPE_BRACKETS:
            paces: list[float] = []
            hrs: list[float] = []
            count = 0
            for i in range(n):
                s = slope_pct[i]
                if (lo is None or s >= lo) and (hi is None or s < hi):
                    count += 1
                    if seg_pace[i] is not None:
                        paces.append(seg_pace[i])
                    if grid_hr[i] is not None:
                        hrs.append(grid_hr[i])
            if count == 0:
                continue
            bracket_stats.append(
                {
                    "label": label,
                    "min_pct": lo,
                    "max_pct": hi,
                    "km": round(count * interval_km, 2),
                    "avg_pace_min_per_km": round(sum(paces) / len(paces), 2) if paces else None,
                    "avg_hr_bpm": round(sum(hrs) / len(hrs), 0) if hrs else None,
                }
            )

    # actual polyline, downsampled to ~2000 points for the map overlay
    actual_latlng: list[list[float]] = []
    if latlng:
        step = max(1, len(latlng) // 2000)
        actual_latlng = [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in latlng[::step] if isinstance(p, (list, tuple)) and len(p) == 2]

    return {
        "route_distance_km": round(route_total_km, 2),
        "activity_distance_km": round(activity_total_km, 2),
        "distance_mismatch_pct": round(mismatch_pct, 1),
        "distance_mismatch_warning": mismatch_pct > 5.0,
        "pace_min_per_km": seg_pace,
        "hr_bpm": [round(h, 0) if h is not None else None for h in grid_hr],
        "km_splits": splits,
        "bracket_stats": bracket_stats,
        "actual_latlng": actual_latlng,
    }


def build_route_text_summary(route: Any, markers: list[Any], histogram: list[dict[str, Any]]) -> str:
    """Compact text block describing a route for the coach LLM. Never includes track arrays."""
    lines = [f"Route: {route.name} (id {route.id})"]
    lines.append(f"Distance: {route.distance_km:.1f} km")
    if route.has_elevation:
        lines.append(
            f"Elevation: +{route.elevation_gain_m:.0f} m / -{route.elevation_loss_m:.0f} m "
            f"(min {route.min_elevation_m:.0f} m, max {route.max_elevation_m:.0f} m)"
        )
        nonzero = [b for b in histogram if b["km"] > 0]
        if nonzero:
            lines.append("Gradient distribution:")
            for b in nonzero:
                lines.append(f"  {b['label']}: {b['km']} km ({b['pct_of_route']}% of route)")
    else:
        lines.append("No elevation data in this route's GPX file.")

    if markers:
        lines.append("Markers:")
        for m in sorted(markers, key=lambda m: float(m.distance_km or 0)):
            label = (m.label or "").strip()
            note = (m.note or "").strip()
            text = f"  km {float(m.distance_km):.1f} [{m.kind}]"
            if label:
                text += f" {label}"
            if note:
                text += f": {note}"
            lines.append(text)

    if route.notes and str(route.notes).strip():
        lines.append(f"Route notes: {str(route.notes).strip()}")

    return "\n".join(lines)
