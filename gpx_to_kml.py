#!/usr/bin/env python3
"""Convert GPX files in a directory to a single KML with colored routes."""

from __future__ import annotations

import argparse
import colorsys
import html
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from concatenate_gpx import (
    activity_for_route,
    inside_rectangle,
    load_activity_times,
    parse_include_rectangle,
    parse_quadrilateral,
    route_datetime,
    route_from_file,
)

GPX_NS = "http://www.topografix.com/GPX/1/1"
KML_NS = "http://www.opengis.net/kml/2.2"
ET.register_namespace("", KML_NS)

def kml_tag(name: str) -> str:
    return f"{{{KML_NS}}}{name}"


def gpx_tag(name: str) -> str:
    return f"{{{GPX_NS}}}{name}"


def colormap_color(name: str, position: float) -> str:
    """Return a Matplotlib colormap color as KML aabbggrr."""
    if name == "winter":
        red, green, blue = 0, round(255 * position), round(255 * (1 - position))
    elif name == "autumn":
        red, green, blue = 255, round(255 * position), 0
    elif name == "rainbow":
        # Full hue sweep with high saturation and a slightly darker value.
        red_float, green_float, blue_float = colorsys.hsv_to_rgb(position, 1.0, 0.80)
        red = round(255 * red_float)
        green = round(255 * green_float)
        blue = round(255 * blue_float)
    else:  # reversed cool: cyan at x=0, magenta at x=1
        red, green, blue = round(255 * position), round(255 * (1 - position)), 255
    return f"ff{blue:02x}{green:02x}{red:02x}"


def kml_to_svg_color(kml_color: str) -> str:
    """Convert KML aabbggrr color notation to SVG #rrggbb."""
    value = kml_color.replace(" ", "")
    return f"#{value[6:8]}{value[4:6]}{value[2:4]}"


def bearing_from_center(points: list[tuple[str, str]], center: tuple[float, float]) -> float:
    #midpoint = points[len(points) // 2]
    midpoint = points[0]
    if isinstance(midpoint, ET.Element):
        lon, lat = float(midpoint.attrib["lon"]), float(midpoint.attrib["lat"])
    else:
        lon, lat = map(float, midpoint)
    lat1, lon1 = map(math.radians, center)
    lat2, lon2 = math.radians(lat), math.radians(lon)
    return (math.degrees(math.atan2(
        math.sin(lon2 - lon1) * math.cos(lat2),
        math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1),
    )) + 360) % 360


def make_style(document: ET.Element, index: int, color: str) -> str:
    style_id = f"route-style-{index}"
    style = ET.SubElement(document, kml_tag("Style"), {"id": style_id})
    line_style = ET.SubElement(style, kml_tag("LineStyle"))
    ET.SubElement(line_style, kml_tag("color")).text = color.replace(" ", "")
    ET.SubElement(line_style, kml_tag("width")).text = "4"
    return f"#{style_id}"


def load_excluded_routes(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def parse_coordinate(value: str) -> tuple[float, float]:
    try:
        lat, lon = value.split(",")
        return float(lat), float(lon)
    except (ValueError, TypeError):
        raise ValueError("coordinate must be LATITUDE,LONGITUDE") from None


def speed_from_point(point: ET.Element) -> float | None:
    for child in point.iter():
        if child.tag.rsplit("}", 1)[-1] == "speed" and child.text:
            try:
                return float(child.text)
            except ValueError:
                return None
    return None


def speed_color(speed: float | None, minimum: float, maximum: float) -> str:
    if speed is None or maximum <= minimum:
        return "#666666"
    position = max(0.0, min(1.0, (speed - minimum) / (maximum - minimum)))
    if position < 0.5:
        t = position * 2
        red, green = 255, round(255 * t)
    else:
        t = (position - 0.5) * 2
        red, green = round(255 * (1 - t)), 255
    return f"#{red:02x}{green:02x}00"


def convert_svg(
    input_dir: Path,
    output: Path,
    excluded_quadrilaterals: list[list[tuple[float, float]]],
    include_rectangle: tuple[float, float, float, float] | None,
    min_distance_metres: float,
    activity_type: str,
    workouts: list[tuple],
    excluded_routes: set[str],
    colormap: str,
    color_order: str,
    color_center: tuple[float, float] | None,
    include_markers: bool,
    route_marker_size: float,
    segment_opacity: float,
    segments_per_path: int,
    stroke_width: float,
) -> int:
    routes = []
    all_points = []
    for path in sorted(input_dir.glob("*.gpx")):
        if path.name in excluded_routes or path.stem in excluded_routes:
            continue
        if activity_type != "all" and activity_for_route(path, workouts) != f"HKWorkoutActivityType{activity_type.title()}":
            continue
        try:
            route = route_from_file(path, excluded_quadrilaterals, include_rectangle, min_distance_metres)
        except ET.ParseError as error:
            print(f"Skipping {path}: {error}")
            continue
        points = list(route.iter(gpx_tag("rtept")))
        if len(points) < 2:
            continue
        print(f"Processed {route_datetime(path).date()}: {route_length_metres(route) / 1000:.2f} km")
        routes.append((path, points))
        all_points.extend((float(p.attrib["lon"]), float(p.attrib["lat"])) for p in points)

    if not all_points:
        raise FileNotFoundError("No routes with at least two points found")
    min_lon = min(p[0] for p in all_points); max_lon = max(p[0] for p in all_points)
    min_lat = min(p[1] for p in all_points); max_lat = max(p[1] for p in all_points)
    margin_lon = max((max_lon - min_lon) * 0.02, 0.001)
    margin_lat = max((max_lat - min_lat) * 0.02, 0.001)
    min_lon -= margin_lon; max_lon += margin_lon; min_lat -= margin_lat; max_lat += margin_lat
    width = 1200
    latitude_scale = math.cos(math.radians((min_lat + max_lat) / 2))
    geographic_width = (max_lon - min_lon) * latitude_scale
    geographic_height = max_lat - min_lat
    height = max(1, round(width * geographic_height / geographic_width))

    def project(point: ET.Element) -> tuple[float, float]:
        lon, lat = float(point.attrib["lon"]), float(point.attrib["lat"])
        x = (lon - min_lon) * latitude_scale / geographic_width * width
        y = height - (lat - min_lat) / (max_lat - min_lat) * height
        return x, y

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        'preserveAspectRatio="xMidYMid meet">',
        '<rect width="100%" height="100%" fill="black"/>',
    ]
    if include_markers and include_rectangle is not None:
        marker_size = 10
        for lon, lat in ((include_rectangle[0], include_rectangle[3]), (include_rectangle[2], include_rectangle[1])):
            x = (lon - min_lon) * latitude_scale / geographic_width * width
            y = height - (lat - min_lat) / (max_lat - min_lat) * height
            svg.append(
                f'<path d="M{x - marker_size:.2f},{y:.2f} L{x + marker_size:.2f},{y:.2f} '
                f'M{x:.2f},{y - marker_size:.2f} L{x:.2f},{y + marker_size:.2f}" '
                'stroke="#ffffff" stroke-width="3" fill="none"/>'
            )
    route_dates = sorted({route_datetime(path).date() for path, _ in routes})
    date_positions = {
        day: (0.5 if len(route_dates) == 1 else index / (len(route_dates) - 1))
        for index, day in enumerate(route_dates)
    }
    bearing_positions = {}
    if color_order == "bearing":
        if color_center is None:
            starts = [
                (float(points[0].attrib["lat"]), float(points[0].attrib["lon"]))
                for _, points in routes
            ]
            color_center = (
                sum(lat for lat, _ in starts) / len(starts),
                sum(lon for _, lon in starts) / len(starts),
            )
        bearing_records = sorted(routes, key=lambda record: bearing_from_center(record[1], color_center))
        for index, record in enumerate(bearing_records):
            bearing_positions[record[0]] = 0.5 if len(bearing_records) == 1 else index / (len(bearing_records) - 1)

    route_markers = []
    for index, (path, points) in enumerate(routes):
        coordinates = [project(point) for point in points]
        position = bearing_positions[path] if color_order == "bearing" else date_positions[route_datetime(path).date()]
        if colormap == "rainbow":
            hue_position = (0.45 - ((0.45 + 1.0) - 0.58) * position) % 1
            color = kml_to_svg_color(colormap_color("rainbow", hue_position))
        else:
            color = kml_to_svg_color(colormap_color(colormap, position))
        escaped_name = html.escape(path.stem, quote=True)
        for segment_index, first_point in enumerate(range(0, len(coordinates) - 1, segments_per_path)):
            path_points = coordinates[first_point:first_point + segments_per_path + 1]
            path_data = " ".join(
                f"{'M' if point_index == 0 else 'L'} {x:.2f},{y:.2f}"
                for point_index, (x, y) in enumerate(path_points)
            )
            svg.append(
                f'<path id="route-{index}-segment-{segment_index}" data-name="{escaped_name}" '
                f'd="{path_data}" '
                f'stroke="{color}" stroke-width="{stroke_width:g}" stroke-opacity="{segment_opacity:.3f}" '
                'stroke-linecap="butt" stroke-linejoin="round" fill="none"/>'
            )
        if route_marker_size > 0:
            start_x, start_y = project(points[0])
            end_x, end_y = project(points[-1])
            route_markers.append(
                f'<circle cx="{start_x:.2f}" cy="{start_y:.2f}" r="{route_marker_size:.2f}" '
                f'fill="{color}" stroke="none"/>'
            )
            diameter = 2 * route_marker_size
            route_markers.append(
                f'<rect x="{end_x - route_marker_size:.2f}" y="{end_y - route_marker_size:.2f}" '
                f'width="{diameter:.2f}" height="{diameter:.2f}" fill="{color}" stroke="none"/>'
            )
    svg.extend(route_markers)
    svg.append('</svg>')
    output.write_text("\n".join(svg), encoding="utf-8")
    return len(routes)


def route_length_metres(route: ET.Element) -> float:
    points = list(route.iter(gpx_tag("rtept")))
    total = 0.0
    for first, second in zip(points, points[1:]):
        try:
            # Reuse the GPX distance calculation from concatenate_gpx.
            from concatenate_gpx import distance_metres
            total += distance_metres(first, second)
        except (KeyError, ValueError):
            continue
    return total


def unique_route_length_metres(routes: list[ET.Element], tolerance_metres: float) -> float:
    """Approximate road-union length, deduplicating only between routes."""
    if tolerance_metres <= 0:
        return sum(route_length_metres(route) for route in routes)
    covered: dict[tuple[int, int], list[tuple[float, float]]] = {}
    cell_size = tolerance_metres
    unique_total = 0.0
    from concatenate_gpx import distance_metres

    for route in routes:
        points = list(route.iter(gpx_tag("rtept")))
        route_midpoints = []
        route_total = 0.0
        for first, second in zip(points, points[1:]):
            try:
                length = distance_metres(first, second)
                mid_lon = (float(first.attrib["lon"]) + float(second.attrib["lon"])) / 2
                mid_lat = (float(first.attrib["lat"]) + float(second.attrib["lat"])) / 2
            except (KeyError, ValueError):
                continue
            x = mid_lon * 111_320 * 0.65
            y = mid_lat * 111_320
            key = (round(x / cell_size), round(y / cell_size))
            nearby = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nearby.extend(covered.get((key[0] + dx, key[1] + dy), []))
            if not any((x - old_x) ** 2 + (y - old_y) ** 2 <= tolerance_metres**2 for old_x, old_y in nearby):
                route_total += length
            route_midpoints.append((key, x, y))
        # Do not compare a route with itself: internal adjacent segments must
        # remain part of its length even when they fall in the same grid cell.
        for key, x, y in route_midpoints:
            covered.setdefault(key, []).append((x, y))
        unique_total += route_total
    return unique_total


def report_walking_distance(
    input_dir: Path,
    include_rectangle: tuple[float, float, float, float] | None,
    min_distance_metres: float,
    workouts: list[tuple],
    excluded_routes: set[str],
    dedup_distance_metres: float,
) -> tuple[float, float, int]:
    total = 0.0
    routes = []
    for path in sorted(input_dir.glob("*.gpx")):
        if path.name in excluded_routes or path.stem in excluded_routes:
            continue
        if activity_for_route(path, workouts) != "HKWorkoutActivityTypeWalking":
            continue
        # Deliberately pass no excluded circle: this report uses --include only.
        route = route_from_file(path, None, include_rectangle, min_distance_metres)
        if len(list(route.iter(gpx_tag("rtept")))) < 2:
            continue
        routes.append(route)
    total = sum(route_length_metres(route) for route in routes)
    return total, unique_route_length_metres(routes, dedup_distance_metres), len(routes)


def convert(
    input_dir: Path,
    output: Path,
    excluded_quadrilaterals: list[list[tuple[float, float]]],
    include_rectangle: tuple[float, float, float, float] | None,
    min_distance_metres: float,
    activity_type: str,
    workouts: list[tuple],
    colormap: str,
    excluded_routes: set[str],
    color_order: str,
    color_center: tuple[float, float] | None,
) -> int:
    files = sorted(input_dir.glob("*.gpx"))
    if not files:
        raise FileNotFoundError(f"No .gpx files found in {input_dir}")

    root = ET.Element(kml_tag("kml"))
    document = ET.SubElement(root, kml_tag("Document"))
    ET.SubElement(document, kml_tag("name")).text = "GPX routes"

    route_records = []
    for path in files:
        if path.name in excluded_routes or path.stem in excluded_routes:
            continue
        if activity_type != "all":
            activity = activity_for_route(path, workouts)
            if activity != f"HKWorkoutActivityType{activity_type.title()}":
                continue
        try:
            route = route_from_file(path, excluded_quadrilaterals, include_rectangle, min_distance_metres)
        except ET.ParseError as error:
            print(f"Skipping {path}: {error}")
            continue
        points = [
            (point.attrib["lon"], point.attrib["lat"])
            for point in route.iter(gpx_tag("rtept"))
            if "lon" in point.attrib and "lat" in point.attrib
        ]
        if not points:
            continue

        name = route.findtext(gpx_tag("name")) or path.stem
        print(f"Processed {route_datetime(path).date()}: {route_length_metres(route) / 1000:.2f} km")
        route_records.append((name, points, path))

    route_index = 0
    total_routes = len(route_records)
    most_recent_path = max(route_records, key=lambda record: route_datetime(record[2]))[2]
    most_recent_date = route_datetime(most_recent_path).date()
    non_recent_dates = sorted({route_datetime(record[2]).date() for record in route_records if route_datetime(record[2]).date() != most_recent_date})
    date_positions = {
        day: (0.5 if len(non_recent_dates) == 1 else index / (len(non_recent_dates) - 1))
        for index, day in enumerate(non_recent_dates)
    }
    bearing_positions = {}
    if color_order == "bearing":
        if color_center is None:
            starts = [
                (float(points[0][1]), float(points[0][0]))
                for _, points, _ in route_records
            ]
            color_center = (
                sum(lat for lat, _ in starts) / len(starts),
                sum(lon for _, lon in starts) / len(starts),
            )
        bearing_records = sorted(route_records, key=lambda record: bearing_from_center(record[1], color_center))
        for index, record in enumerate(bearing_records):
            bearing_positions[record[2]] = 0.5 if len(bearing_records) == 1 else index / (len(bearing_records) - 1)
    for name, points, path in route_records:
        if route_datetime(path).date() == most_recent_date:
            color = "ff000000"
        else:
            position = bearing_positions[path] if color_order == "bearing" else date_positions[route_datetime(path).date()]
            if colormap == "rainbow":
            # Open arc with a gap around cyan: blue -> magenta -> red ->
            # yellow -> green, stopping before cyan becomes visible.
                cyan_gap_start = 0.58
                cyan_gap_end = 0.45
                hue_span = (cyan_gap_end + 1.0) - cyan_gap_start
                hue_position = (cyan_gap_end - hue_span * position) % 1
                color = colormap_color("rainbow", hue_position)
            else:
                color = colormap_color(colormap, position)
        style_url = make_style(document, route_index, color)
        route_index += 1
        placemark = ET.SubElement(document, kml_tag("Placemark"))
        ET.SubElement(placemark, kml_tag("name")).text = name
        ET.SubElement(placemark, kml_tag("styleUrl")).text = style_url
        line = ET.SubElement(placemark, kml_tag("LineString"))
        ET.SubElement(line, kml_tag("tessellate")).text = "1"
        ET.SubElement(line, kml_tag("coordinates")).text = "\n" + "\n".join(
            f"{lon},{lat},0" for lon, lat in points
        ) + "\n"

    ET.indent(root, space="  ")
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return route_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("apple_health_export/workout-routes"))
    parser.add_argument("--output", type=Path, default=Path("all-routes.kml"))
    parser.add_argument(
        "--output-format", choices=("kml", "svg"), default="kml",
        help="output format (default: kml)",
    )
    parser.add_argument(
        "--include-markers", action="store_true",
        help="for SVG output, add + markers at the include box corners",
    )
    parser.add_argument(
        "--route-marker-size", type=float, default=0.0, metavar="N",
        help="for SVG output, add start circles and end squares of radius N pixels (default: disabled)",
    )
    parser.add_argument(
        "--segment-opacity", type=float, default=0.3, metavar="0-1",
        help="for SVG output, opacity of each independently drawn route segment (default: 0.3)",
    )
    parser.add_argument(
        "--segments-per-path", type=int, default=10, metavar="N",
        help="number of consecutive GPS segments to combine into each SVG path (default: 10)",
    )
    parser.add_argument(
        "--stroke-width", type=float, default=3.0, metavar="PIXELS",
        help="SVG route stroke width in pixels (default: 3)",
    )
    parser.add_argument(
        "--exclude", nargs=4, action="append",
        metavar=("LAT1,LON1", "LAT2,LON2", "LAT3,LON3", "LAT4,LON4"),
        help="trim points inside this quadrilateral; corners are LATITUDE,LONGITUDE pairs in perimeter order",
    )
    parser.add_argument(
        "--include", nargs=2,
        metavar=("LAT1,LON1", "LAT2,LON2"),
        help="keep only points inside the rectangle defined by two LATITUDE,LONGITUDE corners",
    )
    parser.add_argument(
        "--min-distance-metres", type=float, default=0.0, metavar="N",
        help="keep no consecutive points closer than N metres (default: keep all points)",
    )
    parser.add_argument(
        "--activity-type", choices=("all", "walking", "cycling"), default="all",
        help="include only walking or cycling routes (default: all)",
    )
    parser.add_argument(
        "--health-export", type=Path, default=Path("apple_health_export/export.xml"),
        help="Apple Health export.xml used for activity classification",
    )
    parser.add_argument(
        "--colormap", choices=("winter", "autumn", "cool", "rainbow"), default="winter",
        help="colormap used for routes (default: winter)",
    )
    parser.add_argument(
        "--color-order", choices=("date", "bearing"), default="date",
        help="order colors by date or midpoint bearing from the color center (default: date)",
    )
    parser.add_argument(
        "--color-center",
        metavar="LAT,LON",
        help="center for bearing color ordering (default: centroid of included route starting points)",
    )
    parser.add_argument(
        "--excluded-routes-file", type=Path, default=Path("excluded-routes.txt"),
        help="file containing GPX filenames to skip (default: excluded-routes.txt)",
    )
    parser.add_argument(
        "--report-total-walking-distance", action="store_true",
        help="report total walking distance using --include only; ignores --exclude",
    )
    parser.add_argument(
        "--dedup-distance-metres", type=float, default=10.0, metavar="N",
        help="distance tolerance for deduplicating overlapping roads (default: 10)",
    )
    args = parser.parse_args()
    if args.min_distance_metres < 0:
        parser.error("--min-distance-metres must be non-negative")
    if args.dedup_distance_metres < 0:
        parser.error("--dedup-distance-metres must be non-negative")
    if args.route_marker_size < 0:
        parser.error("--route-marker-size must be non-negative")
    if not 0 <= args.segment_opacity <= 1:
        parser.error("--segment-opacity must be between 0 and 1")
    if args.segments_per_path < 1:
        parser.error("--segments-per-path must be at least 1")
    if args.stroke_width <= 0:
        parser.error("--stroke-width must be greater than 0")
    try:
        excluded_quadrilaterals = [parse_quadrilateral(values) for values in args.exclude] if args.exclude else []
    except ValueError as error:
        parser.error(str(error))
    try:
        include_rectangle = parse_include_rectangle(args.include) if args.include else None
    except ValueError as error:
        parser.error(str(error))
    try:
        color_center = parse_coordinate(args.color_center) if args.color_center else None
    except ValueError as error:
        parser.error(str(error))
    workouts = load_activity_times(args.health_export) if args.activity_type != "all" else []
    excluded_routes = load_excluded_routes(args.excluded_routes_file)
    if args.output_format == "svg":
        count = convert_svg(
            args.input_dir, args.output, excluded_quadrilaterals, include_rectangle,
            args.min_distance_metres, args.activity_type, workouts, excluded_routes,
            args.colormap, args.color_order, color_center,
            args.include_markers, args.route_marker_size, args.segment_opacity,
            args.segments_per_path, args.stroke_width,
        )
    else:
        count = convert(
            args.input_dir, args.output, excluded_quadrilaterals, include_rectangle,
            args.min_distance_metres, args.activity_type, workouts,
            args.colormap, excluded_routes, args.color_order, color_center,
        )
    size_bytes = args.output.stat().st_size
    print(f"Wrote {count} routes to {args.output}; file size: {size_bytes:,} bytes ({size_bytes / 1_000_000:.2f} MB)")
    if args.report_total_walking_distance:
        walking_workouts = load_activity_times(args.health_export)
        total_distance, unique_distance, counted = report_walking_distance(
            args.input_dir, include_rectangle, args.min_distance_metres,
            walking_workouts, excluded_routes, args.dedup_distance_metres,
        )
        print(f"Total walking distance: {total_distance / 1000:.2f} km ({counted} routes)")
        print(f"Deduplicated walking distance: {unique_distance / 1000:.2f} km (tolerance: {args.dedup_distance_metres:g} m)")


if __name__ == "__main__":
    main()
