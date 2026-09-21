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

# ============================================================
# SPACE UPDATE v0.6.3 · NORDIC · SINGLE-SCREEN FIX
# 16:9 information display for 24–40" monitors
#
# LOCKED CORE FEATURES
# - 16:9 single-screen wall display
# - 5 major actor cards
# - launches + new orbital objects (24h / 7d)
# - Europe capability picture
# - all European launch actors
# - orbital picture + What Changed?
# - next launches
# - launch image gallery
# - rolling news ticker
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
HEADERS = {"User-Agent": "SpaceUpdateDashboard/0.6.3-nordic (Streamlit 16:9 wall display)"}

NEWS_FEEDS = [
    ("EUSPA", "https://www.euspa.europa.eu/pressroom/press-releases/rss.xml", 0),
    ("ESA", "https://www.esa.int/rssfeed/Our_Activities/Space_News", 1),
    ("JPL", "https://www.jpl.nasa.gov/feeds/news/", 2),
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
    items = []
    for source, url, priority in NEWS_FEEDS:
        try:
            xml = _request_text(url, timeout=15)
            root = ET.fromstring(xml)
            candidates = [x for x in root.iter() if x.tag.split("}")[-1].lower() in ("item", "entry")]
            for item in candidates[:6]:
                title = _xml_text(item, {"title"})
                link = _xml_text(item, {"link"})
                if not link:
                    for child in item.iter():
                        if child.tag.split("}")[-1].lower() == "link":
                            link = child.attrib.get("href", "")
                            if link:
                                break
                raw_date = _xml_text(item, {"pubdate", "published", "updated", "date"})
                dt = None
                if raw_date:
                    try:
                        dt = parsedate_to_datetime(raw_date)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                        except Exception:
                            dt = None
                if title:
                    items.append({"source": source, "title": title, "link": link, "date": dt, "priority": priority})
        except Exception:
            continue

    # Remove duplicates, then favour recent items; Europe wins ties.
    seen = set()
    unique = []
    for x in items:
        key = re.sub(r"\W+", "", x["title"].lower())[:120]
        if key and key not in seen:
            seen.add(key)
            unique.append(x)

    floor = datetime.now(timezone.utc) - timedelta(days=10)
    fresh = [x for x in unique if x["date"] is None or x["date"].astimezone(timezone.utc) >= floor]
    fresh.sort(key=lambda x: (x["date"] or datetime(1970,1,1,tzinfo=timezone.utc), -x["priority"]), reverse=True)
    return fresh[:10]


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
# 06 · VISUAL DESIGN / CSS
# Nordic direction: calm, flat, spacious, restrained accents.
# Keep class names stable so individual sections can be restyled later.
# ============================================================

st.markdown(
    """
<style>
/* ==========================================================
   A. STREAMLIT CHROME + 16:9 CANVAS
   ========================================================== */
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {display:none !important;}
[data-testid="stSidebar"] {display:none !important;}
.block-container {
    max-width:100vw !important;
    padding:0.42rem 0.70rem 0.30rem 0.70rem !important;
}
.stApp {
    color:#F3F6F8;
    background:#09131D;
}
html, body, [class*="css"] {
    font-family:Inter,"Segoe UI",Arial,sans-serif;
    letter-spacing:0;
}

/* ==========================================================
   B. HEADER
   ========================================================== */
.hero {
    display:flex;
    justify-content:space-between;
    align-items:flex-end;
    margin:0 0 .20rem 0;
}
.hero-title {
    font-size:clamp(27px,1.85vw,37px);
    font-weight:780;
    letter-spacing:.075em;
    line-height:1;
    color:#F5F7F8;
}
.hero-sub {
    font-size:clamp(9px,.68vw,13px);
    color:#8FA2B1;
    letter-spacing:.10em;
    margin-top:.28rem;
}
.hero-time {
    text-align:right;
    color:#8397A6;
    font-size:9px;
    letter-spacing:.08em;
}
.hero-time strong {
    display:block;
    color:#EDF2F5;
    font-size:16px;
    font-weight:680;
    margin-top:2px;
}
.live-dot {
    display:inline-block;
    width:6px;
    height:6px;
    border-radius:50%;
    background:#7BC5A4;
    margin-right:5px;
}

/* ==========================================================
   C. SECTION HEADINGS
   ========================================================== */
.section-row {
    display:flex;
    justify-content:space-between;
    align-items:center;
    margin:.31rem 0 .26rem;
}
.section-title {
    font-size:10px;
    font-weight:760;
    color:#A6B6C2;
    letter-spacing:.12em;
}
.source-note {
    font-size:8px;
    color:#52697B;
    letter-spacing:.09em;
}

/* ==========================================================
   D. NEWS TICKER
   ========================================================== */
.news-ticker {
    height:24px;
    overflow:hidden;
    border:1px solid #1B2B39;
    border-radius:9px;
    background:#0D1924;
    margin:.16rem 0 .30rem;
    position:relative;
}
.news-ticker:before {
    content:"NEWS";
    position:absolute;
    z-index:3;
    left:0;
    top:0;
    bottom:0;
    display:flex;
    align-items:center;
    padding:0 10px;
    background:#152737;
    color:#9FC6DD;
    font-size:8px;
    font-weight:800;
    letter-spacing:.12em;
    border-right:1px solid #263A49;
}
.news-track {
    display:flex;
    width:max-content;
    height:24px;
    align-items:center;
    animation:news-scroll 88s linear infinite;
    padding-left:68px;
}
.news-set {display:flex; align-items:center; white-space:nowrap;}
.news-item {font-size:9px; color:#C9D3DA; margin-right:26px;}
.news-source {color:#8FB9D0; font-weight:760; letter-spacing:.07em; margin-right:5px;}
.news-dot {color:#3F5667; margin-right:9px;}
@keyframes news-scroll {from{transform:translateX(0)} to{transform:translateX(-50%)}}

/* ==========================================================
   E. MAJOR ACTOR CARDS
   Big number = 7 days. Small line below = 24 hours.
   Each card contains LAUNCHES + NEW OBJECTS.
   ========================================================== */
.actor-card {
    border-radius:14px;
    padding:8px 11px 7px;
    min-height:96px;
    background:#11212D;
    border:1px solid #304553;
    border-top:3px solid var(--accent);
    overflow:hidden;
    position:relative;
}
.actor-card:after {display:none;}
.actor-topline {
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:8px;
    margin-bottom:4px;
}
.actor-name {
    font-size:10px;
    font-weight:780;
    letter-spacing:.11em;
    color:var(--accent);
}
.actor-flag {
    width:33px;
    height:25px;
    border-radius:7px;
    display:flex;
    align-items:center;
    justify-content:center;
    background:#142431;
    border:1px solid #324957;
    font-size:18px;
    line-height:1;
    overflow:hidden;
}
.actor-flag img {
    width:100%;
    height:100%;
    object-fit:cover;
    display:block;
}
.actor-flag.globe {
    font-size:18px;
}
.actor-metrics {
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:10px;
}
.actor-metric + .actor-metric {
    border-left:1px solid #293B49;
    padding-left:10px;
}
.actor-metric-label {
    font-size:7px;
    color:#778D9E;
    font-weight:760;
    letter-spacing:.09em;
}
.actor-big {
    font-size:clamp(25px,1.65vw,34px);
    font-weight:760;
    line-height:.95;
    margin-top:3px;
    color:#F4F7F9;
}
.actor-seven-label {
    font-size:7px;
    color:#657B8C;
    letter-spacing:.07em;
    margin-top:2px;
}
.actor-24h {
    font-size:8px;
    color:#93A6B4;
    margin-top:3px;
}
.actor-24h strong {
    color:#E7EDF1;
    font-size:10px;
    font-weight:760;
}

/* ==========================================================
   F. PANEL CONTAINERS
   ========================================================== */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background:#0F1C27;
    border:1px solid #253746 !important;
    border-radius:14px !important;
    box-shadow:none !important;
}
div[data-testid="stVerticalBlockBorderWrapper"] > div {
    padding:.42rem .52rem .38rem !important;
}
div[data-testid="stVerticalBlock"] {
    gap:.34rem;
}
.panel-title {
    font-size:10px;
    font-weight:760;
    color:#AEBCC6;
    letter-spacing:.11em;
    margin-bottom:7px;
    padding-top:1px;
}
.accent-blue,.accent-green,.accent-gold {
    height:2px;
    border-radius:3px;
    margin:-4px 0 8px;
    box-shadow:none;
}
.accent-blue {background:#5FA7D8;}
.accent-green {background:#6EAF9B;}
.accent-gold {background:#B99C62;}

/* ==========================================================
   G. EUROPE CAPABILITY CARDS
   ========================================================== */
.cap-card {
    border-radius:11px;
    padding:5px 6px;
    background:#142534;
    border:1px solid #294052;
    min-height:82px;
}
.cap-title {
    font-size:9px;
    font-weight:760;
    color:#AACBDE;
    letter-spacing:.07em;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}
.cap-grid {
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:5px;
    margin-top:5px;
}
.cap-cell {
    padding:4px 2px;
    text-align:center;
    border-radius:7px;
    background:#0D1C27;
    border:1px solid #1D3140;
}
.cap-label {
    font-size:7px;
    color:#6E8799;
    letter-spacing:.08em;
    font-weight:750;
}
.cap-value {
    font-size:clamp(15px,1.0vw,21px);
    line-height:1.05;
    font-weight:760;
    margin-top:2px;
    color:#F0F4F6;
}
.cap-sub {font-size:7px; color:#637B8D; margin-top:4px;}
.watchbar {
    border-left:3px solid #5FA7D8;
    border-radius:7px;
    background:#122838;
    padding:7px 9px;
    margin-top:5px;
}
.watchbar b {color:#86B8D6; font-size:8px; letter-spacing:.11em;}
.watchbar span {display:block; color:#C4D0D8; font-size:8px; margin-top:1px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}

/* ==========================================================
   H. EUROPE ACTOR TABLE
   ========================================================== */
.eu-summary {display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-bottom:4px;}
.eu-summary-card {
    border-radius:9px;
    background:#142534;
    border:1px solid #294052;
    padding:5px 6px;
}
.eu-summary-label {font-size:7px; color:#7593A9; font-weight:760; letter-spacing:.09em;}
.eu-summary-value {font-size:17px; font-weight:760; line-height:1; margin-top:2px; color:#F1F5F7;}
.eu-table {width:100%;}
.eu-actor-grid {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:3px 5px; margin-top:3px;}
.eu-row {
    display:grid;
    grid-template-columns:1.65fr .55fr .68fr .58fr;
    gap:4px;
    align-items:center;
    min-height:23px;
    padding:2px 5px;
    border:1px solid #213442;
    border-radius:8px;
    background:#0D1B26;
}
.eu-colheads {
    display:grid;
    grid-template-columns:1.65fr .55fr .68fr .58fr;
    gap:4px;
    padding:0 6px;
    color:#667F92;
    font-size:7px;
    font-weight:760;
    letter-spacing:.08em;
}
.eu-namewrap {display:flex; align-items:center; gap:6px; min-width:0;}
.eu-logo {width:16px; height:16px; object-fit:contain; flex:none;}
.eu-logo-fallback {
    width:16px;
    height:16px;
    border-radius:5px;
    border:1px solid #365166;
    color:#8EA9BB;
    display:flex;
    align-items:center;
    justify-content:center;
    font-size:7px;
    font-weight:760;
    flex:none;
}
.eu-name {font-size:8px; font-weight:680; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.eu-num {font-size:9px; font-weight:760; text-align:right;}
.eu-muted {color:#70889c;}

/* Compact Europe-events subheading inside the Europe panel */
.eu-subsection-title {
    margin:6px 0 4px;
    padding-top:5px;
    border-top:1px solid #213442;
    color:#8EB8D0;
    font-size:8px;
    font-weight:780;
    letter-spacing:.11em;
}

/* ==========================================================
   I. WHAT CHANGED + NEXT LAUNCHES
   ========================================================== */
.change-row {
    display:grid;
    grid-template-columns:3px 1fr auto;
    gap:7px;
    padding:3px 0;
    border-bottom:1px solid #1C2E3B;
    align-items:center;
}
.change-line {width:3px; height:24px; border-radius:4px; background:var(--accent);}
.change-name {font-size:9px; font-weight:690; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.change-meta {font-size:7px; color:#698196; margin-top:1px;}
.change-orbit {font-size:8px; color:#8BA3B6; font-weight:760;}
.next-card {
    border-radius:9px;
    padding:5px 6px;
    margin-bottom:5px;
    background:#131F28;
    border:1px solid #2C3A43;
}
.next-date {font-size:8px; color:#BCA56E; font-weight:760;}
.next-title {font-size:9px; font-weight:690; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px;}
.next-provider {font-size:7px; color:#72889A; margin-top:1px;}

/* ==========================================================
   J. LAUNCH GALLERY
   ========================================================== */
.launch-card {
    height:clamp(92px,10.4vh,112px);
    border-radius:12px;
    overflow:hidden;
    border:1px solid #263A49;
    position:relative;
    background:#0B1822;
}
.launch-img {width:100%; height:100%; object-fit:cover; display:block;}
.launch-placeholder {width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:40px; background:#12222E;}
.launch-overlay {position:absolute; left:0; right:0; bottom:0; padding:19px 9px 6px; background:linear-gradient(transparent,rgba(6,13,18,.94) 46%);}
.launch-name {font-size:10px; font-weight:730; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.launch-meta {font-size:8px; color:#9AAEBB; margin-top:2px;}
.launch-credit {position:absolute; right:5px; top:5px; max-width:70%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; border-radius:4px; padding:2px 4px; background:rgba(5,10,14,.62); color:#D0D8DE; font-size:6px;}

/* ==========================================================
   K. WARNINGS + FOOTER
   ========================================================== */
.source-warning {
    margin:.3rem 0 .15rem;
    padding:5px 8px;
    border-radius:7px;
    background:#2A2418;
    border:1px solid #574C31;
    color:#CDBD92;
    font-size:8px;
}
.footerline {display:flex; justify-content:space-between; margin-top:5px; color:#40596D; font-size:7px; letter-spacing:.06em;}

/* 16:9 monitor tuning */
@media(max-width:1400px){
  .actor-card{min-height:92px;}
  .launch-card{height:94px;}
}
@media(max-height:900px){
  .block-container{padding-top:.28rem !important;padding-bottom:.20rem !important;}
  .hero-title{font-size:28px;}
  .news-ticker,.news-track{height:22px;}
  .actor-card{min-height:90px;}
  .cap-card{min-height:76px;}
  .launch-card{height:88px;}
  .section-row{margin:.20rem 0 .18rem;}
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# RENDER HELPERS
# ============================================================

def section_header(title, note=""):
    st.markdown(
        f'<div class="section-row"><div class="section-title">{esc(title)}</div><div class="source-note">{esc(note)}</div></div>',
        unsafe_allow_html=True,
    )


def raw_html(markup):
    """Render trusted dashboard HTML without Markdown parsing it as code."""
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def _display_object_total(known, unknown, estimated=False):
    if unknown:
        text = f"≥{known}" if known else "?"
    else:
        text = str(known)
    if estimated and text != "?":
        text += "*"
    return text


def render_actor_card(actor, launches24, launches7, obj24, obj7, logo=None, data_ok=True):
    """
    LOCKED DESIGN RULE FOR TOP CARDS:
    - Large value = LAST 7 DAYS
    - Small value underneath = LAST 24 HOURS
    - Always show a real flag / globe marker
    - Show both LAUNCHES and NEW OBJECTS

    IMPORTANT: Keep this HTML compact. Blank lines inside nested raw HTML blocks can
    make Streamlit/Markdown terminate the HTML block and render the remainder as text.
    """
    colour = ACTOR_COLOURS[actor]

    l24 = launches24 if data_ok else "—"
    l7 = launches7 if data_ok else "—"
    o24 = obj24 if data_ok else "—"
    o7 = obj7 if data_ok else "—"

    if actor in ACTOR_FLAG_URLS:
        flag_html = f'<img src="{ACTOR_FLAG_URLS[actor]}" alt="{actor} flag">'
        flag_class = "actor-flag"
    else:
        flag_html = "🌍"
        flag_class = "actor-flag globe"

    card_html = (
        f'<div class="actor-card" style="--accent:{colour};">'
        f'<div class="actor-topline"><div class="actor-name">{actor}</div>'
        f'<div class="{flag_class}" aria-label="{actor} flag">{flag_html}</div></div>'
        f'<div class="actor-metrics">'
        f'<div class="actor-metric"><div class="actor-metric-label">LAUNCHES</div>'
        f'<div class="actor-big">{l7}</div><div class="actor-seven-label">LAST 7 DAYS</div>'
        f'<div class="actor-24h"><strong>{l24}</strong> · LAST 24H</div></div>'
        f'<div class="actor-metric"><div class="actor-metric-label">NEW OBJECTS</div>'
        f'<div class="actor-big">{o7}</div><div class="actor-seven-label">LAST 7 DAYS</div>'
        f'<div class="actor-24h"><strong>{o24}</strong> · LAST 24H</div></div>'
        f'</div></div>'
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_capability_card(item):
    st.markdown(
        f"""
        <div class="cap-card">
          <div class="cap-title">{esc(item['name'])}</div>
          <div class="cap-grid">
            <div class="cap-cell"><div class="cap-label">Δ 7D</div><div class="cap-value">{esc(item['change'])}</div></div>
            <div class="cap-cell"><div class="cap-label">TOTAL</div><div class="cap-value">{esc(item['total'])}</div></div>
            <div class="cap-cell"><div class="cap-label">PLAN 30D</div><div class="cap-value">{esc(item['planned'])}</div></div>
          </div>
          <div class="cap-sub">{esc(item['sub'])}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_europe_actor_table(recent, upcoming):
    """Compact two-column European actor matrix for the 16:9 Europe panel."""
    rows = build_europe_actor_stats(recent, upcoming)
    total_launches = sum(v["launches_7d"] for _, v in rows)
    total_objects = sum(v["objects_known"] for _, v in rows)
    unknown_launches = sum(v["objects_unknown_launches"] for _, v in rows)
    total_planned = sum(v["planned_30d"] for _, v in rows)
    any_estimated = any(v["objects_estimated"] for _, v in rows)

    object_total_label = str(total_objects)
    if unknown_launches:
        object_total_label = f"≥{total_objects}" if total_objects else "?"
    if any_estimated and object_total_label != "?":
        object_total_label += "*"

    cards = []
    for actor, values in rows:
        obj = str(values["objects_known"])
        if values["objects_unknown_launches"]:
            obj = f"≥{obj}" if values["objects_known"] else "?"
        if values["objects_estimated"] and obj != "?":
            obj += "*"

        logo = values["logo"]
        if logo:
            logo_html = f'<img class="eu-logo" src="{esc(logo)}" alt="{esc(actor)}">'
        else:
            initials = "".join(x[0] for x in actor.split()[:2]).upper()
            logo_html = f'<div class="eu-logo-fallback">{esc(initials)}</div>'

        active = values["launches_7d"] or values["planned_30d"]
        opacity = "1" if active else ".48"
        # Intentionally one-line HTML: avoids Markdown treating nested divs as code blocks.
        cards.append(
            f'<div class="eu-row" style="opacity:{opacity}">'
            f'<div class="eu-namewrap">{logo_html}<div class="eu-name">{esc(actor)}</div></div>'
            f'<div class="eu-num">{values["launches_7d"]}</div>'
            f'<div class="eu-num">{obj}</div>'
            f'<div class="eu-num">{values["planned_30d"]}</div>'
            f'</div>'
        )

    markup = (
        f'<div class="eu-summary">'
        f'<div class="eu-summary-card"><div class="eu-summary-label">LAUNCHES · 7D</div><div class="eu-summary-value">{total_launches}</div></div>'
        f'<div class="eu-summary-card"><div class="eu-summary-label">NEW OBJECTS · 7D</div><div class="eu-summary-value">{object_total_label}</div></div>'
        f'<div class="eu-summary-card"><div class="eu-summary-label">PLANNED · 30D</div><div class="eu-summary-value">{total_planned}</div></div>'
        f'</div>'
        f'<div class="eu-colheads"><div>EUROPEAN ACTOR</div><div style="text-align:right">LCH</div><div style="text-align:right">OBJECTS</div><div style="text-align:right">PLAN</div></div>'
        f'<div class="eu-actor-grid">{"".join(cards)}</div>'
        f'<div style="font-size:6px;color:#526b7e;margin-top:2px">* estimated minimum · ? catalogue not yet resolved</div>'
    )
    raw_html(markup)

def render_news_ticker(items):
    if not items:
        st.markdown('<div class="news-ticker"><div class="news-track"><div class="news-set"><span class="news-item"><span class="news-source">NEWS</span> Feed temporarily unavailable · automatic retry every 15 minutes</span></div></div></div>', unsafe_allow_html=True)
        return

    parts = []
    for item in items[:8]:
        title = esc(item.get("title"))
        source = esc(item.get("source"))
        link = item.get("link") or ""
        if link:
            title_html = f'<a href="{esc(link)}" target="_blank" style="color:inherit;text-decoration:none">{title}</a>'
        else:
            title_html = title
        parts.append(f'<span class="news-item"><span class="news-source">{source}</span>{title_html}</span><span class="news-dot">◆</span>')
    one_set = ''.join(parts)
    st.markdown(f'<div class="news-ticker"><div class="news-track"><div class="news-set">{one_set}</div><div class="news-set" aria-hidden="true">{one_set}</div></div></div>', unsafe_allow_html=True)


def orbit_visual(recent):
    counts = defaultdict(int)
    for launch in recent:
        counts[orbit_group(launch)] += 1

    # Dots are mathematically placed ON each ring.
    # Centre=(210,145), radii: LEO=48, MEO=88, GEO=128.
    svg = f"""
    <html><head><style>
      body{{margin:0;background:transparent;color:#EDF2F5;font-family:Inter,Segoe UI,Arial,sans-serif;}}
      .wrap{{height:282px;border-radius:12px;background:#0D1B26;position:relative;border:1px solid #213442;}}
      svg{{width:100%;height:240px;display:block;}}
      .ring{{fill:none;stroke-width:1.35;}}
      .label{{font-size:10px;font-weight:760;letter-spacing:1px;}}
      .count{{font-size:15px;font-weight:760;}}
      .earth{{fill:#17384B;stroke:#577B91;stroke-width:1.1;}}
      .minor{{font-size:8px;fill:#6F8596;}}
      .legend{{display:flex;justify-content:center;gap:22px;font-size:9px;color:#7890A0;margin-top:-7px;}}
      .dot{{filter:none;}}
    </style></head><body>
      <div class="wrap">
      <svg viewBox="0 0 420 250" role="img" aria-label="LEO MEO GEO orbital activity">
        <circle cx="210" cy="125" r="128" class="ring" stroke="#B99C62" opacity=".60"/>
        <circle cx="210" cy="125" r="88" class="ring" stroke="#6EAF9B" opacity=".66"/>
        <circle cx="210" cy="125" r="48" class="ring" stroke="#5FA7D8" opacity=".75"/>
        <circle cx="210" cy="125" r="27" class="earth"/>
        <text x="210" y="129" text-anchor="middle" fill="#e7f4fc" font-size="9" font-weight="850">EARTH</text>

        <!-- LEO marker, angle 140°, radius 48 -->
        <circle cx="173.2" cy="155.9" r="5" fill="#5FA7D8" class="dot"/>
        <text x="154" y="170" text-anchor="middle" fill="#8FB9D0" class="label">LEO</text>
        <text x="154" y="184" text-anchor="middle" fill="#ffffff" class="count">{counts['LEO']}</text>

        <!-- MEO marker, angle -42°, radius 88 -->
        <circle cx="275.4" cy="66.1" r="5" fill="#6EAF9B" class="dot"/>
        <text x="294" y="57" text-anchor="middle" fill="#91C7B6" class="label">MEO</text>
        <text x="294" y="71" text-anchor="middle" fill="#ffffff" class="count">{counts['MEO']}</text>

        <!-- GEO marker, angle 18°, radius 128 -->
        <circle cx="331.7" cy="164.6" r="5" fill="#B99C62" class="dot"/>
        <text x="353" y="165" text-anchor="middle" fill="#C8B17B" class="label">GEO</text>
        <text x="353" y="179" text-anchor="middle" fill="#ffffff" class="count">{counts['GEO']}</text>

        <text x="210" y="235" text-anchor="middle" class="minor">launch destination / mission orbit · last 7 days</text>
      </svg>
      <div class="legend"><span>● LEO</span><span>● MEO</span><span>● GEO</span></div>
      </div>
    </body></html>
    """
    components.html(svg, height=232, scrolling=False)


def render_changes(recent, limit=6):
    if not recent:
        st.caption("No recent launch data available.")
        return
    for launch in recent[:limit]:
        actor = major_actor(launch)
        colour = ACTOR_COLOURS[actor]
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b · %H:%M") if d else ""
        count, source = object_count_for_launch(launch)
        obj = "? obj" if count is None else f"+{count}{'*' if source == 'estimated' else ''} obj"
        st.markdown(
            f"""
            <div class="change-row" style="--accent:{colour}">
              <div class="change-line"></div>
              <div><div class="change-name">{esc(launch.get('name'))}</div><div class="change-meta">{actor} · {esc(launch_provider_name(launch))} · {when} · {obj}</div></div>
              <div class="change-orbit">{orbit_group(launch)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_upcoming(upcoming, limit=5):
    if not upcoming:
        st.caption("Upcoming launch data unavailable.")
        return
    for launch in upcoming[:limit]:
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b · %H:%M") if d else "TBD"
        st.markdown(
            f"""
            <div class="next-card">
              <div class="next-date">{when}</div>
              <div class="next-title">{esc(launch.get('name'))}</div>
              <div class="next-provider">{esc(launch_provider_name(launch))} · {orbit_group(launch)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def launch_image_data(launch):
    image = launch.get("image") or {}
    return image.get("image_url"), image.get("credit") or ""


def render_launch_card(launch):
    url, credit = launch_image_data(launch)
    d = parse_dt(launch.get("net"))
    when = d.astimezone(LOCAL_TZ).strftime("%d %b") if d else ""
    if url:
        visual = f'<img class="launch-img" src="{esc(url)}">'
    else:
        visual = '<div class="launch-placeholder">🚀</div>'
    credit_html = f'<div class="launch-credit">{esc(credit)}</div>' if credit else ""
    st.markdown(
        f"""
        <div class="launch-card">
          {visual}{credit_html}
          <div class="launch-overlay">
            <div class="launch-name">{esc(launch.get('name'))}</div>
            <div class="launch-meta">{major_actor(launch)} · {when} · {orbit_group(launch)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# DASHBOARD
# ============================================================

@st.fragment(run_every=900)
def render_dashboard():
    recent, recent_ok = get_recent_launches(7)
    upcoming, upcoming_ok = get_upcoming_launches(30)
    news, news_ok = get_news()
    now = datetime.now(LOCAL_TZ)

    # Primary live status is launch activity. Catalogue/YTD are secondary feeds.
    status = "LIVE" if recent_ok and upcoming_ok else "PARTIAL DATA"
    dot = '<span class="live-dot"></span>' if recent_ok else ""

    st.markdown(
        f"""
        <div class="hero">
          <div><div class="hero-title">SPACE UPDATE</div><div class="hero-sub">GLOBAL SPACE ACTIVITY · 7 DAY PICTURE</div></div>
          <div class="hero-time">{dot}{status}<strong>{now.strftime('%d %b · %H:%M')}</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    render_news_ticker(news)

    if not recent_ok or not upcoming_ok:
        st.markdown('<div class="source-warning">Launch Library is temporarily unavailable. Values are shown as — and the dashboard will retry automatically; no failed response is cached.</div>', unsafe_allow_html=True)

    # -------------------- Major actors --------------------
    section_header("NEW EVENTS BY MAJOR ACTOR", "AUTO · LAUNCHES + NEW ORBITAL OBJECTS · BIG = 7D · SMALL = 24H")

    # The header + news ticker are already on screen before the slower SATCAT work.
    # This is the only bounded catalogue warm-up in the render; later sections reuse it.
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

    actor_logos = {}
    for launch in recent:
        actor = major_actor(launch)
        if actor not in actor_logos:
            logo = provider_logo_from_launch(launch)
            if logo:
                actor_logos[actor] = logo

    cols = st.columns(5, gap="small")
    for col, actor in zip(cols, ["EUROPE", "USA", "CHINA", "RUSSIA", "OTHER"]):
        with col:
            o24 = _display_object_total(obj24_known[actor], obj24_unknown[actor], obj24_est[actor])
            o7 = _display_object_total(obj7_known[actor], obj7_unknown[actor], obj7_est[actor])
            render_actor_card(actor, counts24[actor], counts7[actor], o24, o7, actor_logos.get(actor), recent_ok)

    # -------------------- Main area --------------------
    left, middle, right = st.columns([1.12, 1.05, .83], gap="small")

    # Fill the fast panels first so the wall display never looks empty while a secondary catalogue responds.
    with middle:
        with st.container(border=True):
            st.markdown('<div class="accent-green"></div><div class="panel-title">ORBITAL ACTIVITY · LAST 7 DAYS</div>', unsafe_allow_html=True)
            orbit_visual(recent)
            st.markdown('<div class="panel-title" style="margin-top:-3px">WHAT CHANGED?</div>', unsafe_allow_html=True)
            render_changes(recent, limit=4)

    with right:
        with st.container(border=True):
            st.markdown('<div class="accent-gold"></div><div class="panel-title">NEXT LAUNCHES · 30 DAYS</div>', unsafe_allow_html=True)
            render_upcoming(upcoming, limit=5)

    with left:
        with st.container(border=True):
            st.markdown('<div class="accent-blue"></div><div class="panel-title">EUROPE · CAPABILITY PICTURE</div>', unsafe_allow_html=True)
            # YTD is secondary data. Fetch it only now, after the actor/orbit/next-launch panels exist.
            ytd, ytd_ok = get_ytd_launches()
            caps = europe_capability_stats(recent, upcoming, ytd if ytd_ok else None)
            c1, c2 = st.columns(2, gap="small")
            with c1:
                render_capability_card(caps[0])
                render_capability_card(caps[2])
            with c2:
                render_capability_card(caps[1])
                render_capability_card(caps[3])
            st.markdown('<div class="watchbar"><b>EUROPE · BUILDING / WATCH</b><span>IRIS² · GOVSATCOM · Ariane 6 · Vega-C · Spectrum · RFA One · Orbex Prime · Miura 5</span></div>', unsafe_allow_html=True)
            st.markdown('<div class="eu-subsection-title">EUROPE EVENTS · ALL LAUNCH ACTORS · LCH / OBJECTS / PLAN 30D</div>', unsafe_allow_html=True)
            render_europe_actor_table(recent, upcoming)

    # -------------------- Launch gallery --------------------
    section_header("LAUNCHES · LAST 7 DAYS", "IMAGES · SOURCE/CREDIT SHOWN ON IMAGE")
    image_launches = [x for x in recent if launch_image_data(x)[0]]
    chosen = image_launches[:3] if len(image_launches) >= 3 else recent[:3]
    gallery_cols = st.columns(3, gap="small")
    for i, col in enumerate(gallery_cols):
        with col:
            if i < len(chosen):
                render_launch_card(chosen[i])
            else:
                st.markdown('<div class="launch-card"><div class="launch-placeholder">🛰️</div></div>', unsafe_allow_html=True)

    st.markdown(
        '<div class="footerline"><span>AUTO SOURCES · LAUNCH LIBRARY 2 · CELESTRAK SATCAT · ESA / EUSPA / JPL RSS</span><span>16:9 single-screen · v0.6.3 · launch/news 15 min · SATCAT fast-cache 5 min/6 h</span></div>',
        unsafe_allow_html=True,
    )


render_dashboard()
