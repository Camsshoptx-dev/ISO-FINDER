import datetime as dt
import os
import threading
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from iso_reader import build_doc, read_iso
import store as S

st.set_page_config(page_title="Iso Finder", page_icon="🔧", layout="wide", initial_sidebar_state="collapsed")
st.markdown(
    """
    <style>
      header, footer, #MainMenu {visibility: hidden; height: 0;}
      .block-container {padding: 0.5rem 1rem 0 1rem !important; max-width: 100% !important;}
      iframe[title="streamlit.components.v1.html"] {height: 100dvh !important; border: 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

# Home page: shown until the visitor presses "Open the app" (adds ?app=1 to the address).
if not st.query_params.get("app"):
    if st.button("Open the app", type="primary", use_container_width=True):
        st.query_params["app"] = "1"
        st.rerun()
    components.html((Path(__file__).parent / "landing.html").read_text(encoding="utf-8"), height=900, scrolling=True)
    st.stop()


def secret(name, default=None):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


MAX_PER_DAY = int(secret("MAX_READS_PER_DAY", 100))  # whole app, protects your API bill
FREE_PER_MONTH = int(secret("FREE_READS_PER_MONTH", 10))  # per signed-in user (can be overridden per user)
COOLDOWN_S = int(secret("COOLDOWN_SECONDS", 30))
MODEL_QUICK = secret("MODEL_QUICK", "claude-sonnet-5-5")
MODEL_DEEP = secret("MODEL_DEEP", "claude-opus-5-5")
ACCESS_CODE = secret("ACCESS_CODE")  # only used when Google sign-in is not set up
TESTER_CODES = {c.strip() for c in str(secret("ACCESS_CODES", "") or "").split(",") if c.strip()}
if ACCESS_CODE:
    TESTER_CODES.add(str(ACCESS_CODE).strip())
OWNER_CODE = str(secret("OWNER_CODE", "") or "").strip()  # your own code: big limit and the Admin page
ADMINS = {e.strip().lower() for e in str(secret("ADMIN_EMAILS", "")).split(",") if e.strip()}
AUTH_ON = bool(secret("auth")) if hasattr(st, "login") else False


@st.cache_resource
def store():
    url, key = secret("SUPABASE_URL"), secret("SUPABASE_KEY")
    if url and key:
        return S.SupabaseStore(url, key)
    return S.MemoryStore()  # not saved between restarts: fine for testing, not for the public


@st.cache_resource
def _client():
    import anthropic

    return anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))


def ask(prompt, jpegs, tier):
    import base64

    msg = _client().messages.create(
        model=MODEL_DEEP if tier == "deep" else MODEL_QUICK,
        max_tokens=8000,
        messages=[
            {
                "role": "user",
                "content": [
                    *[{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(j).decode()}} for j in jpegs],
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


st.session_state.setdefault("result", None)

# ---- who is this? ----
email = None
IS_OWNER = False
if AUTH_ON:
    if not st.user.is_logged_in:
        st.markdown("### Sign in to use Iso Finder")
        st.caption("We only use your email to count your reads and keep the app fair for everyone. Your photos are not saved.")
        st.button("Sign in with Google", type="primary", on_click=st.login)
        st.stop()
    if st.user.get("email_verified") is False:
        st.error("Your Google email is not verified.")
        st.stop()
    email = str(st.user.email).strip().lower()
else:
    st.session_state.setdefault("code", None)
    if st.session_state.code is None:
        st.markdown("### Enter your access code")
        st.caption("Testers get a code from the app owner. Your photos are not saved.")
        code = st.text_input("Access code", type="password")
        if code:
            c = code.strip()
            if c == OWNER_CODE or c in TESTER_CODES:
                st.session_state.code = c
                st.rerun()
            else:
                st.error("That code is not right.")
        st.stop()
    email = "code-" + st.session_state.code.lower()
    if OWNER_CODE and st.session_state.code == OWNER_CODE:
        IS_OWNER = True

user = store().get_or_create_user(email)
if user.get("blocked"):
    st.error("This account has been turned off. Contact the app owner.")
    st.stop()

if IS_OWNER and user.get("monthly_limit") is None:
    store().set_user(email, monthly_limit=10000)
    user["monthly_limit"] = 10000
limit = user.get("monthly_limit")
limit = FREE_PER_MONTH if limit is None else int(limit)
used = store().count_reads(S.month_start(), email)

bar = st.columns([5, 2, 1])
bar[0].caption(("Signed in as " + email + " · " if AUTH_ON else ("Owner · " if IS_OWNER else "")) + "%d of %d reads left this month" % (max(0, limit - used), limit))
IS_ADMIN = IS_OWNER or email in ADMINS
if IS_ADMIN:
    bar[1].markdown("[Admin page](?app=1&admin=1)")
if AUTH_ON:
    bar[2].button("Sign out", on_click=st.logout)

# ---- admin page ----
if IS_ADMIN and st.query_params.get("admin"):
    st.subheader("Admin")
    users = store().list_users()
    st.metric("Reads today (all users)", store().count_reads(S.day_start()))
    st.metric("Reads this month (all users)", store().count_reads(S.month_start()))
    rows = [{"email": u["email"], "plan": u.get("plan"), "limit": u.get("monthly_limit") or FREE_PER_MONTH, "blocked": u.get("blocked"), "reads this month": store().count_reads(S.month_start(), u["email"])} for u in users]
    st.dataframe(rows, use_container_width=True)
    who = st.selectbox("Pick a user", [u["email"] for u in users] or [""])
    c1, c2, c3 = st.columns(3)
    new_limit = c1.number_input("Monthly reads", 0, 10000, FREE_PER_MONTH)
    if c1.button("Set limit") and who:
        store().set_user(who, monthly_limit=int(new_limit)); st.rerun()
    if c2.button("Block") and who:
        store().set_user(who, blocked=True); st.rerun()
    if c3.button("Unblock") and who:
        store().set_user(who, blocked=False); st.rerun()
    st.markdown("[Back to the app](?app=1)")
    st.stop()

with st.expander("1. Add your iso photo" if st.session_state.result is None else "Photo added. Open to read a different iso", expanded=st.session_state.result is None):
    st.caption("Lay the iso flat, shoot straight down, and fill the frame with the pipe drawing. Bright, even light helps Claude read the small numbers.")
    up = st.file_uploader("Photo of the iso", type=["jpg", "jpeg", "png", "webp", "heic", "heif"], label_visibility="collapsed")
    up2 = st.file_uploader(
        "Optional: photo of the material list (bill of material)",
        type=["jpg", "jpeg", "png", "webp", "heic", "heif"],
        accept_multiple_files=True,
        help="Use this when the material list is on another part of the sheet or did not fit in the first photo. It is how the circled numbers get their names.",
    )
    can = True
    if up and can and st.button("Read the iso for me", type="primary", use_container_width=True):
        ok, msg, _ = S.check_allowed(store(), email, default_monthly=FREE_PER_MONTH, app_daily=MAX_PER_DAY, cooldown_s=COOLDOWN_S)
        if up.size > 15 * 1024 * 1024:
            st.error("That photo is over 15 MB. Take it again at a lower resolution.")
        elif not ok:
            st.error(msg)
        else:
            rid = store().add_read(email)
            try:
                with st.spinner("Claude is reading the iso and zooming in on the small print. This takes about a minute."):
                    st.session_state.result = read_iso(up.getvalue(), ask, [f.getvalue() for f in (up2 or [])][:3])
                store().finish_read(rid, "ok")
                st.rerun()
            except Exception as e:  # show a short reason, never the key
                store().finish_read(rid, "failed")
                st.error("Claude could not read that photo (" + type(e).__name__ + "). That read was not counted. Try again, or trace by hand.")

_res = st.session_state.result or {}
_marks = [x for x in ((_res.get("list") or {}).get("items") or []) if isinstance(x, dict) and x.get("kind") == "mark"]
if _marks and sum(1 for x in _marks if not x.get("desc") and not x.get("what")) > len(_marks) / 2:
    st.warning("The numbered circles have no names because the material list was not readable in your photo. Open the photo box above, add a close-up photo of the material list (the table on the sheet), and read again. You can also look the numbers up on the iso itself.")

page = (Path(__file__).parent / "iso_finder.html").read_text(encoding="utf-8")
components.html(build_doc(page, st.session_state.result), height=900, scrolling=False)
