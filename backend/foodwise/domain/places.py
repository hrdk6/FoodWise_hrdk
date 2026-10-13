"""Nearby places that feed people, from OpenStreetMap tags. Pure functions, no network.

FoodWise does not keep a register of partner organisations. When food is left over it looks
around the person's device (or the canteen) for places such as community kitchens, gurdwara
langars, food banks, shelters, and children's and elders' homes, and ranks them:

    score = 0.40 proximity + 0.20 kind of place + 0.15 can be contacted
          + 0.15 your history with them + 0.10 open now

Nothing is hidden for being closed or far: those places get a note instead, because the
person on the spot knows things the map does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from foodwise.core.geo import estimate_eta_min, haversine_km


@dataclass(frozen=True)
class Kind:
    label: str
    route: str  # people | divert
    weight: float


KINDS: dict[str, Kind] = {
    "community_kitchen": Kind("Community kitchen", "people", 1.0),
    "food_bank": Kind("Food bank", "people", 1.0),
    "shelter": Kind("Shelter", "people", 0.95),
    "children_home": Kind("Children's home", "people", 0.9),
    "elder_home": Kind("Elders' home", "people", 0.85),
    "ngo": Kind("NGO", "people", 0.75),
    "social_facility": Kind("Social service", "people", 0.65),
    "animal_shelter": Kind("Animal shelter or gaushala", "divert", 1.0),
    "compost": Kind("Compost or biogas", "divert", 0.8),
}

CHILD = re.compile(r"child|orphan|juvenile|youth", re.I)
# In India "nursing home" usually names a small private hospital, not a home for elders.
MEDICAL = re.compile(r"clinic|hospital|maternity|diagnostic|nursing home|medical|dental|eye care", re.I)
SENIOR = re.compile(r"senior|elder|aged", re.I)
SENIOR_NAME = re.compile(r"old ?age|vruddh|vridh|senior|elders|ashram", re.I)
NAME_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"gau ?shala|go ?shala|gaushala|goshala|panjrapol|pinjrapol|animal shelter", re.I), "animal_shelter"),
    (re.compile(r"compost|biogas", re.I), "compost"),
    (re.compile(r"langar|annakshetra|anna ?dan|anna ?chhatra|community kitchen|free kitchen", re.I), "community_kitchen"),
    (re.compile(r"roti bank|food bank|feeding india", re.I), "food_bank"),
    (re.compile(r"anath|orphan|bal ?ashram|balgram|bal ?sadan|children'?s home|bal ?bhavan", re.I), "children_home"),
    (re.compile(r"vruddh|vridh|old ?age|senior citizen|elders", re.I), "elder_home"),
    (re.compile(r"night shelter|rain basera|shelter home|homeless", re.I), "shelter"),
]

# The Overpass queries behind each route. `{r}`, `{lat}`, `{lon}` are filled in by the service.
# Tag filters only: a name-pattern search over a city is too slow for public Overpass servers.
# Names are still used by `classify` to sort tagged places (e.g. an "old age home" social facility).
QUERIES = {
    "people": """
[out:json][timeout:25];
(
  nwr["social_facility"](around:{r},{lat},{lon});
  nwr["amenity"~"^(social_facility|food_bank|soup_kitchen|orphanage|nursing_home)$"](around:{r},{lat},{lon});
  nwr["amenity"="place_of_worship"]["religion"="sikh"](around:{r},{lat},{lon});
  nwr["office"~"^(ngo|charity)$"](around:{r},{lat},{lon});
);
out center tags 300;
""",
    "divert": """
[out:json][timeout:25];
(
  nwr["amenity"="animal_shelter"](around:{r},{lat},{lon});
  nwr["animal_shelter"](around:{r},{lat},{lon});
  nwr["amenity"="recycling"]["recycling:organic"="yes"](around:{r},{lat},{lon});
  nwr["amenity"="recycling"]["recycling:green_waste"="yes"](around:{r},{lat},{lon});
);
out center tags 150;
""",
}


def classify(tags: dict) -> str | None:
    """Map OpenStreetMap tags to one of KINDS, or None if the place does not feed anyone."""
    name = tags.get("name", "") or tags.get("operator", "")
    if MEDICAL.search(name) and not SENIOR_NAME.search(name):
        return None
    sf = tags.get("social_facility")
    who = tags.get("social_facility:for", "")
    amenity = tags.get("amenity")
    if amenity == "animal_shelter" or tags.get("animal_shelter"):
        return "animal_shelter"
    if amenity == "recycling" and any(tags.get(k) == "yes" for k in ("recycling:organic", "recycling:green_waste", "recycling:food_waste")):
        return "compost"
    if sf:
        if sf == "food_bank":
            return "food_bank"
        if sf == "soup_kitchen":
            return "community_kitchen"
        if sf in ("shelter", "hostel"):
            return "children_home" if CHILD.search(who) else "shelter"
        if sf in ("group_home", "day_care", "assisted_living", "nursing_home"):
            if CHILD.search(who):
                return "children_home"
            if SENIOR.search(who) or sf in ("assisted_living", "nursing_home"):
                return "elder_home"
        for rx, kind in NAME_RULES:
            if KINDS[kind].route == "people" and rx.search(name):
                return kind
        return "social_facility"
    if amenity == "food_bank":
        return "food_bank"
    if amenity == "soup_kitchen":
        return "community_kitchen"
    if amenity == "orphanage":
        return "children_home"
    if amenity == "nursing_home":
        return "elder_home"
    if amenity == "place_of_worship" and tags.get("religion") == "sikh":
        return "community_kitchen"  # gurdwaras serve langar
    for rx, kind in NAME_RULES:
        if rx.search(name):
            return kind
    if tags.get("office") in ("ngo", "charity"):
        return "ngo"
    if amenity == "social_facility":
        return "social_facility"
    return None


# --------------------------------------------------------------------------- opening hours

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_SPAN = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


def _days(spec: str) -> set[int] | None:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in DAYS or b not in DAYS:
                return None
            i, j = DAYS.index(a), DAYS.index(b)
            out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        elif part in DAYS:
            out.add(DAYS.index(part))
        elif part == "PH":
            continue
        else:
            return None
    return out


def is_open(opening_hours: str | None, at: datetime) -> bool | None:
    """True / False for the common OSM forms ("24/7", "Mo-Sa 09:00-18:00; Su off",
    "08:00-12:00,16:00-20:00"); None when the hours are missing or too unusual to read."""
    if not opening_hours:
        return None
    text = opening_hours.strip()
    if text == "24/7":
        return True
    weekday, minutes = at.weekday(), at.hour * 60 + at.minute
    verdict: bool | None = None
    matched_any = False
    for rule in [r.strip() for r in text.split(";") if r.strip()]:
        parts = rule.split(None, 1)
        if len(parts) == 2 and _days(parts[0]) is not None:
            days, times = _days(parts[0]), parts[1].strip()
        elif _SPAN.match(rule.split(",")[0].strip()) or rule in ("off", "closed"):
            days, times = set(range(7)), rule
        else:
            return None
        if weekday not in days:
            continue
        matched_any = True
        if times in ("off", "closed"):
            verdict = False
            continue
        spans = [t.strip() for t in times.split(",")]
        inside = False
        for span in spans:
            m = _SPAN.match(span)
            if not m:
                return None
            start = int(m[1]) * 60 + int(m[2])
            end = int(m[3]) * 60 + int(m[4])
            if end <= start:
                end += 24 * 60
            if start <= minutes < end or start <= minutes + 24 * 60 < end:
                inside = True
        verdict = inside  # later rules override earlier ones, as in the OSM spec
    return verdict if matched_any else False


# --------------------------------------------------------------------------- places and ranking


@dataclass
class Place:
    ref: str
    name: str
    kind: str
    lat: float
    lon: float
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    opening_hours: str | None = None
    note: str | None = None  # e.g. "Langar" or who the place serves
    source: str = "osm"


@dataclass
class History:
    given: int = 0
    fell_through: int = 0
    kg: float = 0.0
    last: datetime | None = None


@dataclass
class Suggestion:
    place: Place
    distance_km: float
    eta_min: float
    open_now: bool | None
    fits_window: bool
    score: float
    breakdown: dict
    notes: list[str] = field(default_factory=list)
    history: History = field(default_factory=History)


def _phone(tags: dict) -> str | None:
    for k in ("phone", "contact:phone", "contact:mobile", "mobile", "contact:whatsapp"):
        if tags.get(k):
            return tags[k].split(";")[0].strip()[:40]
    return None


def _address(tags: dict) -> str | None:
    if tags.get("addr:full"):
        return tags["addr:full"]
    parts = [tags.get("addr:housenumber"), tags.get("addr:street"), tags.get("addr:suburb") or tags.get("addr:place"),
             tags.get("addr:city")]
    text = ", ".join(p for p in parts if p)
    return text or None


SERVES = {
    "senior": "older people", "homeless": "homeless people", "orphan": "orphans", "child": "children",
    "juvenile": "young people", "women": "women", "mental_health": "people with mental illness",
    "disabled": "disabled people", "drug_addicted": "people recovering from addiction", "migrant": "migrants",
    "refugee": "refugees", "underprivileged": "people in need", "abused": "people escaping abuse",
}


def from_osm(element: dict) -> Place | None:
    tags = element.get("tags") or {}
    kind = classify(tags)
    if kind is None:
        return None
    lat = element.get("lat", (element.get("center") or {}).get("lat"))
    lon = element.get("lon", (element.get("center") or {}).get("lon"))
    if lat is None or lon is None:
        return None
    name = tags.get("name:en") or tags.get("name") or tags.get("operator")
    if name and name.strip().lower() in ("private", "yes", "no", "unknown", "none"):
        name = None
    if not name:
        if tags.get("amenity") == "place_of_worship" and tags.get("religion") == "sikh":
            name = "Gurdwara"
        else:
            return None  # an unnamed place cannot be found or phoned
    note = None
    if tags.get("amenity") == "place_of_worship":
        note = "Gurdwara: langar is served to everyone"
    elif tags.get("social_facility:for"):
        who = [SERVES.get(w.strip(), w.strip().replace("_", " ")) for w in tags["social_facility:for"].split(";") if w.strip()]
        note = "Serves " + ", ".join(dict.fromkeys(who))
    return Place(
        ref=f"osm:{element['type']}/{element['id']}", name=name[:200], kind=kind, lat=float(lat), lon=float(lon),
        address=_address(tags), phone=_phone(tags), website=tags.get("website") or tags.get("contact:website"),
        opening_hours=tags.get("opening_hours"), note=note,
    )


def dedupe(places: list[Place]) -> list[Place]:
    """OSM often maps the same place as a node and a building outline; keep the richer one."""
    out: list[Place] = []
    for p in sorted(places, key=lambda x: -(bool(x.phone) + bool(x.address) + bool(x.opening_hours))):
        if any(q.name.lower() == p.name.lower() and haversine_km(q.lat, q.lon, p.lat, p.lon) < 0.15 for q in out):
            continue
        out.append(p)
    return out


WEIGHTS = {"proximity": 0.40, "kind": 0.20, "contact": 0.15, "history": 0.15, "open": 0.10}


def rank(
    places: list[Place],
    *,
    origin: tuple[float, float],
    radius_km: float,
    now_local: datetime,
    safe_until_local: datetime | None,
    buffer_min: float,
    history: dict[str, History],
) -> list[Suggestion]:
    max_eta = estimate_eta_min(max(radius_km, 1.0))
    out = []
    for p in places:
        km = haversine_km(origin[0], origin[1], p.lat, p.lon)
        eta = estimate_eta_min(km)
        open_now = is_open(p.opening_hours, now_local + timedelta(minutes=eta))
        h = history.get(p.ref, History())
        fits = True
        notes = []
        if safe_until_local is not None:
            spare = (safe_until_local - now_local).total_seconds() / 60 - eta - buffer_min
            if spare < 0:
                fits = False
                notes.append(f"About {int(eta)} min away: too far to reach before the food stops being safe")
        if open_now is False:
            notes.append("Listed as closed at the time you would arrive")
        if not p.phone:
            notes.append("No phone number on the map; use directions or visit")
        if h.fell_through:
            notes.append(f"Fell through {h.fell_through} time{'s' if h.fell_through > 1 else ''} before")
        parts = {
            "proximity": max(0.0, 1.0 - eta / max_eta),
            "kind": KINDS[p.kind].weight,
            "contact": 1.0 if p.phone else 0.4 if p.website else 0.0,
            "history": max(-1.0, min(1.0, h.given / 3 - 0.35 * h.fell_through)),  # let-downs count against
            "open": {True: 1.0, None: 0.5, False: 0.0}[open_now],
        }
        score = sum(WEIGHTS[k] * v for k, v in parts.items())
        out.append(Suggestion(place=p, distance_km=round(km, 2), eta_min=round(eta, 1), open_now=open_now,
                              fits_window=fits, score=round(score, 4), breakdown={k: round(v, 3) for k, v in parts.items()},
                              notes=notes, history=h))
    return sorted(out, key=lambda s: (not s.fits_window, -s.score, s.distance_km, s.place.ref))
