import html
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
import plotly.graph_objects as go

# ============================================================
# SPACE UPDATE v0.8 · NORDIC CONTRAST · GLOBAL ACTIVITY MAP
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

                low = title.lower()
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
    gap:12px;
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
.eu-commercial {
    margin-top:7px;
    padding:6px 8px;
    border-radius:8px;
    background:#EFF4F3;
    border-left:3px solid #7E9F90;
    color:#48646A;
    font-size:9px;
}
.eu-commercial strong {color:#213D46;}
.access-strip {
    margin-top:7px;
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:8px;
    padding:7px 8px;
    background:#17313A;
    color:#EFF4F4;
    border-radius:8px;
}
.access-name {font-size:9px;font-weight:820;letter-spacing:.08em;}
.access-stats {font-size:9px;color:#C9D8DC;}
.access-stats strong {color:#FFFFFF;font-size:12px;}
.ecosystem {
    display:flex;
    flex-wrap:wrap;
    gap:4px;
    margin-top:6px;
}
.eco-pill {
    display:flex;
    align-items:center;
    gap:4px;
    border:1px solid #CCD7DA;
    background:#F6F9F9;
    border-radius:999px;
    padding:3px 6px;
    color:#556D76;
    font-size:8px;
}
.dot-active,.dot-plan,.dot-idle {
    width:6px;height:6px;border-radius:50%;display:inline-block;
}
.dot-active {background:#668F7A;}
.dot-plan {border:1px solid #B58B3E;background:transparent;}
.dot-idle {background:#B6C1C5;}

/* ----------------------------------------------------------
   H. MAP + ORBIT SUMMARY
   ---------------------------------------------------------- */
.map-legend {
    display:flex;
    gap:12px;
    align-items:center;
    color:#637983;
    font-size:8px;
    margin-top:-2px;
    margin-bottom:5px;
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
    grid-template-columns:62px 1fr;
    gap:8px;
    padding:7px 0;
    border-bottom:1px solid #E0E7E9;
}
.next-v08:last-child {border-bottom:none;}
.next-when {color:#947536;font-size:10px;font-weight:820;}
.next-tminus {color:#87999F;font-size:8px;margin-top:2px;}
.next-name {
    color:#1D3139;font-size:10px;font-weight:780;line-height:1.15;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.next-spaceport {
    color:#526E79;font-size:9px;margin-top:3px;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
}
.next-meta {color:#84969D;font-size:8px;margin-top:1px;}

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
    "IRIS2_TARGET": "348 sats",
    "IRIS2_ORBITS": "LEO + MEO",
    "IRIS2_FIRST": "2029",
    "IRIS2_FULL": "2030",
    "GALILEO_G2_BUILD": "12 G2",
    "GALILEO_NEXT": "2 FOC · end 2026",
    "COPERNICUS_EXPANSION": "6 missions · 12 sats",
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
    Countries are shaded when they have important news and/or launch activity.
    Launch sites are plotted as actor-coloured solid dots.
    Important-news countries get an open ring at the country centroid.
    """
    activity = defaultdict(lambda: {"launches": 0, "news": 0, "names": []})

    launch_points = defaultdict(list)
    for launch in recent:
        iso3 = launch_country_iso3(launch)
        actor = major_actor(launch)
        if iso3:
            activity[iso3]["launches"] += 1
            activity[iso3]["names"].append(launch.get("name") or "")

        lat, lon = launch_site_coords(launch)
        if lat is not None and lon is not None:
            launch_points[actor].append({
                "lat": lat,
                "lon": lon,
                "spaceport": launch_spaceport(launch),
                "name": launch.get("name") or "",
            })

    news_points = {}
    for item in news[:10]:
        for hit in news_geo_tags(item):
            activity[hit["iso3"]]["news"] += 1
            activity[hit["iso3"]]["names"].append(hit["headline"])
            news_points[hit["iso3"]] = hit

    locations = []
    z = []
    hover = []
    for iso3, data in activity.items():
        if data["launches"] and data["news"]:
            state = 3
            label = "Launch + important news"
        elif data["launches"]:
            state = 2
            label = "Launch activity"
        else:
            state = 1
            label = "Important news"

        locations.append(iso3)
        z.append(state)
        hover.append(
            f"{iso3}<br>{label}<br>"
            f"{data['launches']} launches · {data['news']} news items"
        )

    fig = go.Figure()

    if locations:
        fig.add_trace(
            go.Choropleth(
                locations=locations,
                z=z,
                zmin=1,
                zmax=3,
                text=hover,
                hoverinfo="text",
                showscale=False,
                colorscale=[
                    [0.00, "#BFD6DE"], [0.32, "#BFD6DE"],
                    [0.33, "#E4C582"], [0.65, "#E4C582"],
                    [0.66, "#83AD9B"], [1.00, "#83AD9B"],
                ],
                marker_line_color="#AEBCC1",
                marker_line_width=0.6,
            )
        )

    # Launch sites, grouped by major actor for consistent colours.
    for actor, points in launch_points.items():
        if not points:
            continue
        fig.add_trace(
            go.Scattergeo(
                lat=[p["lat"] for p in points],
                lon=[p["lon"] for p in points],
                text=[
                    f'{p["spaceport"]}<br>{p["name"]}'
                    for p in points
                ],
                hoverinfo="text",
                mode="markers",
                marker=dict(
                    size=8,
                    color=ACTOR_COLOURS.get(actor, "#B58B3E"),
                    line=dict(width=1.3, color="#FFFFFF"),
                ),
                showlegend=False,
            )
        )

    # Important-news rings.
    if news_points:
        points = list(news_points.values())
        fig.add_trace(
            go.Scattergeo(
                lat=[p["lat"] for p in points],
                lon=[p["lon"] for p in points],
                text=[
                    f'{p["name"]}<br>{p["source"]}: {p["headline"]}'
                    for p in points
                ],
                hoverinfo="text",
                mode="markers",
                marker=dict(
                    size=15,
                    color="rgba(0,0,0,0)",
                    line=dict(width=2, color="#426B79"),
                ),
                showlegend=False,
            )
        )

    fig.update_geos(
        projection_type="natural earth",
        showframe=False,
        showcoastlines=True,
        coastlinecolor="#A8B8BE",
        coastlinewidth=0.6,
        showcountries=True,
        countrycolor="#B9C6CA",
        countrywidth=0.6,
        showland=True,
        landcolor="#F8FAFA",
        showocean=True,
        oceancolor="#DDE8EB",
        showlakes=False,
        bgcolor="rgba(0,0,0,0)",
    )
    fig.update_layout(
        height=286,
        margin=dict(l=0, r=0, t=0, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, Arial", color="#30464F"),
        uirevision="space-map",
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False, "staticPlot": True},
        key="global_activity_map",
    )

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
    totals = europe_live_totals()
    actor_rows = build_europe_actor_stats(recent, upcoming)

    launches_7d = sum(v["launches_7d"] for _, v in actor_rows)
    plan_30d = sum(v["planned_30d"] for _, v in actor_rows)

    objects_known = sum(v["objects_known"] for _, v in actor_rows)
    unknown = sum(v["objects_unknown_launches"] for _, v in actor_rows)
    estimated = any(v["objects_estimated"] for _, v in actor_rows)
    objects_7d = _display_object_total(objects_known, unknown, estimated)

    launches_ytd = (
        sum(1 for x in ytd if european_launch_actor(x))
        if ytd is not None else "—"
    )

    gal_now = f'{fmt(totals["galileo"])} tracked' if totals["galileo"] is not None else "constellation live"
    sent_now = f'{fmt(totals["sentinel"])} tracked' if totals["sentinel"] is not None else "fleet live"
    oneweb_now = f'{fmt(totals["oneweb"])} tracked' if totals["oneweb"] is not None else "LEO fleet"

    # Only show the most useful launch actors on the wall display; all remain
    # represented in the data engine and will appear if active/planned.
    ecosystem = []
    for actor, values in actor_rows:
        active = values["launches_7d"] > 0
        planned = values["planned_30d"] > 0
        if not active and not planned and actor not in {
            "Arianespace", "Avio", "Isar Aerospace",
            "Rocket Factory Augsburg", "Orbex", "PLD Space",
        }:
            continue

        dots = (
            ('<span class="dot-active"></span>' if active else '<span class="dot-idle"></span>')
            + ('<span class="dot-plan"></span>' if planned else '')
        )
        short = {
            "Rocket Factory Augsburg": "RFA",
            "Isar Aerospace": "Isar",
            "Arianespace": "Arianespace",
        }.get(actor, actor)
        ecosystem.append(f'<span class="eco-pill">{dots}{esc(short)}</span>')

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

        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">GALILEO</div>'
        f'<div class="eu-ref-role">POSITION & TIME</div></div>'
        f'<div class="eu-ref-main">{gal_now}</div></div>'
        f'<div class="eu-ref-line"><strong>NEXT</strong> {EU_REFERENCE["GALILEO_NEXT"]}</div>'
        f'<div class="eu-ref-line"><strong>BUILDING</strong> {EU_REFERENCE["GALILEO_G2_BUILD"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> navigation · precise timing · encrypted PRS</div>'
        f'</div>'

        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">COPERNICUS / SENTINEL</div>'
        f'<div class="eu-ref-role">SEE & MONITOR</div></div>'
        f'<div class="eu-ref-main">{sent_now}</div></div>'
        f'<div class="eu-ref-line"><strong>EXPANDING</strong> {EU_REFERENCE["COPERNICUS_EXPANSION"]}</div>'
        f'<div class="eu-ref-line"><strong>SENSORS</strong> radar · IR · hyperspectral · CO₂</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> sea ice · ships · land · disasters</div>'
        f'</div>'

        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">IRIS²</div>'
        f'<div class="eu-ref-role">SECURELY CONNECT</div></div>'
        f'<div class="eu-ref-main">{EU_REFERENCE["IRIS2_TARGET"]}</div></div>'
        f'<div class="eu-ref-line"><strong>ORBITS</strong> {EU_REFERENCE["IRIS2_ORBITS"]}</div>'
        f'<div class="eu-ref-line"><strong>SERVICE</strong> first {EU_REFERENCE["IRIS2_FIRST"]} · full {EU_REFERENCE["IRIS2_FULL"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> government · defence · crisis connectivity</div>'
        f'</div>'

        f'<div class="eu-ref-card">'
        f'<div class="eu-ref-top"><div><div class="eu-ref-name">GOVSATCOM</div>'
        f'<div class="eu-ref-role">GOVERNMENT SATCOM</div></div>'
        f'<div class="eu-ref-main">LIVE</div></div>'
        f'<div class="eu-ref-line"><strong>SINCE</strong> {EU_REFERENCE["GOVSATCOM_LIVE"]}</div>'
        f'<div class="eu-ref-line"><strong>CAPACITY</strong> {EU_REFERENCE["GOVSATCOM_POOL"]} · {EU_REFERENCE["GOVSATCOM_IRIS"]}</div>'
        f'<div class="eu-ref-use"><b>ENABLES</b> secure government & military communications</div>'
        f'</div>'

        f'</div>'

        f'<div class="eu-commercial"><strong>EUTELSAT ONEWEB</strong> · {oneweb_now} · commercial LEO connectivity</div>'

        f'<div class="access-strip"><div class="access-name">EUROPEAN ACCESS TO SPACE</div>'
        f'<div class="access-stats"><strong>{launches_ytd}</strong> YTD · '
        f'<strong>{launches_7d}</strong> 7D · <strong>{plan_30d}</strong> NEXT 30D</div></div>'

        f'<div class="ecosystem">{"".join(ecosystem)}</div>'
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
              <img src="{esc(url)}" alt="{esc(launch.get("name"))}">
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
      .frame{{height:205px;border:1px solid #C6D3D7;border-radius:9px;overflow:hidden;position:relative;background:#DDE6E8;}}
      .slide{{position:absolute;inset:0;opacity:0;animation:fade {total_duration}s linear infinite;}}
      .slide img{{width:100%;height:100%;object-fit:cover;display:block;}}
      .shade{{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.01) 28%,rgba(9,22,28,.86) 100%);}}
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
    components.html(html_blob, height=207, scrolling=False)


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
        f'<div class="hero-time">{dot}{status}'
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
    left, middle, right = st.columns([1.02, 1.22, .84], gap="small")

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
                '<div class="panel-note">30 DAYS · SPACEPORT INCLUDED</div></div>'
            )
            render_upcoming_v08(upcoming, limit=3)
            raw_html('<div class="featured-label">FEATURED LAUNCH</div>')
            featured_launch_slideshow_v08(recent)

    raw_html(
        '<div class="footerline">'
        '<span>AUTO · LAUNCH LIBRARY 2 · CELESTRAK · SpaceNews · Spaceflight Now · ESA · EUSPA · JPL</span>'
        '<span>v0.8 Nordic Contrast · 16:9 · 24–40&quot; · refresh 15 min</span>'
        '</div>'
    )


render_dashboard()
