import html
import json
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# SPACE UPDATE v0.8.7.2 · NORDIC CONTRAST · FOCUSED EUROPE + COUNTERSPACE VISUAL
# 16:9 information display for 24–40" monitors
#
# LOCKED CORE FEATURES
# - 16:9 single-screen wall display
# - 5 major actor cards
# - launches + new orbital objects (24h / 7d)
# - Europe capability picture
# - all European launch actors
# - global activity map + compact orbit summary + What Changed?
# - next launches with spaceport
# - featured launch imagery
# - global rolling news ticker
# - automatic data refresh
# ============================================================

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

LL2_BASES = [
    "https://ll.thespacedevs.com/2.3.0",
    "https://ll.thespacedevs.com/2.2.0",
]
CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php"
CELESTRAK_SATCAT = "https://celestrak.org/satcat/records.php"
HEADERS = {"User-Agent": "SpaceUpdateDashboard/0.8-nordic-map (Streamlit 16:9 wall display)"}

NEWS_FEEDS = [
    # Global sources first: business/policy + launch/mission operations.
    ("SpaceNews", "https://spacenews.com/feed/", 0),
    ("Spaceflight Now", "https://spaceflightnow.com/feed/", 1),

    # Institutional sources add verified programme milestones and science.
    ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_News", 2),
    ("EUSPA", "https://www.euspa.europa.eu/pressroom/press-releases/rss.xml", 3),
    ("JPL", "https://www.jpl.nasa.gov/feeds/news/", 4),
]
COUNTERSPACE_FEEDS = [
    # Space / defence acquisition and capability sources.
    ("SpaceNews", "https://spacenews.com/feed/", 0),
    ("DefenseScoop", "https://defensescoop.com/feed/", 0),
    ("Breaking Defense", "https://feeds.feedburner.com/BreakingDefense", 1),
    ("Spaceflight Now", "https://spaceflightnow.com/feed/", 2),
]

LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

ACTOR_COLOURS = {
    # Deliberately muted Nordic accents: clear separation without neon/gaming feel.
    "EUROPE": "#5FA7D8",
    "USA": "#8EA9D1",
    "CHINA": "#D88472",
    "RUSSIA": "#B77A97",
    "OTHER": "#C7A86A",
}

ACTOR_FLAGS = {
    "EUROPE": "🇪🇺",
    "USA": "🇺🇸",
    "CHINA": "🇨🇳",
    "RUSSIA": "🇷🇺",
    "OTHER": "🌍",
}

# Use actual SVG flags in the dashboard instead of relying on OS emoji rendering.
# Windows often renders flag emoji as the two-letter regional code (EU/US/CN/RU).
ACTOR_FLAG_URLS = {
    "EUROPE": "https://flagcdn.com/eu.svg",
    "USA": "https://flagcdn.com/us.svg",
    "CHINA": "https://flagcdn.com/cn.svg",
    "RUSSIA": "https://flagcdn.com/ru.svg",
}

# Fixed European launch actors we always want visible, even when their count is zero.
EUROPEAN_LAUNCH_ACTORS = [
    "Arianespace",
    "Avio",
    "Isar Aerospace",
    "Rocket Factory Augsburg",
    "Orbex",
    "PLD Space",
    "MaiaSpace",
    "Skyrora",
    "HyImpulse",
    "Latitude",
]

# Aliases used to fold API naming variations into one dashboard row.
EUROPEAN_ACTOR_ALIASES = {
    "Arianespace": ["arianespace"],
    "Avio": ["avio"],
    "Isar Aerospace": ["isar aerospace", "isar"],
    "Rocket Factory Augsburg": ["rocket factory augsburg", "rfa"],
    "Orbex": ["orbex"],
    "PLD Space": ["pld space"],
    "MaiaSpace": ["maiaspace", "maia space"],
    "Skyrora": ["skyrora"],
    "HyImpulse": ["hyimpulse"],
    "Latitude": ["latitude"],
}

EUROPEAN_COUNTRY_CODES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "NO", "GB", "CH", "IS",
}

# -----------------------------
# Helpers
# -----------------------------

def esc(value):
    return html.escape(str(value or ""))


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def fmt(value):
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    return str(value)


def launch_provider_name(launch):
    return ((launch.get("launch_service_provider") or {}).get("name") or "Unknown").strip()


def mission_text(launch):
    mission = launch.get("mission") or {}
    return " ".join(
        [
            str(launch.get("name") or ""),
            str(mission.get("name") or ""),
            str(mission.get("description") or ""),
        ]
    )


def orbit_group(launch):
    mission = launch.get("mission") or {}
    orbit = mission.get("orbit") or {}
    value = f"{orbit.get('abbrev', '')} {orbit.get('name', '')}".upper()
    if any(x in value for x in ["LEO", "SSO", "LOW EARTH", "SUN-SYNCHRONOUS", "POLAR"]):
        return "LEO"
    if any(x in value for x in ["MEO", "MEDIUM EARTH"]):
        return "MEO"
    if any(x in value for x in ["GEO", "GEOSTATIONARY", "GTO"]):
        return "GEO"
    return "OTHER"


# ============================================================
# API DATA
# ============================================================


def _session():
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.7,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _request_json(url, params=None, timeout=15):
    """HTTP JSON request with short retries. Exceptions are not cached."""
    with _session() as session:
        r = session.get(url, params=params, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.json()


def _request_text(url, timeout=15):
    with _session() as session:
        r = session.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r.text


def _ll2_json(path, params, timeout=18):
    last_error = None
    for base in LL2_BASES:
        try:
            return _request_json(f"{base}{path}", params, timeout=timeout)
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Launch Library unavailable")


@st.cache_data(ttl=900, show_spinner=False)
def _recent_launches_cached(days=7):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    params = {
        "format": "json",
        "limit": 60,
        "ordering": "-net",
        "net__gte": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        payload = _ll2_json("/launches/previous/", params, timeout=18)
        return payload.get("results", [])
    except Exception:
        # Fallback to an unfiltered recent page and filter locally.
        payload = _ll2_json(
            "/launches/previous/",
            {"format": "json", "limit": 60, "ordering": "-net"},
            timeout=18,
        )
        return [
            x for x in payload.get("results", [])
            if (parse_dt(x.get("net")) and start <= parse_dt(x.get("net")) <= now)
        ]


def get_recent_launches(days=7):
    try:
        data = _recent_launches_cached(days)
        if data:
            st.session_state["last_recent_launches"] = data
        return data, True
    except Exception:
        stale = st.session_state.get("last_recent_launches", [])
        return stale, bool(stale)


@st.cache_data(ttl=900, show_spinner=False)
def _upcoming_launches_cached(days=30):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    params = {
        "format": "json",
        "limit": 60,
        "ordering": "net",
        "net__gte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        payload = _ll2_json("/launches/upcoming/", params, timeout=18)
        return payload.get("results", [])
    except Exception:
        payload = _ll2_json(
            "/launches/upcoming/",
            {"format": "json", "limit": 60, "ordering": "net"},
            timeout=18,
        )
        return [
            x for x in payload.get("results", [])
            if (parse_dt(x.get("net")) and now <= parse_dt(x.get("net")) <= end)
        ]


def get_upcoming_launches(days=30):
    try:
        data = _upcoming_launches_cached(days)
        if data:
            st.session_state["last_upcoming_launches"] = data
        return data, True
    except Exception:
        stale = st.session_state.get("last_upcoming_launches", [])
        return stale, bool(stale)


@st.cache_data(ttl=21600, show_spinner=False)
def _ytd_launches_cached():
    year = datetime.now(timezone.utc).year
    url_path = "/launches/previous/"
    params = {
        "format": "json",
        "limit": 100,
        "ordering": "-net",
        "year": year,
    }
    results = []
    next_url = None
    for page in range(4):
        if page == 0:
            payload = _ll2_json(url_path, params, timeout=18)
        else:
            payload = _request_json(next_url, None, timeout=18)
        results.extend(payload.get("results", []))
        next_url = payload.get("next")
        if not next_url:
            break
    return results


def get_ytd_launches():
    try:
        data = _ytd_launches_cached()
        if data:
            st.session_state["last_ytd_launches"] = data
        return data, True
    except Exception:
        stale = st.session_state.get("last_ytd_launches", [])
        return stale, bool(stale)


@st.cache_data(ttl=7200, show_spinner=False)
def _celestrak_count_cached(query_type, value):
    """Fast GP catalogue count; no retry cascade on a wall display."""
    with requests.Session() as session:
        r = session.get(
            CELESTRAK_GP,
            params={query_type.upper(): value, "FORMAT": "JSON"},
            headers=HEADERS,
            timeout=(1.5, 2.8),
        )
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected CelesTrak response")
    return len(data)


def celestrak_count(query_type, value):
    key = f"{query_type.upper()}::{value}"
    memo = st.session_state.setdefault("_gp_runtime_memo", {})
    now_ts = time.time()
    hit = memo.get(key)
    if hit and hit.get("until", 0) > now_ts:
        return hit.get("value")
    try:
        value_out = _celestrak_count_cached(query_type, value)
        memo[key] = {"value": value_out, "until": now_ts + 7200}
        return value_out
    except Exception:
        memo[key] = {"value": None, "until": now_ts + 300}
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def _catalogued_objects_cached(launch_designator):
    """Fast SATCAT lookup. Intentionally no retry loop: wall-display speed wins."""
    with requests.Session() as session:
        r = session.get(
            CELESTRAK_SATCAT,
            params={"INTDES": launch_designator, "FORMAT": "JSON", "ONORBIT": 1},
            headers=HEADERS,
            timeout=(1.5, 2.8),
        )
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected CelesTrak SATCAT response")
    return len(data)


def catalogued_objects_for_launch(launch_designator):
    """
    One SATCAT attempt per designator per render/session window.
    Successful values live in Streamlit's 6 h cache; temporary failures are
    remembered for 5 minutes so the same dead endpoint cannot stall every panel.
    """
    if not launch_designator:
        return None

    memo = st.session_state.setdefault("_satcat_runtime_memo", {})
    now_ts = time.time()
    hit = memo.get(launch_designator)
    if hit and hit.get("until", 0) > now_ts:
        return hit.get("value")

    try:
        value = _catalogued_objects_cached(launch_designator)
        memo[launch_designator] = {"value": value, "until": now_ts + 21600}
        return value
    except Exception:
        memo[launch_designator] = {"value": None, "until": now_ts + 300}
        return None


def prefetch_object_catalogue(launches):
    """
    Warm only the object counts we actually need, in parallel.
    With 6 workers and a ~3 s read timeout this stays bounded instead of
    freezing the whole 16:9 display for tens of seconds.
    """
    designators = sorted({x.get("launch_designator") for x in launches if x.get("launch_designator")})
    if not designators:
        return

    # Seven days is normally a small set; cap pathological feeds defensively.
    designators = designators[:24]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(catalogued_objects_for_launch, d) for d in designators]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception:
                pass


def _xml_text(element, names):
    for child in element.iter():
        tag = child.tag.split("}")[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


@st.cache_data(ttl=900, show_spinner=False)
def _news_cached():
    """
    Global space-news stream.

    Design goals:
    - SpaceNews + Spaceflight Now are primary global sources.
    - ESA/EUSPA/JPL add verified institutional milestones.
    - Slow/dead feeds may not delay the wall display.
    - Headlines are ranked for strategic/operational relevance rather than
      simply showing whichever source published most recently.
    """

    importance_terms = {
        # Strategic / security
        "military": 8, "defence": 8, "defense": 8, "security": 6,
        "space force": 7, "intelligence": 6, "sda": 7, "ssa": 6,
        "counterspace": 8, "anti-satellite": 9, "asat": 9,
        "jamming": 7, "spoofing": 7, "missile": 6,

        # Major space infrastructure / programmes
        "constellation": 6, "satellite": 4, "launch": 5, "rocket": 4,
        "spaceport": 5, "mission": 3, "contract": 4, "funding": 4,
        "galileo": 7, "copernicus": 7, "iris": 8, "govsatcom": 8,
        "starlink": 5, "oneweb": 5, "kuiper": 5, "amazon leo": 5,
        "qianfan": 6, "guowang": 6, "sentinel": 6,
        "ariane": 6, "vega": 6,

        # Significant operational events
        "failure": 8, "anomaly": 7, "collision": 9, "debris": 7,
        "reentry": 5, "maneuver": 5, "manoeuvre": 5,
        "deployment": 5, "operational": 4, "service": 3,
    }

    downrank_terms = {
        "podcast": -5, "webinar": -5, "opinion": -4, "sponsored": -6,
        "career": -5, "internship": -5, "merch": -8, "pokemon": -8,
        "anniversary": -3, "photo of the week": -5,
    }

    source_boost = {
        "SpaceNews": 4,
        "Spaceflight Now": 3,
        "ESA": 2,
        "EUSPA": 2,
        "JPL": 1,
    }

    def parse_feed(source, url, priority):
        out = []
        try:
            xml = _request_text(url, timeout=7)
            root = ET.fromstring(xml)
            candidates = [
                x for x in root.iter()
                if x.tag.split("}")[-1].lower() in ("item", "entry")
            ]

            for item in candidates[:12]:
                title = _xml_text(item, {"title"}).strip()
                if not title:
                    continue

                link = _xml_text(item, {"link"})
                if not link:
                    for child in item.iter():
                        if child.tag.split("}")[-1].lower() == "link":
                            link = child.attrib.get("href", "")
                            if link:
                                break

                raw_date = _xml_text(
                    item, {"pubdate", "published", "updated", "date"}
                )
                dt = None
                if raw_date:
                    try:
                        dt = parsedate_to_datetime(raw_date)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            dt = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00")
                            )
                        except Exception:
                            dt = None

                description = _xml_text(
                    item, {"description", "summary", "content", "encoded"}
                )

                # Prefer image URLs already present in RSS/Atom.
                image_url = ""
                for child in item.iter():
                    tag = child.tag.split("}")[-1].lower()
                    if tag in ("content", "thumbnail", "enclosure"):
                        candidate = (
                            child.attrib.get("url")
                            or child.attrib.get("href")
                            or ""
                        )
                        media_type = (child.attrib.get("type") or "").lower()
                        if candidate and (
                            media_type.startswith("image/")
                            or re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", candidate, re.I)
                            or tag in ("thumbnail", "content")
                        ):
                            image_url = candidate
                            break

                if not image_url and description:
                    m = re.search(
                        r'<img[^>]+src=["\']([^"\']+)["\']',
                        description,
                        flags=re.I,
                    )
                    if m:
                        image_url = m.group(1)

                low = f"{title} {description}".lower()
                score = source_boost.get(source, 0)

                for term, points in importance_terms.items():
                    if term in low:
                        score += points

                for term, points in downrank_terms.items():
                    if term in low:
                        score += points

                # Freshness matters, but it cannot make a trivial story outrank
                # a strategically important one from a day earlier.
                if dt:
                    age_hours = max(
                        0,
                        (datetime.now(timezone.utc) - dt.astimezone(timezone.utc))
                        .total_seconds() / 3600,
                    )
                    if age_hours <= 24:
                        score += 4
                    elif age_hours <= 72:
                        score += 3
                    elif age_hours <= 168:
                        score += 1

                out.append({
                    "source": source,
                    "title": title,
                    "link": link,
                    "date": dt,
                    "priority": priority,
                    "score": score,
                    "summary": description,
                    "image": image_url,
                })
        except Exception:
            return []
        return out

    items = []
    with ThreadPoolExecutor(max_workers=len(COUNTERSPACE_FEEDS)) as pool:
        futures = [
            pool.submit(parse_feed, source, url, priority)
            for source, url, priority in COUNTERSPACE_FEEDS
        ]
        for future in as_completed(futures):
            try:
                items.extend(future.result())
            except Exception:
                pass

    # Deduplicate near-identical headlines.
    seen = set()
    unique = []
    for item in items:
        key = re.sub(r"\W+", "", item["title"].lower())[:130]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    floor = datetime.now(timezone.utc) - timedelta(days=10)
    fresh = [
        x for x in unique
        if x["date"] is None or x["date"].astimezone(timezone.utc) >= floor
    ]

    fresh.sort(
        key=lambda x: (
            x.get("score", 0),
            x["date"] or datetime(1970, 1, 1, tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    # Source diversity: no single publisher gets to fill the whole ticker.
    selected = []
    per_source = defaultdict(int)
    for item in fresh:
        if per_source[item["source"]] >= 4:
            continue
        selected.append(item)
        per_source[item["source"]] += 1
        if len(selected) >= 12:
            break

    return selected


def get_news():
    try:
        data = _news_cached()
        if data:
            st.session_state["last_news"] = data
        return data, bool(data)
    except Exception:
        stale = st.session_state.get("last_news", [])
        return stale, bool(stale)


def provider_logo_from_launch(launch):
    """Use the logo already embedded in LL2 launch data; no extra API call."""
    agency = launch.get("launch_service_provider") or {}
    logo = agency.get("logo") or agency.get("social_logo") or {}
    return logo.get("image_url")


# ============================================================
# CLASSIFICATION
# ============================================================

def major_actor(launch):
    provider = launch_provider_name(launch).lower()
    pad_country = (((launch.get("pad") or {}).get("country") or {}).get("alpha_2_code") or "").upper()

    if any(k in provider for k in [
        "arianespace", "avio", "isar aerospace", "rocket factory augsburg",
        "orbex", "pld space", "maiaspace", "skyrora", "hyimpulse", "latitude",
    ]):
        return "EUROPE"
    if any(k in provider for k in [
        "spacex", "united launch alliance", "blue origin", "firefly", "northrop",
        "rocket lab", "astra", "abl", "varda",
    ]):
        return "USA"
    if any(k in provider for k in [
        "china aerospace", "casc", "expace", "galactic energy", "landspace",
        "space pioneer", "cas space", "ispace china", "orien", "deep blue",
    ]):
        return "CHINA"
    if any(k in provider for k in ["roscosmos", "russian space", "khrunichev", "progress rocket"]):
        return "RUSSIA"

    if pad_country == "US":
        return "USA"
    if pad_country == "CN":
        return "CHINA"
    if pad_country == "RU":
        return "RUSSIA"
    if pad_country in EUROPEAN_COUNTRY_CODES:
        return "EUROPE"
    return "OTHER"


def european_launch_actor(launch):
    """
    European launch-provider actor. Uses provider first, then European launch site
    as fallback for an otherwise-unmapped provider.
    """
    provider = launch_provider_name(launch)
    p = provider.lower()
    for canonical, aliases in EUROPEAN_ACTOR_ALIASES.items():
        if any(alias in p for alias in aliases):
            return canonical

    pad_country = (((launch.get("pad") or {}).get("country") or {}).get("alpha_2_code") or "").upper()
    if pad_country in EUROPEAN_COUNTRY_CODES:
        return provider if provider and provider != "Unknown" else "Other Europe"
    return None


# ============================================================
# NEW OBJECT COUNTS
# ============================================================

def estimate_payload_count_from_text(launch):
    """
    Conservative fallback when a new launch has no launch_designator yet.
    Returns an estimate only when the mission text contains an explicit count.
    """
    text = mission_text(launch).lower()

    # Strong phrases first.
    patterns = [
        r"batch of\s+(\d+)\s+(?:satellites|spacecraft|cubesats|payloads)",
        r"carry(?:ing)?\s+(\d+)\s+(?:satellites|spacecraft|cubesats|payloads)",
        r"with\s+(\d+)\s+(?:satellites|spacecraft|cubesats|payloads)",
        r"(\d+)\s+x\s+[a-z0-9-]+",
        r"(\d+)\s+(?:satellites|cubesats)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            try:
                n = int(m.group(1))
                if 1 <= n <= 500:
                    return n
            except Exception:
                pass

    # Explicit list pattern such as "5 cubesats and 1 non-separable experiment":
    # count only the orbital spacecraft phrase we can identify safely.
    return None


def object_count_for_launch(launch):
    designator = launch.get("launch_designator")
    exact = catalogued_objects_for_launch(designator) if designator else None
    status = ((launch.get("status") or {}).get("abbrev") or "").lower()
    if exact is not None:
        # A successful orbital launch with zero SATCAT records is usually catalogue lag, not a real zero.
        if exact == 0 and status in ("success", "go", "tbc"):
            exact = None
        else:
            return exact, "catalogued"
    estimate = estimate_payload_count_from_text(launch)
    if estimate is not None:
        return estimate, "estimated"
    return None, "unknown"


# ============================================================
# EUROPE STATS
# ============================================================

def build_europe_actor_stats(recent, upcoming):
    stats = {
        actor: {
            "launches_7d": 0,
            "objects_known": 0,
            "objects_unknown_launches": 0,
            "objects_estimated": False,
            "planned_30d": 0,
            "logo": None,
        }
        for actor in EUROPEAN_LAUNCH_ACTORS
    }

    # Add dynamically discovered European launch providers too.
    for launch in list(recent) + list(upcoming):
        actor = european_launch_actor(launch)
        if actor and actor not in stats:
            stats[actor] = {
                "launches_7d": 0,
                "objects_known": 0,
                "objects_unknown_launches": 0,
                "objects_estimated": False,
                "planned_30d": 0,
                "logo": None,
            }

    for launch in recent:
        actor = european_launch_actor(launch)
        if not actor:
            continue
        stats[actor]["launches_7d"] += 1
        if not stats[actor]["logo"]:
            stats[actor]["logo"] = provider_logo_from_launch(launch)
        count, source = object_count_for_launch(launch)
        if count is None:
            stats[actor]["objects_unknown_launches"] += 1
        else:
            stats[actor]["objects_known"] += count
            if source == "estimated":
                stats[actor]["objects_estimated"] = True

    for launch in upcoming:
        actor = european_launch_actor(launch)
        if actor:
            stats[actor]["planned_30d"] += 1
            if not stats[actor]["logo"]:
                stats[actor]["logo"] = provider_logo_from_launch(launch)

    # Keep fixed actors + dynamic actors with any activity. Zero fixed rows stay visible.
    ordered = []
    for actor in EUROPEAN_LAUNCH_ACTORS:
        ordered.append((actor, stats[actor]))
    extras = [
        (actor, values)
        for actor, values in stats.items()
        if actor not in EUROPEAN_LAUNCH_ACTORS
        and (values["launches_7d"] or values["planned_30d"])
    ]
    extras.sort(key=lambda x: (-x[1]["launches_7d"], -x[1]["planned_30d"], x[0]))
    return ordered + extras


def europe_capability_stats(recent, upcoming, ytd):
    def matches(launch, needles):
        t = mission_text(launch).lower()
        return any(n in t for n in needles)

    def delta_objects(needles):
        total = 0
        unknown = False
        for launch in recent:
            if matches(launch, needles):
                count, _ = object_count_for_launch(launch)
                if count is None:
                    unknown = True
                else:
                    total += count
        return f"≥{total}" if unknown and total else ("?" if unknown else str(total))

    def planned_launches(needles):
        return sum(1 for launch in upcoming if matches(launch, needles))

    # Current tracked totals from CelesTrak. Fetch the three independent
    # catalogue counts in parallel so a slow endpoint costs ~3 s, not ~9 s.
    catalogue_queries = {
        "galileo": ("GROUP", "GALILEO"),
        "sentinel": ("NAME", "SENTINEL"),
        "oneweb": ("GROUP", "ONEWEB"),
    }
    catalogue_totals = {k: None for k in catalogue_queries}
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_map = {pool.submit(celestrak_count, *q): k for k, q in catalogue_queries.items()}
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                catalogue_totals[key] = future.result()
            except Exception:
                catalogue_totals[key] = None
    galileo_total = catalogue_totals["galileo"]
    sentinel_total = catalogue_totals["sentinel"]
    oneweb_total = catalogue_totals["oneweb"]

    # European launch totals are launch events, not spacecraft.
    europe_launch_7d = sum(1 for x in recent if european_launch_actor(x))
    europe_launch_ytd = sum(1 for x in ytd if european_launch_actor(x)) if ytd is not None else None
    europe_plan_30d = sum(1 for x in upcoming if european_launch_actor(x))

    return [
        {
            "name": "GALILEO",
            "change": delta_objects(["galileo"]),
            "total": fmt(galileo_total),
            "planned": planned_launches(["galileo"]),
            "sub": "MEO · tracked objects",
        },
        {
            "name": "COPERNICUS / SENTINEL",
            "change": delta_objects(["sentinel", "copernicus"]),
            "total": fmt(sentinel_total),
            "planned": planned_launches(["sentinel", "copernicus"]),
            "sub": "LEO · tracked objects",
        },
        {
            "name": "ONEWEB",
            "change": delta_objects(["oneweb"]),
            "total": fmt(oneweb_total),
            "planned": planned_launches(["oneweb"]),
            "sub": "LEO · tracked objects",
        },
        {
            "name": "EUROPEAN LAUNCH",
            "change": europe_launch_7d,
            "total": fmt(europe_launch_ytd),
            "planned": europe_plan_30d,
            "sub": "launches · total = year to date",
        },
    ]




# ============================================================
# 06 · NORDIC CONTRAST DESIGN · v0.8
#
# INTENT
# - 24" 1080p is the readability baseline.
# - High-contrast Scandinavian "daylight" palette.
# - One 16:9 screen, no interaction required.
# - Global activity map is the primary graphic.
# - Europe remains the capability-focus panel.
# ============================================================

st.markdown(
    """
<style>
/* ----------------------------------------------------------
   A. APP SHELL
   ---------------------------------------------------------- */
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {
    display:none !important;
}
[data-testid="stSidebar"] {display:none !important;}

.block-container {
    max-width:100vw !important;
    padding:.38rem .62rem .30rem .62rem !important;
}
.stApp {
    background:#E8EEF0;
    color:#17262D;
}
html, body, [class*="css"] {
    font-family:Inter,"Segoe UI",Arial,sans-serif;
}
div[data-testid="stVerticalBlock"] {gap:.34rem;}
div[data-testid="stHorizontalBlock"] {gap:.58rem;}

/* ----------------------------------------------------------
   B. HEADER
   ---------------------------------------------------------- */
.hero {
    display:flex;
    justify-content:space-between;
    align-items:flex-end;
    margin:0 0 .12rem 0;
}
.hero-title {
    color:#17313A;
    font-size:clamp(31px,1.85vw,39px);
    font-weight:780;
    letter-spacing:.075em;
    line-height:1;
}
.hero-sub {
    color:#5E747E;
    font-size:clamp(11px,.69vw,14px);
    letter-spacing:.105em;
    margin-top:.24rem;
}
.hero-time {
    text-align:right;
    color:#617780;
    font-size:9px;
    letter-spacing:.09em;
}

.hero-owner {
    color:#365B68;
    font-size:10px;
    font-weight:820;
    letter-spacing:.11em;
    margin-bottom:3px;
}
.hero-time strong {
    display:block;
    color:#17313A;
    font-size:18px;
    font-weight:760;
    margin-top:1px;
}
.live-dot {
    display:inline-block;
    width:7px; height:7px;
    border-radius:50%;
    background:#6F9F87;
    margin-right:5px;
}

/* ----------------------------------------------------------
   C. GLOBAL NEWS TICKER
   ---------------------------------------------------------- */
.news-ticker {
    height:30px;
    overflow:hidden;
    position:relative;
    background:#17313A;
    border-radius:7px;
    margin:.12rem 0 .20rem;
}
.news-ticker:before {
    content:"GLOBAL SPACE NEWS";
    position:absolute;
    z-index:3;
    left:0; top:0; bottom:0;
    display:flex;
    align-items:center;
    padding:0 11px;
    background:#224550;
    color:#E6F0F2;
    font-size:9px;
    font-weight:800;
    letter-spacing:.11em;
    border-right:1px solid #41606A;
}
.news-track {
    display:flex;
    width:max-content;
    height:30px;
    align-items:center;
    animation:news-scroll 100s linear infinite;
    padding-left:126px;
}
.news-set {display:flex;align-items:center;white-space:nowrap;}
.news-item {font-size:10px;color:#EFF4F4;margin-right:32px;}
.news-source {color:#9EC8D5;font-weight:800;letter-spacing:.05em;margin-right:6px;}
.news-dot {color:#66808A;margin-right:11px;}
@keyframes news-scroll {from{transform:translateX(0)} to{transform:translateX(-50%)}}

/* ----------------------------------------------------------
   D. SECTION LABELS
   ---------------------------------------------------------- */
.section-row {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin:.16rem 0 .16rem;
}
.section-title {
    font-size:11px;
    font-weight:800;
    color:#415861;
    letter-spacing:.12em;
}
.source-note {
    font-size:8px;
    color:#778C94;
    letter-spacing:.08em;
}

/* ----------------------------------------------------------
   E. MAJOR ACTOR CARDS
   ---------------------------------------------------------- */
.actor-card {
    min-height:103px;
    padding:9px 12px;
    border-radius:11px;
    background:#FFFFFF;
    border:1px solid #C5D2D6;
    border-top:4px solid var(--accent);
    box-shadow:0 1px 2px rgba(25,50,60,.05);
}
.actor-topline {
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin-bottom:5px;
}
.actor-name {
    color:var(--accent);
    font-size:11px;
    font-weight:820;
    letter-spacing:.10em;
}
.actor-flag {
    width:32px; height:23px;
    border-radius:5px;
    overflow:hidden;
    border:1px solid #B8C6CB;
    background:#EDF2F3;
    display:flex; align-items:center; justify-content:center;
}
.actor-flag img {width:100%;height:100%;object-fit:cover;display:block;}
.actor-flag.globe {font-size:16px;}
.actor-metrics {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:8px;
}
.actor-metric + .actor-metric {
    border-left:1px solid #D5DEE1;
    padding-left:12px;
}
.actor-metric-label {
    color:#70858E;
    font-size:8px;
    font-weight:800;
    letter-spacing:.08em;
}
.actor-value-line {
    display:flex;
    align-items:flex-end;
    gap:7px;
    margin-top:2px;
}
.actor-big {
    color:#17262D;
    font-size:clamp(29px,1.75vw,36px);
    font-weight:780;
    line-height:.92;
}
.actor-period {
    color:#80939B;
    font-size:8px;
    padding-bottom:2px;
}
.actor-24h {
    color:#6D828B;
    font-size:9px;
    margin-top:4px;
}
.actor-24h strong {color:#1F353E;font-size:11px;font-weight:800;}

/* ----------------------------------------------------------
   F. PANEL CONTAINERS
   ---------------------------------------------------------- */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background:#FFFFFF;
    border:1px solid #C5D2D6 !important;
    border-radius:12px !important;
    box-shadow:0 1px 3px rgba(25,50,60,.05) !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding:.50rem .60rem .48rem !important;
}
.panel-heading {
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding-bottom:7px;
    border-bottom:1px solid #D3DDE0;
    margin-bottom:7px;
}
.panel-title {
    font-size:12px;
    font-weight:820;
    color:#29434D;
    letter-spacing:.10em;
}
.panel-note {
    font-size:8px;
    color:#7A8F97;
    letter-spacing:.06em;
}

/* ----------------------------------------------------------
   G. EUROPE · REFERENCE CARDS
   ---------------------------------------------------------- */
.eu-summary {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:6px;
    margin-bottom:7px;
}
.eu-summary-box {
    background:#F0F5F6;
    border:1px solid #D1DDE0;
    border-radius:8px;
    padding:6px 8px;
}
.eu-summary-label {
    color:#64808D;
    font-size:8px;
    font-weight:800;
    letter-spacing:.08em;
}
.eu-summary-value {
    color:#18343E;
    font-size:21px;
    font-weight:800;
    margin-top:2px;
    line-height:1;
}
.eu-ref-grid {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:7px;
}
.eu-ref-card {
    border:1px solid #CCD9DD;
    border-radius:9px;
    padding:8px 9px;
    background:#FAFCFC;
    min-height:106px;
}
.eu-ref-top {
    display:flex;
    justify-content:space-between;
    align-items:flex-start;
    gap:8px;
}
.eu-ref-name {
    color:#2A5668;
    font-size:10px;
    font-weight:830;
    letter-spacing:.05em;
}
.eu-ref-role {
    color:#6A838D;
    font-size:8px;
    font-weight:800;
    letter-spacing:.075em;
    margin-top:2px;
}
.eu-ref-main {
    color:#172A32;
    font-size:17px;
    font-weight:800;
    text-align:right;
    white-space:nowrap;
}
.eu-ref-line {
    color:#536B75;
    font-size:9px;
    margin-top:5px;
    line-height:1.25;
}
.eu-ref-line strong {color:#213A43;font-weight:800;}
.eu-ref-use {
    margin-top:5px;
    padding-top:5px;
    border-top:1px solid #E0E7E9;
    color:#365B68;
    font-size:9px;
    line-height:1.25;
}
.eu-ref-use b {
    color:#6A8792;
    font-size:8px;
    letter-spacing:.06em;
}
.eu-commercial strong {color:#213D46;}
.dot-active,.dot-plan,.dot-idle {
    width:6px;height:6px;border-radius:50%;display:inline-block;
}
.legend-dot,.legend-ring,.legend-both {
    display:inline-block;
    width:8px;height:8px;border-radius:50%;
    margin-right:4px;vertical-align:-1px;
}
.legend-dot {background:#B58B3E;}
.legend-ring {border:2px solid #477080;}
.legend-both {background:#668F7A;border:1px solid #477080;}
.orbit-strip {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:6px;
    margin-top:3px;
}
.orbit-pill {
    background:#F0F5F6;
    border:1px solid #D2DDE0;
    border-radius:8px;
    padding:6px 8px;
    display:flex;
    align-items:center;
    justify-content:space-between;
}
.orbit-pill-name {
    color:#5D747E;
    font-size:9px;
    font-weight:800;
}
.orbit-pill-count {
    color:#18333D;
    font-size:18px;
    font-weight:800;
}

/* ----------------------------------------------------------
   I. WHAT CHANGED
   ---------------------------------------------------------- */
.change-v08 {
    display:grid;
    grid-template-columns:4px 28px 1fr auto;
    gap:7px;
    align-items:center;
    padding:6px 0;
    border-bottom:1px solid #E0E7E9;
}
.change-v08:last-child {border-bottom:none;}
.change-accent {
    width:4px;height:30px;border-radius:3px;background:var(--accent);
}
.change-flag {
    width:25px;height:18px;border-radius:4px;overflow:hidden;
    border:1px solid #C2CED2;display:flex;align-items:center;justify-content:center;
    font-size:12px;background:#F1F5F6;
}
.change-flag img {width:100%;height:100%;object-fit:cover;}
.change-main {
    font-size:10px;color:#20353D;font-weight:760;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.change-sub {
    color:#6D828B;font-size:8px;margin-top:1px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.change-orbit {color:#63808B;font-size:9px;font-weight:800;}

/* ----------------------------------------------------------
   J. NEXT LAUNCHES
   ---------------------------------------------------------- */
.next-v08 {
    display:grid;
    grid-template-columns:66px 1fr;
    gap:9px;
    padding:6px 0;
    border-bottom:1px solid #E0E7E9;
}
.next-v08:last-child {border-bottom:none;}
.next-when {color:#947536;font-size:11px;font-weight:820;}
.next-tminus {color:#87999F;font-size:8px;margin-top:2px;}
.next-name {
    color:#1D3139;font-size:11px;font-weight:780;line-height:1.18;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.next-spaceport {
    color:#526E79;font-size:9px;margin-top:2px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.next-meta {color:#84969D;font-size:7px;margin-top:0;}

/* ----------------------------------------------------------
   K. FEATURED LAUNCH
   ---------------------------------------------------------- */
.featured-label {
    margin-top:8px;color:#506B75;font-size:9px;font-weight:820;letter-spacing:.09em;
}
.featured-fallback {
    height:194px;margin-top:5px;border:1px solid #CDD8DB;border-radius:9px;
    background:#EEF3F4;display:flex;align-items:center;justify-content:center;
    color:#71858D;font-size:10px;
}


/* ----------------------------------------------------------
   L. COUNTERSPACE WATCH
   ---------------------------------------------------------- */
.counter-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin-top:7px;
    padding-top:6px;
    border-top:1px solid #D5DFE2;
}
.counter-title {
    color:#7A493F;
    font-size:9px;
    font-weight:830;
    letter-spacing:.10em;
}
.counter-period {
    color:#8A9BA1;
    font-size:8px;
}
.counter-card {
    margin-top:5px;
    border:1px solid #D8C9C5;
    border-radius:9px;
    overflow:hidden;
    background:#FCF8F7;
}
.counter-image {
    height:214px;
    position:relative;
    overflow:hidden;
    background:
      linear-gradient(135deg,#D8E0E1 0%,#EEF2F2 55%,#E2D7D3 100%);
}
.counter-image img {
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.counter-image-shade {
    position:absolute;
    inset:0;
    background:linear-gradient(180deg,rgba(12,22,27,.02) 25%,rgba(15,27,32,.84) 100%);
}
.counter-badge {
    position:absolute;
    top:6px;
    left:7px;
    padding:3px 6px;
    border-radius:999px;
    background:#8B5549;
    color:#FFF8F5;
    font-size:7px;
    font-weight:820;
    letter-spacing:.07em;
}
.counter-caption {
    position:absolute;
    left:9px;
    right:9px;
    bottom:7px;
    color:#FFFFFF;
}
.counter-story {
    font-size:12px;
    font-weight:780;
    line-height:1.18;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.counter-source {
    margin-top:3px;
    color:#D7E0E1;
    font-size:8px;
}
.counter-no-image {
    height:214px;
    display:flex;
    align-items:center;
    justify-content:center;
    padding:10px;
    text-align:center;
    color:#7B6560;
    font-size:10px;
    font-weight:700;
}
.counter-second {
    display:grid;
    grid-template-columns:7px 1fr;
    gap:8px;
    align-items:center;
    padding:7px 9px;
    border-top:1px solid #E7DCD9;
}
.counter-dot {
    width:7px;
    height:7px;
    border-radius:50%;
    background:#A3685B;
}
.counter-second-title {
    color:#4A3935;
    font-size:9px;
    font-weight:700;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.counter-empty {
    margin-top:5px;
    padding:9px;
    border:1px dashed #D8C9C5;
    border-radius:8px;
    background:#FCF8F7;
    color:#806B65;
    font-size:9px;
}


.counter-baseline {
    margin-top:5px;
    min-height:104px;
    border:1px solid #D8C9C5;
    border-radius:9px;
    background:
        radial-gradient(circle at 80% 22%, rgba(163,104,91,.13), transparent 24%),
        linear-gradient(135deg,#FCF8F7,#F2ECEA);
    padding:10px;
}
.counter-baseline-title {
    color:#65483F;
    font-size:10px;
    font-weight:820;
    letter-spacing:.06em;
}
.counter-baseline-sub {
    color:#806B65;
    font-size:8px;
    margin-top:3px;
}
.counter-baseline-grid {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:5px;
    margin-top:9px;
}
.counter-baseline-chip {
    border:1px solid #DFCFCA;
    border-radius:7px;
    padding:5px 4px;
    background:rgba(255,255,255,.62);
    color:#674F48;
    font-size:7px;
    font-weight:730;
    text-align:center;
}

/* ----------------------------------------------------------
   L. WARNINGS + FOOTER
   ---------------------------------------------------------- */
.source-warning {
    background:#FFF4DB;border:1px solid #E2C98F;color:#715D31;
    border-radius:7px;padding:5px 8px;font-size:9px;margin-bottom:4px;
}
.footerline {
    display:flex;justify-content:space-between;
    color:#73888F;font-size:7px;letter-spacing:.04em;margin-top:3px;
}

@media (max-width:1450px) {
    .hero-title {font-size:30px;}
    .actor-big {font-size:29px;}
    .eu-ref-line,.eu-ref-use {font-size:8.5px;}
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# 07 · HELPERS FOR v0.8
# ============================================================

# Curated European programme reference data.
# These values change slowly and are deliberately separated from the live feeds.
EU_REFERENCE_UPDATED = "SEP 2026"

EU_REFERENCE = {
    # Galileo: ESA describes the current system as 28 satellites in all.
    # Nominal full constellation: 30 (27 operational + 3 active spares).
    "GALILEO_NOW": "28 sats",
    "GALILEO_NOMINAL": "30 nominal",
    "GALILEO_G2_BUILD": "12 G2",
    "GALILEO_NEXT": "2 FOC · end 2026",

    # Core Sentinel platforms currently in orbit / operational or commissioning:
    # S1C/D (2), S2A/B/C (3), S3A/B/C (3), S5P (1) = 9.
    "COPERNICUS_NOW": "9 core sats",
    "COPERNICUS_EXPANSION": "6 missions · 12 sats",

    # IRIS²: dedicated constellation is not yet deployed.
    "IRIS2_NOW": "0 sats",
    "IRIS2_TARGET": "348 sats",
    "IRIS2_ORBITS": "LEO + MEO",
    "IRIS2_FIRST": "2029",
    "IRIS2_FULL": "2030",

    # GOVSATCOM is a service pooling existing sovereign/commercial capacity,
    # not a dedicated satellite constellation.
    "GOVSATCOM_DEDICATED": "0 dedicated",
    "GOVSATCOM_LIVE": "Jan 2026",
    "GOVSATCOM_POOL": "5 Member States",
    "GOVSATCOM_IRIS": "IRIS² from 2029",
}

# Country centroids used for news rings on the map.
NEWS_GEO = [
    ("United States", "USA", 39.5, -98.35, [
        "united states", "u.s.", " us ", "nasa", "spacex", "starlink",
        "blue origin", "united launch alliance", "ula", "vandenberg",
        "cape canaveral", "kennedy space center", "space force",
    ]),
    ("China", "CHN", 35.9, 104.2, [
        "china", "chinese", "long march", "cnsa", "casc",
        "qianfan", "guowang", "jiuquan", "wenchang", "xichang",
    ]),
    ("Russia", "RUS", 61.5, 105.3, [
        "russia", "russian", "roscosmos", "soyuz", "vostochny", "plesetsk",
    ]),
    ("India", "IND", 20.6, 78.9, [
        "india", "indian", "isro", "sriharikota",
    ]),
    ("Japan", "JPN", 36.2, 138.3, [
        "japan", "japanese", "jaxa", "tanegashima",
    ]),
    ("South Korea", "KOR", 36.3, 127.8, [
        "south korea", "korean", "hanwha", "kari",
    ]),
    ("France", "FRA", 46.2, 2.2, [
        "france", "french", "arianespace", "cnes", "kourou",
    ]),
    ("Germany", "DEU", 51.2, 10.4, [
        "germany", "german", "isar aerospace", "rfa", "rocket factory augsburg",
    ]),
    ("Italy", "ITA", 42.8, 12.8, [
        "italy", "italian", "avio", "thales alenia space italy",
    ]),
    ("United Kingdom", "GBR", 54.5, -2.5, [
        "united kingdom", "britain", "british", "uk space", "saxavord", "orbex",
    ]),
    ("Norway", "NOR", 61.0, 8.5, [
        "norway", "norwegian", "andøya", "andoya",
    ]),
    ("Sweden", "SWE", 62.0, 15.0, [
        "sweden", "swedish", "esrange",
    ]),
    ("Denmark", "DNK", 56.0, 10.0, [
        "denmark", "danish",
    ]),
    ("Spain", "ESP", 40.4, -3.7, [
        "spain", "spanish", "pld space", "miura",
    ]),
    ("Belgium / EU", "BEL", 50.85, 4.35, [
        "european commission", "european union", "eu space",
        "iris²", "iris2", "govsatcom", "galileo", "copernicus",
    ]),
    ("New Zealand", "NZL", -41.0, 174.0, [
        "new zealand", "mahia", "rocket lab",
    ]),
    ("Australia", "AUS", -25.3, 133.8, [
        "australia", "australian",
    ]),
    ("Canada", "CAN", 56.1, -106.3, [
        "canada", "canadian",
    ]),
    ("Brazil", "BRA", -14.2, -51.9, [
        "brazil", "brazilian", "alcantara", "alcântara",
    ]),
    ("United Arab Emirates", "ARE", 23.4, 53.8, [
        "united arab emirates", "uae", "emirati",
    ]),
]

ALPHA2_TO_ALPHA3 = {
    "US":"USA","CN":"CHN","RU":"RUS","IN":"IND","JP":"JPN","KR":"KOR",
    "FR":"FRA","DE":"DEU","IT":"ITA","GB":"GBR","NO":"NOR","SE":"SWE",
    "DK":"DNK","ES":"ESP","BE":"BEL","NZ":"NZL","AU":"AUS","CA":"CAN",
    "BR":"BRA","AE":"ARE","GF":"GUF","KZ":"KAZ","IR":"IRN","IL":"ISR",
}


def raw_html(markup):
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def section_header(title, note=""):
    raw_html(
        f'<div class="section-row"><div class="section-title">{esc(title)}</div>'
        f'<div class="source-note">{esc(note)}</div></div>'
    )


def _display_object_total(known, unknown, estimated=False):
    if unknown:
        text = f"≥{known}" if known else "?"
    else:
        text = str(known)
    if estimated and text != "?":
        text += "*"
    return text


def flag_markup(actor, css_class="actor-flag"):
    if actor in ACTOR_FLAG_URLS:
        return (
            f'<div class="{css_class}"><img src="{ACTOR_FLAG_URLS[actor]}" '
            f'alt="{actor} flag"></div>'
        )
    return f'<div class="{css_class} globe">🌍</div>'


def render_actor_card(actor, launches24, launches7, obj24, obj7, data_ok=True):
    colour = ACTOR_COLOURS[actor]
    l24 = launches24 if data_ok else "—"
    l7 = launches7 if data_ok else "—"
    o24 = obj24 if data_ok else "—"
    o7 = obj7 if data_ok else "—"

    raw_html(
        f'<div class="actor-card" style="--accent:{colour}">'
        f'<div class="actor-topline"><div class="actor-name">{actor}</div>{flag_markup(actor)}</div>'
        f'<div class="actor-metrics">'
        f'<div class="actor-metric"><div class="actor-metric-label">LAUNCHES</div>'
        f'<div class="actor-value-line"><div class="actor-big">{l7}</div><div class="actor-period">7 DAYS</div></div>'
        f'<div class="actor-24h"><strong>{l24}</strong> · 24H</div></div>'
        f'<div class="actor-metric"><div class="actor-metric-label">NEW OBJECTS</div>'
        f'<div class="actor-value-line"><div class="actor-big">{o7}</div><div class="actor-period">7 DAYS</div></div>'
        f'<div class="actor-24h"><strong>{o24}</strong> · 24H</div></div>'
        f'</div></div>'
    )


def render_news_ticker(items):
    if not items:
        raw_html(
            '<div class="news-ticker"><div class="news-track"><div class="news-set">'
            '<span class="news-item"><span class="news-source">NEWS</span>'
            'Global feed temporarily unavailable · automatic retry every 15 minutes'
            '</span></div></div></div>'
        )
        return

    parts = []
    for item in items[:10]:
        title = esc(item.get("title"))
        source = esc(item.get("source"))
        link = item.get("link") or ""
        title_html = (
            f'<a href="{esc(link)}" target="_blank" '
            f'style="color:inherit;text-decoration:none">{title}</a>'
            if link else title
        )
        parts.append(
            f'<span class="news-item"><span class="news-source">{source}</span>'
            f'{title_html}</span><span class="news-dot">◆</span>'
        )
    one_set = "".join(parts)
    raw_html(
        f'<div class="news-ticker"><div class="news-track">'
        f'<div class="news-set">{one_set}</div>'
        f'<div class="news-set" aria-hidden="true">{one_set}</div>'
        f'</div></div>'
    )


def launch_spaceport(launch):
    pad = launch.get("pad") or {}
    location = pad.get("location") or {}

    if isinstance(location, dict):
        location_name = location.get("name") or ""
    else:
        location_name = str(location or "")

    return (
        location_name
        or pad.get("name")
        or (((pad.get("country") or {}).get("name")) or "")
        or "Spaceport TBD"
    )


def launch_site_coords(launch):
    pad = launch.get("pad") or {}
    try:
        lat = float(pad.get("latitude"))
        lon = float(pad.get("longitude"))
        return lat, lon
    except Exception:
        return None, None


def launch_country_iso3(launch):
    country = (launch.get("pad") or {}).get("country") or {}
    iso3 = (
        country.get("alpha_3_code")
        or country.get("alpha3_code")
        or country.get("alpha3")
    )
    if iso3:
        return str(iso3).upper()
    iso2 = str(country.get("alpha_2_code") or "").upper()
    return ALPHA2_TO_ALPHA3.get(iso2)


def news_geo_tags(item):
    title = f" {str(item.get('title') or '').lower()} "
    hits = []

    # Specific country matches first.
    for name, iso3, lat, lon, needles in NEWS_GEO:
        if any(needle in title for needle in needles):
            hits.append({
                "name": name, "iso3": iso3, "lat": lat, "lon": lon,
                "headline": item.get("title") or "",
                "source": item.get("source") or "",
            })

    # A generic Europe/ESA headline that did not resolve to a named country
    # gets an EU-policy marker at Brussels.
    if not hits and any(
        term in title
        for term in [" europe ", " european ", " esa ", "europe's", "europe’s"]
    ):
        hits.append({
            "name": "Europe / EU",
            "iso3": "BEL",
            "lat": 50.85,
            "lon": 4.35,
            "headline": item.get("title") or "",
            "source": item.get("source") or "",
        })

    # Deduplicate one headline mapping to the same country twice.
    dedup = {}
    for hit in hits:
        dedup[hit["iso3"]] = hit
    return list(dedup.values())


def activity_map(recent, news):
    """
    Browser-side world map using Leaflet.
    No extra Python package is required.

    Visual logic:
    - pale blue country = important space news
    - sand country = launch activity
    - sage country = both launch + important news
    - solid dot = launch site
    - open ring = important-news country
    """
    activity = defaultdict(lambda: {"launches": 0, "news": 0})
    launch_points = []
    news_points = {}

    for launch in recent:
        iso3 = launch_country_iso3(launch)
        actor = major_actor(launch)
        if iso3:
            activity[iso3]["launches"] += 1

        lat, lon = launch_site_coords(launch)
        if lat is not None and lon is not None:
            launch_points.append({
                "lat": lat,
                "lon": lon,
                "actor": actor,
                "colour": ACTOR_COLOURS.get(actor, "#B58B3E"),
                "spaceport": launch_spaceport(launch),
                "name": launch.get("name") or "",
            })

    for item in news[:10]:
        for hit in news_geo_tags(item):
            activity[hit["iso3"]]["news"] += 1
            news_points[hit["iso3"]] = hit

    activity_payload = {}
    for iso3, values in activity.items():
        if values["launches"] and values["news"]:
            state = "both"
        elif values["launches"]:
            state = "launch"
        else:
            state = "news"

        activity_payload[iso3] = {
            "state": state,
            "launches": values["launches"],
            "news": values["news"],
        }

    html_blob = f"""
    <html>
    <head>
      <meta charset="utf-8">
      <link
        rel="stylesheet"
        href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
        crossorigin=""
      />
      <script
        src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        crossorigin="">
      </script>
      <style>
        html, body {{
          margin:0;
          padding:0;
          background:#F7FAFA;
          font-family:Inter,"Segoe UI",Arial,sans-serif;
        }}
        #map {{
          height:286px;
          width:100%;
          background:#DCE7EA;
          border:1px solid #CCD8DC;
          border-radius:9px;
          overflow:hidden;
          box-sizing:border-box;
        }}
        .leaflet-control-zoom {{
          display:none;
        }}
        .launch-dot {{
          border-radius:50%;
          border:2px solid #fff;
          box-shadow:0 0 0 1px rgba(32,54,62,.25);
        }}
        .news-ring {{
          border-radius:50%;
          background:transparent;
          border:2px solid #426B79;
          box-sizing:border-box;
        }}
        .leaflet-tooltip {{
          background:#17313A;
          color:#F4F7F6;
          border:0;
          border-radius:5px;
          box-shadow:none;
          font-size:9px;
          padding:5px 7px;
        }}
        .leaflet-tooltip:before {{
          display:none;
        }}
      </style>
    </head>
    <body>
      <div id="map"></div>
      <script>
        const activity = {json.dumps(activity_payload, ensure_ascii=False)};
        const launchPoints = {json.dumps(launch_points, ensure_ascii=False)};
        const newsPoints = {json.dumps(list(news_points.values()), ensure_ascii=False)};

        const map = L.map('map', {{
          zoomControl:false,
          attributionControl:false,
          worldCopyJump:false,
          minZoom:1,
          maxZoom:4,
          dragging:false,
          scrollWheelZoom:false,
          doubleClickZoom:false,
          boxZoom:false,
          keyboard:false,
          tap:false
        }}).setView([22, 8], 1.45);


        const colours = {{
          news:'#BFD6DE',
          launch:'#E4C582',
          both:'#83AD9B'
        }};

        fetch('https://cdn.jsdelivr.net/gh/johan/world.geo.json@master/countries.geo.json')
          .then(r => r.json())
          .then(geo => {{
            L.geoJSON(geo, {{
              style: feature => {{
                const iso3 = feature.id || '';
                const item = activity[iso3];
                return {{
                  fillColor: item ? colours[item.state] : '#F8FAFA',
                  fillOpacity: item ? 0.86 : 1.0,
                  color:'#AEBEC4',
                  weight:0.55
                }};
              }},
              onEachFeature: (feature, layer) => {{
                const iso3 = feature.id || '';
                const item = activity[iso3];
                if (item) {{
                  const name = feature.properties && feature.properties.name
                    ? feature.properties.name
                    : iso3;
                  layer.bindTooltip(
                    `<b>${{name}}</b><br>${{item.launches}} launches · ${{item.news}} news`,
                    {{sticky:false}}
                  );
                }}
              }}
            }}).addTo(map);
          }})
          .catch(() => {{
            // The map still remains useful with launch/news markers if the
            // country-outline CDN is temporarily unavailable.
          }});

        launchPoints.forEach(p => {{
          const icon = L.divIcon({{
            className:'',
            html:`<div class="launch-dot" style="width:10px;height:10px;background:${{p.colour}}"></div>`,
            iconSize:[14,14],
            iconAnchor:[7,7]
          }});
          L.marker([p.lat,p.lon], {{icon}})
            .bindTooltip(`<b>${{p.spaceport}}</b><br>${{p.name}}`)
            .addTo(map);
        }});

        newsPoints.forEach(p => {{
          const icon = L.divIcon({{
            className:'',
            html:'<div class="news-ring" style="width:17px;height:17px"></div>',
            iconSize:[17,17],
            iconAnchor:[8.5,8.5]
          }});
          L.marker([p.lat,p.lon], {{icon}})
            .bindTooltip(`<b>${{p.name}}</b><br>${{p.source}}: ${{p.headline}}`)
            .addTo(map);
        }});
      </script>
    </body>
    </html>
    """

    components.html(html_blob, height=288, scrolling=False)

    raw_html(
        '<div class="map-legend">'
        '<span><span class="legend-dot"></span>launch country</span>'
        '<span><span class="legend-ring"></span>important news</span>'
        '<span><span class="legend-both"></span>both</span>'
        '<span>● launch site</span>'
        '</div>'
    )

def orbit_summary(recent):
    counts = defaultdict(int)
    for launch in recent:
        counts[orbit_group(launch)] += 1
    raw_html(
        '<div class="orbit-strip">'
        f'<div class="orbit-pill"><span class="orbit-pill-name">LEO / SSO</span>'
        f'<span class="orbit-pill-count">{counts["LEO"]}</span></div>'
        f'<div class="orbit-pill"><span class="orbit-pill-name">MEO</span>'
        f'<span class="orbit-pill-count">{counts["MEO"]}</span></div>'
        f'<div class="orbit-pill"><span class="orbit-pill-name">GEO / GTO</span>'
        f'<span class="orbit-pill-count">{counts["GEO"]}</span></div>'
        '</div>'
    )


def europe_live_totals():
    queries = {
        "galileo": ("GROUP", "GALILEO"),
        "sentinel": ("NAME", "SENTINEL"),
        "oneweb": ("GROUP", "ONEWEB"),
    }
    totals = {k: None for k in queries}
    with ThreadPoolExecutor(max_workers=3) as pool:
        future_map = {
            pool.submit(celestrak_count, *query): key
            for key, query in queries.items()
        }
        for future in as_completed(future_map):
            key = future_map[future]
            try:
                totals[key] = future.result()
            except Exception:
                totals[key] = None
    return totals


def render_europe_panel_v08(recent, upcoming, ytd):
    """
    Focused Europe reference panel.

    Only information that adds value on a 24-inch wall display remains:
    - current European activity
    - what the major programmes provide
    - where each programme is now
    - what is being built / what comes next

    Detailed launcher/company status was deliberately removed from the
    main wall display to free space for Counterspace Capability Watch.
    """
    actor_rows = build_europe_actor_stats(recent, upcoming)

    launches_7d = sum(v["launches_7d"] for _, v in actor_rows)
    plan_30d = sum(v["planned_30d"] for _, v in actor_rows)

    objects_known = sum(v["objects_known"] for _, v in actor_rows)
    unknown = sum(v["objects_unknown_launches"] for _, v in actor_rows)
    estimated = any(v["objects_estimated"] for _, v in actor_rows)
    objects_7d = _display_object_total(
        objects_known, unknown, estimated
    )

    raw_html(
        f'<div class="eu-summary">'
        f'<div class="eu-summary-box"><div class="eu-summary-label">LAUNCHES · 7D</div>'
        f'<div class="eu-summary-value">{launches_7d}</div></div>'
        f'<div class="eu-summary-box"><div class="eu-summary-label">NEW OBJECTS · 7D</div>'
        f'<div class="eu-summary-value">{objects_7d}</div></div>'
        f'<div class="eu-summary-box"><div class="eu-summary-label">PLANNED · 30D</div>'
        f'<div class="eu-summary-value">{plan_30d}</div></div>'
        f'</div>'

        f'<div class="eu-ref-grid">'

        # GALILEO
        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">GALILEO</div>'
        f'<div class="eu-ref-role">POSITION &amp; TIME</div></div>'
        f'<div class="eu-ref-main">{EU_REFERENCE["GALILEO_NOW"]}</div></div>'
        f'<div class="eu-ref-line"><strong>NOMINAL</strong> {EU_REFERENCE["GALILEO_NOMINAL"]}</div>'
        f'<div class="eu-ref-line"><strong>NEXT</strong> {EU_REFERENCE["GALILEO_NEXT"]} · '
        f'<strong>BUILDING</strong> {EU_REFERENCE["GALILEO_G2_BUILD"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> navigation · precise timing · encrypted PRS</div>'
        f'</div>'

        # COPERNICUS
        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">COPERNICUS / SENTINEL</div>'
        f'<div class="eu-ref-role">SEE &amp; MONITOR</div></div>'
        f'<div class="eu-ref-main">{EU_REFERENCE["COPERNICUS_NOW"]}</div></div>'
        f'<div class="eu-ref-line"><strong>EXPANDING</strong> {EU_REFERENCE["COPERNICUS_EXPANSION"]}</div>'
        f'<div class="eu-ref-line"><strong>SENSORS</strong> radar · IR · hyperspectral · CO₂</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> sea ice · ship detection · land imagery · disaster mapping</div>'
        f'</div>'

        # IRIS2
        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">IRIS²</div>'
        f'<div class="eu-ref-role">SECURELY CONNECT</div></div>'
        f'<div class="eu-ref-main">{EU_REFERENCE["IRIS2_NOW"]}</div></div>'
        f'<div class="eu-ref-line"><strong>TARGET</strong> {EU_REFERENCE["IRIS2_TARGET"]} · '
        f'{EU_REFERENCE["IRIS2_ORBITS"]}</div>'
        f'<div class="eu-ref-line"><strong>SERVICE</strong> first {EU_REFERENCE["IRIS2_FIRST"]} · '
        f'full {EU_REFERENCE["IRIS2_FULL"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> secure government · defence · crisis connectivity</div>'
        f'</div>'

        # GOVSATCOM
        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">GOVSATCOM</div>'
        f'<div class="eu-ref-role">GOVERNMENT SATCOM</div></div>'
        f'<div class="eu-ref-main">{EU_REFERENCE["GOVSATCOM_DEDICATED"]}</div></div>'
        f'<div class="eu-ref-line"><strong>LIVE</strong> since {EU_REFERENCE["GOVSATCOM_LIVE"]}</div>'
        f'<div class="eu-ref-line"><strong>POOL</strong> {EU_REFERENCE["GOVSATCOM_POOL"]} · '
        f'{EU_REFERENCE["GOVSATCOM_IRIS"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> pooled secure satellite capacity for government &amp; military users</div>'
        f'</div>'

        f'</div>'
    )

def render_changes_v08(recent, limit=3):
    if not recent:
        st.caption("No recent launch data available.")
        return

    for launch in recent[:limit]:
        actor = major_actor(launch)
        colour = ACTOR_COLOURS[actor]
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b") if d else ""
        count, source = object_count_for_launch(launch)
        obj = (
            "catalogue pending"
            if count is None
            else f'+{count}{"*" if source == "estimated" else ""} objects'
        )

        raw_html(
            f'<div class="change-v08" style="--accent:{colour}">'
            f'<div class="change-accent"></div>{flag_markup(actor, "change-flag")}'
            f'<div><div class="change-main">{esc(launch.get("name"))}</div>'
            f'<div class="change-sub">{esc(actor)} · {esc(when)} · {esc(obj)}</div></div>'
            f'<div class="change-orbit">{orbit_group(launch)}</div>'
            f'</div>'
        )


def _tminus_label(d):
    if not d:
        return ""
    delta = d - datetime.now(timezone.utc)
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours < 24:
        return f"T−{hours}H"
    return f"T−{max(1, int(round(hours / 24)))}D"


def render_upcoming_v08(upcoming, limit=3):
    if not upcoming:
        st.caption("Upcoming launch data unavailable.")
        return

    for launch in upcoming[:limit]:
        d = parse_dt(launch.get("net"))
        when = (
            d.astimezone(LOCAL_TZ).strftime("%d %b · %H:%M")
            if d else "TBD"
        )
        spaceport = launch_spaceport(launch)

        raw_html(
            f'<div class="next-v08">'
            f'<div><div class="next-when">{esc(when)}</div>'
            f'<div class="next-tminus">{esc(_tminus_label(d))}</div></div>'
            f'<div><div class="next-name">{esc(launch.get("name"))}</div>'
            f'<div class="next-spaceport">⌖ {esc(spaceport)}</div>'
            f'<div class="next-meta">{esc(launch_provider_name(launch))} · {orbit_group(launch)}</div>'
            f'</div></div>'
        )


def launch_image_data(launch):
    image = launch.get("image") or {}
    return image.get("image_url"), image.get("credit") or ""


def featured_launch_slideshow_v08(recent):
    with_images = [x for x in recent if launch_image_data(x)[0]]
    if not with_images:
        raw_html('<div class="featured-fallback">Launch image feed unavailable</div>')
        return

    # Europe first when there is a European launch in the current 7-day window,
    # then the newest globally significant launches.
    chosen = []
    europe = [x for x in with_images if major_actor(x) == "EUROPE"]
    if europe:
        chosen.append(europe[0])
    for launch in with_images:
        if len(chosen) >= 3:
            break
        if launch not in chosen:
            chosen.append(launch)

    slides = []
    css_delays = []
    total_duration = max(8, len(chosen) * 8)

    for i, launch in enumerate(chosen):
        url, credit = launch_image_data(launch)
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b") if d else ""
        spaceport = launch_spaceport(launch)
        slides.append(
            f"""
            <div class="slide s{i}">
              <img class="fg" src="{esc(url)}" alt="{esc(launch.get("name"))}">
              <div class="shade"></div>
              <div class="credit">{esc(credit)}</div>
              <div class="caption">
                <div class="cap-title">{esc(launch.get("name"))}</div>
                <div class="cap-space">⌖ {esc(spaceport)}</div>
                <div class="cap-meta">{esc(major_actor(launch))} · {esc(when)} · {esc(orbit_group(launch))}</div>
              </div>
            </div>
            """
        )
        css_delays.append(f".s{i}{{animation-delay:{i*8}s;}}")

    # When only one image exists, keep it visible without cycling.
    if len(chosen) == 1:
        animation_css = ".slide{opacity:1 !important;animation:none !important;}"
    else:
        visible_pct = max(18, int((7.2 / total_duration) * 100))
        animation_css = (
            f"@keyframes fade{{0%{{opacity:0}} 3%{{opacity:1}} "
            f"{visible_pct}%{{opacity:1}} {min(visible_pct+5,95)}%{{opacity:0}} "
            f"100%{{opacity:0}}}}"
        )

    html_blob = f"""
    <html><head><style>
      body{{margin:0;background:transparent;font-family:Inter,Segoe UI,Arial,sans-serif;}}
      .frame{{height:242px;border:1px solid #C6D3D7;border-radius:9px;overflow:hidden;position:relative;background:#D7E0E2;}}
      .slide{{position:absolute;inset:0;opacity:0;animation:fade {total_duration}s linear infinite;overflow:hidden;}}
      .slide .fg{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:center center;display:block;}}
      .shade{{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.00) 42%,rgba(9,22,28,.80) 100%);}}
      .caption{{position:absolute;left:12px;right:12px;bottom:9px;color:#F5F7F6;}}
      .cap-title{{font-size:13px;font-weight:780;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .cap-space{{font-size:9px;color:#E1E9E9;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .cap-meta{{font-size:8px;color:#BCCBCE;margin-top:2px;}}
      .credit{{position:absolute;right:7px;top:6px;background:rgba(10,25,31,.62);color:#DDE5E6;padding:2px 5px;border-radius:4px;font-size:7px;}}
      {" ".join(css_delays)}
      {animation_css}
      .s0{{opacity:1;}}
    </style></head><body>
      <div class="frame">{"".join(slides)}</div>
    </body></html>
    """
    components.html(html_blob, height=244, scrolling=False)



# ============================================================
# 07B · COUNTERSPACE WATCH
# ============================================================

COUNTERSPACE_CORE_TERMS = {
    "counterspace": 12,
    "counter-space": 12,
    "space control": 12,
    "space-control": 12,
    "space weapon": 12,
    "space weapons": 12,
    "weapon in orbit": 12,
    "weapons in orbit": 12,
    "on-orbit weapon": 12,
    "on-orbit weapons": 12,
    "orbital weapon": 12,
    "orbital weapons": 12,
    "anti-satellite": 12,
    "antisatellite": 12,
    "asat": 12,
    "co-orbital": 9,
    "coorbital": 9,
    "proximity operations": 8,
    "proximity operation": 8,
    "rendezvous and proximity": 8,
    "space electronic warfare": 10,
    "satellite jamming": 9,
    "satcom jamming": 9,
    "directed energy": 9,
    "laser dazzling": 9,
}

CAPABILITY_ACTION_TERMS = {
    "deploy": 7,
    "deployed": 8,
    "deployment": 7,
    "field": 6,
    "fielded": 8,
    "fielding": 7,
    "procure": 7,
    "procurement": 8,
    "acquisition": 8,
    "acquire": 7,
    "contract": 6,
    "award": 6,
    "selected": 4,
    "strategy": 7,
    "strategic": 4,
    "doctrine": 7,
    "framework": 6,
    "policy": 4,
    "budget": 5,
    "funding": 5,
    "investment": 5,
    "develop": 4,
    "developing": 4,
    "demonstration": 5,
    "demonstrate": 5,
    "test": 4,
    "exercise": 4,
    "operational": 5,
    "capability": 5,
    "system": 3,
    "unit": 3,
    "squadron": 4,
}

COUNTERSPACE_DOWNRANK = {
    # Useful resilience/PNT stories, but not the capability-watch product
    # the user wants in this box.
    "osnma": -20,
    "authentication service": -15,
    "civil navigation": -10,
    "resilience service": -8,
    "interference monitoring": -6,
}


def counterspace_score(item):
    """
    Score developments in counterspace CAPABILITY, not routine interference events.

    A strong result normally needs BOTH:
      - a counterspace / space-control concept; and
      - a capability action: deployment, procurement, strategy, fielding,
        testing, funding, acquisition, organisation, etc.
    """
    title = str(item.get("title") or "")
    summary = re.sub(r"<[^>]+>", " ", str(item.get("summary") or ""))
    low = f"{title} {summary}".lower()

    core_score = 0
    for term, points in COUNTERSPACE_CORE_TERMS.items():
        if term in low:
            core_score += points

    action_score = 0
    for term, points in CAPABILITY_ACTION_TERMS.items():
        if term in low:
            action_score += points

    # Strong explicit phrases such as "weapons in orbit" are inherently
    # capability developments even if the headline contains no procurement verb.
    explicit_capability = any(
        phrase in low
        for phrase in [
            "space control weapons",
            "space-control weapons",
            "weapons in orbit",
            "weapon in orbit",
            "on-orbit weapons",
            "on-orbit weapon",
            "orbital weapons",
            "orbital weapon",
            "anti-satellite weapon",
            "counterspace capability",
            "counter-space capability",
        ]
    )

    if not explicit_capability and (core_score == 0 or action_score == 0):
        return 0

    score = core_score + action_score

    for term, penalty in COUNTERSPACE_DOWNRANK.items():
        if term in low:
            score += penalty

    dt = item.get("date")
    if dt:
        age_hours = (
            datetime.now(timezone.utc)
            - dt.astimezone(timezone.utc)
        ).total_seconds() / 3600
        if age_hours <= 24:
            score += 3
        elif age_hours <= 168:
            score += 2
        elif age_hours <= 720:
            score += 1

    return max(0, score)


@st.cache_data(ttl=3600, show_spinner=False)
def article_og_image(url):
    """Best-effort fallback image. Failure never blocks the dashboard."""
    if not url:
        return ""
    try:
        html_text = _request_text(url, timeout=3)
        patterns = [
            r"<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']",
            r"<meta[^>]+content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:image[\"\']",
            r"<meta[^>]+name=[\"\']twitter:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']",
        ]
        for pattern in patterns:
            m = re.search(pattern, html_text, flags=re.I)
            if m:
                return html.unescape(m.group(1))
    except Exception:
        pass
    return ""



@st.cache_data(ttl=1800, show_spinner=False)
def _counterspace_archive_cached():
    """
    Deeper scan of the same trusted global feeds.

    The rolling ticker stays current. Counterspace Watch may look back
    30 days when no significant item exists in the last 7 days.
    """

    def parse_feed(source, url, priority):
        out = []
        try:
            xml = _request_text(url, timeout=6)
            root = ET.fromstring(xml)
            candidates = [
                x for x in root.iter()
                if x.tag.split("}")[-1].lower() in ("item", "entry")
            ]

            for item in candidates[:50]:
                title = _xml_text(item, {"title"}).strip()
                if not title:
                    continue

                link = _xml_text(item, {"link"})
                if not link:
                    for child in item.iter():
                        if child.tag.split("}")[-1].lower() == "link":
                            link = child.attrib.get("href", "")
                            if link:
                                break

                raw_date = _xml_text(
                    item, {"pubdate", "published", "updated", "date"}
                )
                dt = None
                if raw_date:
                    try:
                        dt = parsedate_to_datetime(raw_date)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            dt = datetime.fromisoformat(
                                raw_date.replace("Z", "+00:00")
                            )
                        except Exception:
                            dt = None

                description = _xml_text(
                    item, {"description", "summary", "content", "encoded"}
                )

                image_url = ""
                for child in item.iter():
                    tag = child.tag.split("}")[-1].lower()
                    if tag in ("content", "thumbnail", "enclosure"):
                        candidate = (
                            child.attrib.get("url")
                            or child.attrib.get("href")
                            or ""
                        )
                        media_type = (child.attrib.get("type") or "").lower()
                        if candidate and (
                            media_type.startswith("image/")
                            or re.search(
                                r"\.(?:jpg|jpeg|png|webp)(?:\?|$)",
                                candidate,
                                re.I,
                            )
                            or tag in ("thumbnail", "content")
                        ):
                            image_url = candidate
                            break

                if not image_url and description:
                    match = re.search(
                        r"<img[^>]+src=[\"']([^\"']+)[\"']",
                        description,
                        flags=re.I,
                    )
                    if match:
                        image_url = match.group(1)

                out.append({
                    "source": source,
                    "title": title,
                    "link": link,
                    "date": dt,
                    "priority": priority,
                    "summary": description,
                    "image": image_url,
                })

        except Exception:
            return []

        return out

    items = []
    with ThreadPoolExecutor(max_workers=len(NEWS_FEEDS)) as pool:
        futures = [
            pool.submit(parse_feed, source, url, priority)
            for source, url, priority in NEWS_FEEDS
        ]
        for future in as_completed(futures):
            try:
                items.extend(future.result())
            except Exception:
                pass

    seen = set()
    unique = []
    for item in items:
        key = re.sub(r"\W+", "", item["title"].lower())[:150]
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique


def _select_counterspace(pool, days=None, limit=2):
    floor = (
        datetime.now(timezone.utc) - timedelta(days=days)
        if days is not None
        else None
    )
    candidates = []

    for item in pool:
        dt = item.get("date")
        if floor is not None and dt:
            if dt.astimezone(timezone.utc) < floor:
                continue

        score = counterspace_score(item)
        if score < 7:
            continue

        enriched = dict(item)
        enriched["counterspace_score"] = score
        candidates.append(enriched)

    candidates.sort(
        key=lambda x: (
            x.get("counterspace_score", 0),
            x.get("date")
            or datetime(1970, 1, 1, tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return candidates[:limit]


CURATED_COUNTERSPACE_FALLBACK = {
    "source": "DefenseScoop",
    "title": "Space Force has deployed space control weapons to orbit",
    "link": "https://defensescoop.com/2026/09/14/meink-space-force-has-deployed-space-control-weapons-to-orbit/",
    "date": datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc),
    "summary": (
        "U.S. Air Force Secretary Troy Meink publicly confirmed that the "
        "U.S. Space Force has on-orbit space-control weapons. Specific "
        "systems and numbers were not disclosed."
    ),
    "image": "",
    "counterspace_score": 100,
}

def get_counterspace_items(news, limit=2):
    """
    Capability-watch priority:
      1) significant capability development within 30 days
      2) within 90 days
      3) latest significant development available in capability feeds
      4) curated, dated fallback (never an unrelated counterspace event)
    """
    archive = _counterspace_archive_cached()

    merged = []
    seen = set()
    for item in list(news) + list(archive):
        key = re.sub(
            r"\W+", "", str(item.get("title") or "").lower()
        )[:150]
        if key and key not in seen:
            seen.add(key)
            merged.append(item)

    items = _select_counterspace(merged, days=30, limit=limit)
    if items:
        window = "30D"
    else:
        items = _select_counterspace(merged, days=90, limit=limit)
        if items:
            window = "90D"
        else:
            items = _select_counterspace(merged, days=None, limit=limit)
            if items:
                window = "LATEST"
            else:
                items = [dict(CURATED_COUNTERSPACE_FALLBACK)]
                window = "LATEST KNOWN"

    # Best-effort story-specific hero image.
    # Prefer the article's own OG image over a generic RSS thumbnail.
    if items:
        article_image = article_og_image(
            items[0].get("link") or ""
        )
        if article_image:
            items[0]["image"] = article_image

    return items, window


def counterspace_type(item):
    text_blob = (
        f'{item.get("title","")} {item.get("summary","")}'
    ).lower()

    if any(x in text_blob for x in ["deployed", "deployment", "fielded", "fielding", "on-orbit weapon", "weapons in orbit"]):
        return "FIELDING / DEPLOYMENT"

    if any(x in text_blob for x in ["procurement", "acquisition", "contract", "award", "selected", "buy", "purchase"]):
        return "ACQUISITION"

    if any(x in text_blob for x in ["strategy", "doctrine", "framework", "policy"]):
        return "STRATEGY / DOCTRINE"

    if any(x in text_blob for x in ["demonstration", "demonstrate", "test", "exercise"]):
        return "TEST / DEMONSTRATION"

    if any(x in text_blob for x in ["budget", "funding", "investment"]):
        return "INVESTMENT"

    if any(x in text_blob for x in ["unit", "squadron", "organization", "organisation", "office"]):
        return "ORGANISATION"

    return "CAPABILITY DEVELOPMENT"


def counterspace_fallback_visual(event_type, title):
    """
    Internal thematic visual used only when the source provides no usable image.
    No external image service, no API key, no extra dependency.
    """
    label = esc(event_type)

    # Different simple symbols make categories distinguishable without implying
    # that a specific pictured platform/system caused the event.
    icon = "◈"
    if "ACQUISITION" in event_type:
        icon = "▤"
    elif "STRATEGY" in event_type:
        icon = "⌁"
    elif "TEST" in event_type:
        icon = "◎"
    elif "INVESTMENT" in event_type:
        icon = "＋"
    elif "ORGANISATION" in event_type:
        icon = "▦"

    return (
        f'<div class="counter-image">'
        f'<div class="counter-no-image" style="position:relative;overflow:hidden;">'
        f'<div style="position:absolute;width:180px;height:180px;border:1px solid rgba(139,85,73,.22);'
        f'border-radius:50%;right:-45px;top:-70px;"></div>'
        f'<div style="position:absolute;width:110px;height:110px;border:1px solid rgba(139,85,73,.18);'
        f'border-radius:50%;right:-8px;top:-34px;"></div>'
        f'<div style="position:relative;z-index:2;">'
        f'<div style="font-size:34px;color:#8B5549;line-height:1;">{icon}</div>'
        f'<div style="font-size:9px;letter-spacing:.08em;margin-top:8px;color:#8B5549;font-weight:820;">{label}</div>'
        f'<div style="font-size:11px;line-height:1.25;margin-top:5px;color:#624A43;">{esc(title)}</div>'
        f'</div></div></div>'
    )


def render_counterspace_watch(news):
    items, window = get_counterspace_items(news, limit=3)

    raw_html(
        '<div class="counter-head">'
        '<div class="counter-title">COUNTERSPACE CAPABILITY WATCH</div>'
        f'<div class="counter-period">OPEN SOURCE · {esc(window)}</div>'
        '</div>'
    )

    if not items:
        raw_html(
            '<div class="counter-baseline">'
            '<div class="counter-baseline-title">'
            'NO NEW CAPABILITY DEVELOPMENT IDENTIFIED'
            '</div>'
            '<div class="counter-baseline-sub">Capability areas to watch</div>'
            '<div class="counter-baseline-grid">'
            '<div class="counter-baseline-chip">EW / JAMMING</div>'
            '<div class="counter-baseline-chip">GNSS SPOOFING</div>'
            '<div class="counter-baseline-chip">CO-ORBITAL / RPO</div>'
            '<div class="counter-baseline-chip">ASAT</div>'
            '<div class="counter-baseline-chip">CYBER</div>'
            '<div class="counter-baseline-chip">DIRECTED ENERGY</div>'
            '</div></div>'
        )
        return

    primary = items[0]
    image = primary.get("image") or ""
    title = esc(primary.get("title"))
    source = esc(primary.get("source"))
    event_type = esc(counterspace_type(primary))
    date = primary.get("date")
    when = (
        date.astimezone(LOCAL_TZ).strftime("%d %b")
        if date else ""
    )
    link = esc(primary.get("link") or "")

    if image:
        visual = (
            f'<div class="counter-image">'
            f'<img src="{esc(image)}" alt="{title}">'
            f'<div class="counter-image-shade"></div>'
            f'<div class="counter-badge">{event_type}</div>'
            f'<div class="counter-caption">'
            f'<div class="counter-story">{title}</div>'
            f'<div class="counter-source">{source} · {esc(when)}</div>'
            f'</div></div>'
        )
    else:
        visual = counterspace_fallback_visual(
            counterspace_type(primary),
            primary.get("title") or "",
        )

    secondary = ""
    for item in items[1:3]:
        secondary += (
            f'<div class="counter-second">'
            f'<span class="counter-dot"></span>'
            f'<div class="counter-second-title">'
            f'{esc(counterspace_type(item))} · '
            f'{esc(item.get("title"))}'
            f'</div></div>'
        )

    if link:
        content = (
            f'<a href="{link}" target="_blank" '
            f'style="text-decoration:none;color:inherit">'
            f'{visual}{secondary}</a>'
        )
    else:
        content = visual + secondary

    raw_html(f'<div class="counter-card">{content}</div>')



# ============================================================
# 08 · DASHBOARD · v0.8
# ============================================================

@st.fragment(run_every=900)
def render_dashboard():
    recent, recent_ok = get_recent_launches(7)
    upcoming, upcoming_ok = get_upcoming_launches(30)
    news, news_ok = get_news()
    now = datetime.now(LOCAL_TZ)

    status = "LIVE" if recent_ok and upcoming_ok else "PARTIAL DATA"
    dot = '<span class="live-dot"></span>' if recent_ok else ""

    raw_html(
        f'<div class="hero">'
        f'<div><div class="hero-title">SPACE UPDATE</div>'
        f'<div class="hero-sub">GLOBAL ACTIVITY · EUROPEAN CAPABILITY · 7 DAY PICTURE</div></div>'
        f'<div class="hero-time">'
        f'<div class="hero-owner">AIR &amp; SPACE WARFARE CENTRE</div>'
        f'{dot}{status}'
        f'<strong>{now.strftime("%d %b · %H:%M")}</strong></div>'
        f'</div>'
    )

    render_news_ticker(news)

    if not recent_ok or not upcoming_ok:
        raw_html(
            '<div class="source-warning">Live launch data is temporarily incomplete. '
            'The dashboard retries automatically.</div>'
        )

    # --------------------------------------------------------
    # MAJOR ACTORS
    # --------------------------------------------------------
    section_header("MAJOR ACTORS", "BIG = 7 DAYS · SMALL = 24H")

    if recent:
        prefetch_object_catalogue(recent)

    now_utc = datetime.now(timezone.utc)
    counts7 = defaultdict(int)
    counts24 = defaultdict(int)
    obj7_known = defaultdict(int)
    obj24_known = defaultdict(int)
    obj7_unknown = defaultdict(int)
    obj24_unknown = defaultdict(int)
    obj7_est = defaultdict(bool)
    obj24_est = defaultdict(bool)

    for launch in recent:
        actor = major_actor(launch)
        counts7[actor] += 1
        d = parse_dt(launch.get("net"))
        is24 = bool(d and d >= now_utc - timedelta(hours=24))
        if is24:
            counts24[actor] += 1

        count, source = object_count_for_launch(launch)
        if count is None:
            obj7_unknown[actor] += 1
            if is24:
                obj24_unknown[actor] += 1
        else:
            obj7_known[actor] += count
            if source == "estimated":
                obj7_est[actor] = True
            if is24:
                obj24_known[actor] += count
                if source == "estimated":
                    obj24_est[actor] = True

    actor_cols = st.columns(5, gap="small")
    for col, actor in zip(
        actor_cols, ["EUROPE", "USA", "CHINA", "RUSSIA", "OTHER"]
    ):
        with col:
            o24 = _display_object_total(
                obj24_known[actor], obj24_unknown[actor], obj24_est[actor]
            )
            o7 = _display_object_total(
                obj7_known[actor], obj7_unknown[actor], obj7_est[actor]
            )
            render_actor_card(
                actor, counts24[actor], counts7[actor], o24, o7, recent_ok
            )

    # --------------------------------------------------------
    # MAIN INFORMATION AREA
    # --------------------------------------------------------
    left, middle, right = st.columns([1.02, 1.14, .94], gap="small")

    with left:
        with st.container(border=True):
            raw_html(
                f'<div class="panel-heading"><div class="panel-title">EUROPE</div>'
                f'<div class="panel-note">REFERENCE · {EU_REFERENCE_UPDATED}</div></div>'
            )
            ytd, ytd_ok = get_ytd_launches()
            render_europe_panel_v08(
                recent, upcoming, ytd if ytd_ok else None
            )

        with st.container(border=True):
            render_counterspace_watch(news)

    with middle:
        with st.container(border=True):
            raw_html(
                '<div class="panel-heading"><div class="panel-title">GLOBAL ACTIVITY MAP</div>'
                '<div class="panel-note">LAUNCHES + IMPORTANT NEWS · 7D</div></div>'
            )
            activity_map(recent, news)
            orbit_summary(recent)

            raw_html(
                '<div class="panel-heading" style="margin-top:7px">'
                '<div class="panel-title">WHAT CHANGED?</div>'
                '<div class="panel-note">TOP 3 · NEWEST FIRST</div></div>'
            )
            render_changes_v08(recent, limit=3)

    with right:
        with st.container(border=True):
            raw_html(
                '<div class="panel-heading"><div class="panel-title">NEXT LAUNCHES</div>'
                '<div class="panel-note">TOP 4 · SPACEPORT</div></div>'
            )
            render_upcoming_v08(upcoming, limit=4)

            raw_html('<div class="featured-label">FEATURED LAUNCH</div>')
            featured_launch_slideshow_v08(recent)

    raw_html(
        '<div class="footerline">'
        '<span>AUTO · LAUNCH LIBRARY 2 · CELESTRAK · SpaceNews · Spaceflight Now · ESA · EUSPA · JPL · COUNTERSPACE = CAPABILITY WATCH · ARTICLE IMAGE → INTERNAL GRAPHIC · OPEN SOURCE</span>'
        '<span>v0.8.7.2 Nordic Contrast · 16:9 · 24–40&quot; · refresh 15 min</span>'
        '</div>'
    )


render_dashboard()
