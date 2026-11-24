"""Look up UHSLC Fast Delivery stations in the live station catalog."""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import get_close_matches
from threading import Lock
from time import monotonic
from typing import Literal
from urllib.parse import quote

import requests
from langchain_core.tools import tool


CATALOG_URL = "https://uhslc.soest.hawaii.edu/data/meta.geojson"
ERDDAP_URL = "https://uhslc.soest.hawaii.edu/erddap/tabledap"
CATALOG_TTL_SECONDS = 900
CATALOG_RETRY_SECONDS = 60
REQUEST_TIMEOUT = (3, 12)
HAWAII_BOUNDS = (-179.0, 18.5, -154.5, 29.0)


@dataclass(frozen=True)
class Station:
    station_id: int
    name: str
    country: str
    country_code: str
    codes: tuple[str, ...]
    longitude: float
    latitude: float
    timezone_name: str
    first_date: str
    last_date: str

    @property
    def label(self) -> str:
        return f"{self.station_id:03d} {self.name} ({self.country})"


_catalog_lock = Lock()
_catalog: tuple[Station, ...] = ()
_catalog_fetched_at: datetime | None = None
_catalog_expires_at = 0.0
_catalog_retry_at = 0.0


def _fetch_catalog() -> tuple[Station, ...]:
    response = requests.get(CATALOG_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if data["type"] != "FeatureCollection" or not isinstance(data["features"], list):
        raise ValueError("Expected a GeoJSON FeatureCollection")

    stations = {}
    for feature in data["features"]:
        properties = feature["properties"]
        span = properties.get("fd_span") or {}
        if not span.get("latest"):
            continue
        longitude, latitude = map(float, feature["geometry"]["coordinates"][:2])
        station = Station(
            station_id=int(properties["uhslc_id"]),
            name=properties["name"],
            country=properties["country"],
            country_code=properties.get("country_code_alpha") or "",
            codes=tuple(properties[key] for key in ("uhslc_code", "ssc_id") if properties.get(key)),
            longitude=(longitude + 180) % 360 - 180,
            latitude=latitude,
            timezone_name=properties.get("timezone_name") or "unknown",
            first_date=span.get("oldest") or "unknown",
            last_date=span["latest"],
        )
        if not station.name or not -90 <= latitude <= 90 or not -180 <= longitude <= 360:
            raise ValueError("Invalid station metadata")
        if station.station_id in stations:
            raise ValueError("Duplicate UHSLC station ID")
        stations[station.station_id] = station

    if not stations:
        raise ValueError("Empty Fast Delivery catalog")
    return tuple(stations[key] for key in sorted(stations))


def _get_catalog() -> tuple[tuple[Station, ...], str]:
    global _catalog, _catalog_fetched_at, _catalog_expires_at, _catalog_retry_at
    with _catalog_lock:
        now = monotonic()
        if now >= _catalog_expires_at and now >= _catalog_retry_at:
            try:
                _catalog = _fetch_catalog()
            except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
                _catalog_retry_at = monotonic() + CATALOG_RETRY_SECONDS
            else:
                _catalog_fetched_at = datetime.now(timezone.utc)
                _catalog_expires_at = monotonic() + CATALOG_TTL_SECONDS
        if _catalog_fetched_at is None:
            return (), f"UHSLC station catalog unavailable. Source: {CATALOG_URL}"
        source = f"Source: {CATALOG_URL}"
        if monotonic() >= _catalog_expires_at:
            retrieved = _catalog_fetched_at.strftime("%Y-%m-%d %H:%M UTC")
            source = f"Live catalog unavailable. Using cached metadata retrieved {retrieved}, which may be stale. {source}"
        return _catalog, source


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    plain = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch) and ch not in "'ʻ’"
    )
    return " ".join(re.findall(r"[^\W_]+", plain))


def _region_in_query(query: str) -> str | None:
    station_terms = r"(?:tide )?(?:gauges|stations)"
    after = re.search(rf"\b{station_terms} (?:are )?(?:in|around|for|off) (.+)$", query)
    before = re.search(rf"^(?:all|list) (?:the )?(.+?) {station_terms}$", query)
    if after or before:
        return (after or before).group(1)
    return None


def _in_bounds(station: Station, bounds: list[float] | tuple[float, float, float, float]) -> bool:
    west, south, east, north = bounds
    if west <= east:
        longitude_matches = west <= station.longitude <= east
    else:
        longitude_matches = station.longitude >= west or station.longitude <= east
    return south <= station.latitude <= north and longitude_matches


def _match_stations(query: str, stations: tuple[Station, ...]) -> tuple[list[Station], bool]:
    """Return candidate stations and whether the caller needs to disambiguate."""
    framed = f" {query} "
    id_list = r"[0-9]{1,3}(?: (?:and )?[0-9]{1,3})*"
    ids = re.fullmatch(rf"({id_list})", query) or re.search(
        rf"\b(?:stations?|uhslc|id) (?:id )?({id_list})\b", query
    )
    if ids:
        by_id = {station.station_id: station for station in stations}
        ordered_ids = dict.fromkeys(map(int, re.findall(r"[0-9]+", ids[1])))
        if any(key not in by_id for key in ordered_ids):
            return [], False
        return [by_id[key] for key in ordered_ids], False

    names = [(_normalize(station.name), station) for station in stations]
    exact = [
        station for name, station in names
        if query == name or query in {_normalize(code) for code in station.codes}
    ]
    if exact:
        return exact, len(exact) > 1

    mentioned = [(name, station) for name, station in names if f" {name} " in framed]
    if mentioned:
        longest = [
            (name, station) for name, station in mentioned
            if not any(name != other and f" {name} " in f" {other} " for other, _ in mentioned)
        ]
        matches = [station for _, station in longest]
        distinct_names = {name for name, _ in longest}
        if len(distinct_names) == 1:
            country_matches = [
                station for station in matches if f" {_normalize(station.country)} " in framed
            ]
            matches = country_matches or matches
        elif " and " in framed and len(matches) == len(distinct_names):
            longest.sort(key=lambda match: framed.index(f" {match[0]} "))
            return [station for _, station in longest], False
        return matches, len(matches) > 1

    trailing_id = re.search(r"\b(?:for|at) ([0-9]{1,3})$", query)
    if trailing_id:
        return [station for station in stations if station.station_id == int(trailing_id[1])], False

    partial = [station for name, station in names if query and f" {query}" in f" {name}"]
    if partial:
        return partial, len(partial) > 1

    similar = get_close_matches(query, [name for name, _ in names], n=3, cutoff=0.82)
    return [station for name, station in names if name in similar], True


def _latest_observation(station_id: int, frequency: str) -> tuple[str | None, str]:
    query = f'time&uhslc_id={station_id}&sea_level!=NaN&orderByMax("time")'
    url = f"{ERDDAP_URL}/global_{frequency}_fast.csvp?{quote(query, safe='&=()!')}"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404 and "no matching results" in response.text.lower():
            return None, url
        response.raise_for_status()
        row = next(csv.DictReader(io.StringIO(response.text)))
        timestamp = row["time (UTC)"]
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (requests.RequestException, KeyError, StopIteration, TypeError, ValueError):
        raise ValueError(url) from None
    return timestamp, url


@tool(parse_docstring=True)
def get_station_info_tool(
    station_query: str,
    region: str | None = None,
    bounds: list[float] | None = None,
    frequency: Literal["hourly", "daily"] | None = None,
) -> str:
    """Look up UHSLC Fast Delivery stations using the live UHSLC catalog.

    Use for station IDs, canonical names, metadata, or regional station lists.

    Args:
        station_query: Station name, station code, three-digit ID, or a
            natural-language request such as "all Hawaii stations". Use an
            empty query to list stations selected by region or bounds.
        region: Optional country name, ISO alpha-2 code, or Hawaii.
        bounds: Optional longitude/latitude bounds (west, south, east, north)
            in degrees, with longitudes between -180 and 180. West greater
            than east selects a region crossing the antimeridian.
        frequency: Check the latest valid observation in this ERDDAP product
            for a single matched station. Omit for ordinary metadata lookup.
    """
    query = _normalize(station_query)
    region_query = _region_in_query(query)
    region = _normalize(region) if region else region_query
    latest = frequency is not None or bool(re.search(r"\b(?:latest|most recent|newest)\b", query))
    if bounds is not None:
        if len(bounds) != 4:
            return "Provide four bounds in this order: west, south, east, north."
        west, south, east, north = bounds
        if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
            return "Use west/east longitudes from -180 to 180 and ordered south/north latitudes from -90 to 90."
    if not query and not region and bounds is None:
        return "Provide a UHSLC station name, ID, station code, country, or bounding box."

    stations, source = _get_catalog()
    if not stations:
        return source

    if region in {"hawaii", "hi"}:
        stations = tuple(
            s for s in stations
            if s.country_code == "US" and _in_bounds(s, HAWAII_BOUNDS)
        )
    elif region:
        country_names = {"us": "united states", "usa": "united states"}
        region = country_names.get(region, region)
        stations = tuple(
            s for s in stations
            if region in {_normalize(s.country), _normalize(s.country_code)}
            or (region == "united states" and s.country_code == "US")
        )
        if not stations:
            return f"No Fast Delivery stations found for that country or region. For a subregion, specify bounds.\n{source}"
    if bounds is not None:
        stations = tuple(s for s in stations if _in_bounds(s, bounds))

    listing = not query or bool(region_query) or (
        (region or bounds is not None) and query in {"stations", "all stations", "tide gauges", "all tide gauges"}
    )
    matches, ambiguous = (list(stations), False) if listing else _match_stations(query, stations)
    if not matches:
        return f"No matching UHSLC Fast Delivery station found.\n{source}"
    if ambiguous:
        options = "; ".join(station.label for station in matches[:3])
        return f"Please confirm the UHSLC station. Possible matches: {options}.\n{source}"

    if listing or len(matches) > 1:
        labels = "\n".join(station.label for station in matches)
        extra = "\nSpecify one station to check its latest observation." if latest else ""
        return f"{len(matches)} UHSLC Fast Delivery stations:\n{labels}{extra}\n{source}"

    station = matches[0]
    details = [
        station.label,
        f"Location: {station.latitude:.5f} latitude, {station.longitude:.5f} longitude (degrees). Time zone: {station.timezone_name}.",
        f"Fast Delivery catalog coverage: {station.first_date} through {station.last_date}.",
        source,
    ]
    if latest:
        products = [frequency] if frequency else re.findall(r"\b(?:hourly|daily)\b", query)
        for product in dict.fromkeys(products or ["hourly", "daily"]):
            try:
                timestamp, url = _latest_observation(station.station_id, product)
            except ValueError as error:
                details.append(f"Latest {product} observation could not be verified in ERDDAP. Source: {error}")
                continue
            if timestamp is None:
                details.append(f"No valid {product} observations in ERDDAP. Source: {url}")
                continue
            discrepancy = " This differs from the catalog coverage end date." if timestamp[:10] != station.last_date else ""
            details.append(f"Latest valid {product} observation in ERDDAP: {timestamp}.{discrepancy} Source: {url}")

    return "\n".join(details)
