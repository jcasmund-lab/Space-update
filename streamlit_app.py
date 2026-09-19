import streamlit as st
import requests
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ============================================================
# SPACE UPDATE v0.2
# Designed for 16:9 information screens / 24–40" monitors
# ============================================================

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

LL2 = "https://ll.thespacedevs.com/2.3.0"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"

HEADERS = {
    "User-Agent": "SpaceUpdateDashboard/0.2"
}

COPENHAGEN = ZoneInfo("Europe/Copenhagen")


# ============================================================
# COLOURS
# ============================================================

ACTOR_COLOURS = {
    "EUROPE": "#36B7FF",
    "USA": "#A9C7FF",
    "CHINA": "#FF735C",
    "RUSSIA": "#D95B8B",
    "OTHER": "#E7B95E"
}

ACTOR_SHORT = {
    "EUROPE": "EU",
    "USA": "US",
    "CHINA": "CN",
    "RUSSIA": "RU",
    "OTHER": "🌍"
}


# ============================================================
# DATA
# ============================================================

@st.cache_data(ttl=900, show_spinner=False)
def get_recent_launches():
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=7)

    params = {
        "format": "json",
        "limit": 100,
        "ordering": "-net",
        "net__gte": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": now.strftime("%Y-%m-%dT%H:%M:%SZ")
    }

    try:
        r = requests.get(
            f"{LL2}/launches/previous/",
            params=params,
            headers=HEADERS,
            timeout=15
        )
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False


@st.cache_data(ttl=900, show_spinner=False)
def get_upcoming_launches():
    try:
        r = requests.get(
            f"{LL2}/launches/upcoming/",
            params={
                "format": "json",
                "limit": 6,
                "ordering": "net"
            },
            headers=HEADERS,
            timeout=15
        )
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False


@st.cache_data(ttl=7200, show_spinner=False)
def celestrak_count(query_type, value):
    """
    Counts objects with current GP data.
    Cached for 2 hours in accordance with CelesTrak update cadence.
    """

    try:
        r = requests.get(
            CELESTRAK,
            params={
                query_type: value,
                "FORMAT": "JSON"
            },
            headers=HEADERS,
            timeout=15
        )

        if r.status_code != 200:
            return None

        data = r.json()

        if isinstance(data, list):
            return len(data)

        return None

    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_agency_logo(search):
    try:
        r = requests.get(
            f"{LL2}/agencies/",
            params={
                "format": "json",
                "search": search,
                "limit": 5
            },
            headers=HEADERS,
            timeout=15
        )

        r.raise_for_status()

        results = r.json().get("results", [])

        for agency in results:
            logo = agency.get("logo") or agency.get("social_logo")

            if logo and logo.get("image_url"):
                return logo["image_url"]

    except Exception:
        pass

    return None


# ============================================================
# CLASSIFICATION
# ============================================================

EUROPE_COUNTRIES = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE",
    "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV",
    "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE", "NO", "GB", "CH", "IS", "GF"
}


def classify_actor(launch):

    provider = (
        (launch.get("launch_service_provider") or {})
        .get("name", "")
        .lower()
    )

    country = (
        ((launch.get("pad") or {}).get("country") or {})
        .get("alpha_2_code", "")
        .upper()
    )

    # Provider beats launch-site country
    europe_terms = [
        "arianespace",
        "isar aerospace",
        "rocket factory augsburg",
        "orbex",
        "avio",
        "pld space",
        "maia",
        "european space agency"
    ]

    usa_terms = [
        "spacex",
        "united launch alliance",
        "blue origin",
        "firefly",
        "northrop grumman",
        "rocket lab",
        "astra",
        "abl",
        "nasa",
        "united states space force"
    ]

    china_terms = [
        "china aerospace",
        "casc",
        "expace",
        "galactic energy",
        "landspace",
        "space pioneer",
        "cas space",
        "ispace china"
    ]

    russia_terms = [
        "roscosmos",
        "russian space",
        "khrunichev",
        "progress rocket"
    ]

    if any(x in provider for x in europe_terms):
        return "EUROPE"

    if any(x in provider for x in usa_terms):
        return "USA"

    if any(x in provider for x in china_terms):
        return "CHINA"

    if any(x in provider for x in russia_terms):
        return "RUSSIA"

    if country == "US":
        return "USA"

    if country == "CN":
        return "CHINA"

    if country == "RU":
        return "RUSSIA"

    if country in EUROPE_COUNTRIES:
        return "EUROPE"

    return "OTHER"


def parse_date(value):

    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except Exception:
        return None


def orbit_group(launch):

    mission = launch.get("mission") or {}
    orbit = mission.get("orbit") or {}

    value = (
        orbit.get("abbrev")
        or orbit.get("name")
        or ""
    ).upper()

    if any(x in value for x in ["LEO", "SSO", "VLEO", "POLAR"]):
        return "LEO"

    if "MEO" in value:
        return "MEO"

    if any(x in value for x in ["GEO", "GTO"]):
        return "GEO"

    return "OTHER"


# ============================================================
# SMALL HELPERS
# ============================================================

def fmt_number(value):

    if value is None:
        return "—"

    return f"{value:,}"


def safe(value):
    return html.escape(str(value or ""))


def logo_html(url, fallback):

    if url:
        return f"""
        <img class="actor-logo"
             src="{safe(url)}"
             alt="{safe(fallback)}">
        """

    return f"""
    <div class="logo-fallback">
        {safe(fallback)}
    </div>
    """


def launch_image(launch):

    image = launch.get("image")

    if not image:
        return None, None

    url = image.get("image_url")

    if not url:
        return None, None

    credit = image.get("credit") or ""

    license_info = image.get("license") or {}
    license_name = license_info.get("name") or ""

    credit_line = credit

    if license_name and license_name != "Unknown":
        if credit_line:
            credit_line += f" · {license_name}"
        else:
            credit_line = license_name

    return url, credit_line


# ============================================================
# STYLE
# ============================================================

st.markdown("""
<style>

/* ---------- STREAMLIT CHROME ---------- */

header[data-testid="stHeader"] {
    display: none;
}

footer {
    display: none;
}

#MainMenu {
    visibility: hidden;
}

[data-testid="stToolbar"] {
    display: none;
}

[data-testid="stSidebar"] {
    display: none;
}

.block-container {
    padding: 1.0vh 1.1vw 0.8vh 1.1vw !important;
    max-width: 100vw !important;
}

html, body, [class*="css"] {
    font-family:
        Inter,
        "Segoe UI",
        Arial,
        sans-serif;
}

.stApp {
    background:
        radial-gradient(circle at 30% 0%,
        #102740 0%,
        #071321 32%,
        #040B13 72%);
}


/* ---------- MASTER ---------- */

.dashboard {
    width: 100%;
    color: #F5F9FD;
}


/* ---------- HEADER ---------- */

.top-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 0.8vh;
}

.title-wrap {
    display: flex;
    align-items: baseline;
    gap: 18px;
}

.main-title {
    font-size: clamp(29px, 2.15vw, 44px);
    line-height: 1;
    font-weight: 850;
    letter-spacing: 0.09em;
}

.main-subtitle {
    color: #7891A8;
    font-size: clamp(12px, 0.8vw, 16px);
    letter-spacing: 0.08em;
}

.update-box {
    text-align: right;
    color: #718AA0;
    font-size: clamp(10px, 0.66vw, 13px);
    letter-spacing: .06em;
}

.update-time {
    color: #F8FBFF;
    font-size: clamp(15px, 1vw, 20px);
    font-weight: 700;
}

.live-dot {
    width: 8px;
    height: 8px;
    display: inline-block;
    background: #51D88A;
    border-radius: 50%;
    box-shadow: 0 0 10px #51D88A;
    margin-right: 6px;
}


/* ---------- SECTION HEAD ---------- */

.section-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin: .45vh 0 .55vh 0;
}

.section-title {
    color: #9CB0C3;
    font-size: clamp(11px, .78vw, 15px);
    font-weight: 800;
    letter-spacing: .13em;
}

.source-label {
    color: #4F6C84;
    font-size: clamp(9px, .58vw, 11px);
    letter-spacing: .08em;
}


/* ---------- ACTORS ---------- */

.actor-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: .55vw;
    margin-bottom: .8vh;
}

.actor-card {
    min-height: 88px;
    border-radius: 11px;
    padding: 10px 13px;
    background:
        linear-gradient(
            145deg,
            rgba(19,37,56,.95),
            rgba(9,23,36,.96)
        );
    border: 1px solid rgba(110,145,175,.22);
    position: relative;
    overflow: hidden;
}

.actor-card::after {
    content: "";
    position: absolute;
    width: 100px;
    height: 100px;
    right: -45px;
    top: -50px;
    border-radius: 50%;
    background: var(--actor);
    opacity: .09;
}

.actor-top {
    display: flex;
    justify-content: space-between;
    align-items: center;
}

.actor-logo {
    width: 34px;
    height: 34px;
    object-fit: contain;
}

.logo-fallback {
    width: 34px;
    height: 34px;
    border: 1px solid var(--actor);
    border-radius: 7px;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--actor);
    font-weight: 800;
    font-size: 12px;
}

.actor-name {
    font-size: clamp(11px, .7vw, 14px);
    font-weight: 800;
    letter-spacing: .11em;
    color: var(--actor);
}

.actor-data {
    display: flex;
    align-items: flex-end;
    gap: 11px;
    margin-top: 3px;
}

.actor-24 {
    font-size: clamp(28px, 2.25vw, 46px);
    font-weight: 850;
    line-height: .95;
}

.actor-meta {
    color: #839BAF;
    font-size: clamp(9px, .59vw, 12px);
    line-height: 1.3;
    padding-bottom: 2px;
}

.actor-week {
    color: #EDF5FC;
    font-weight: 700;
}


/* ---------- MAIN GRID ---------- */

.main-grid {
    display: grid;
    grid-template-columns: 1.2fr 1.15fr .85fr;
    gap: .6vw;
}

.panel {
    background:
        linear-gradient(
            150deg,
            rgba(15,32,49,.98),
            rgba(8,20,32,.98)
        );
    border: 1px solid #183752;
    border-radius: 13px;
    padding: 11px 13px;
    min-height: 355px;
}

.panel-europe {
    border-color: rgba(54,183,255,.45);
    box-shadow:
        inset 0 3px 0 rgba(54,183,255,.75);
}

.panel-orbit {
    border-color: rgba(68,215,185,.34);
    box-shadow:
        inset 0 3px 0 rgba(68,215,185,.6);
}

.panel-next {
    border-color: rgba(231,185,94,.35);
    box-shadow:
        inset 0 3px 0 rgba(231,185,94,.65);
}

.panel-title {
    color: #A9BDD0;
    font-size: clamp(11px, .74vw, 15px);
    font-weight: 800;
    letter-spacing: .12em;
    margin-bottom: 9px;
}


/* ---------- EUROPE ---------- */

.capability-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 7px;
}

.capability {
    background: rgba(21,45,66,.76);
    border: 1px solid rgba(63,143,197,.27);
    border-radius: 10px;
    padding: 10px 11px;
}

.cap-name {
    color: #A6CBE8;
    font-size: clamp(10px, .66vw, 13px);
    font-weight: 700;
}

.cap-number {
    font-size: clamp(25px, 1.8vw, 36px);
    font-weight: 850;
    line-height: 1.08;
    margin-top: 2px;
}

.cap-small {
    color: #738DA3;
    font-size: clamp(9px, .56vw, 11px);
}

.europe-next {
    margin-top: 8px;
    background: rgba(14,47,70,.55);
    border-left: 3px solid #36B7FF;
    border-radius: 7px;
    padding: 8px 10px;
}

.next-label {
    color: #4BAFE7;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: .13em;
}

.next-programmes {
    font-size: clamp(11px, .72vw, 14px);
    margin-top: 2px;
    color: #D4E6F3;
}


/* ---------- CONSTELLATIONS ---------- */

.constellation-title {
    margin-top: 9px;
    color: #708CA3;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: .12em;
}

.constellation-row {
    display: grid;
    grid-template-columns: 1.2fr .75fr .65fr;
    border-bottom: 1px solid rgba(102,132,156,.12);
    padding: 4px 2px;
    align-items: center;
}

.constellation-name {
    font-size: clamp(10px, .65vw, 13px);
    font-weight: 650;
}

.constellation-count {
    text-align: right;
    font-weight: 800;
    font-size: clamp(12px, .8vw, 16px);
}

.constellation-orbit {
    text-align: right;
    color: #6F879B;
    font-size: 10px;
}


/* ---------- ORBITS ---------- */

.orbit-row {
    display: grid;
    grid-template-columns: 52px 1fr 34px;
    gap: 8px;
    align-items: center;
    margin-bottom: 9px;
}

.orbit-label {
    font-weight: 800;
    color: #A8C3D7;
    font-size: 12px;
}

.orbit-track {
    height: 12px;
    border-radius: 20px;
    background: rgba(84,112,133,.15);
    overflow: hidden;
}

.orbit-bar {
    height: 100%;
    border-radius: 20px;
    background:
        linear-gradient(
            90deg,
            #32B4FF,
            #44D7B9
        );
}

.orbit-number {
    text-align: right;
    font-weight: 800;
}


/* ---------- CHANGES ---------- */

.changes-title {
    color: #708CA3;
    font-size: 10px;
    font-weight: 800;
    letter-spacing: .12em;
    margin: 11px 0 5px;
}

.change-item {
    display: grid;
    grid-template-columns: 5px 1fr auto;
    gap: 7px;
    padding: 5px 0;
    border-bottom: 1px solid rgba(110,140,165,.12);
}

.change-line {
    width: 4px;
    border-radius: 4px;
    background: var(--actor);
}

.change-name {
    font-size: clamp(10px, .64vw, 13px);
    font-weight: 650;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.change-meta {
    color: #71889C;
    font-size: 9px;
    margin-top: 1px;
}

.change-orbit {
    color: #9AB1C4;
    font-size: 10px;
    white-space: nowrap;
}


/* ---------- NEXT LAUNCHES ---------- */

.next-launch {
    background: rgba(30,40,49,.6);
    border: 1px solid rgba(217,181,108,.20);
    border-radius: 9px;
    padding: 8px 9px;
    margin-bottom: 7px;
}

.next-date {
    color: #E7B95E;
    font-size: 10px;
    font-weight: 750;
}

.next-name {
    font-size: clamp(10px, .68vw, 13px);
    font-weight: 700;
    line-height: 1.15;
    margin-top: 2px;
}

.next-provider {
    color: #798FA1;
    font-size: 9px;
    margin-top: 2px;
}


/* ---------- LAUNCH GALLERY ---------- */

.launch-gallery {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: .6vw;
}

.launch-card {
    height: clamp(150px, 18vh, 195px);
    border: 1px solid #1B3C57;
    border-radius: 11px;
    position: relative;
    overflow: hidden;
    background: #0C1B29;
}

.launch-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.launch-placeholder {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 44px;
    background:
        radial-gradient(circle,
        #18354C,
        #091724);
}

.launch-overlay {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 22px 11px 8px 11px;
    background:
        linear-gradient(
            transparent,
            rgba(2,8,14,.95) 45%
        );
}

.launch-title {
    font-weight: 750;
    font-size: clamp(10px, .67vw, 13px);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.launch-meta {
    color: #A7BAC9;
    font-size: 9px;
    margin-top: 2px;
}

.launch-credit {
    position: absolute;
    right: 6px;
    top: 5px;
    padding: 2px 5px;
    border-radius: 4px;
    background: rgba(0,0,0,.55);
    color: rgba(255,255,255,.73);
    font-size: 7px;
}


/* ---------- FOOTER ---------- */

.footer-line {
    margin-top: .55vh;
    display: flex;
    justify-content: space-between;
    color: #466177;
    font-size: 9px;
    letter-spacing: .05em;
}

@media (max-width: 1400px) {

    .panel {
        min-height: 325px;
    }

    .actor-card {
        min-height: 80px;
    }

}

</style>
""", unsafe_allow_html=True)


# ============================================================
# RENDER
# ============================================================

def render_dashboard():

    launches, launch_ok = get_recent_launches()
    upcoming, upcoming_ok = get_upcoming_launches()

    now_utc = datetime.now(timezone.utc)
    now_local = datetime.now(COPENHAGEN)

    # --------------------------------------------------------
    # Actor counts
    # --------------------------------------------------------

    week_counts = {
        x: 0 for x in ACTOR_COLOURS
    }

    day_counts = {
        x: 0 for x in ACTOR_COLOURS
    }

    for launch in launches:

        actor = classify_actor(launch)

        week_counts[actor] += 1

        d = parse_date(launch.get("net"))

        if d and d >= now_utc - timedelta(hours=24):
            day_counts[actor] += 1

    # --------------------------------------------------------
    # Orbit counts
    # --------------------------------------------------------

    orbit_counts = {
        "LEO": 0,
        "MEO": 0,
        "GEO": 0,
        "OTHER": 0
    }

    for launch in launches:
        orbit_counts[orbit_group(launch)] += 1

    max_orbit = max(
        1,
        max(orbit_counts.values())
    )

    # --------------------------------------------------------
    # CelesTrak constellation picture
    # --------------------------------------------------------

    galileo = celestrak_count("GROUP", "GALILEO")
    starlink = celestrak_count("GROUP", "STARLINK")
    oneweb = celestrak_count("GROUP", "ONEWEB")

    sentinel = celestrak_count("NAME", "SENTINEL")
    kuiper = celestrak_count("NAME", "KUIPER")
    qianfan = celestrak_count("NAME", "QIANFAN")
    guowang = celestrak_count("NAME", "GUOWANG")

    # --------------------------------------------------------
    # Logos
    # --------------------------------------------------------

    logos = {
        "EUROPE": get_agency_logo("European Space Agency"),
        "USA": get_agency_logo("NASA"),
        "CHINA": get_agency_logo("China National Space Administration"),
        "RUSSIA": get_agency_logo("ROSCOSMOS"),
        "OTHER": None
    }

    # --------------------------------------------------------
    # Actor cards
    # --------------------------------------------------------

    actor_cards = ""

    for actor in [
        "EUROPE",
        "USA",
        "CHINA",
        "RUSSIA",
        "OTHER"
    ]:

        colour = ACTOR_COLOURS[actor]

        actor_cards += f"""
        <div class="actor-card"
             style="--actor:{colour}; border-top:3px solid {colour};">

            <div class="actor-top">

                <div>
                    {logo_html(
                        logos.get(actor),
                        ACTOR_SHORT[actor]
                    )}
                </div>

                <div class="actor-name">
                    {actor}
                </div>

            </div>

            <div class="actor-data">

                <div class="actor-24">
                    {day_counts[actor]}
                </div>

                <div class="actor-meta">
                    LAST 24H<br>
                    <span class="actor-week">
                        {week_counts[actor]} / 7 DAYS
                    </span>
                </div>

            </div>

        </div>
        """

    # --------------------------------------------------------
    # Orbit bars
    # --------------------------------------------------------

    orbit_html = ""

    for orbit in ["LEO", "MEO", "GEO"]:

        number = orbit_counts[orbit]

        width = int(
            (number / max_orbit) * 100
        )

        orbit_html += f"""
        <div class="orbit-row">

            <div class="orbit-label">
                {orbit}
            </div>

            <div class="orbit-track">
                <div class="orbit-bar"
                     style="width:{width}%;">
                </div>
            </div>

            <div class="orbit-number">
                {number}
            </div>

        </div>
        """

    # --------------------------------------------------------
    # Changes feed
    # --------------------------------------------------------

    changes_html = ""

    for launch in launches[:7]:

        actor = classify_actor(launch)

        colour = ACTOR_COLOURS[actor]

        mission = launch.get("mission") or {}

        orbit = (
            (mission.get("orbit") or {}).get("abbrev")
            or orbit_group(launch)
        )

        d = parse_date(launch.get("net"))

        when = (
            d.astimezone(COPENHAGEN)
            .strftime("%d %b · %H:%M")
            if d
            else ""
        )

        changes_html += f"""
        <div class="change-item"
             style="--actor:{colour};">

            <div class="change-line">
            </div>

            <div>

                <div class="change-name">
                    {safe(launch.get("name"))}
                </div>

                <div class="change-meta">
                    {actor} · {when}
                </div>

            </div>

            <div class="change-orbit">
                {safe(orbit)}
            </div>

        </div>
        """

    if not changes_html:
        changes_html = """
        <div style="color:#748A9D;font-size:12px;">
            No launch activity received.
        </div>
        """

    # --------------------------------------------------------
    # Upcoming
    # --------------------------------------------------------

    upcoming_html = ""

    for launch in upcoming[:4]:

        d = parse_date(launch.get("net"))

        when = (
            d.astimezone(COPENHAGEN)
            .strftime("%d %b · %H:%M")
            if d
            else "TBD"
        )

        provider = (
            (launch.get("launch_service_provider") or {})
            .get("name", "")
        )

        upcoming_html += f"""
        <div class="next-launch">

            <div class="next-date">
                {when}
            </div>

            <div class="next-name">
                {safe(launch.get("name"))}
            </div>

            <div class="next-provider">
                {safe(provider)}
            </div>

        </div>
        """

    if not upcoming_html:
        upcoming_html = """
        <div style="color:#748A9D;font-size:12px;">
            Upcoming data unavailable.
        </div>
        """

    # --------------------------------------------------------
    # Launch gallery
    # --------------------------------------------------------

    launches_with_images = []

    # Try to provide geographical variety first
    for actor in [
        "EUROPE",
        "USA",
        "CHINA",
        "RUSSIA",
        "OTHER"
    ]:

        for launch in launches:

            if classify_actor(launch) != actor:
                continue

            image_url, credit = launch_image(launch)

            if image_url:
                launches_with_images.append(
                    (launch, image_url, credit)
                )
                break

    # Fill remaining places
    for launch in launches:

        if len(launches_with_images) >= 3:
            break

        if any(
            existing[0].get("id") == launch.get("id")
            for existing in launches_with_images
        ):
            continue

        image_url, credit = launch_image(launch)

        if image_url:
            launches_with_images.append(
                (launch, image_url, credit)
            )

    # If fewer than 3 images, include normal launch cards
    for launch in launches:

        if len(launches_with_images) >= 3:
            break

        if any(
            existing[0].get("id") == launch.get("id")
            for existing in launches_with_images
        ):
            continue

        launches_with_images.append(
            (launch, None, None)
        )

    gallery_html = ""

    for launch, image_url, credit in launches_with_images[:3]:

        actor = classify_actor(launch)

        d = parse_date(launch.get("net"))

        when = (
            d.astimezone(COPENHAGEN)
            .strftime("%d %b")
            if d
            else ""
        )

        if image_url:
            visual = f"""
            <img class="launch-img"
                 src="{safe(image_url)}">
            """
        else:
            visual = """
            <div class="launch-placeholder">
                🚀
            </div>
            """

        credit_html = ""

        if credit:
            credit_html = f"""
            <div class="launch-credit">
                {safe(credit)}
            </div>
            """

        gallery_html += f"""
        <div class="launch-card">

            {visual}
            {credit_html}

            <div class="launch-overlay">

                <div class="launch-title">
                    {safe(launch.get("name"))}
                </div>

                <div class="launch-meta">
                    {actor} · {when} · {orbit_group(launch)}
                </div>

            </div>

        </div>
        """

    if not gallery_html:

        gallery_html = """
        <div class="launch-card">
            <div class="launch-placeholder">🚀</div>
        </div>

        <div class="launch-card">
            <div class="launch-placeholder">🛰️</div>
        </div>

        <div class="launch-card">
            <div class="launch-placeholder">🌍</div>
        </div>
        """

    # --------------------------------------------------------
    # API status
    # --------------------------------------------------------

    status = (
        "LIVE"
        if launch_ok and upcoming_ok
        else "PARTIAL DATA"
    )

    live_dot = (
        '<span class="live-dot"></span>'
        if launch_ok
        else ""
    )

    # --------------------------------------------------------
    # FULL DASHBOARD
    # --------------------------------------------------------

    dashboard = f"""

    <div class="dashboard">

        <!-- HEADER -->

        <div class="top-header">

            <div class="title-wrap">

                <div class="main-title">
                    SPACE UPDATE
                </div>

                <div class="main-subtitle">
                    GLOBAL SPACE ACTIVITY · 7 DAY PICTURE
                </div>

            </div>

            <div class="update-box">

                {live_dot}{status}<br>

                <span class="update-time">
                    {now_local.strftime("%d %b · %H:%M")}
                </span>

            </div>

        </div>


        <!-- ACTOR EVENTS -->

        <div class="section-head">

            <div class="section-title">
                NEW EVENTS BY MAJOR ACTOR
            </div>

            <div class="source-label">
                AUTO · LAUNCH ACTIVITY · 24H / 7D
            </div>

        </div>

        <div class="actor-grid">
            {actor_cards}
        </div>


        <!-- MAIN GRID -->

        <div class="main-grid">


            <!-- EUROPE -->

            <div class="panel panel-europe">

                <div class="panel-title">
                    EUROPE · CAPABILITY PICTURE
                </div>

                <div class="capability-grid">

                    <div class="capability">
                        <div class="cap-name">
                            GALILEO
                        </div>

                        <div class="cap-number">
                            {fmt_number(galileo)}
                        </div>

                        <div class="cap-small">
                            tracked · MEO
                        </div>
                    </div>


                    <div class="capability">
                        <div class="cap-name">
                            COPERNICUS / SENTINEL
                        </div>

                        <div class="cap-number">
                            {fmt_number(sentinel)}
                        </div>

                        <div class="cap-small">
                            tracked · LEO
                        </div>
                    </div>


                    <div class="capability">
                        <div class="cap-name">
                            ONEWEB
                        </div>

                        <div class="cap-number">
                            {fmt_number(oneweb)}
                        </div>

                        <div class="cap-small">
                            tracked · LEO
                        </div>
                    </div>


                    <div class="capability">
                        <div class="cap-name">
                            EUROPEAN LAUNCHES
                        </div>

                        <div class="cap-number">
                            {week_counts["EUROPE"]}
                        </div>

                        <div class="cap-small">
                            last 7 days
                        </div>
                    </div>

                </div>


                <div class="europe-next">

                    <div class="next-label">
                        EUROPE · COMING / BUILDING
                    </div>

                    <div class="next-programmes">
                        IRIS² · GOVSATCOM · Ariane 6 · Vega-C · European launchers
                    </div>

                </div>


                <div class="constellation-title">
                    GLOBAL CONSTELLATIONS · CURRENT TRACKED OBJECTS
                </div>

                <div class="constellation-row">
                    <div class="constellation-name">Starlink</div>
                    <div class="constellation-count">{fmt_number(starlink)}</div>
                    <div class="constellation-orbit">LEO</div>
                </div>

                <div class="constellation-row">
                    <div class="constellation-name">OneWeb</div>
                    <div class="constellation-count">{fmt_number(oneweb)}</div>
                    <div class="constellation-orbit">LEO</div>
                </div>

                <div class="constellation-row">
                    <div class="constellation-name">Amazon / Kuiper</div>
                    <div class="constellation-count">{fmt_number(kuiper)}</div>
                    <div class="constellation-orbit">LEO</div>
                </div>

                <div class="constellation-row">
                    <div class="constellation-name">Qianfan</div>
                    <div class="constellation-count">{fmt_number(qianfan)}</div>
                    <div class="constellation-orbit">LEO</div>
                </div>

                <div class="constellation-row">
                    <div class="constellation-name">Guowang</div>
                    <div class="constellation-count">{fmt_number(guowang)}</div>
                    <div class="constellation-orbit">LEO</div>
                </div>

            </div>


            <!-- ORBIT / CHANGES -->

            <div class="panel panel-orbit">

                <div class="panel-title">
                    ORBITAL ACTIVITY · LAST 7 DAYS
                </div>

                {orbit_html}

                <div class="changes-title">
                    WHAT CHANGED?
                </div>

                {changes_html}

            </div>


            <!-- UPCOMING -->

            <div class="panel panel-next">

                <div class="panel-title">
                    NEXT LAUNCHES
                </div>

                {upcoming_html}

            </div>

        </div>


        <!-- GALLERY -->

        <div class="section-head"
             style="margin-top:.7vh;">

            <div class="section-title">
                LAUNCHES · LAST 7 DAYS
            </div>

            <div class="source-label">
                IMAGES INSTEAD OF VIDEO
            </div>

        </div>


        <div class="launch-gallery">
            {gallery_html}
        </div>


        <!-- FOOTER -->

        <div class="footer-line">

            <div>
                AUTO SOURCES · LAUNCH LIBRARY 2 · CELESTRAK
            </div>

            <div>
                launch refresh 15 min · orbital data max every 2 hours
            </div>

        </div>

    </div>
    """

    st.markdown(
        dashboard,
        unsafe_allow_html=True
    )


# Auto refresh every 15 minutes without user interaction.
# Works on current Streamlit versions.
if hasattr(st, "fragment"):
    render_dashboard = st.fragment(
        run_every=900
    )(render_dashboard)

render_dashboard()
