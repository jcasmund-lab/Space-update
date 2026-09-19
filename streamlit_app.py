import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ---------- STYLE ----------
st.markdown("""
<style>
    .stApp {
        background: #07111f;
        color: #ffffff;
    }

    header[data-testid="stHeader"] {
        background: rgba(0,0,0,0);
    }

    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 1rem;
        max-width: 1800px;
    }

    .title {
        font-size: 42px;
        font-weight: 800;
        letter-spacing: 2px;
        margin-bottom: 0;
    }

    .subtitle {
        color: #8fa6bf;
        font-size: 16px;
        margin-top: -5px;
        margin-bottom: 20px;
    }

    .section-title {
        font-size: 18px;
        font-weight: 700;
        letter-spacing: 1.5px;
        color: #8fa6bf;
        margin-top: 12px;
        margin-bottom: 8px;
    }

    .kpi {
        background: linear-gradient(145deg, #101f31, #0b1726);
        border: 1px solid #1d3954;
        border-radius: 12px;
        padding: 18px 20px;
        min-height: 105px;
    }

    .kpi-number {
        font-size: 38px;
        line-height: 1;
        font-weight: 800;
        color: white;
    }

    .kpi-label {
        font-size: 13px;
        color: #8fa6bf;
        margin-top: 7px;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    .card {
        background: #0d1a2a;
        border: 1px solid #1b344d;
        border-radius: 12px;
        padding: 17px;
        margin-bottom: 10px;
    }

    .europe {
        border-left: 4px solid #4f8cff;
    }

    .usa {
        border-left: 4px solid #e4e9ef;
    }

    .other {
        border-left: 4px solid #e2aa4f;
    }

    .alert {
        border-left: 4px solid #ef6351;
    }

    .small {
        color: #8fa6bf;
        font-size: 13px;
    }

    .big {
        font-size: 18px;
        font-weight: 700;
    }

    .orbit {
        background: #0d1a2a;
        border-radius: 10px;
        padding: 13px 16px;
        margin-bottom: 8px;
        border: 1px solid #1b344d;
    }

    .orbit-name {
        font-weight: 800;
        font-size: 20px;
        display: inline-block;
        width: 70px;
    }

    .orbit-data {
        color: #b7c7d9;
    }

    hr {
        border-color: #17314a;
    }
</style>
""", unsafe_allow_html=True)


# ---------- HEADER ----------
left, right = st.columns([4, 1])

with left:
    st.markdown('<div class="title">SPACE UPDATE</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="subtitle">Operational picture · developments during the last 7 days</div>',
        unsafe_allow_html=True
    )

with right:
    st.markdown(
        f"""
        <div style="text-align:right; color:#8fa6bf; padding-top:12px;">
            UPDATED<br>
            <span style="color:white;font-size:20px;font-weight:700;">
                {datetime.now().strftime("%d %b · %H:%M")}
            </span>
        </div>
        """,
        unsafe_allow_html=True
    )


# ---------- TOP KPIs ----------
st.markdown('<div class="section-title">LAST 7 DAYS</div>', unsafe_allow_html=True)

a, b, c, d, e = st.columns(5)

with a:
    st.markdown("""
    <div class="kpi">
        <div class="kpi-number">12</div>
        <div class="kpi-label">Launches</div>
    </div>
    """, unsafe_allow_html=True)

with b:
    st.markdown("""
    <div class="kpi">
        <div class="kpi-number">+47</div>
        <div class="kpi-label">Satellites launched</div>
    </div>
    """, unsafe_allow_html=True)

with c:
    st.markdown("""
    <div class="kpi">
        <div class="kpi-number">4</div>
        <div class="kpi-label">European developments</div>
    </div>
    """, unsafe_allow_html=True)

with d:
    st.markdown("""
    <div class="kpi">
        <div class="kpi-number">3</div>
        <div class="kpi-label">Capability changes</div>
    </div>
    """, unsafe_allow_html=True)

with e:
    st.markdown("""
    <div class="kpi">
        <div class="kpi-number">2</div>
        <div class="kpi-label">Items to watch</div>
    </div>
    """, unsafe_allow_html=True)


# ---------- MAIN ----------
col1, col2 = st.columns([1.45, 1])

with col1:

    st.markdown('<div class="section-title">EUROPE</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card europe">
        <div class="big">🇪🇺 Galileo · constellation strengthened</div>
        <div class="small">Three additional satellites deployed · MEO</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card europe">
        <div class="big">🚀 New European launch activity</div>
        <div class="small">Launch infrastructure and sovereign access to space</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card europe">
        <div class="big">🛰️ Earth observation capacity</div>
        <div class="small">New activity across European EO programmes</div>
    </div>
    """, unsafe_allow_html=True)


    st.markdown('<div class="section-title">UNITED STATES</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card usa">
        <div class="big">🇺🇸 Starlink V3</div>
        <div class="small">Next-generation satellites · increased capacity</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card usa">
        <div class="big">🛡️ Military space architecture</div>
        <div class="small">Continued expansion of proliferated LEO capabilities</div>
    </div>
    """, unsafe_allow_html=True)


    st.markdown('<div class="section-title">OTHER</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card other">
        <div class="big">🇨🇳 China · on-orbit activity</div>
        <div class="small">Developments relevant to space domain awareness</div>
    </div>
    """, unsafe_allow_html=True)


with col2:

    st.markdown('<div class="section-title">ORBITAL PICTURE</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="orbit">
        <span class="orbit-name">LEO</span>
        <span class="orbit-data">↑ High activity · launches · ISR · communications</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="orbit">
        <span class="orbit-name">MEO</span>
        <span class="orbit-data">Galileo · GPS · PNT</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="orbit">
        <span class="orbit-name">GEO</span>
        <span class="orbit-data">SATCOM · missile warning · strategic ISR</span>
    </div>
    """, unsafe_allow_html=True)


    st.markdown('<div class="section-title">CAPABILITY CHANGES</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card">
        <div class="big">PNT</div>
        <div class="small">European resilience ↑</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card">
        <div class="big">SATCOM</div>
        <div class="small">LEO capacity ↑</div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="card">
        <div class="big">ISR</div>
        <div class="small">More sensors · shorter revisit</div>
    </div>
    """, unsafe_allow_html=True)


    st.markdown('<div class="section-title">WATCH</div>', unsafe_allow_html=True)

    st.markdown("""
    <div class="card alert">
        <div class="big">⚠ Space Domain Awareness</div>
        <div class="small">Track unusual manoeuvres and proximity operations</div>
    </div>
    """, unsafe_allow_html=True)
