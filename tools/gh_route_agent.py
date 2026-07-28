#!/usr/bin/env python3
"""
Small CLI helper to test a local GraphHopper service from addresses or coordinates.

Examples:
  tools/gh_route_agent.py --from "Bolzano stazione" --to "Trento stazione" --profile car
  tools/gh_route_agent.py --from "46.4983,11.3548" --to "46.0679,11.1211" --profile car
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import ipaddress
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_GRAPHHOPPER_URL = "http://127.0.0.1:8987"
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
DEFAULT_GEOSM_URL = "https://geosm.paginegialle.it/lbs"
USER_AGENT = "graphhopper-route-agent/1.0"
DIRECT_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
HIGHWAY_ROAD_CLASSES = {"motorway", "motorway_link", "trunk", "trunk_link"}
HIGHWAY_TEXT_PATTERN = re.compile(
    r"(?:\b(?:autostrada|tangenziale|raccordo|superstrada|motorway)\b|"
    r"(?<![A-Z0-9])(?:A|E|RA)\s?\d+(?![A-Z0-9]))",
    re.IGNORECASE,
)


def is_loopback_url(url: str) -> bool:
    hostname = urllib.parse.urlsplit(url).hostname
    if not hostname:
        return False
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Point:
    label: str
    lat: float
    lon: float

    @property
    def graphhopper_value(self) -> str:
        return f"{self.lat:.7f},{self.lon:.7f}"


@dataclass
class BatchResult:
    index: int
    origin_input: str
    destination_input: str
    profile: str
    expected_time_ms: int | None = None
    start: Point | None = None
    end: Point | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


def http_json(url: str, timeout: int, payload: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {"User-Agent": USER_AGENT}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        open_request = DIRECT_OPENER.open if is_loopback_url(url) else urllib.request.urlopen
        with open_request(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {error.code} da {url}\n{body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Connessione fallita verso {url}: {error.reason}") from error


def parse_coordinate(value: str) -> Point | None:
    cleaned = value.strip()
    if ";" in cleaned:
        cleaned = cleaned.replace(";", ",")
    pieces = [piece.strip() for piece in cleaned.split(",")]
    if len(pieces) != 2:
        return None
    try:
        lat = float(pieces[0])
        lon = float(pieces[1])
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Coordinate fuori range: {value}")
    return Point(value, lat, lon)


def geocode(address: str, country: str | None, timeout: int) -> Point:
    direct_point = parse_coordinate(address)
    if direct_point:
        return direct_point

    params = {
        "q": address,
        "format": "jsonv2",
        "limit": "1",
    }
    if country:
        params["countrycodes"] = country

    url = f"{DEFAULT_NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    results = http_json(url, timeout)
    if not results:
        country_hint = f" in country={country}" if country else ""
        raise RuntimeError(f"Nessun risultato geocoding per '{address}'{country_hint}")

    first = results[0]
    return Point(
        first.get("display_name", address),
        float(first["lat"]),
        float(first["lon"]),
    )


def find_geosm_candidates(value: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "lat" in value and "lon" in value:
            candidates.append(value)
        for child in value.values():
            candidates.extend(find_geosm_candidates(child))
    elif isinstance(value, list):
        for item in value:
            candidates.extend(find_geosm_candidates(item))
    return candidates


def geocode_geosm(address: str, timeout: int) -> Point:
    direct_point = parse_coordinate(address)
    if direct_point:
        return direct_point

    params = {
        "dv": address,
        "format": "javascript",
    }
    url = f"{DEFAULT_GEOSM_URL}?{urllib.parse.urlencode(params)}"
    data = http_json(url, timeout)
    candidates = find_geosm_candidates(data)
    if not candidates:
        if data.get("ret") not in (0, "0", None):
            raise RuntimeError(f"GEOSM non ha risolto '{address}': {json.dumps(data, ensure_ascii=False)[:600]}")
        raise RuntimeError(f"Nessuna coordinata GEOSM per '{address}'")

    first = candidates[0]
    label_parts = [
        first.get("topo"),
        first.get("com") or first.get("name"),
        first.get("prov"),
    ]
    label = ", ".join(str(part) for part in label_parts if part) or address
    return Point(label, float(first["lat"]), float(first["lon"]))


def graphhopper_info(base_url: str, timeout: int) -> dict[str, Any] | None:
    try:
        return http_json(f"{base_url.rstrip('/')}/info", timeout)
    except RuntimeError:
        return None


def route_get(base_url: str, start: Point, end: Point, profile: str, timeout: int) -> dict[str, Any]:
    params = [
        ("random", uuid.uuid4().hex),
        ("profile", profile),
        ("point", start.graphhopper_value),
        ("point", end.graphhopper_value),
        ("type", "json"),
        ("points_encoded", "false"),
        ("instructions", "true"),
        ("locale", "it"),
    ]
    url = f"{base_url.rstrip('/')}/route?{urllib.parse.urlencode(params)}"
    return http_json(url, timeout)


def route_post(base_url: str, start: Point, end: Point, profile: str, timeout: int, alternatives: int) -> dict[str, Any]:
    payload = {
        "points": [
            [start.lon, start.lat],
            [end.lon, end.lat],
        ],
        "profile": profile,
        "calc_points": True,
        "elevation": False,
        "use_miles": False,
        "instructions": True,
        "locale": "it",
        "points_encoded": True,
        "ch.disable": False,
        "points_encoded_multiplier": 1000000,
        "snap_preventions": ["ferry"],
        "details": [
            "road_class",
            "road_environment",
            "max_speed",
            "average_speed",
            "car_access",
            "car_average_speed",
        ],
        "custom_model": None,
    }
    if alternatives > 1:
        payload.update({
            "alternative_route.max_paths": alternatives,
            "alternative_route.max_share_factor": 0.7,
            "alternative_route.max_weight_factor": 1.5,
            "alternative_route.max_exploration_factor": 1.6,
            "algorithm": "alternative_route",
        })
    url = f"{base_url.rstrip('/')}/route?=null&random={uuid.uuid4().hex}"
    return http_json(url, timeout, payload=payload)


def format_duration(milliseconds: int | float) -> str:
    total_seconds = int(round(milliseconds / 1000))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def parse_expected_time(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if text.isdigit():
        return int(text) * 60 * 1000
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            hours = 0
            minutes, seconds = parts
        elif len(parts) == 3:
            hours, minutes, seconds = parts
        else:
            raise ValueError(f"Tempo previsto non valido: {value}")
        return (int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000

    total_seconds = 0
    number = ""
    found_unit = False
    for char in text:
        if char.isdigit():
            number += char
            continue
        if char.isspace():
            continue
        if not number:
            raise ValueError(f"Tempo previsto non valido: {value}")
        amount = int(number)
        number = ""
        if char == "h":
            total_seconds += amount * 3600
            found_unit = True
        elif char == "m":
            total_seconds += amount * 60
            found_unit = True
        elif char == "s":
            total_seconds += amount
            found_unit = True
        else:
            raise ValueError(f"Unita tempo non valida '{char}' in: {value}")
    if number:
        total_seconds += int(number) * 60
    if not found_unit and total_seconds == 0:
        raise ValueError(f"Tempo previsto non valido: {value}")
    return total_seconds * 1000


def format_delta(milliseconds: int | float) -> str:
    sign = "+" if milliseconds >= 0 else "-"
    return sign + format_duration(abs(milliseconds))


def _instruction_overlaps_highway(instruction: dict[str, Any], path: dict[str, Any]) -> bool:
    interval = instruction.get("interval")
    if not isinstance(interval, list) or len(interval) != 2:
        return False

    start, end = interval
    road_classes = (path.get("details") or {}).get("road_class") or []
    for detail in road_classes:
        if not isinstance(detail, list) or len(detail) != 3 or detail[2] not in HIGHWAY_ROAD_CLASSES:
            continue
        detail_start, detail_end = detail[:2]
        if start == end:
            if detail_start <= start < detail_end:
                return True
        elif start < detail_end and end > detail_start:
            return True
    return False


def main_highway_junctions(path: dict[str, Any]) -> list[str]:
    junctions: list[str] = []
    seen: set[str] = set()
    for instruction in path.get("instructions") or []:
        text = (instruction.get("text") or "").strip()
        motorway_junction = str(instruction.get("motorway_junction") or "").strip()
        searchable = " ".join(
            str(instruction.get(field) or "")
            for field in (
                "text",
                "street_name",
                "street_ref",
                "street_destination",
                "street_destination_ref",
            )
        )
        sign = instruction.get("sign")
        has_highway_label = bool(HIGHWAY_TEXT_PATTERN.search(searchable))
        is_direction_change = sign not in (None, 0, 4, 5, 6) or (sign == 6 and has_highway_label)
        is_highway_context = has_highway_label or _instruction_overlaps_highway(instruction, path)
        if not motorway_junction and not (is_direction_change and is_highway_context):
            continue

        if not text:
            text = "Svincolo autostradale"
        if motorway_junction and motorway_junction.casefold() not in text.casefold():
            text += f" (svincolo {motorway_junction})"

        normalized = " ".join(text.casefold().split())
        if normalized not in seen:
            seen.add(normalized)
            junctions.append(text)
    return junctions


def alternative_summaries(data: dict[str, Any]) -> list[str]:
    return [
        f"alt {idx}: {path['distance'] / 1000:.2f} km, {format_duration(path['time'])}"
        for idx, path in enumerate(data.get("paths", [])[1:], start=2)
    ]


def alternative_junction_summaries(data: dict[str, Any]) -> list[str]:
    summaries: list[str] = []
    for idx, path in enumerate(data.get("paths", [])[1:], start=2):
        junctions = main_highway_junctions(path)
        summaries.append(f"alt {idx}: " + (" | ".join(junctions) if junctions else "nessuno rilevato"))
    return summaries


def alternative_output_lines(data: dict[str, Any]) -> list[str]:
    if len(data.get("paths", [])) <= 1:
        return []

    lines = ["Alternative: " + "; ".join(alternative_summaries(data))]
    for idx, path in enumerate(data["paths"][1:], start=2):
        junctions = main_highway_junctions(path)
        lines.append(f"Svincoli principali alt {idx}:")
        lines.extend(f"  - {junction}" for junction in junctions or ["nessuno rilevato"])
    return lines


def print_summary(
    data: dict[str, Any],
    start: Point,
    end: Point,
    profile: str,
    max_steps: int,
) -> None:
    errors = data.get("message") or data.get("hints")
    if "paths" not in data:
        raise RuntimeError(f"Risposta GraphHopper inattesa: {json.dumps(errors or data, ensure_ascii=False)[:1200]}")

    path = data["paths"][0]
    distance_km = path["distance"] / 1000
    print(f"Profilo: {profile}")
    print(f"Da: {start.label}")
    print(f"  -> {start.graphhopper_value}")
    print(f"A:  {end.label}")
    print(f"  -> {end.graphhopper_value}")
    print()
    print(f"Distanza: {distance_km:.2f} km")
    print(f"Tempo:    {format_duration(path['time'])}")
    if "ascend" in path and "descend" in path:
        print(f"Salita/discesa: {path['ascend']:.0f} m / {path['descend']:.0f} m")
    alternative_lines = alternative_output_lines(data)
    if alternative_lines:
        print()
        print("\n".join(alternative_lines))

    instructions = path.get("instructions") or []
    if instructions and max_steps:
        print()
        print("Prime istruzioni:")
        for index, instruction in enumerate(instructions[:max_steps], start=1):
            text = instruction.get("text", "").strip() or "(senza testo)"
            dist = instruction.get("distance", 0)
            print(f"{index:2d}. {text} ({dist:.0f} m, {format_duration(instruction.get('time', 0))})")


def result_lines(result: BatchResult, max_steps: int) -> list[str]:
    title = f"#{result.index} - {result.profile}: {result.origin_input} -> {result.destination_input}"
    lines = [title]
    if result.error:
        return lines + [f"ERRORE: {result.error}"]
    if not result.data or not result.start or not result.end:
        return lines + ["ERRORE: risultato incompleto"]
    if "paths" not in result.data:
        return lines + [f"ERRORE: risposta inattesa {json.dumps(result.data, ensure_ascii=False)[:500]}"]

    path = result.data["paths"][0]
    lines.extend([
        f"Partenza risolta: {result.start.label} ({result.start.graphhopper_value})",
        f"Arrivo risolto:   {result.end.label} ({result.end.graphhopper_value})",
        f"Distanza: {path['distance'] / 1000:.2f} km",
        f"Tempo GraphHopper: {format_duration(path['time'])}",
    ])
    if result.expected_time_ms is not None:
        delta = path["time"] - result.expected_time_ms
        lines.append(f"Tempo previsto: {format_duration(result.expected_time_ms)}")
        lines.append(f"Scostamento: {format_delta(delta)}")
    lines.extend(alternative_output_lines(result.data))

    instructions = path.get("instructions") or []
    for idx, instruction in enumerate(instructions[:max_steps], start=1):
        text = instruction.get("text", "").strip() or "(senza testo)"
        lines.append(f"  {idx}. {text} ({instruction.get('distance', 0):.0f} m, {format_duration(instruction.get('time', 0))})")
    return lines


def route_one(
    args: argparse.Namespace,
    origin: str,
    destination: str,
    profile: str,
    index: int,
    expected_time_ms: int | None = None,
) -> BatchResult:
    result = BatchResult(
        index=index,
        origin_input=origin,
        destination_input=destination,
        profile=profile,
        expected_time_ms=expected_time_ms,
    )
    try:
        country = args.country.strip() or None
        if args.geocoder == "geosm":
            result.start = geocode_geosm(origin, args.timeout)
            result.end = geocode_geosm(destination, args.timeout)
        else:
            result.start = geocode(origin, country, args.timeout)
            time.sleep(1)
            result.end = geocode(destination, country, args.timeout)

    except Exception as error:
        result.error = str(error)
        return result

    try:
        if args.method == "post":
            result.data = route_post(
                args.gh_url.rstrip("/"),
                result.start,
                result.end,
                profile,
                args.route_timeout,
                args.alternatives,
            )
        else:
            result.data = route_get(args.gh_url.rstrip("/"), result.start, result.end, profile, args.route_timeout)
    except Exception as error:
        result.error = str(error)

    return result


def read_batch(path: str, default_profile: str) -> list[tuple[str, str, str, int | None]]:
    rows: list[tuple[str, str, str, int | None]] = []
    with open(path, newline="", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file)
        required = {"from", "to"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError("Il CSV batch deve avere almeno le colonne: from,to. La colonna profile e' opzionale.")
        for row in reader:
            origin = (row.get("from") or "").strip()
            destination = (row.get("to") or "").strip()
            if not origin or not destination:
                continue
            expected_raw = row.get("expected_time") or row.get("tempo_previsto") or row.get("expected")
            try:
                expected_time_ms = parse_expected_time(expected_raw)
            except ValueError as error:
                raise RuntimeError(f"Riga {reader.line_num}: {error}") from error
            rows.append((origin, destination, (row.get("profile") or default_profile).strip() or default_profile, expected_time_ms))
    if not rows:
        raise RuntimeError(f"Nessuna riga valida trovata in {path}")
    return rows


def pdf_escape(text: str) -> bytes:
    encoded = text.encode("latin-1", errors="replace").decode("latin-1")
    escaped = encoded.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped.encode("latin-1", errors="replace")


def wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) <= max_chars:
            current += " " + word
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def write_pdf(path: str, results: list[BatchResult], max_steps: int) -> None:
    page_width, page_height = 595, 842
    margin = 42
    line_height = 14
    max_chars = 92
    pages: list[list[tuple[int, str]]] = [[]]

    def add_line(text: str = "", size: int = 10) -> None:
        for wrapped in wrap_text(text, max_chars):
            if len(pages[-1]) >= 52:
                pages.append([])
            pages[-1].append((size, wrapped))

    add_line("Report test GraphHopper", 16)
    add_line(f"Generato: {dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", 10)
    add_line()
    for result in results:
        for line_no, line in enumerate(result_lines(result, max_steps)):
            add_line(line, 12 if line_no == 0 else 10)
        add_line()

    objects: list[bytes] = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    page_object_numbers: list[int] = []
    content_object_numbers: list[int] = []
    next_obj = 3
    for _ in pages:
        page_object_numbers.append(next_obj)
        content_object_numbers.append(next_obj + 1)
        next_obj += 2
    kids = b" ".join(f"{number} 0 R".encode("ascii") for number in page_object_numbers)
    objects.append(b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(len(pages)).encode("ascii") + b" >>")

    for page_number, lines in enumerate(pages):
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_width} {page_height}] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
            f"/Contents {content_object_numbers[page_number]} 0 R >>"
        ).encode("ascii")
        objects.append(page_obj)

        stream_parts = [b"BT"]
        y = page_height - margin
        for size, text in lines:
            stream_parts.append(f"/F1 {size} Tf 1 0 0 1 {margin} {y} Tm (".encode("ascii") + pdf_escape(text) + b") Tj")
            y -= line_height
        stream_parts.append(b"ET")
        stream = b"\n".join(stream_parts)
        objects.append(b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for obj_number, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{obj_number} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    Path(path).write_bytes(output)


def write_csv(path: str, results: list[BatchResult]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow([
            "index",
            "from",
            "to",
            "profile",
            "from_resolved",
            "to_resolved",
            "distance_km",
            "graphhopper_time",
            "expected_time",
            "delta",
            "alternatives",
            "alternative_main_junctions",
            "error",
        ])
        for result in results:
            path_data = result.data["paths"][0] if result.data and "paths" in result.data else None
            delta = path_data["time"] - result.expected_time_ms if path_data and result.expected_time_ms is not None else None
            writer.writerow([
                result.index,
                result.origin_input,
                result.destination_input,
                result.profile,
                result.start.label if result.start else "",
                result.end.label if result.end else "",
                f"{path_data['distance'] / 1000:.3f}" if path_data else "",
                format_duration(path_data["time"]) if path_data else "",
                format_duration(result.expected_time_ms) if result.expected_time_ms is not None else "",
                format_delta(delta) if delta is not None else "",
                "; ".join(alternative_summaries(result.data)) if result.data else "",
                "; ".join(alternative_junction_summaries(result.data)) if result.data else "",
                result.error or "",
            ])


def path_coordinates(path: dict[str, Any]) -> list[list[float]]:
    points = path.get("points")
    if isinstance(points, dict):
        coordinates = points.get("coordinates")
        if isinstance(coordinates, list):
            return coordinates
        raise RuntimeError("Geometria GraphHopper senza coordinates")
    if not isinstance(points, str):
        raise RuntimeError("Geometria GraphHopper mancante")

    multiplier = float(path.get("points_encoded_multiplier") or 100000)
    coordinates: list[list[float]] = []
    index = latitude = longitude = 0
    while index < len(points):
        deltas: list[int] = []
        for _ in range(2):
            shift = result = 0
            while True:
                if index >= len(points):
                    raise RuntimeError("Geometria GraphHopper codificata non valida")
                value = ord(points[index]) - 63
                index += 1
                result |= (value & 0x1F) << shift
                shift += 5
                if value < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        latitude += deltas[0]
        longitude += deltas[1]
        coordinates.append([longitude / multiplier, latitude / multiplier])
    return coordinates


def write_geojson(path: str, results: list[BatchResult]) -> None:
    features: list[dict[str, Any]] = []
    failed_tests: list[dict[str, Any]] = []
    for result in results:
        if result.error or not result.data or "paths" not in result.data:
            failed_tests.append({"test_index": result.index, "error": result.error or "risultato incompleto"})
            continue

        for path_rank, path_data in enumerate(result.data["paths"], start=1):
            coordinates = path_coordinates(path_data)
            if len(coordinates) < 2:
                raise RuntimeError(f"Test {result.index}, percorso {path_rank}: geometria con meno di due punti")

            is_best = path_rank == 1
            delta = path_data["time"] - result.expected_time_ms if result.expected_time_ms is not None else None
            junctions = main_highway_junctions(path_data)
            feature_id = f"test-{result.index}-path-{path_rank}"
            features.append({
                "type": "Feature",
                "id": feature_id,
                "properties": {
                    "route_id": feature_id,
                    "test_index": result.index,
                    "path_rank": path_rank,
                    "route_type": "principale" if is_best else "alternativa",
                    "alternative_index": 0 if is_best else path_rank - 1,
                    "is_best": is_best,
                    "from": result.origin_input,
                    "to": result.destination_input,
                    "from_resolved": result.start.label if result.start else "",
                    "to_resolved": result.end.label if result.end else "",
                    "profile": result.profile,
                    "distance_km": round(path_data["distance"] / 1000, 3),
                    "duration_min": round(path_data["time"] / 60000, 2),
                    "duration": format_duration(path_data["time"]),
                    "expected_time": format_duration(result.expected_time_ms) if result.expected_time_ms is not None else None,
                    "delta": format_delta(delta) if delta is not None else None,
                    "weight": path_data.get("weight"),
                    "main_junctions": " | ".join(junctions),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
            })

    feature_collection = {
        "type": "FeatureCollection",
        "name": "GraphHopper test routes",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "failed_tests": failed_tests,
        "features": features,
    }
    Path(path).write_text(json.dumps(feature_collection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test rapido di un servizio GraphHopper locale usando indirizzi o coordinate.",
    )
    parser.add_argument("--from", dest="origin", help="Indirizzo o coordinate lat,lon di partenza")
    parser.add_argument("--to", dest="destination", help="Indirizzo o coordinate lat,lon di arrivo")
    parser.add_argument("--batch", help="CSV con colonne from,to e profile opzionale")
    parser.add_argument("--pdf", help="Percorso PDF report da generare, ad esempio report.pdf")
    parser.add_argument("--geojson", help="Percorso GeoJSON con percorso principale e alternative")
    parser.add_argument("--csv-output", help="Percorso CSV riepilogo da generare")
    parser.add_argument("--profile", default="car", help="Profilo GraphHopper, ad esempio car, bike, foot")
    parser.add_argument("--gh-url", default=DEFAULT_GRAPHHOPPER_URL, help="Base URL GraphHopper")
    parser.add_argument("--method", choices=["post", "get"], default="post", help="Metodo chiamata route")
    parser.add_argument("--geocoder", choices=["geosm", "nominatim"], default="geosm", help="Servizio di geocodifica")
    parser.add_argument("--country", default="it", help="Filtro country code Nominatim, vuoto per disattivare")
    parser.add_argument("--timeout", type=int, default=20, help="Timeout geocodifica e /info in secondi")
    parser.add_argument("--route-timeout", type=int, default=180, help="Timeout GraphHopper /route in secondi")
    parser.add_argument("--max-steps", type=int, default=999, help="Numero massimo di istruzioni da stampare")
    parser.add_argument("--alternatives", type=int, default=3, help="Numero massimo di alternative per POST")
    parser.add_argument("--list-profiles", action="store_true", help="Mostra i profili esposti da /info e termina")
    args = parser.parse_args()
    if not args.list_profiles and not args.batch and (not args.origin or not args.destination):
        parser.error("--from e --to sono obbligatori, tranne quando usi --batch o --list-profiles")
    return args


def main() -> int:
    args = parse_args()
    base_url = args.gh_url.rstrip("/")
    country = args.country.strip() or None

    info = graphhopper_info(base_url, args.timeout)
    if args.list_profiles:
        if not info:
            print(f"Non riesco a leggere {base_url}/info", file=sys.stderr)
            return 2
        profiles = [profile["name"] for profile in info.get("profiles", [])]
        print("\n".join(profiles) if profiles else "Nessun profilo trovato in /info")
        return 0

    if info:
        profiles = {profile["name"] for profile in info.get("profiles", [])}
        if profiles and args.profile not in profiles:
            print(
                f"Attenzione: profilo '{args.profile}' non trovato in /info. Disponibili: {', '.join(sorted(profiles))}",
                file=sys.stderr,
            )

    if args.batch:
        jobs = read_batch(args.batch, args.profile)
        results = []
        for index, (origin, destination, profile, expected_time_ms) in enumerate(jobs, start=1):
            print(f"[{index}/{len(jobs)}] {origin} -> {destination} ({profile})", file=sys.stderr)
            results.append(route_one(args, origin, destination, profile, index, expected_time_ms))
        for result in results:
            print("\n".join(result_lines(result, args.max_steps)))
            print()
        if args.pdf:
            write_pdf(args.pdf, results, args.max_steps)
            print(f"PDF scritto: {args.pdf}", file=sys.stderr)
        if args.csv_output:
            write_csv(args.csv_output, results)
            print(f"CSV scritto: {args.csv_output}", file=sys.stderr)
        if args.geojson:
            write_geojson(args.geojson, results)
            print(f"GeoJSON scritto: {args.geojson}", file=sys.stderr)
        return 1 if any(result.error for result in results) else 0

    result = route_one(args, args.origin, args.destination, args.profile, 1)
    if result.error:
        raise RuntimeError(result.error)
    print_summary(
        result.data,
        result.start,
        result.end,
        result.profile,
        args.max_steps,
    )
    if args.pdf:
        write_pdf(args.pdf, [result], args.max_steps)
        print(f"PDF scritto: {args.pdf}", file=sys.stderr)
    if args.geojson:
        write_geojson(args.geojson, [result])
        print(f"GeoJSON scritto: {args.geojson}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as error:
        print(f"Errore: {error}", file=sys.stderr)
        raise SystemExit(1)
