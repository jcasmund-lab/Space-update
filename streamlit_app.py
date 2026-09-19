import streamlit as st
from datetime import datetime

st.set_page_config(
    page_title="Space Update",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>

header[data-testid="stHeader"] {
    display: none;
}

.block-container {
    max-width: 100vw;
    padding: 1.2vh 1.2vw;
}

.stApp {
    background: #06111f;
    color: white;
}

.title {
    font-size: 42px;
    font-weight: 800;
    letter-spacing: 3px;
}

.subtitle {
    color: #87a3bb;
    font-size: 16px;
}

.card {
    background: #102238;
    border: 2px solid #245478;
    border-radius: 14px;
    padding: 20px;
    min-height: 110px;
}

.number {
    font-size: 42px;
    font-weight: 800;
}

.label {
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 1px;
}

.eu {
    border-color: #35b9ff;
}

.us {
    border-color: #93baff;
}

.cn {
    border-color: #ff725e;
}

.ru {
    border-color: #d65b8a;
}

.other {
    border-color: #e6b657;
}

</style>
""", unsafe_allow_html=True)


st.markdown(
    '<div class="title">SPACE UPDATE</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">GLOBAL SPACE ACTIVITY · LAST 7 DAYS</div>',
    unsafe_allow_html=True
)

st.write("")

eu, us, cn, ru, other = st.columns(5)

with eu:
    st.markdown("""
    <div class="card eu">
        <div class="label">EUROPE</div>
        <div class="number">3</div>
        <div class="label">NEW EVENTS · 24H</div>
    </div>
    """, unsafe_allow_html=True)

with us:
    st.markdown("""
    <div class="card us">
        <div class="label">USA</div>
        <div class="number">5</div>
        <div class="label">NEW EVENTS · 24H</div>
    </div>
    """, unsafe_allow_html=True)

with cn:
    st.markdown("""
    <div class="card cn">
        <div class="label">CHINA</div>
        <div class="number">2</div>
        <div class="label">NEW EVENTS · 24H</div>
    </div>
    """, unsafe_allow_html=True)

with ru:
    st.markdown("""
    <div class="card ru">
        <div class="label">RUSSIA</div>
        <div class="number">1</div>
        <div class="label">NEW EVENTS · 24H</div>
    </div>
    """, unsafe_allow_html=True)

with other:
    st.markdown("""
    <div class="card other">
        <div class="label">OTHER</div>
        <div class="number">2</div>
        <div class="label">NEW EVENTS · 24H</div>
    </div>
    """, unsafe_allow_html=True)

st.write("")

left, middle, right = st.columns([1.25, 1.15, 0.9])

with left:
    st.markdown("""
    <div class="card eu">
        <div class="label">EUROPE · CAPABILITY PICTURE</div>
        <div class="number">GALILEO</div>
        <div class="subtitle">Copernicus · IRIS² · GOVSATCOM · Launch</div>
    </div>
    """, unsafe_allow_html=True)

with middle:
    st.markdown("""
    <div class="card">
        <div class="label">ORBITAL PICTURE</div>
        <div class="number">LEO · MEO · GEO</div>
        <div class="subtitle">Activity during the last seven days</div>
    </div>
    """, unsafe_allow_html=True)

with right:
    st.markdown("""
    <div class="card other">
        <div class="label">NEXT LAUNCHES</div>
        <div class="number">🚀</div>
        <div class="subtitle">Upcoming launch activity</div>
    </div>
    """, unsafe_allow_html=True)

st.caption(
    "Dashboard rendering test · "
    + datetime.now().strftime("%d %b %Y · %H:%M")
)
