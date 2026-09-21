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
# SPACE UPDATE v0.6.4 · NORDIC · 24-INCH READABLE
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
HEADERS = {"User-Agent": "SpaceUpdateDashboard/0.6.4-nordic-readable (Streamlit 16:9 wall display)"}

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
# 06 · VISUAL DESIGN / CSS · v0.7 NORDIC REDESIGN
# ============================================================

st.markdown(
    """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {display:none !important;}
[data-testid="stSidebar"] {display:none !important;}
.block-container {max-width:100vw !important; padding:.42rem .72rem .34rem .72rem !important;}
.stApp {color:#F3F5F4; background:#0B141A;}
html, body, [class*="css"] {font-family:Inter,"Segoe UI",Arial,sans-serif; letter-spacing:0;}
div[data-testid="stVerticalBlock"] {gap:.36rem;}
div[data-testid="stHorizontalBlock"] {gap:.62rem;}

/* HEADER */
.hero {display:flex;justify-content:space-between;align-items:flex-end;margin:0 0 .12rem 0;}
.hero-title {font-size:clamp(31px,1.9vw,39px);font-weight:760;letter-spacing:.075em;line-height:1;color:#F6F7F5;}
.hero-sub {font-size:clamp(11px,.70vw,14px);color:#8FA0A9;letter-spacing:.11em;margin-top:.25rem;}
.hero-time {text-align:right;color:#83949D;font-size:10px;letter-spacing:.09em;}
.hero-time strong {display:block;color:#F2F4F3;font-size:18px;font-weight:700;margin-top:1px;}
.live-dot {display:inline-block;width:6px;height:6px;border-radius:50%;background:#86B79E;margin-right:5px;}

/* NEWS */
.news-ticker {height:28px;overflow:hidden;border-top:1px solid #20313A;border-bottom:1px solid #20313A;background:#0D181E;margin:.12rem 0 .22rem;position:relative;}
.news-ticker:before {content:"NEWS";position:absolute;z-index:3;left:0;top:0;bottom:0;display:flex;align-items:center;padding:0 10px;background:#13232C;color:#8FB7CB;font-size:9px;font-weight:800;letter-spacing:.13em;border-right:1px solid #263B45;}
.news-track {display:flex;width:max-content;height:28px;align-items:center;animation:news-scroll 92s linear infinite;padding-left:66px;}
.news-set {display:flex;align-items:center;white-space:nowrap;}
.news-item {font-size:10px;color:#C4CDD1;margin-right:30px;}
.news-source {color:#82AABE;font-weight:760;letter-spacing:.07em;margin-right:5px;}
.news-dot {color:#3A515C;margin-right:10px;}
@keyframes news-scroll {from{transform:translateX(0)} to{transform:translateX(-50%)}}

/* SECTION LABELS */
.section-row {display:flex;justify-content:space-between;align-items:center;margin:.20rem 0 .18rem;}
.section-title {font-size:11px;font-weight:760;color:#A9B7BD;letter-spacing:.13em;}
.source-note {font-size:8px;color:#516773;letter-spacing:.09em;}

/* ACTOR STRIP */
.actor-card {min-height:104px;padding:9px 12px 9px;border-radius:12px;background:#101E26;border:1px solid #273A43;border-top:3px solid var(--accent);}
.actor-topline {display:flex;align-items:center;justify-content:space-between;margin-bottom:5px;}
.actor-name {color:var(--accent);font-size:11px;font-weight:780;letter-spacing:.11em;}
.actor-flag {width:32px;height:23px;border-radius:5px;overflow:hidden;border:1px solid #344B55;background:#14242C;display:flex;align-items:center;justify-content:center;}
.actor-flag img {width:100%;height:100%;object-fit:cover;display:block;}
.actor-flag.globe {font-size:16px;}
.actor-metrics {display:grid;grid-template-columns:1fr 1fr;gap:12px;}
.actor-metric + .actor-metric {border-left:1px solid #2B3C45;padding-left:12px;}
.actor-metric-label {color:#718690;font-size:8px;font-weight:780;letter-spacing:.09em;}
.actor-value-line {display:flex;align-items:flex-end;gap:7px;margin-top:2px;}
.actor-big {color:#F5F6F4;font-size:clamp(28px,1.75vw,36px);font-weight:740;line-height:.92;}
.actor-period {color:#627983;font-size:8px;letter-spacing:.06em;padding-bottom:2px;}
.actor-24h {color:#8FA0A8;font-size:9px;margin-top:4px;}
.actor-24h strong {color:#E7ECEA;font-size:11px;font-weight:760;}

/* PANELS */
div[data-testid="stVerticalBlockBorderWrapper"] {background:#0F1C22;border:1px solid #263841 !important;border-radius:13px !important;box-shadow:none !important;}
div[data-testid="stVerticalBlockBorderWrapper"] > div {padding:.52rem .62rem .50rem !important;}
.panel-heading {display:flex;justify-content:space-between;align-items:center;padding-bottom:7px;border-bottom:1px solid #243740;margin-bottom:8px;}
.panel-title {font-size:12px;font-weight:760;color:#B5C0C4;letter-spacing:.105em;}
.panel-note {font-size:8px;color:#5D737E;letter-spacing:.07em;}

/* EUROPE */
.eu-kpi-strip {display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:8px;}
.eu-kpi {background:#13242C;border:1px solid #2A414B;border-radius:9px;padding:7px 9px;}
.eu-kpi-label {font-size:8px;color:#7191A0;font-weight:760;letter-spacing:.09em;}
.eu-kpi-value {color:#F1F4F2;font-size:23px;font-weight:740;line-height:1;margin-top:3px;}
.cap-grid-v07 {display:grid;grid-template-columns:1fr 1fr;gap:7px;}
.cap-v07 {border-radius:10px;background:#12232B;border:1px solid #29404A;padding:8px 10px;min-height:78px;}
.cap-v07-head {display:flex;align-items:center;justify-content:space-between;gap:8px;}
.cap-v07-title {color:#B1CAD5;font-size:10px;font-weight:760;letter-spacing:.06em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.cap-v07-main {color:#F4F5F3;font-size:24px;font-weight:740;line-height:1;}
.cap-v07-meta {display:flex;gap:10px;color:#78909A;font-size:9px;margin-top:7px;}
.cap-v07-meta strong {color:#D9E1E0;font-weight:740;}
.cap-v07-sub {color:#5F7782;font-size:8px;margin-top:4px;}
.programmes {display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;}
.programme-chip {border:1px solid #2E4650;color:#9FB4BE;background:#102027;border-radius:999px;padding:4px 8px;font-size:8px;font-weight:680;}
.programme-chip.build {border-color:#5A7D8C;color:#B7CDD6;}
.programme-chip.service {border-color:#5E796D;color:#AAC4B6;}

/* EUROPEAN LAUNCH ECOSYSTEM */
.ecosystem-head {display:flex;justify-content:space-between;align-items:center;margin-top:9px;padding-top:7px;border-top:1px solid #243740;}
.ecosystem-title {color:#93AEBB;font-size:9px;font-weight:780;letter-spacing:.11em;}
.ecosystem-legend {color:#5F7680;font-size:8px;}
.ecosystem-grid {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px;margin-top:6px;}
.eco-chip {min-height:28px;display:flex;align-items:center;justify-content:space-between;gap:6px;padding:4px 7px;border-radius:8px;border:1px solid #253A44;background:#0D1A20;}
.eco-name {min-width:0;color:#BFC9CC;font-size:9px;font-weight:680;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.eco-status {display:flex;align-items:center;gap:4px;flex:none;}
.dot-active,.dot-plan,.dot-idle {width:7px;height:7px;border-radius:50%;display:inline-block;}
.dot-active {background:#79AD9A;}
.dot-plan {border:1px solid #BEA267;background:transparent;}
.dot-idle {background:#3E5058;}
.eco-count {color:#E5EAE8;font-size:9px;font-weight:740;}

/* WHAT CHANGED */
.change-v07 {display:grid;grid-template-columns:4px 30px 1fr auto;gap:8px;align-items:center;padding:7px 0;border-bottom:1px solid #1E313A;}
.change-v07:last-child {border-bottom:none;}
.change-accent {width:4px;height:34px;border-radius:4px;background:var(--accent);}
.change-flag {width:27px;height:19px;border-radius:4px;overflow:hidden;border:1px solid #304650;display:flex;align-items:center;justify-content:center;font-size:13px;}
.change-flag img {width:100%;height:100%;object-fit:cover;}
.change-main {font-size:11px;color:#EBEFED;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.change-sub {color:#667E88;font-size:8px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.change-orbit {color:#8397A0;font-size:9px;font-weight:740;}

/* NEXT LAUNCHES */
.next-v07 {display:grid;grid-template-columns:63px 1fr;gap:9px;padding:8px 0;border-bottom:1px solid #21343D;}
.next-v07:last-child {border-bottom:none;}
.next-when {color:#B8A16D;font-size:10px;font-weight:760;}
.next-tminus {color:#697F89;font-size:8px;margin-top:2px;}
.next-name {color:#F0F2F0;font-size:11px;font-weight:700;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.next-provider {color:#657C86;font-size:8px;margin-top:3px;}
.featured-label {margin-top:9px;color:#93AEBB;font-size:9px;font-weight:780;letter-spacing:.11em;}
.featured-fallback {height:205px;margin-top:6px;border:1px solid #2A3E47;border-radius:10px;background:#12232B;display:flex;align-items:center;justify-content:center;color:#78909A;}

/* WARNINGS / FOOTER */
.source-warning {background:#2A2418;border:1px solid #5A4B2D;color:#D8C89E;border-radius:8px;padding:5px 8px;font-size:9px;margin-bottom:4px;}
.footerline {display:flex;justify-content:space-between;color:#49616A;font-size:7px;letter-spacing:.05em;margin-top:3px;}

@media (max-width:1450px) {
  .hero-title {font-size:30px;}
  .actor-big {font-size:29px;}
  .actor-card {min-height:100px;}
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# 07 · RENDER HELPERS
# ============================================================

def section_header(title, note=""):
    st.markdown(
        f'<div class="section-row"><div class="section-title">{esc(title)}</div><div class="source-note">{esc(note)}</div></div>',
        unsafe_allow_html=True,
    )


def raw_html(markup):
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


def flag_markup(actor, css_class="actor-flag"):
    if actor in ACTOR_FLAG_URLS:
        return f'<div class="{css_class}"><img src="{ACTOR_FLAG_URLS[actor]}" alt="{actor} flag"></div>'
    return f'<div class="{css_class}">🌍</div>'


def render_actor_card(actor, launches24, launches7, obj24, obj7, data_ok=True):
    colour = ACTOR_COLOURS[actor]
    l24 = launches24 if data_ok else "—"
    l7 = launches7 if data_ok else "—"
    o24 = obj24 if data_ok else "—"
    o7 = obj7 if data_ok else "—"
    markup = (
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
    raw_html(markup)


def render_news_ticker(items):
    if not items:
        raw_html('<div class="news-ticker"><div class="news-track"><div class="news-set"><span class="news-item"><span class="news-source">NEWS</span>Feed temporarily unavailable · automatic retry every 15 minutes</span></div></div></div>')
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
    one_set = "".join(parts)
    raw_html(f'<div class="news-ticker"><div class="news-track"><div class="news-set">{one_set}</div><div class="news-set" aria-hidden="true">{one_set}</div></div></div>')


def capability_markup(item):
    return (
        f'<div class="cap-v07">'
        f'<div class="cap-v07-head"><div class="cap-v07-title">{esc(item["name"])}</div>'
        f'<div class="cap-v07-main">{esc(item["total"])}</div></div>'
        f'<div class="cap-v07-meta"><span>Δ7D <strong>{esc(item["change"])}</strong></span>'
        f'<span>PLAN <strong>{esc(item["planned"])}</strong></span></div>'
        f'<div class="cap-v07-sub">{esc(item["sub"])}</div>'
        f'</div>'
    )


def render_europe_panel(recent, upcoming, ytd):
    caps = europe_capability_stats(recent, upcoming, ytd)
    rows = build_europe_actor_stats(recent, upcoming)
    total_launches = sum(v["launches_7d"] for _, v in rows)
    total_objects = sum(v["objects_known"] for _, v in rows)
    unknown_launches = sum(v["objects_unknown_launches"] for _, v in rows)
    total_planned = sum(v["planned_30d"] for _, v in rows)
    any_estimated = any(v["objects_estimated"] for _, v in rows)
    object_total = str(total_objects)
    if unknown_launches:
        object_total = f"≥{total_objects}" if total_objects else "?"
    if any_estimated and object_total != "?":
        object_total += "*"
    cap_html = "".join(capability_markup(x) for x in caps)
    chips = []
    for actor, values in rows:
        active = values["launches_7d"] > 0
        planned = values["planned_30d"] > 0
        dot_a = '<span class="dot-active"></span>' if active else '<span class="dot-idle"></span>'
        dot_p = '<span class="dot-plan"></span>' if planned else ''
        activity = []
        if active:
            activity.append(f'{values["launches_7d"]}×7D')
        if planned:
            activity.append(f'{values["planned_30d"]}×30D')
        count_txt = " · ".join(activity) if activity else ""
        chips.append(
            f'<div class="eco-chip" title="{esc(actor)}"><div class="eco-name">{esc(actor)}</div>'
            f'<div class="eco-status">{dot_a}{dot_p}<span class="eco-count">{esc(count_txt)}</span></div></div>'
        )
    markup = (
        f'<div class="eu-kpi-strip">'
        f'<div class="eu-kpi"><div class="eu-kpi-label">LAUNCHES · 7D</div><div class="eu-kpi-value">{total_launches}</div></div>'
        f'<div class="eu-kpi"><div class="eu-kpi-label">NEW OBJECTS · 7D</div><div class="eu-kpi-value">{object_total}</div></div>'
        f'<div class="eu-kpi"><div class="eu-kpi-label">PLANNED · 30D</div><div class="eu-kpi-value">{total_planned}</div></div>'
        f'</div>'
        f'<div class="cap-grid-v07">{cap_html}</div>'
        f'<div class="programmes">'
        f'<span class="programme-chip build">IRIS² · BUILDING</span>'
        f'<span class="programme-chip service">GOVSATCOM · SERVICE</span>'
        f'<span class="programme-chip">ARIANE 6</span><span class="programme-chip">VEGA-C</span>'
        f'<span class="programme-chip">SPECTRUM</span><span class="programme-chip">RFA ONE</span>'
        f'</div>'
        f'<div class="ecosystem-head"><div class="ecosystem-title">EUROPEAN LAUNCH ECOSYSTEM</div>'
        f'<div class="ecosystem-legend">● active 7d · ○ planned 30d</div></div>'
        f'<div class="ecosystem-grid">{"".join(chips)}</div>'
    )
    raw_html(markup)


def orbit_visual_v07(recent):
    counts = defaultdict(int)
    for launch in recent:
        counts[orbit_group(launch)] += 1
    svg = f"""
    <html><head><style>
      body{{margin:0;background:transparent;color:#EEF2F0;font-family:Inter,Segoe UI,Arial,sans-serif;}}
      .wrap{{height:274px;border-radius:10px;background:#0D1A20;border:1px solid #243842;}}
      svg{{width:100%;height:268px;display:block;}}
      .ring{{fill:none;stroke-width:1.2;}}
      .label{{font-size:12px;font-weight:760;letter-spacing:.8px;}}
      .count{{font-size:18px;font-weight:760;}}
      .earth{{fill:#173440;stroke:#62828F;stroke-width:1;}}
      .minor{{font-size:9px;fill:#657B84;}}
    </style></head><body><div class="wrap"><svg viewBox="0 0 420 250">
      <circle cx="210" cy="122" r="126" class="ring" stroke="#B8A169" opacity=".58"/>
      <circle cx="210" cy="122" r="87" class="ring" stroke="#7FAE9C" opacity=".65"/>
      <circle cx="210" cy="122" r="48" class="ring" stroke="#6FAFD2" opacity=".74"/>
      <circle cx="210" cy="122" r="25" class="earth"/>
      <text x="210" y="126" text-anchor="middle" fill="#EEF3F0" font-size="10" font-weight="800">EARTH</text>
      <circle cx="173.2" cy="152.9" r="4.8" fill="#6FAFD2"/><text x="154" y="167" text-anchor="middle" fill="#8EBBD0" class="label">LEO</text><text x="154" y="181" text-anchor="middle" fill="#fff" class="count">{counts['LEO']}</text>
      <circle cx="274.7" cy="63.8" r="4.8" fill="#7FAE9C"/><text x="294" y="55" text-anchor="middle" fill="#9BC0B2" class="label">MEO</text><text x="294" y="69" text-anchor="middle" fill="#fff" class="count">{counts['MEO']}</text>
      <circle cx="329.8" cy="160.9" r="4.8" fill="#B8A169"/><text x="351" y="161" text-anchor="middle" fill="#C7B481" class="label">GEO</text><text x="351" y="175" text-anchor="middle" fill="#fff" class="count">{counts['GEO']}</text>
      <text x="210" y="232" text-anchor="middle" class="minor">mission destination / launch orbit · last 7 days</text>
    </svg></div></body></html>
    """
    components.html(svg, height=276, scrolling=False)


def render_changes_v07(recent, limit=3):
    if not recent:
        st.caption("No recent launch data available.")
        return
    for launch in recent[:limit]:
        actor = major_actor(launch)
        colour = ACTOR_COLOURS[actor]
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b") if d else ""
        count, source = object_count_for_launch(launch)
        obj = "catalogue pending" if count is None else f'+{count}{"*" if source == "estimated" else ""} objects'
        flag = flag_markup(actor, "change-flag")
        raw_html(
            f'<div class="change-v07" style="--accent:{colour}"><div class="change-accent"></div>{flag}'
            f'<div><div class="change-main">{esc(launch.get("name"))}</div><div class="change-sub">{esc(actor)} · {esc(when)} · {esc(obj)}</div></div>'
            f'<div class="change-orbit">{orbit_group(launch)}</div></div>'
        )


def _tminus_label(d):
    if not d:
        return ""
    delta = d - datetime.now(timezone.utc)
    hours = max(0, int(delta.total_seconds() // 3600))
    if hours < 24:
        return f"T−{hours}H"
    return f"T−{max(1, int(round(hours / 24)))}D"


def render_upcoming_v07(upcoming, limit=3):
    if not upcoming:
        st.caption("Upcoming launch data unavailable.")
        return
    for launch in upcoming[:limit]:
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b · %H:%M") if d else "TBD"
        raw_html(
            f'<div class="next-v07"><div><div class="next-when">{esc(when)}</div><div class="next-tminus">{esc(_tminus_label(d))}</div></div>'
            f'<div><div class="next-name">{esc(launch.get("name"))}</div><div class="next-provider">{esc(launch_provider_name(launch))} · {orbit_group(launch)}</div></div></div>'
        )


def launch_image_data(launch):
    image = launch.get("image") or {}
    return image.get("image_url"), image.get("credit") or ""


def featured_launch_slideshow(recent):
    with_images = [x for x in recent if launch_image_data(x)[0]]
    if not with_images:
        raw_html('<div class="featured-fallback">Launch image feed unavailable</div>')
        return
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
    for i, launch in enumerate(chosen):
        url, credit = launch_image_data(launch)
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b") if d else ""
        slides.append(f'''<div class="slide s{i}"><img src="{esc(url)}" alt="{esc(launch.get("name"))}"><div class="shade"></div><div class="credit">{esc(credit)}</div><div class="caption"><div class="cap-title">{esc(launch.get("name"))}</div><div class="cap-meta">{esc(major_actor(launch))} · {esc(when)} · {esc(orbit_group(launch))}</div></div></div>''')
    if len(chosen) == 1:
        anim_duration = 9999
        delay_css = ".s0{opacity:1 !important;}"
        keyframes = "@keyframes fade {0%,100%{opacity:1}}"
    else:
        anim_duration = len(chosen) * 8
        delay_css = "\n".join(f".s{i}{{animation-delay:{i*8}s;}}" for i in range(len(chosen)))
        visible_pct = int(100 / len(chosen))
        keyframes = f"@keyframes fade {{0%{{opacity:0}} 4%{{opacity:1}} {max(10, visible_pct-4)}%{{opacity:1}} {visible_pct}%{{opacity:0}} 100%{{opacity:0}}}}"
    html_blob = f"""
    <html><head><style>
      body{{margin:0;background:transparent;font-family:Inter,Segoe UI,Arial,sans-serif;}}
      .frame{{height:214px;border:1px solid #2A3E47;border-radius:10px;overflow:hidden;position:relative;background:#111F26;}}
      .slide{{position:absolute;inset:0;opacity:0;animation:fade {anim_duration}s linear infinite;}}
      .slide img{{width:100%;height:100%;object-fit:cover;display:block;}}
      .shade{{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.02) 34%,rgba(4,10,14,.88) 100%);}}
      .caption{{position:absolute;left:12px;right:12px;bottom:10px;color:#F4F6F4;}}
      .cap-title{{font-size:13px;font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
      .cap-meta{{font-size:9px;color:#C0CBCE;margin-top:3px;}}
      .credit{{position:absolute;right:7px;top:6px;background:rgba(4,10,14,.58);color:#CAD2D3;padding:2px 5px;border-radius:4px;font-size:7px;}}
      {delay_css}
      {keyframes}
    </style></head><body><div class="frame">{"".join(slides)}</div></body></html>
    """
    components.html(html_blob, height=216, scrolling=False)


# ============================================================
# 08 · DASHBOARD · v0.7
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
        f'<div class="hero"><div><div class="hero-title">SPACE UPDATE</div><div class="hero-sub">GLOBAL SPACE ACTIVITY · 7 DAY PICTURE</div></div>'
        f'<div class="hero-time">{dot}{status}<strong>{now.strftime("%d %b · %H:%M")}</strong></div></div>'
    )
    render_news_ticker(news)
    if not recent_ok or not upcoming_ok:
        raw_html('<div class="source-warning">Live launch data is temporarily incomplete. The dashboard retries automatically.</div>')

    section_header("MAJOR ACTORS", "7 DAYS · 24H SHOWN AS SECONDARY")
    if recent:
        prefetch_object_catalogue(recent)
    now_utc = datetime.now(timezone.utc)
    counts7 = defaultdict(int); counts24 = defaultdict(int)
    obj7_known = defaultdict(int); obj24_known = defaultdict(int)
    obj7_unknown = defaultdict(int); obj24_unknown = defaultdict(int)
    obj7_est = defaultdict(bool); obj24_est = defaultdict(bool)
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
            if is24: obj24_unknown[actor] += 1
        else:
            obj7_known[actor] += count
            if source == "estimated": obj7_est[actor] = True
            if is24:
                obj24_known[actor] += count
                if source == "estimated": obj24_est[actor] = True
    cols = st.columns(5, gap="small")
    for col, actor in zip(cols, ["EUROPE", "USA", "CHINA", "RUSSIA", "OTHER"]):
        with col:
            o24 = _display_object_total(obj24_known[actor], obj24_unknown[actor], obj24_est[actor])
            o7 = _display_object_total(obj7_known[actor], obj7_unknown[actor], obj7_est[actor])
            render_actor_card(actor, counts24[actor], counts7[actor], o24, o7, recent_ok)

    left, middle, right = st.columns([1.14, 1.04, .82], gap="small")
    with left:
        with st.container(border=True):
            raw_html('<div class="panel-heading"><div class="panel-title">EUROPE</div><div class="panel-note">CAPABILITY · CHANGE · PLAN</div></div>')
            ytd, ytd_ok = get_ytd_launches()
            render_europe_panel(recent, upcoming, ytd if ytd_ok else None)
    with middle:
        with st.container(border=True):
            raw_html('<div class="panel-heading"><div class="panel-title">ORBITAL PICTURE</div><div class="panel-note">LAUNCH DESTINATIONS · 7D</div></div>')
            orbit_visual_v07(recent)
            raw_html('<div class="panel-heading" style="margin-top:7px"><div class="panel-title">WHAT CHANGED?</div><div class="panel-note">TOP 3</div></div>')
            render_changes_v07(recent, limit=3)
    with right:
        with st.container(border=True):
            raw_html('<div class="panel-heading"><div class="panel-title">NEXT LAUNCHES</div><div class="panel-note">30 DAYS</div></div>')
            render_upcoming_v07(upcoming, limit=3)
            raw_html('<div class="featured-label">FEATURED LAUNCH</div>')
            featured_launch_slideshow(recent)
    raw_html('<div class="footerline"><span>AUTO · LAUNCH LIBRARY 2 · CELESTRAK SATCAT · ESA / EUSPA / JPL RSS</span><span>v0.7 Nordic · 16:9 · 24–40\" · refresh 15 min</span></div>')


render_dashboard()
