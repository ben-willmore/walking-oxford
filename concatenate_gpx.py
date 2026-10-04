#!/usr/bin/env python3
"""Concatenate Apple Health GPX tracks into one GPX file of routes.

Each input GPX file becomes one <rte> element.  An optional rectangle can
remove points whose longitude and latitude fall within its bounds.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import math
from pathlib import Path
import xml.etree.ElementTree as ET

GPX_NS = "http://www.topografix.com/GPX/1/1"
ET.register_namespace("", GPX_NS)


def tag(name: str) -> str:
    return f"{{{GPX_NS}}}{name}"


def inside_rectangle(point: ET.Element, rectangle: tuple[float, float, float, float]) -> bool:
    min_lon, min_lat, max_lon, max_lat = rectangle
    try:
        lon = float(point.attrib["lon"])
        lat = float(point.attrib["lat"])
    except (KeyError, ValueError):
        return False
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def inside_quadrilateral(
    point: ET.Element, quadrilateral: list[tuple[float, float]]
) -> bool:
    """Return whether a point is inside a quadrilateral of (lat, lon) corners."""
    try:
        x = float(point.attrib["lon"])
        y = float(point.attrib["lat"])
    except (KeyError, ValueError):
        return False
    inside = False
    for (lat1, lon1), (lat2, lon2) in zip(quadrilateral, quadrilateral[1:] + quadrilateral[:1]):
        if (lat1 > y) != (lat2 > y):
            crossing_lon = (lon2 - lon1) * (y - lat1) / (lat2 - lat1) + lon1
            if x < crossing_lon:
                inside = not inside
    return inside


def inside_any_quadrilateral(
    point: ET.Element, quadrilaterals: list[list[tuple[float, float]]]
) -> bool:
    return any(inside_quadrilateral(point, quadrilateral) for quadrilateral in quadrilaterals)


def parse_quadrilateral(values: list[str]) -> list[tuple[float, float]]:
    if len(values) != 4:
        raise ValueError("a quadrilateral needs four LATITUDE,LONGITUDE corners")
    try:
        corners = []
        for value in values:
            lat, lon = value.split(",")
            corners.append((float(lat), float(lon)))
        return corners
    except (ValueError, TypeError):
        raise ValueError("each --exclude corner must be LATITUDE,LONGITUDE") from None


def parse_include_rectangle(values: list[str]) -> tuple[float, float, float, float]:
    if len(values) != 2:
        raise ValueError("--include needs two LATITUDE,LONGITUDE corners")
    try:
        corners = []
        for value in values:
            lat, lon = value.split(",")
            corners.append((float(lat), float(lon)))
        (lat1, lon1), (lat2, lon2) = corners
        return min(lon1, lon2), min(lat1, lat2), max(lon1, lon2), max(lat1, lat2)
    except (ValueError, TypeError):
        raise ValueError("each --include corner must be LATITUDE,LONGITUDE") from None


def distance_metres(first: ET.Element, second: ET.Element) -> float:
    """Return the approximate great-circle distance between two GPX points."""
    earth_radius = 6_371_000
    lat1, lon1 = math.radians(float(first.attrib["lat"])), math.radians(float(first.attrib["lon"]))
    lat2, lon2 = math.radians(float(second.attrib["lat"])), math.radians(float(second.attrib["lon"]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * earth_radius * math.asin(math.sqrt(a))


def route_datetime(path: Path) -> datetime:
    return datetime.strptime(path.stem.removeprefix("route_"), "%Y-%m-%d_%I.%M%p")


def load_activity_times(path: Path) -> list[tuple[datetime, str]]:
    root = ET.parse(path).getroot()
    workouts = []
    for workout in root.findall("Workout"):
        activity = workout.attrib.get("workoutActivityType", "")
        end_date = workout.attrib.get("endDate")
        if activity and end_date:
            # Compare local clock times; route filenames do not contain timezone data.
            end = datetime.strptime(end_date.rsplit(" ", 1)[0], "%Y-%m-%d %H:%M:%S")
            workouts.append((end, activity))
    return workouts


def activity_for_route(path: Path, workouts: list[tuple[datetime, str]]) -> str | None:
    route_time = route_datetime(path)
    if not workouts:
        return None
    end, activity = min(workouts, key=lambda item: abs(item[0] - route_time))
    return activity if abs(end - route_time).total_seconds() <= 120 else None


def route_from_file(
    path: Path,
    excluded_quadrilaterals: list[list[tuple[float, float]]],
    include_rectangle: tuple[float, float, float, float] | None,
    min_distance_metres: float,
) -> ET.Element:
    source_root = ET.parse(path).getroot()
    route = ET.Element(tag("rte"))

    track = source_root.find(tag("trk"))
    if track is None:
        raise ValueError("does not contain a <trk> element")

    name = track.find(tag("name"))
    if name is not None and name.text:
        ET.SubElement(route, tag("name")).text = name.text
    else:
        ET.SubElement(route, tag("name")).text = path.stem

    points = list(track.iter(tag("trkpt")))
    filtered_points = []
    for point in points:
        if include_rectangle is not None and not inside_rectangle(point, include_rectangle):
            continue
        filtered_points.append(point)
    points = filtered_points

    # For --exclude, trim only the leading and trailing points inside the
    # circle. Points inside it in the middle of a route are retained.
    if excluded_quadrilaterals:
        first = 0
        while first < len(points) and inside_any_quadrilateral(points[first], excluded_quadrilaterals):
            first += 1
        last = len(points)
        while last > first and inside_any_quadrilateral(points[last - 1], excluded_quadrilaterals):
            last -= 1
        points = points[first:last]

    last_kept_point = None
    for point in points:
        if last_kept_point is not None and distance_metres(last_kept_point, point) < min_distance_metres:
            continue
        # The point and its children are read-only after this; a shallow copy
        # avoids an expensive deep copy of every extension element.
        copied_point = copy.copy(point)
        copied_point.tag = tag("rtept")
        route.append(copied_point)
        last_kept_point = point

    return route


def concatenate(
    input_dir: Path,
    output: Path,
    excluded_quadrilaterals: list[list[tuple[float, float]]],
    include_rectangle: tuple[float, float, float, float] | None,
    min_distance_metres: float,
    activity_type: str,
    workouts: list[tuple[datetime, str]],
) -> tuple[int, int]:
    files = sorted(input_dir.glob("*.gpx"))
    if not files:
        raise FileNotFoundError(f"No .gpx files found in {input_dir}")

    root = ET.Element(tag("gpx"), {"version": "1.1", "creator": "concatenate_gpx.py"})
    count = 0
    empty = 0
    for path in files:
        if activity_type != "all":
            activity = activity_for_route(path, workouts)
            wanted = f"HKWorkoutActivityType{activity_type.title()}"
            if activity != wanted:
                continue
        try:
            route = route_from_file(path, excluded_quadrilaterals, include_rectangle, min_distance_metres)
            if route.findall(tag("rtept")):
                root.append(route)
                count += 1
            else:
                empty += 1
        except (ET.ParseError, ValueError) as error:
            print(f"Skipping {path}: {error}")

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return count, empty


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("apple_health_export/workout-routes"))
    parser.add_argument("--output", type=Path, default=Path("all-routes.gpx"))
    parser.add_argument(
        "--exclude",
        nargs=4, action="append",
        metavar=("LAT1,LON1", "LAT2,LON2", "LAT3,LON3", "LAT4,LON4"),
        help="trim points inside this quadrilateral; corners are LATITUDE,LONGITUDE pairs in perimeter order",
    )
    parser.add_argument(
        "--include",
        nargs=2,
        metavar=("LAT1,LON1", "LAT2,LON2"),
        help="keep only points inside the rectangle defined by two LATITUDE,LONGITUDE corners",
    )
    parser.add_argument(
        "--min-distance-metres",
        type=float,
        default=0.0,
        metavar="N",
        help="keep no consecutive points closer than N metres (default: keep all points)",
    )
    parser.add_argument(
        "--activity-type",
        choices=("all", "walking", "cycling"),
        default="all",
        help="include only walking or cycling routes (default: all)",
    )
    parser.add_argument(
        "--health-export",
        type=Path,
        default=Path("apple_health_export/export.xml"),
        help="Apple Health export.xml used for activity classification",
    )
    args = parser.parse_args()
    if args.min_distance_metres < 0:
        parser.error("--min-distance-metres must be non-negative")
    try:
        excluded_quadrilaterals = [parse_quadrilateral(values) for values in args.exclude] if args.exclude else []
    except ValueError as error:
        parser.error(str(error))
    try:
        include_rectangle = parse_include_rectangle(args.include) if args.include else None
    except ValueError as error:
        parser.error(str(error))
    for option_name, bounds in (("--include", include_rectangle),):
        if bounds and (bounds[0] > bounds[2] or bounds[1] > bounds[3]):
            parser.error(f"{option_name} bounds must be min_lon min_lat max_lon max_lat")
    workouts = load_activity_times(args.health_export) if args.activity_type != "all" else []
    count, empty = concatenate(
        args.input_dir,
        args.output,
        excluded_quadrilaterals,
        include_rectangle,
        args.min_distance_metres,
        args.activity_type,
        workouts,
    )
    size_bytes = args.output.stat().st_size
    print(
        f"Wrote {count} routes to {args.output} ({empty} empty routes omitted); "
        f"file size: {size_bytes:,} bytes ({size_bytes / 1_000_000:.2f} MB)"
    )


if __name__ == "__main__":
    main()
