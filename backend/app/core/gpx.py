"""GPX processing: parse, resample on a fixed distance grid, smooth elevation, slope stats."""

from __future__ import annotations

import math
from typing import Any

import gpxpy

GRID_INTERVAL_M = 20.0
SMOOTHING_WINDOW_POINTS = 9  # centered rolling mean ~180 m
SLOPE_CLAMP_PCT = 50.0

def slope_brackets_for(slopes: list[float]) -> list[tuple[float, float, str]]:
    """Gradient brackets in 5% steps (with a flat -2..2 bucket), covering exactly the data range."""
    if not slopes:
        return []
    lo_needed = min(slopes)
    hi_needed = max(slopes)

    boundaries = [-2.0, 2.0]
    while boundaries[0] > lo_needed:
        boundaries.insert(0, (boundaries[0] - 3.0) if boundaries[0] == -2.0 else boundaries[0] - 5.0)
    while boundaries[-1] < hi_needed:
        boundaries.append((boundaries[-1] + 3.0) if boundaries[-1] == 2.0 else boundaries[-1] + 5.0)

    return [(lo, hi, f"{lo:.0f}..{hi:.0f}%") for lo, hi in zip(boundaries, boundaries[1:])]


def _bracket_index(s: float, brackets: list[tuple[float, float, str]]) -> int | None:
    for idx, (lo, hi, _label) in enumerate(brackets):
        # last bracket is inclusive of its upper bound so the max slope lands in it
        if lo <= s < hi or (idx == len(brackets) - 1 and s <= hi):
            return idx
    return None


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


def extract_waypoints(xml_text: str) -> list[dict[str, Any]]:
    """Return waypoints (<wpt>) from a GPX file as a list of dicts with lat/lng/name/desc."""
    try:
        gpx = gpxpy.parse(xml_text)
    except Exception:
        return []
    wpts = []
    for w in gpx.waypoints:
        wpts.append({
            "lat": w.latitude,
            "lng": w.longitude,
            "elevation": w.elevation,
            "name": (w.name or "").strip() or None,
            "desc": (w.description or w.comment or "").strip() or None,
        })
    return wpts


def process_gpx(xml_text: str) -> dict[str, Any]:
    """Parse GPX and return processed track + summary stats.

    Returns dict with keys: name, track (column-oriented dict), distance_km,
    elevation_gain_m, elevation_loss_m, min_elevation_m, max_elevation_m, has_elevation,
    waypoints (list of dicts with lat/lng/name/desc).
    """
    points = _extract_points(xml_text)

    # cumulative distance along raw points
    cum_m = [0.0]
    for i in range(1, len(points)):
        d = _haversine_m(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1])
        cum_m.append(cum_m[-1] + d)

    result = _build_track(points, cum_m)
    result["name"] = _extract_name(xml_text)
    result["waypoints"] = extract_waypoints(xml_text)
    return result


def process_streams(streams: dict[str, Any]) -> dict[str, Any]:
    """Build a processed track from Strava activity streams (latlng/altitude/distance).

    Same output shape as process_gpx (name comes from the activity, set by the caller).
    """

    def _stream_data(key: str) -> list[Any] | None:
        entry = streams.get(key) if isinstance(streams, dict) else None
        data = entry.get("data") if isinstance(entry, dict) else None
        return data if isinstance(data, list) and data else None

    latlng = _stream_data("latlng")
    dist_m = _stream_data("distance")
    if not latlng or not dist_m or len(latlng) != len(dist_m):
        raise GPXProcessingError("Activity streams are missing latlng or distance data.")

    altitude = _stream_data("altitude")
    has_altitude = bool(altitude) and len(altitude) == len(latlng)

    points: list[tuple[float, float, float | None]] = []
    cum_m: list[float] = []
    last_m = -1.0
    for i, pair in enumerate(latlng):
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        m = float(dist_m[i])
        if m <= last_m:  # distance stream must be strictly increasing for interpolation
            continue
        last_m = m
        ele = float(altitude[i]) if has_altitude and altitude[i] is not None else None
        points.append((float(pair[0]), float(pair[1]), ele))
        cum_m.append(m)

    if len(points) < 2:
        raise GPXProcessingError("Activity streams contain fewer than 2 GPS points.")

    # normalize so the grid starts at 0
    offset = cum_m[0]
    cum_m = [m - offset for m in cum_m]

    result = _build_track(points, cum_m)
    result["name"] = None
    return result


def _build_track(points: list[tuple[float, float, float | None]], cum_m: list[float]) -> dict[str, Any]:
    """Resample points onto the fixed grid, smooth elevation, compute slope and summary stats."""
    has_elevation = all(p[2] is not None for p in points)

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
            delta = ele[i] - ele[i - 1]
            if delta > 0:
                gain += delta
            else:
                loss -= delta
        gain = round(gain, 1)
        loss = round(loss, 1)
        min_ele = round(min(ele), 1)
        max_ele = round(max(ele), 1)
        ele_smooth = [round(v, 1) for v in ele_smooth]

    track = {
        "interval_m": GRID_INTERVAL_M,
        "n": n,
        "lat": lat,
        "lng": lng,
        "dist_km": dist_km,
        "ele_m": ele_smooth,
        "ele_raw_m": [round(v, 1) for v in ele] if has_elevation else None,
        "slope_pct": slope_pct,
    }

    return {
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
    brackets = slope_brackets_for(slope_pct)
    counts = [0] * len(brackets)
    for s in slope_pct:
        idx = _bracket_index(s, brackets)
        if idx is not None:
            counts[idx] += 1
    total = len(slope_pct)
    out = []
    for (lo, hi, label), count in zip(brackets, counts):
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


def nearest_distance_km(track: dict[str, Any], lat: float, lng: float) -> float:
    """Return the distance_km on the track closest (Haversine) to the given lat/lng."""
    lats = track["lat"]
    lngs = track["lng"]
    dists = track["dist_km"]
    best_idx = min(range(len(lats)), key=lambda i: _haversine_m(lat, lng, lats[i], lngs[i]))
    return float(dists[best_idx])


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
    moving = _stream_data("moving")
    velocity = _stream_data("velocity_smooth")
    cadence = _stream_data("cadence")

    # Stop detection priority: cadence > velocity_smooth > Strava moving boolean.
    # Cadence is non-directional (shuffling at an aid station = 0 spm regardless of GPS drift).
    # Threshold of 20 spm sits well below slow hiking (~70 spm) and catches pacing/shuffling.
    # None cadence values (device gap) are treated as moving to avoid false stops.
    # Runs shorter than MIN_STOP_DURATION_S are ignored (corners / GPS jitter).
    STOP_CADENCE_SPM = 20
    STOP_SPEED_MS = 0.5
    MIN_STOP_DURATION_S = 5
    has_cadence = bool(cadence) and len(cadence) == len(time_s)
    has_velocity = bool(velocity) and len(velocity) == len(time_s)
    has_moving_data = has_cadence or has_velocity or (bool(moving) and len(moving) == len(time_s))

    cum_moving_s: list[float] | None = None
    if has_moving_data:
        if has_cadence:
            raw_moving: list[bool] = [
                c is None or float(c) >= STOP_CADENCE_SPM for c in cadence
            ]
        elif has_velocity:
            raw_moving = [float(v) >= STOP_SPEED_MS for v in velocity]
        else:
            raw_moving = [bool(v) for v in moving]  # type: ignore[assignment]

        filtered_moving = list(raw_moving)
        i = 0
        while i < len(filtered_moving):
            if not filtered_moving[i]:
                j = i + 1
                while j < len(filtered_moving) and not filtered_moving[j]:
                    j += 1
                stop_end = j - 1
                stop_dur = float(time_s[stop_end]) - float(time_s[i]) if stop_end < len(time_s) else 0.0
                if stop_dur < MIN_STOP_DURATION_S:
                    for k in range(i, j):
                        filtered_moving[k] = True
                i = j
            else:
                i += 1

        cum_moving_s = [0.0]
        for i in range(1, len(time_s)):
            dt = float(time_s[i]) - float(time_s[i - 1])
            cum_moving_s.append(cum_moving_s[-1] + (dt if filtered_moving[i] and dt > 0 else 0.0))

    route_dist_km = track["dist_km"]
    n = len(route_dist_km)
    route_total_km = route_dist_km[-1]
    activity_total_km = float(dist_m[-1]) / 1000.0

    mismatch_pct = abs(activity_total_km - route_total_km) / route_total_km * 100.0 if route_total_km > 0 else 0.0

    # time (elapsed + moving), HR and cadence at each route grid distance, interpolated over the activity distance stream
    grid_time: list[float | None] = []
    grid_moving_time: list[float | None] = []
    grid_hr: list[float | None] = []
    grid_cadence: list[float | None] = []
    j = 0
    has_hr = hr and len(hr) == len(dist_m)
    has_cad = cadence and len(cadence) == len(dist_m)
    for dk in route_dist_km:
        target_m = dk * 1000.0
        if target_m > dist_m[-1]:
            grid_time.append(None)
            grid_moving_time.append(None)
            grid_hr.append(None)
            grid_cadence.append(None)
            continue
        while j < len(dist_m) - 2 and dist_m[j + 1] < target_m:
            j += 1
        x0, x1 = float(dist_m[j]), float(dist_m[j + 1])
        grid_time.append(_interp(target_m, x0, x1, float(time_s[j]), float(time_s[j + 1])))
        if cum_moving_s is not None:
            grid_moving_time.append(_interp(target_m, x0, x1, cum_moving_s[j], cum_moving_s[j + 1]))
        else:
            grid_moving_time.append(grid_time[-1])
        if has_hr:
            try:
                grid_hr.append(_interp(target_m, x0, x1, float(hr[j]), float(hr[j + 1])))
            except (TypeError, ValueError):
                grid_hr.append(None)
        else:
            grid_hr.append(None)
        if has_cad:
            try:
                v0, v1 = cadence[j], cadence[j + 1]
                grid_cadence.append(_interp(target_m, x0, x1, float(v0), float(v1)) if v0 is not None and v1 is not None else None)
            except (TypeError, ValueError):
                grid_cadence.append(None)
        else:
            grid_cadence.append(None)

    # moving pace per grid segment (min/km), stops excluded; lightly smoothed over ~9 points like elevation
    seg_pace: list[float | None] = [None] * n
    for i in range(1, n):
        t0, t1 = grid_moving_time[i - 1], grid_moving_time[i]
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

    # smooth cadence the same way
    seg_cadence: list[float | None] = grid_cadence
    if any(v is not None for v in grid_cadence):
        half = SMOOTHING_WINDOW_POINTS // 2
        smoothed_cad: list[float | None] = []
        for i in range(n):
            window = [v for v in grid_cadence[max(0, i - half):min(n, i + half + 1)] if v is not None]
            smoothed_cad.append(round(sum(window) / len(window), 1) if window else None)
        seg_cadence = smoothed_cad

    # per-km splits (elapsed time, stopped time shown separately)
    # prefer raw elevation for gain/loss (smoothed version undercounts repeated short climbs)
    ele_m = track.get("ele_raw_m") or track.get("ele_m")
    splits: list[dict[str, Any]] = []
    km = 1
    prev_time = grid_time[0]
    prev_moving = grid_moving_time[0]
    prev_idx = 0
    for i in range(1, n):
        if route_dist_km[i] >= km or i == n - 1:
            t = grid_time[i]
            mt = grid_moving_time[i]
            if prev_time is not None and t is not None and route_dist_km[i] > route_dist_km[prev_idx]:
                dur_s = t - prev_time
                moving_s = (mt - prev_moving) if (mt is not None and prev_moving is not None) else dur_s
                stopped_s = max(0.0, dur_s - moving_s)
                d_km = route_dist_km[i] - route_dist_km[prev_idx]
                hr_window = [h for h in grid_hr[prev_idx:i + 1] if h is not None]
                gain = loss = None
                if ele_m:
                    gain = loss = 0.0
                    for k in range(prev_idx + 1, i + 1):
                        delta = ele_m[k] - ele_m[k - 1]
                        if delta > 0:
                            gain += delta
                        else:
                            loss -= delta
                    gain = round(gain)
                    loss = round(loss)
                splits.append(
                    {
                        "km": km if route_dist_km[i] >= km else round(route_dist_km[i], 2),
                        "duration_s": round(dur_s),
                        "stopped_s": round(stopped_s),
                        "pace_min_per_km": round(moving_s / 60.0 / d_km, 2),
                        "avg_hr_bpm": round(sum(hr_window) / len(hr_window), 0) if hr_window else None,
                        "d_plus_m": gain,
                        "d_minus_m": loss,
                    }
                )
            prev_time = grid_time[i]
            prev_moving = grid_moving_time[i]
            prev_idx = i
            km += 1

    # per-slope-bracket pace/HR (uses the route's slope at each grid point)
    bracket_stats: list[dict[str, Any]] = []
    slope_pct = track.get("slope_pct")
    if slope_pct:
        interval_km = float(track.get("interval_m", GRID_INTERVAL_M)) / 1000.0
        brackets = slope_brackets_for(slope_pct)
        for bracket_idx, (lo, hi, label) in enumerate(brackets):
            paces: list[float] = []
            hrs: list[float] = []
            count = 0
            for i in range(n):
                if _bracket_index(slope_pct[i], brackets) == bracket_idx:
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

    total_elapsed_s = round(float(time_s[-1]) - float(time_s[0]))
    total_moving_s = round(cum_moving_s[-1]) if cum_moving_s is not None else total_elapsed_s

    return {
        "route_distance_km": round(route_total_km, 2),
        "activity_distance_km": round(activity_total_km, 2),
        "distance_mismatch_pct": round(mismatch_pct, 1),
        "distance_mismatch_warning": mismatch_pct > 5.0,
        "has_moving_data": has_moving_data,
        "total_elapsed_s": total_elapsed_s,
        "total_moving_s": total_moving_s,
        "total_stopped_s": max(0, total_elapsed_s - total_moving_s),
        "pace_min_per_km": seg_pace,
        "hr_bpm": [round(h, 0) if h is not None else None for h in grid_hr],
        "cadence_spm": [round(c, 0) if c is not None else None for c in seg_cadence],
        "km_splits": splits,
        "bracket_stats": bracket_stats,
        "actual_latlng": actual_latlng,
    }


def _km_splits_from_track(track: dict[str, Any]) -> list[dict[str, Any]]:
    """Compute per-km D+/D- from the processed track grid."""
    dist = track.get("dist_km") or []
    ele = track.get("ele_m") or []
    if not dist or not ele or len(dist) != len(ele):
        return []
    total_km = dist[-1]
    n_km = max(1, int(math.ceil(total_km)))
    splits: list[dict[str, Any]] = []
    for k in range(n_km):
        lo_km = float(k)
        hi_km = float(k + 1)
        idxs = [i for i, d in enumerate(dist) if lo_km <= d < hi_km]
        if not idxs:
            # last partial km — grab everything from lo_km onwards
            idxs = [i for i, d in enumerate(dist) if d >= lo_km]
        if len(idxs) < 2:
            continue
        d_plus = 0.0
        d_minus = 0.0
        for i in range(idxs[0] + 1, idxs[-1] + 1):
            if ele[i] is not None and ele[i - 1] is not None:
                diff = float(ele[i]) - float(ele[i - 1])
                if diff > 0:
                    d_plus += diff
                else:
                    d_minus += abs(diff)
        splits.append({"km": k + 1, "d_plus_m": round(d_plus), "d_minus_m": round(d_minus)})
    return splits


def build_route_text_summary(
    route: Any,
    markers: list[Any],
    histogram: list[dict[str, Any]],
    track: dict[str, Any] | None = None,
) -> str:
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
        if track:
            km_splits = _km_splits_from_track(track)
            if km_splits:
                lines.append("Per-km elevation profile (D+/D-):")
                for s in km_splits:
                    lines.append(f"  km {s['km']}: +{s['d_plus_m']}m/-{s['d_minus_m']}m")
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
