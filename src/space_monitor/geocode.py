"""Geocoding service backed by the bundled ``city`` gazetteer table.

The workbook included a 42,905-row ``LatLong Auto-Tagging`` sheet that paired
city names with their coordinates. We don't need to copy that sheet around —
the same data lives in the ``city`` table (loaded from SimpleMaps' world-cities
export by ``load.py``). This module wraps it with one function:

    geocode("Toulouse", "France") -> (43.6, 1.43)

The lookup is case-insensitive, falls back from city+country to ASCII city
name, and returns ``None`` when no row matches. Future external-fallback
(Mapbox / Nominatim) can plug in here without changing call sites.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from . import db


@dataclass(frozen=True)
class GeoHit:
    city: str
    country: str
    lat: float
    lng: float
    population: int | None
    source: str  # 'gazetteer' for now; will become 'mapbox' / 'nominatim' later


def geocode(
    city: str | None,
    country: str | None = None,
    *,
    db_arg: str | None = None,
) -> GeoHit | None:
    """Resolve (city, country) to a single best (lat, lng) hit.

    Strategy: exact match on (city, country) first; if multiple cities share
    the name within the country, prefer the most populous. Then loosen to
    case-insensitive city + country, then to city alone (returning the
    most-populous match globally). Returns None when nothing matches.
    """
    if not city:
        return None
    target = db.resolve_db(db_arg)
    with db.connect(target) as conn:
        row = _lookup(conn, city, country)
    return row


def geocode_many(
    pairs: Iterable[tuple[str | None, str | None]],
    *,
    db_arg: str | None = None,
) -> list[GeoHit | None]:
    """Resolve a batch under one DB connection. Use when geocoding many rows
    in one CLI / loader pass — saves the per-call connect overhead."""
    target = db.resolve_db(db_arg)
    out: list[GeoHit | None] = []
    with db.connect(target) as conn:
        for city, country in pairs:
            out.append(_lookup(conn, city, country) if city else None)
    return out


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def _lookup(conn, city: str, country: str | None) -> GeoHit | None:
    city_norm = city.strip()
    country_norm = (country or "").strip()

    # 1. Exact case-sensitive (city, country).
    if country_norm:
        row = _query(
            conn,
            "WHERE city = ? AND country = ? "
            "ORDER BY COALESCE(population, 0) DESC LIMIT 1",
            (city_norm, country_norm),
        )
        if row:
            return row

    # 2. Case-insensitive (city, country), also matching city_ascii for
    #    diacritic-stripped inputs ("Toulouse" / "Sao Paulo").
    if country_norm:
        row = _query(
            conn,
            "WHERE LOWER(country) = LOWER(?) "
            "  AND (LOWER(city) = LOWER(?) OR LOWER(city_ascii) = LOWER(?)) "
            "ORDER BY COALESCE(population, 0) DESC LIMIT 1",
            (country_norm, city_norm, city_norm),
        )
        if row:
            return row

    # 3. City alone, most populous globally — last-resort fallback when
    #    country is unknown or doesn't match (the workbook's country labels
    #    aren't 1:1 with the gazetteer's).
    row = _query(
        conn,
        "WHERE LOWER(city) = LOWER(?) OR LOWER(city_ascii) = LOWER(?) "
        "ORDER BY COALESCE(population, 0) DESC LIMIT 1",
        (city_norm, city_norm),
    )
    return row


def _query(conn, where_and_order: str, params: tuple) -> GeoHit | None:
    sql = (
        "SELECT city, country, lat, lng, population FROM city " + where_and_order
    )
    row = conn.execute(sql, params).fetchone()
    if not row or row[2] is None or row[3] is None:
        return None
    return GeoHit(
        city=row[0],
        country=row[1],
        lat=float(row[2]),
        lng=float(row[3]),
        population=int(row[4]) if row[4] is not None else None,
        source="gazetteer",
    )
