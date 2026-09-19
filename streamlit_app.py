
import streamlit as st
import requests
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

LL2 = "https://ll.thespacedevs.com/2.3.0"
CELESTRAK = "https://celestrak.org/NORAD/elements/gp.php"
TZ = ZoneInfo("Europe/Copenhagen")
HEADERS = {"User-Agent": "SpaceUpdateDashboard/0.3 (internal situational display)"}

ACTORS = {
    "EUROPE": {"color": "#2EA8FF", "flag": "🇪🇺", "logo_search": "European Space Agency"},
    "USA": {"color": "#8CB4FF", "flag": "🇺🇸", "logo_search": "NASA"},
    "CHINA": {"color": "#FF6B57", "flag": "🇨🇳", "logo_search": "China National Space Administration"},
    "RUSSIA": {"color": "#D85A8C", "flag": "🇷🇺", "logo_search": "Roscosmos"},
    "OTHER": {"color": "#E1B15A", "flag": "🌍", "logo_search": None},
}

EUROPE_COUNTRIES = {
    "AT","BE","BG","HR","CY","CZ","DK","EE","FI","FR","DE","GR","HU","IE","IT",
    "LV","LT","LU","MT","NL","PL","PT","RO","SK","SI","ES","SE","NO","GB","CH","IS"
}

EUROPE_PROVIDERS = (
    "arianespace", "isar aerospace", "rocket factory augsburg", "orbex",
    "avio", "pld space", "maiaspace", "european space agency"
)
USA_PROVIDERS = (
    "spacex", "united launch alliance", "blue origin", "firefly aerospace",
    "northrop grumman", "rocket lab", "astra", "abl space systems",
    "national aeronautics and space administration"
)
CHINA_PROVIDERS = (
    "china aerospace science and technology", "china academy of launch vehicle",
    "expace", "galactic energy", "landspace", "cas space", "space pioneer",
    "ispace", "i-space", "orienspace"
)
RUSSIA_PROVIDERS = (
    "roscosmos", "russian space forces", "khrunichev", "progress rocket"
)

@st.cache_data(ttl=900, show_spinner=False)
def get_recent_launches():
    now = datetime.now(timezone.utc)
    params = {
        "format": "json",
        "limit": 100,
        "ordering": "-net",
        "net__gte": (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "net__lte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        r = requests.get(f"{LL2}/launches/previous/", params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False

@st.cache_data(ttl=900, show_spinner=False)
def get_upcoming_launches():
    now = datetime.now(timezone.utc)
    params = {
        "format": "json",
        "limit": 40,
        "ordering": "net",
        "net__gte": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        r = requests.get(f"{LL2}/launches/upcoming/", params=params, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json().get("results", []), True
    except Exception:
        return [], False

@st.cache_data(ttl=86400, show_spinner=False)
def agency_logo(search):
    if not search:
        return None
    try:
        r = requests.get(
            f"{LL2}/agencies/",
            params={"format": "json", "search": search, "limit": 8},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        exact = [x for x in results if x.get("name", "").lower() == search.lower()]
        choices = exact or results
        for a in choices:
            logo = a.get("logo") or a.get("social_logo")
            if logo and logo.get("image_url"):
                return logo["image_url"]
    except Exception:
        pass
    return None

@st.cache_data(ttl=86400, show_spinner=False)
def agency_by_id(agency_id):
    if not agency_id:
        return {}
    try:
        r = requests.get(f"{LL2}/agencies/{agency_id}/", params={"format": "json"},
                         headers=HEADERS, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}

@st.cache_data(ttl=7200, show_spinner=False)
def celestrak_count(query_key, value):
    try:
        r = requests.get(
            CELESTRAK,
            params={query_key: value, "FORMAT": "json"},
            headers=HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None

def esc(value):
    return html.escape(str(value or ""), quote=True)

def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

def provider_name(launch):
    return (launch.get("launch_service_provider") or {}).get("name", "Unknown")

def actor_for_launch(launch):
    provider = provider_name(launch).lower()
    country = ((launch.get("pad") or {}).get("country") or {}).get("alpha_2_code", "").upper()

    if any(x in provider for x in EUROPE_PROVIDERS):
        return "EUROPE"
    if any(x in provider for x in USA_PROVIDERS):
        return "USA"
    if any(x in provider for x in CHINA_PROVIDERS):
        return "CHINA"
    if any(x in provider for x in RUSSIA_PROVIDERS):
        return "RUSSIA"
    if country in EUROPE_COUNTRIES:
        return "EUROPE"
    if country == "US":
        return "USA"
    if country == "CN":
        return "CHINA"
    if country == "RU":
        return "RUSSIA"
    return "OTHER"

def orbit_abbrev(launch):
    orbit = ((launch.get("mission") or {}).get("orbit") or {})
    return orbit.get("abbrev") or orbit.get("name") or "—"

def orbit_bucket(launch):
    v = orbit_abbrev(launch).upper()
    if any(x in v for x in ("LEO", "SSO", "POLAR", "VLEO")):
        return "LEO"
    if "MEO" in v:
        return "MEO"
    if any(x in v for x in ("GEO", "GTO")):
        return "GEO"
    return "OTHER"

def fmt_count(n):
    return "—" if n is None else f"{n:,}"

def fmt_local(dt):
    return "TBD" if not dt else dt.astimezone(TZ).strftime("%d %b · %H:%M")

def launch_image(launch):
    image = launch.get("image") or {}
    url = image.get("thumbnail_url") or image.get("image_url")
    if not url:
        return None, None
    credit = image.get("credit") or ""
    return url, credit

def provider_logo(launch):
    agency_id = (launch.get("launch_service_provider") or {}).get("id")
    a = agency_by_id(agency_id)
    logo = a.get("logo") or a.get("social_logo")
    return logo.get("image_url") if logo else None

st.markdown("""
<style>
header[data-testid="stHeader"], footer, #MainMenu, [data-testid="stToolbar"] {display:none !important;}
[data-testid="stSidebar"] {display:none !important;}
.block-container {max-width:100vw !important; padding:1.05vh 1.15vw .7vh 1.15vw !important;}
.stApp {
  background:
    radial-gradient(circle at 22% -10%, rgba(29,84,124,.30), transparent 35%),
    radial-gradient(circle at 84% 0%, rgba(28,95,100,.18), transparent 28%),
    linear-gradient(180deg,#07121F 0%,#040A12 100%);
  color:#F5F8FC;
}
html, body, [class*="css"] {font-family:Inter,"Segoe UI",Arial,sans-serif;}
div[data-testid="stVerticalBlock"] {gap:.55rem;}
div[data-testid="stHorizontalBlock"] {gap:.65rem;}

.topline {display:flex;align-items:flex-end;justify-content:space-between;margin-bottom:.2rem;}
.brand {font-weight:900;letter-spacing:.115em;font-size:clamp(28px,2.2vw,44px);line-height:.95;}
.brand span {color:#35B7FF;}
.kicker {color:#6E899F;letter-spacing:.11em;font-size:clamp(10px,.68vw,13px);margin-top:6px;}
.status {text-align:right;color:#648198;font-size:10px;letter-spacing:.08em;line-height:1.45;}
.status b {font-size:clamp(14px,.95vw,19px);color:#EEF6FC;}
.live {display:inline-block;width:7px;height:7px;background:#4DDB8C;border-radius:50%;margin-right:6px;box-shadow:0 0 8px #4DDB8C;}

.sectionbar {display:flex;justify-content:space-between;align-items:center;margin:.15rem 0 .35rem;}
.sectitle {font-size:clamp(10px,.72vw,14px);font-weight:850;letter-spacing:.14em;color:#9AB0C3;}
.source {font-size:9px;letter-spacing:.10em;color:#48657C;}

.actor {
  position:relative;overflow:hidden;border-radius:12px;padding:10px 12px 9px;
  background:linear-gradient(145deg,rgba(18,39,58,.97),rgba(9,22,34,.97));
  border:1px solid rgba(120,155,183,.20);min-height:96px;
}
.actor:after {content:"";position:absolute;width:100px;height:100px;border-radius:50%;
 right:-55px;top:-60px;background:var(--c);opacity:.11;}
.actor-top {display:flex;align-items:center;justify-content:space-between;gap:8px;}
.actor-id {display:flex;align-items:center;gap:8px;}
.actor-flag {font-size:20px;line-height:1;}
.actor-logo {width:34px;height:25px;object-fit:contain;filter:drop-shadow(0 0 5px rgba(255,255,255,.08));}
.actor-name {color:var(--c);font-weight:850;font-size:11px;letter-spacing:.12em;}
.actor-num {font-size:clamp(29px,2.1vw,43px);font-weight:900;line-height:.95;margin-top:7px;}
.actor-bottom {display:flex;justify-content:space-between;color:#6F899E;font-size:9px;letter-spacing:.05em;margin-top:3px;}
.actor-week {color:#D5E1EB;font-weight:750;}

.panel {
  height:330px;box-sizing:border-box;border-radius:13px;padding:11px 12px;
  background:linear-gradient(150deg,rgba(14,32,49,.98),rgba(8,20,32,.98));
  border:1px solid rgba(81,119,148,.27);overflow:hidden;
}
.panel.eu {border-color:rgba(46,168,255,.38);box-shadow:inset 0 3px 0 rgba(46,168,255,.72);}
.panel.orbits {border-color:rgba(72,213,183,.34);box-shadow:inset 0 3px 0 rgba(72,213,183,.66);}
.panel.changes {border-color:rgba(225,177,90,.31);box-shadow:inset 0 3px 0 rgba(225,177,90,.65);}
.panel-title {display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;}
.panel-title b {font-size:11px;letter-spacing:.13em;color:#ABC0D2;}
.panel-title span {font-size:9px;color:#4D687E;letter-spacing:.08em;}

.eu-head {display:flex;align-items:center;gap:9px;margin-bottom:8px;}
.eu-head img {height:25px;width:48px;object-fit:contain;}
.eu-head .label {font-size:10px;color:#6C8DA6;letter-spacing:.1em;}

.metric-grid {display:grid;grid-template-columns:repeat(3,1fr);gap:6px;}
.metric {
  background:rgba(24,51,73,.66);border:1px solid rgba(74,143,191,.20);
  border-radius:9px;padding:7px 8px;
}
.metric .m-label {font-size:9px;color:#7FA2BC;letter-spacing:.06em;}
.metric .m-num {font-size:clamp(20px,1.45vw,29px);font-weight:900;line-height:1.05;margin-top:2px;}
.metric .m-sub {font-size:8px;color:#55738A;margin-top:1px;}

.watchlabel {font-size:9px;color:#5F8098;letter-spacing:.12em;font-weight:800;margin:8px 0 5px;}
.chips {display:flex;flex-wrap:wrap;gap:4px;}
.chip {border:1px solid rgba(46,168,255,.25);background:rgba(27,67,94,.40);color:#ADD7F2;
 border-radius:999px;padding:3px 7px;font-size:8px;}

.const {margin-top:7px;border-top:1px solid rgba(97,132,159,.13);padding-top:4px;}
.const-row {display:grid;grid-template-columns:1.35fr .55fr .42fr;align-items:center;padding:3px 1px;
 border-bottom:1px solid rgba(97,132,159,.10);font-size:9px;}
.const-row .name {color:#B7C8D7;font-weight:650;}
.const-row .num {text-align:right;font-weight:850;color:#F0F6FB;font-size:11px;}
.const-row .orb {text-align:right;color:#56758C;}

.orbit-map {height:190px;position:relative;margin:-2px auto 2px;max-width:330px;}
.earth {
  position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:64px;height:64px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:38px;
  background:radial-gradient(circle at 35% 30%,#2D8FD0,#145079 55%,#092D47 100%);
  box-shadow:0 0 30px rgba(47,169,223,.24);
}
.ring {position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);border-radius:50%;border:1px solid;}
.ring.leo {width:112px;height:112px;border-color:rgba(53,183,255,.65);}
.ring.meo {width:170px;height:170px;border-color:rgba(72,213,183,.50);}
.ring.geo {width:228px;height:228px;border-color:rgba(225,177,90,.42);}
.orbit-dot {position:absolute;width:9px;height:9px;border-radius:50%;box-shadow:0 0 8px currentColor;}
.d1 {left:79%;top:45%;color:#35B7FF;background:#35B7FF;}
.d2 {left:27%;top:22%;color:#48D5B7;background:#48D5B7;}
.d3 {left:12%;top:57%;color:#E1B15A;background:#E1B15A;}
.orbit-tag {
  position:absolute;background:rgba(4,13,21,.86);border:1px solid rgba(112,145,169,.18);
  border-radius:6px;padding:3px 6px;font-size:8px;color:#8099AE;
}
.orbit-tag b {font-size:12px;color:#F3F7FB;margin-left:4px;}
.tag-leo {right:1%;top:40%;}
.tag-meo {left:3%;top:14%;}
.tag-geo {left:1%;bottom:2%;}
.orbit-summary {display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:3px;}
.os {background:rgba(21,44,61,.50);border-radius:8px;padding:6px;text-align:center;}
.os b {display:block;font-size:16px;}
.os span {font-size:8px;color:#69869A;letter-spacing:.08em;}

.change-row {display:grid;grid-template-columns:5px 1fr auto;gap:7px;padding:6px 0;
 border-bottom:1px solid rgba(101,132,155,.12);}
.change-bar {width:4px;border-radius:4px;background:var(--c);}
.change-name {font-size:10px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.change-meta {font-size:8px;color:#637E93;margin-top:1px;}
.change-orbit {font-size:9px;color:#91A8BA;white-space:nowrap;padding-top:2px;}
.next-label {margin-top:8px;color:#6E8A9F;font-size:8px;font-weight:850;letter-spacing:.12em;}
.next-row {display:grid;grid-template-columns:56px 1fr auto;gap:6px;align-items:center;padding:4px 0;
 border-bottom:1px solid rgba(101,132,155,.10);}
.next-time {color:#E1B15A;font-size:8px;font-weight:800;}
.next-name {font-size:9px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.next-orbit {font-size:8px;color:#668399;}

.launch-card {
  height:165px;position:relative;overflow:hidden;border-radius:12px;
  border:1px solid rgba(76,116,146,.30);background:#0C1A27;
}
.launch-card img.hero {width:100%;height:100%;object-fit:cover;}
.launch-overlay {position:absolute;left:0;right:0;bottom:0;padding:29px 10px 8px;
 background:linear-gradient(transparent,rgba(2,8,14,.96) 50%);}
.launch-name {font-size:11px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.launch-meta {font-size:8px;color:#A1B4C3;margin-top:2px;}
.provider-logo {position:absolute;left:8px;top:8px;max-width:52px;max-height:25px;object-fit:contain;
 background:rgba(255,255,255,.88);border-radius:5px;padding:3px;}
.credit {position:absolute;right:6px;top:6px;background:rgba(0,0,0,.55);border-radius:4px;padding:2px 4px;
 color:rgba(255,255,255,.72);font-size:7px;max-width:65%;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.placeholder {height:100%;display:flex;align-items:center;justify-content:center;font-size:42px;
 background:radial-gradient(circle,#16374E,#091621 70%);}
.footer {display:flex;justify-content:space-between;color:#3F5D73;font-size:8px;letter-spacing:.06em;margin-top:3px;}

@media (max-width:1400px){
  .panel{height:310px;}
  .launch-card{height:150px;}
  .actor{min-height:88px;}
  .orbit-map{height:170px;transform:scale(.90);transform-origin:top center;margin-bottom:-18px;}
}
</style>
""", unsafe_allow_html=True)

def render():
    recent, recent_ok = get_recent_launches()
    upcoming, upcoming_ok = get_upcoming_launches()
    now = datetime.now(timezone.utc)
    local_now = now.astimezone(TZ)

    day_counts = {k: 0 for k in ACTORS}
    week_counts = {k: 0 for k in ACTORS}
    orbit_counts = {"LEO":0, "MEO":0, "GEO":0, "OTHER":0}

    for launch in recent:
        actor = actor_for_launch(launch)
        week_counts[actor] += 1
        dt = parse_dt(launch.get("net"))
        if dt and dt >= now - timedelta(hours=24):
            day_counts[actor] += 1
        orbit_counts[orbit_bucket(launch)] += 1

    logos = {a: agency_logo(cfg["logo_search"]) for a, cfg in ACTORS.items()}

    galileo = celestrak_count("GROUP", "GALILEO")
    sentinel = celestrak_count("NAME", "SENTINEL")
    oneweb = celestrak_count("GROUP", "ONEWEB")
    starlink = celestrak_count("GROUP", "STARLINK")
    kuiper = celestrak_count("NAME", "KUIPER")
    qianfan = celestrak_count("NAME", "QIANFAN")
    guowang = celestrak_count("NAME", "GUOWANG")

    eu_upcoming_30 = 0
    for launch in upcoming:
        dt = parse_dt(launch.get("net"))
        if dt and dt <= now + timedelta(days=30) and actor_for_launch(launch) == "EUROPE":
            eu_upcoming_30 += 1

    live_status = "LIVE" if recent_ok and upcoming_ok else "PARTIAL DATA"
    st.markdown(f"""
    <div class="topline">
      <div>
        <div class="brand">SPACE <span>UPDATE</span></div>
        <div class="kicker">GLOBAL SPACE ACTIVITY · SINGLE-SCREEN 7-DAY PICTURE</div>
      </div>
      <div class="status"><span class="live"></span>{live_status}<br><b>{local_now.strftime("%d %b · %H:%M")}</b></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="sectionbar">
      <div class="sectitle">NEW EVENTS BY MAJOR ACTOR</div>
      <div class="source">AUTO · VERIFIED LAUNCH EVENTS · 24H / 7D</div>
    </div>
    """, unsafe_allow_html=True)

    cols = st.columns(5)
    for col, actor in zip(cols, ACTORS.keys()):
        cfg = ACTORS[actor]
        logo = logos.get(actor)
        logo_html = f'<img class="actor-logo" src="{esc(logo)}">' if logo else ""
        with col:
            st.markdown(f"""
            <div class="actor" style="--c:{cfg['color']};border-top:3px solid {cfg['color']}">
              <div class="actor-top">
                <div class="actor-id"><span class="actor-flag">{cfg['flag']}</span>{logo_html}</div>
                <div class="actor-name">{actor}</div>
              </div>
              <div class="actor-num">{day_counts[actor]}</div>
              <div class="actor-bottom"><span>LAST 24H</span><span class="actor-week">{week_counts[actor]} / 7 DAYS</span></div>
            </div>
            """, unsafe_allow_html=True)

    left, middle, right = st.columns([1.38, 1.02, 1.10])

    with left:
        esa_logo = logos.get("EUROPE")
        esa_html = f'<img src="{esc(esa_logo)}">' if esa_logo else ""
        st.markdown(f"""
        <div class="panel eu">
          <div class="panel-title"><b>EUROPE · CAPABILITY PICTURE</b><span>AUTO + WATCHLIST</span></div>
          <div class="eu-head">{esa_html}<div class="label">EUROPEAN SPACE CAPABILITY AT A GLANCE</div></div>
          <div class="metric-grid">
            <div class="metric"><div class="m-label">GALILEO</div><div class="m-num">{fmt_count(galileo)}</div><div class="m-sub">tracked · MEO</div></div>
            <div class="metric"><div class="m-label">SENTINEL</div><div class="m-num">{fmt_count(sentinel)}</div><div class="m-sub">tracked · LEO</div></div>
            <div class="metric"><div class="m-label">ONEWEB</div><div class="m-num">{fmt_count(oneweb)}</div><div class="m-sub">tracked · LEO</div></div>
            <div class="metric"><div class="m-label">EU LAUNCHES</div><div class="m-num">{week_counts['EUROPE']}</div><div class="m-sub">last 7 days</div></div>
            <div class="metric"><div class="m-label">EU NEXT 30D</div><div class="m-num">{eu_upcoming_30}</div><div class="m-sub">scheduled launches</div></div>
            <div class="metric"><div class="m-label">DATA</div><div class="m-num">AUTO</div><div class="m-sub">15m / 2h refresh</div></div>
          </div>
          <div class="watchlabel">EUROPE · PROGRAMMES TO WATCH</div>
          <div class="chips">
            <span class="chip">IRIS²</span><span class="chip">GOVSATCOM</span><span class="chip">GALILEO</span>
            <span class="chip">COPERNICUS</span><span class="chip">ARIANE 6</span><span class="chip">VEGA-C</span>
            <span class="chip">SPECTRUM</span><span class="chip">RFA ONE</span>
          </div>
          <div class="const">
            <div class="const-row"><div class="name">Starlink</div><div class="num">{fmt_count(starlink)}</div><div class="orb">LEO</div></div>
            <div class="const-row"><div class="name">OneWeb</div><div class="num">{fmt_count(oneweb)}</div><div class="orb">LEO</div></div>
            <div class="const-row"><div class="name">Amazon Leo / Kuiper</div><div class="num">{fmt_count(kuiper)}</div><div class="orb">LEO</div></div>
            <div class="const-row"><div class="name">Qianfan</div><div class="num">{fmt_count(qianfan)}</div><div class="orb">LEO</div></div>
            <div class="const-row"><div class="name">Guowang</div><div class="num">{fmt_count(guowang)}</div><div class="orb">LEO</div></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    with middle:
        st.markdown(f"""
        <div class="panel orbits">
          <div class="panel-title"><b>ORBITAL PICTURE</b><span>LAUNCH DESTINATIONS · 7D</span></div>
          <div class="orbit-map">
            <div class="ring geo"></div><div class="ring meo"></div><div class="ring leo"></div>
            <div class="earth">🌍</div>
            <div class="orbit-dot d1"></div><div class="orbit-dot d2"></div><div class="orbit-dot d3"></div>
            <div class="orbit-tag tag-leo">LEO <b>{orbit_counts['LEO']}</b></div>
            <div class="orbit-tag tag-meo">MEO <b>{orbit_counts['MEO']}</b></div>
            <div class="orbit-tag tag-geo">GEO/GTO <b>{orbit_counts['GEO']}</b></div>
          </div>
          <div class="orbit-summary">
            <div class="os"><b>{orbit_counts['LEO']}</b><span>LEO / SSO</span></div>
            <div class="os"><b>{orbit_counts['MEO']}</b><span>MEO</span></div>
            <div class="os"><b>{orbit_counts['GEO']}</b><span>GEO / GTO</span></div>
          </div>
        </div>
        """, unsafe_allow_html=True)

    with right:
        change_rows = ""
        for launch in recent[:6]:
            actor = actor_for_launch(launch)
            dt = parse_dt(launch.get("net"))
            change_rows += f"""
            <div class="change-row">
              <div class="change-bar" style="--c:{ACTORS[actor]['color']}"></div>
              <div><div class="change-name">{esc(launch.get('name'))}</div>
              <div class="change-meta">{actor} · {esc(provider_name(launch))} · {fmt_local(dt)}</div></div>
              <div class="change-orbit">{esc(orbit_abbrev(launch))}</div>
            </div>"""
        if not change_rows:
            change_rows = '<div class="change-meta">No recent launch data available.</div>'

        next_rows = ""
        for launch in upcoming[:4]:
            dt = parse_dt(launch.get("net"))
            next_rows += f"""
            <div class="next-row">
              <div class="next-time">{fmt_local(dt).split(" · ")[0]}</div>
              <div class="next-name">{esc(launch.get('name'))}</div>
              <div class="next-orbit">{esc(orbit_abbrev(launch))}</div>
            </div>"""
        if not next_rows:
            next_rows = '<div class="change-meta">Upcoming data unavailable.</div>'

        st.markdown(f"""
        <div class="panel changes">
          <div class="panel-title"><b>WHAT CHANGED?</b><span>NEWEST FIRST</span></div>
          {change_rows}
          <div class="next-label">NEXT LAUNCHES</div>
          {next_rows}
        </div>
        """, unsafe_allow_html=True)

    st.markdown("""
    <div class="sectionbar" style="margin-top:.1rem">
      <div class="sectitle">LAUNCHES · LAST 7 DAYS</div>
      <div class="source">IMAGES · PROVIDER LOGOS · CREDIT SHOWN</div>
    </div>
    """, unsafe_allow_html=True)

    # prefer variety across actors
    gallery = []
    used_ids = set()
    for actor in ("EUROPE","USA","CHINA","RUSSIA","OTHER"):
        for launch in recent:
            if actor_for_launch(launch) != actor:
                continue
            image_url, credit = launch_image(launch)
            if image_url:
                gallery.append((launch, image_url, credit))
                used_ids.add(launch.get("id"))
                break
        if len(gallery) >= 3:
            break
    for launch in recent:
        if len(gallery) >= 3:
            break
        if launch.get("id") in used_ids:
            continue
        image_url, credit = launch_image(launch)
        if image_url:
            gallery.append((launch, image_url, credit))
            used_ids.add(launch.get("id"))
    for launch in recent:
        if len(gallery) >= 3:
            break
        if launch.get("id") not in used_ids:
            gallery.append((launch, None, None))
            used_ids.add(launch.get("id"))

    gallery_cols = st.columns(3)
    for i, col in enumerate(gallery_cols):
        with col:
            if i >= len(gallery):
                st.markdown('<div class="launch-card"><div class="placeholder">🚀</div></div>', unsafe_allow_html=True)
                continue
            launch, image_url, credit = gallery[i]
            actor = actor_for_launch(launch)
            dt = parse_dt(launch.get("net"))
            p_logo = provider_logo(launch)
            visual = f'<img class="hero" src="{esc(image_url)}">' if image_url else '<div class="placeholder">🚀</div>'
            plogo = f'<img class="provider-logo" src="{esc(p_logo)}">' if p_logo else ""
            credit_html = f'<div class="credit">© {esc(credit)}</div>' if credit else ""
            st.markdown(f"""
            <div class="launch-card">
              {visual}{plogo}{credit_html}
              <div class="launch-overlay">
                <div class="launch-name">{esc(launch.get('name'))}</div>
                <div class="launch-meta">{actor} · {fmt_local(dt)} · {esc(orbit_abbrev(launch))}</div>
              </div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("""
    <div class="footer">
      <span>AUTO SOURCES · LAUNCH LIBRARY 2 · CELESTRAK</span>
      <span>launch data 15 min · orbital catalogue 2 h · no user interaction required</span>
    </div>
    """, unsafe_allow_html=True)

if hasattr(st, "fragment"):
    render = st.fragment(run_every=900)(render)

render()
