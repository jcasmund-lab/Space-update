import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import streamlit as st
import streamlit.components.v1 as components

# ============================================================
# SPACE UPDATE v0.4
# 16:9 information display for 24–40" monitors
# ============================================================

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

LL2 = "https://ll.thespacedevs.com/2.3.0"
CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php"
HEADERS = {"User-Agent": "SpaceUpdateDashboard/0.4"}
LOCAL_TZ = ZoneInfo("Europe/Copenhagen")

ACTOR_COLOURS = {
    "EUROPE": "#22b8f0",
    "USA": "#83aef5",
    "CHINA": "#ff6b57",
    "RUSSIA": "#d75a86",
    "OTHER": "#e5b653",
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

@st.cache_data(ttl=900, show_spinner=False)
def get_recent_launches(days=7):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    params = {
        "format": "json",
        "mode": "normal",
        "limit": 100,
        "ordering": "-net",
        "net__gte": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "include_suborbital": "false",
    }
    try:
        r = requests.get(f"{LL2}/launches/previous/", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False


@st.cache_data(ttl=900, show_spinner=False)
def get_upcoming_launches(days=30):
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days)
    params = {
        "format": "json",
        "mode": "normal",
        "limit": 100,
        "ordering": "net",
        "net__gte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "include_suborbital": "false",
    }
    try:
        r = requests.get(f"{LL2}/launches/upcoming/", params=params, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False


@st.cache_data(ttl=3600, show_spinner=False)
def get_ytd_launches():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    params = {
        "format": "json",
        "mode": "normal",
        "limit": 100,
        "ordering": "-net",
        "net__gte": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "include_suborbital": "false",
    }
    all_results = []
    url = f"{LL2}/launches/previous/"
    try:
        while url and len(all_results) < 500:
            r = requests.get(url, params=params if not all_results else None, headers=HEADERS, timeout=20)
            r.raise_for_status()
            payload = r.json()
            all_results.extend(payload.get("results", []))
            url = payload.get("next")
            params = None
        return all_results, True
    except Exception:
        return all_results, False


@st.cache_data(ttl=7200, show_spinner=False)
def celestrak_count(query_type, value):
    """Count current GP records. Cache for two hours."""
    try:
        r = requests.get(
            CELESTRAK_GP,
            params={query_type: value, "FORMAT": "JSON"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


@st.cache_data(ttl=21600, show_spinner=False)
def catalogued_objects_for_launch(launch_designator):
    """
    CelesTrak INTDES count for one launch designator.
    This counts catalogued orbital objects linked to that launch designator.
    """
    if not launch_designator:
        return None
    try:
        r = requests.get(
            CELESTRAK_GP,
            params={"INTDES": launch_designator, "FORMAT": "JSON"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_agency_logo(name):
    """Return a dark-background-friendly logo URL when LL2 has one."""
    try:
        r = requests.get(
            f"{LL2}/agencies/",
            params={"format": "json", "search": name, "limit": 10},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return None
        # Prefer exact name match.
        agency = next((x for x in results if (x.get("name") or "").lower() == name.lower()), results[0])
        logo = agency.get("logo") or agency.get("social_logo")
        if not logo:
            return None
        variants = logo.get("variants") or []
        for variant in variants:
            t = ((variant.get("type") or {}).get("name") or "").lower()
            if "dark background" in t and variant.get("image_url"):
                return variant["image_url"]
        return logo.get("image_url")
    except Exception:
        return None


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
    if exact is not None:
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
            "logo": get_agency_logo(actor),
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
                "logo": get_agency_logo(actor),
            }

    for launch in recent:
        actor = european_launch_actor(launch)
        if not actor:
            continue
        stats[actor]["launches_7d"] += 1
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

    # Current tracked totals from CelesTrak.
    galileo_total = celestrak_count("GROUP", "GALILEO")
    sentinel_total = celestrak_count("NAME", "SENTINEL")
    oneweb_total = celestrak_count("GROUP", "ONEWEB")

    # European launch totals are launch events, not spacecraft.
    europe_launch_7d = sum(1 for x in recent if european_launch_actor(x))
    europe_launch_ytd = sum(1 for x in ytd if european_launch_actor(x))
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
            "total": europe_launch_ytd,
            "planned": europe_plan_30d,
            "sub": "launches · total = year to date",
        },
    ]


# ============================================================
# DISPLAY CSS
# ============================================================

st.markdown(
    """
<style>
header[data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {display:none !important;}
[data-testid="stSidebar"] {display:none !important;}
.block-container {max-width:100vw !important; padding:0.8rem 1.05rem 0.6rem 1.05rem !important;}
.stApp {
    color:#f4f8fc;
    background:
        radial-gradient(circle at 48% -10%, rgba(25,65,103,.32), transparent 42%),
        linear-gradient(180deg,#07111d 0%,#050b12 100%);
}
html, body, [class*="css"] {font-family:Inter,"Segoe UI",Arial,sans-serif;}

.hero {display:flex; justify-content:space-between; align-items:flex-end; margin:0 0 .35rem 0;}
.hero-title {font-size:clamp(30px,2.15vw,43px); font-weight:900; letter-spacing:.095em; line-height:.95;}
.hero-sub {font-size:clamp(10px,.72vw,14px); color:#71879b; letter-spacing:.12em; margin-top:.25rem;}
.hero-time {text-align:right; color:#7890a4; font-size:10px; letter-spacing:.08em;}
.hero-time strong {display:block; color:#f4f8fc; font-size:17px; margin-top:2px;}
.live-dot {display:inline-block; width:7px; height:7px; border-radius:50%; background:#4dd58b; box-shadow:0 0 8px #4dd58b; margin-right:5px;}

.section-row {display:flex; justify-content:space-between; align-items:center; margin:.33rem 0 .28rem;}
.section-title {font-size:11px; font-weight:850; color:#91a8bc; letter-spacing:.14em;}
.source-note {font-size:9px; color:#4f687c; letter-spacing:.10em;}

.actor-card {border-radius:12px; padding:9px 12px 8px; min-height:95px; background:linear-gradient(145deg,rgba(16,35,53,.98),rgba(8,20,31,.98)); border:1px solid rgba(111,145,174,.22); overflow:hidden; position:relative;}
.actor-card:after {content:""; position:absolute; width:100px; height:100px; border-radius:50%; right:-45px; top:-52px; background:var(--accent); opacity:.09;}
.actor-name {font-size:11px; font-weight:850; letter-spacing:.12em; color:var(--accent);}
.actor-big {font-size:clamp(30px,2.1vw,43px); font-weight:900; line-height:.95; margin-top:7px;}
.actor-small {font-size:9px; color:#70889c; line-height:1.35; margin-top:3px;}
.actor-small strong {color:#d9e6f0; font-size:11px;}

div[data-testid="stVerticalBlockBorderWrapper"] {background:linear-gradient(155deg,rgba(14,31,47,.98),rgba(7,18,28,.98)); border:1px solid #18364f !important; border-radius:13px !important;}
.panel-title {font-size:11px; font-weight:850; color:#9db3c6; letter-spacing:.13em; margin-bottom:7px; padding-top:1px;}
.accent-blue {height:3px;border-radius:4px;background:#22b8f0;box-shadow:0 0 12px rgba(34,184,240,.35);margin:-4px 0 7px;}
.accent-green {height:3px;border-radius:4px;background:#43d4b1;box-shadow:0 0 12px rgba(67,212,177,.28);margin:-4px 0 7px;}
.accent-gold {height:3px;border-radius:4px;background:#e5b653;box-shadow:0 0 12px rgba(229,182,83,.28);margin:-4px 0 7px;}

.cap-card {border-radius:10px; padding:9px 10px; background:#132c43; border:1px solid rgba(63,143,197,.28); min-height:108px;}
.cap-title {font-size:10px; font-weight:850; color:#9fd4f6; letter-spacing:.08em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.cap-grid {display:grid; grid-template-columns:repeat(3,1fr); gap:5px; margin-top:7px;}
.cap-cell {padding:5px 2px; text-align:center; border-radius:7px; background:rgba(4,16,27,.36);}
.cap-label {font-size:7px; color:#69849a; letter-spacing:.09em; font-weight:800;}
.cap-value {font-size:clamp(17px,1.25vw,25px); line-height:1.05; font-weight:900; margin-top:2px;}
.cap-sub {font-size:8px; color:#647e94; margin-top:6px;}

.watchbar {border-left:3px solid #26b9ee; border-radius:7px; background:rgba(12,53,78,.48); padding:7px 9px; margin-top:7px;}
.watchbar b {color:#37bdf1; font-size:8px; letter-spacing:.13em;}
.watchbar span {display:block; color:#c5d7e5; font-size:10px; margin-top:2px;}

.eu-summary {display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-bottom:6px;}
.eu-summary-card {border-radius:8px; background:rgba(17,43,64,.75); border:1px solid rgba(72,150,207,.22); padding:7px 8px;}
.eu-summary-label {font-size:7px; color:#6e93b1; font-weight:850; letter-spacing:.10em;}
.eu-summary-value {font-size:22px; font-weight:900; line-height:1; margin-top:3px;}

.eu-table {width:100%;}
.eu-actor-grid {display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:5px 8px; margin-top:5px;}
.eu-row {display:grid; grid-template-columns:1.65fr .55fr .68fr .58fr; gap:4px; align-items:center; min-height:29px; padding:4px 6px; border:1px solid rgba(117,148,172,.11); border-radius:7px; background:rgba(7,21,32,.28);}
.eu-colheads {display:grid;grid-template-columns:1.65fr .55fr .68fr .58fr;gap:4px;padding:0 6px;color:#67869f;font-size:7px;font-weight:850;letter-spacing:.09em;}
.eu-namewrap {display:flex; align-items:center; gap:6px; min-width:0;}
.eu-logo {width:19px; height:19px; object-fit:contain; flex:none;}
.eu-logo-fallback {width:19px; height:19px; border-radius:5px; border:1px solid #31536e; color:#83a8c5; display:flex; align-items:center; justify-content:center; font-size:7px; font-weight:900; flex:none;}
.eu-name {font-size:9px; font-weight:720; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.eu-num {font-size:11px; font-weight:850; text-align:right;}
.eu-muted {color:#70889c;}

.change-row {display:grid; grid-template-columns:4px 1fr auto; gap:7px; padding:5px 0; border-bottom:1px solid rgba(108,139,164,.10); align-items:center;}
.change-line {width:4px; height:28px; border-radius:4px; background:var(--accent);}
.change-name {font-size:9px; font-weight:760; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.change-meta {font-size:7px; color:#698196; margin-top:1px;}
.change-orbit {font-size:8px; color:#8ba3b6; font-weight:800;}

.next-card {border-radius:9px; padding:7px 8px; margin-bottom:5px; background:rgba(38,38,37,.46); border:1px solid rgba(229,182,83,.18);}
.next-date {font-size:8px; color:#e5b653; font-weight:850;}
.next-title {font-size:9px; font-weight:760; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-top:2px;}
.next-provider {font-size:7px; color:#72889a; margin-top:1px;}

.launch-card {height:155px; border-radius:11px; overflow:hidden; border:1px solid #1c3a53; position:relative; background:#0a1723;}
.launch-img {width:100%; height:100%; object-fit:cover; display:block;}
.launch-placeholder {width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:40px; background:radial-gradient(circle,#173750,#08141f);}
.launch-overlay {position:absolute; left:0; right:0; bottom:0; padding:26px 10px 8px; background:linear-gradient(transparent,rgba(2,8,14,.96) 44%);}
.launch-name {font-size:10px; font-weight:820; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;}
.launch-meta {font-size:8px; color:#9db1c1; margin-top:2px;}
.launch-credit {position:absolute; right:5px; top:5px; max-width:70%; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; border-radius:4px; padding:2px 4px; background:rgba(0,0,0,.58); color:#d6dce1; font-size:6px;}

.footerline {display:flex; justify-content:space-between; margin-top:5px; color:#40596d; font-size:7px; letter-spacing:.06em;}

@media(max-width:1400px){
  .panel{min-height:320px;}
  .actor-card{min-height:85px;}
  .launch-card{height:138px;}
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


def render_actor_card(actor, launches24, launches7):
    colour = ACTOR_COLOURS[actor]
    st.markdown(
        f"""
        <div class="actor-card" style="--accent:{colour};border-top:3px solid {colour};">
          <div class="actor-name">{actor}</div>
          <div class="actor-big">{launches24}</div>
          <div class="actor-small">LAUNCHES · 24H<br><strong>{launches7}</strong> · LAST 7 DAYS</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


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
        cards.append(
            f"""
            <div class="eu-row" style="opacity:{opacity}">
              <div class="eu-namewrap">{logo_html}<div class="eu-name">{esc(actor)}</div></div>
              <div class="eu-num">{values['launches_7d']}</div>
              <div class="eu-num">{obj}</div>
              <div class="eu-num">{values['planned_30d']}</div>
            </div>
            """
        )

    st.markdown(
        f"""
        <div class="eu-summary">
          <div class="eu-summary-card"><div class="eu-summary-label">LAUNCHES · 7D</div><div class="eu-summary-value">{total_launches}</div></div>
          <div class="eu-summary-card"><div class="eu-summary-label">NEW OBJECTS · 7D</div><div class="eu-summary-value">{object_total_label}</div></div>
          <div class="eu-summary-card"><div class="eu-summary-label">PLANNED · 30D</div><div class="eu-summary-value">{total_planned}</div></div>
        </div>
        <div class="eu-colheads"><div>EUROPEAN ACTOR</div><div style="text-align:right">LCH</div><div style="text-align:right">OBJECTS</div><div style="text-align:right">PLAN</div></div>
        <div class="eu-actor-grid">{''.join(cards)}</div>
        """,
        unsafe_allow_html=True,
    )
    st.caption("* estimated from an explicit mission payload count when a launch designator is not yet available · ? = not yet resolved")


def orbit_visual(recent):
    counts = defaultdict(int)
    for launch in recent:
        counts[orbit_group(launch)] += 1

    # Dots are mathematically placed ON each ring.
    # Centre=(210,145), radii: LEO=48, MEO=88, GEO=128.
    svg = f"""
    <html><head><style>
      body{{margin:0;background:transparent;color:#eaf3fa;font-family:Inter,Segoe UI,Arial,sans-serif;}}
      .wrap{{height:282px;border-radius:12px;background:radial-gradient(circle at center,rgba(18,47,69,.48),rgba(5,15,24,.0) 65%);position:relative;}}
      svg{{width:100%;height:240px;display:block;}}
      .ring{{fill:none;stroke-width:1.5;}}
      .label{{font-size:10px;font-weight:800;letter-spacing:1px;}}
      .count{{font-size:15px;font-weight:900;}}
      .earth{{fill:url(#earthGrad);stroke:#4783a7;stroke-width:1.2;}}
      .minor{{font-size:8px;fill:#6f879b;}}
      .legend{{display:flex;justify-content:center;gap:22px;font-size:9px;color:#7890a4;margin-top:-7px;}}
      .dot{{filter:drop-shadow(0 0 5px currentColor);}}
    </style></head><body>
      <div class="wrap">
      <svg viewBox="0 0 420 250" role="img" aria-label="LEO MEO GEO orbital activity">
        <defs>
          <radialGradient id="earthGrad"><stop offset="0%" stop-color="#1d6b93"/><stop offset="100%" stop-color="#0b2a43"/></radialGradient>
        </defs>
        <circle cx="210" cy="125" r="128" class="ring" stroke="#d4a747" opacity=".50"/>
        <circle cx="210" cy="125" r="88" class="ring" stroke="#45d4b1" opacity=".58"/>
        <circle cx="210" cy="125" r="48" class="ring" stroke="#24b7ed" opacity=".70"/>
        <circle cx="210" cy="125" r="27" class="earth"/>
        <text x="210" y="129" text-anchor="middle" fill="#e7f4fc" font-size="9" font-weight="850">EARTH</text>

        <!-- LEO marker, angle 140°, radius 48 -->
        <circle cx="173.2" cy="155.9" r="5" fill="#24b7ed" class="dot" style="color:#24b7ed"/>
        <text x="154" y="170" text-anchor="middle" fill="#61cef4" class="label">LEO</text>
        <text x="154" y="184" text-anchor="middle" fill="#ffffff" class="count">{counts['LEO']}</text>

        <!-- MEO marker, angle -42°, radius 88 -->
        <circle cx="275.4" cy="66.1" r="5" fill="#45d4b1" class="dot" style="color:#45d4b1"/>
        <text x="294" y="57" text-anchor="middle" fill="#6ee0c4" class="label">MEO</text>
        <text x="294" y="71" text-anchor="middle" fill="#ffffff" class="count">{counts['MEO']}</text>

        <!-- GEO marker, angle 18°, radius 128 -->
        <circle cx="331.7" cy="164.6" r="5" fill="#d4a747" class="dot" style="color:#d4a747"/>
        <text x="353" y="165" text-anchor="middle" fill="#e5bd68" class="label">GEO</text>
        <text x="353" y="179" text-anchor="middle" fill="#ffffff" class="count">{counts['GEO']}</text>

        <text x="210" y="235" text-anchor="middle" class="minor">launch destination / mission orbit · last 7 days</text>
      </svg>
      <div class="legend"><span>● LEO</span><span>● MEO</span><span>● GEO</span></div>
      </div>
    </body></html>
    """
    components.html(svg, height=292, scrolling=False)


def render_changes(recent, limit=6):
    if not recent:
        st.caption("No recent launch data available.")
        return
    for launch in recent[:limit]:
        actor = major_actor(launch)
        colour = ACTOR_COLOURS[actor]
        d = parse_dt(launch.get("net"))
        when = d.astimezone(LOCAL_TZ).strftime("%d %b · %H:%M") if d else ""
        st.markdown(
            f"""
            <div class="change-row" style="--accent:{colour}">
              <div class="change-line"></div>
              <div><div class="change-name">{esc(launch.get('name'))}</div><div class="change-meta">{actor} · {esc(launch_provider_name(launch))} · {when}</div></div>
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
    ytd, ytd_ok = get_ytd_launches()
    now = datetime.now(LOCAL_TZ)

    status = "LIVE" if recent_ok and upcoming_ok and ytd_ok else "PARTIAL DATA"
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

    # -------------------- Major actors --------------------
    section_header("NEW EVENTS BY MAJOR ACTOR", "AUTO · ORBITAL LAUNCH ACTIVITY · 24H / 7D")
    now_utc = datetime.now(timezone.utc)
    counts7 = defaultdict(int)
    counts24 = defaultdict(int)
    for launch in recent:
        actor = major_actor(launch)
        counts7[actor] += 1
        d = parse_dt(launch.get("net"))
        if d and d >= now_utc - timedelta(hours=24):
            counts24[actor] += 1

    cols = st.columns(5, gap="small")
    for col, actor in zip(cols, ["EUROPE", "USA", "CHINA", "RUSSIA", "OTHER"]):
        with col:
            render_actor_card(actor, counts24[actor], counts7[actor])

    # -------------------- Main area --------------------
    left, middle, right = st.columns([1.12, 1.05, .83], gap="small")

    with left:
        with st.container(border=True):
            st.markdown('<div class="accent-blue"></div><div class="panel-title">EUROPE · CAPABILITY PICTURE</div>', unsafe_allow_html=True)
            caps = europe_capability_stats(recent, upcoming, ytd)
            c1, c2 = st.columns(2, gap="small")
            with c1:
                render_capability_card(caps[0])
                render_capability_card(caps[2])
            with c2:
                render_capability_card(caps[1])
                render_capability_card(caps[3])
            st.markdown('<div class="watchbar"><b>EUROPE · BUILDING / WATCH</b><span>IRIS² · GOVSATCOM · Ariane 6 · Vega-C · Spectrum · RFA One · Orbex Prime · Miura 5</span></div>', unsafe_allow_html=True)

    with middle:
        with st.container(border=True):
            st.markdown('<div class="accent-green"></div><div class="panel-title">ORBITAL ACTIVITY · LAST 7 DAYS</div>', unsafe_allow_html=True)
            orbit_visual(recent)
            st.markdown('<div class="panel-title" style="margin-top:-3px">WHAT CHANGED?</div>', unsafe_allow_html=True)
            render_changes(recent, limit=5)

    with right:
        with st.container(border=True):
            st.markdown('<div class="accent-gold"></div><div class="panel-title">NEXT LAUNCHES · 30 DAYS</div>', unsafe_allow_html=True)
            render_upcoming(upcoming, limit=6)

    # -------------------- European actors --------------------
    section_header("EUROPE EVENTS · ALL LAUNCH ACTORS", "LCH = LAUNCHES · OBJECTS = NEW CATALOGUED/ESTIMATED ORBITAL OBJECTS · PLAN = NEXT 30D")
    with st.container(border=True):
        st.markdown('<div class="accent-blue"></div>', unsafe_allow_html=True)
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
        '<div class="footerline"><span>AUTO SOURCES · LAUNCH LIBRARY 2 · CELESTRAK</span><span>launch refresh 15 min · GP/object cache 2–6 h</span></div>',
        unsafe_allow_html=True,
    )


render_dashboard()
