import datetime as dt
import os
import threading
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from iso_reader import build_doc, read_iso
import store as S
import billing as B

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
    def _go():
        st.query_params["app"] = "1"

    st.markdown(
        """<style>
        .st-key-go_top{position:fixed;bottom:18px;left:16px;right:16px;z-index:9999}
        .st-key-go_top button{height:64px;font-size:1.25rem;font-weight:800;letter-spacing:.02em;box-shadow:0 10px 40px rgba(255,130,64,.55),0 0 0 6px rgba(10,19,25,.6)}
        </style>""",
        unsafe_allow_html=True,
    )
    st.button("Open Iso Finder", type="primary", use_container_width=True, on_click=_go, key="go_top")
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
# USD per million tokens (input, output). Defaults are the list prices for these two models; change if you change models.
PRICES = {
    MODEL_QUICK: (float(secret("PRICE_QUICK_IN", 2)), float(secret("PRICE_QUICK_OUT", 10))),
    MODEL_DEEP: (float(secret("PRICE_DEEP_IN", 4)), float(secret("PRICE_DEEP_OUT", 20))),
}
ACCESS_CODE = secret("ACCESS_CODE")  # only used when Google sign-in is not set up
TESTER_CODES = {c.strip() for c in str(secret("ACCESS_CODES", "") or "").split(",") if c.strip()}
if ACCESS_CODE:
    TESTER_CODES.add(str(ACCESS_CODE).strip())
OWNER_CODE = str(secret("OWNER_CODE", "") or "").strip()  # your own code: big limit and the Admin page
ADMINS = {e.strip().lower() for e in str(secret("ADMIN_EMAILS", "")).split(",") if e.strip()}
AUTH_ON = bool(secret("auth")) if hasattr(st, "login") else False


APP_URL = str(secret("APP_URL", "") or "").rstrip("/")
STRIPE_KEY = secret("STRIPE_SECRET_KEY")
PRICE_IDS = {"individual": secret("STRIPE_PRICE_INDIVIDUAL"), "team10": secret("STRIPE_PRICE_TEAM10"), "team20": secret("STRIPE_PRICE_TEAM20")}


@st.cache_resource
def billing():
    if AUTH_ON and STRIPE_KEY and APP_URL:
        return B.Billing(str(STRIPE_KEY), {k: v for k, v in PRICE_IDS.items() if v})
    return None


@st.cache_data(ttl=300, show_spinner=False)
def _entitlement(email):
    return B.entitlement(store(), billing(), email, FREE_PER_MONTH)


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

    model = MODEL_DEEP if tier == "deep" else MODEL_QUICK
    msg = _client().messages.create(
        model=model,
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
    try:  # remember what this call cost, for the Admin page
        acc = st.session_state.setdefault("_acc", {"in": 0, "out": 0, "cost": 0.0})
        pin, pout = PRICES.get(model, (0, 0))
        ti, to = msg.usage.input_tokens, msg.usage.output_tokens
        acc["in"] += ti
        acc["out"] += to
        acc["cost"] += ti / 1e6 * pin + to / 1e6 * pout
    except Exception:
        pass
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
if st.query_params.get("paid"):
    st.cache_data.clear()  # just came back from Stripe: look up the new plan
    st.query_params.pop("paid", None)
ent = _entitlement(email) if (AUTH_ON and billing()) else {"plan": "free", "limit": FREE_PER_MONTH, "group": None, "role": None, "seats": 0}
if ent["plan"] != "free":
    limit, group = ent["limit"], ent["group"]
else:  # free: the admin override (or default) applies
    limit = user.get("monthly_limit")
    limit = FREE_PER_MONTH if limit is None else int(limit)
    group = None
used = store().count_reads(S.month_start(), emails=group) if group else store().count_reads(S.month_start(), email)

bar = st.columns([5, 2, 1])
bar[0].caption(("Signed in as " + email + " · " if AUTH_ON else ("Owner · " if IS_OWNER else "")) + ("%s plan · " % B.PLANS[ent["plan"]]["label"] if ent["plan"] != "free" else "") + "%d of %d reads left this month" % (max(0, limit - used), limit))
IS_ADMIN = IS_OWNER or email in ADMINS
if IS_ADMIN:
    bar[1].markdown("[Admin page](?app=1&admin=1)")
if AUTH_ON:
    bar[2].button("Sign out", on_click=st.logout)

# ---- plans and team ----
if billing() and not st.query_params.get("admin"):
    back = APP_URL + "/?app=1"
    with st.expander("Plans, team and billing"):
        if ent["plan"] == "free":
            st.write("You are on the free plan. Pick a plan for more reads:")
            for key, p in B.PLANS.items():
                if PRICE_IDS.get(key):
                    c1, c2 = st.columns([3, 2])
                    c1.markdown("**%s** · %s · %d reads a month · %d %s" % (p["label"], p["price"], p["reads"], p["seats"], "person" if p["seats"] == 1 else "people"))
                    if c2.button("Subscribe", key="sub_" + key):
                        try:
                            st.link_button("Continue to payment", billing().checkout_url(email, key, back))
                        except Exception:
                            st.error("Could not open the payment page. Try again in a minute.")
        else:
            st.write("Plan: **%s**." % B.PLANS[ent["plan"]]["label"])
            if ent["role"] == "member":
                st.caption("You are on the team of %s." % ent.get("boss", "your owner"))
            if ent["role"] == "owner":
                try:
                    url = billing().portal_url(email, back)
                    if url:
                        st.link_button("Manage or cancel subscription", url)
                except Exception:
                    st.caption("Billing page is unavailable right now.")
            if ent["role"] == "owner" and ent["seats"] > 1:
                members = store().list_members(email)
                st.markdown("**Team members** (%d of %d seats, besides you)" % (len(members), ent["seats"] - 1))
                for m in members:
                    c1, c2 = st.columns([4, 1])
                    c1.write(m)
                    if c2.button("Remove", key="rm_" + m):
                        store().remove_member(email, m); st.cache_data.clear(); st.rerun()
                if len(members) < ent["seats"] - 1:
                    new = st.text_input("Add a teammate by Google email", key="newm").strip().lower()
                    if st.button("Add teammate") and "@" in new and new != email:
                        if store().owner_of(new):
                            st.error("That person is already on a team.")
                        else:
                            store().add_member(email, new); st.cache_data.clear(); st.rerun()
        if st.button("Refresh my plan"):
            st.cache_data.clear(); st.rerun()

# ---- admin page ----
if IS_ADMIN and st.query_params.get("admin"):
    st.subheader("Admin")
    users = store().list_users()
    st.metric("Reads today (all users)", store().count_reads(S.day_start()))
    st.metric("Reads this month (all users)", store().count_reads(S.month_start()))
    cm, cd = store().sum_cost(S.month_start()), store().sum_cost(S.day_start())
    n_cost = store().count_costed(S.month_start())
    st.metric("Estimated Claude cost today", "$%.2f" % cd)
    st.metric("Estimated Claude cost this month", "$%.2f" % cm)
    st.metric("Average cost per read", ("$%.3f" % (cm / n_cost)) if n_cost else "no data yet")
    st.caption("Worked out from the tokens each read used and the list prices. Reads made before this was added are not included. Compare with the Console's Usage page.")
    rows = [{"email": u["email"], "plan": u.get("plan"), "limit": u.get("monthly_limit") or FREE_PER_MONTH, "blocked": u.get("blocked"), "reads this month": store().count_reads(S.month_start(), u["email"]), "cost this month": "$%.2f" % store().sum_cost(S.month_start(), u["email"])} for u in users]
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

st.markdown(
    """
    <style>
      .stApp{background:radial-gradient(900px 520px at 88% -8%,rgba(255,130,64,.20),transparent 60%),#0a1319;}
      .iso-hero{padding:6px 2px 4px}
      .iso-kick{display:inline-block;font-weight:800;letter-spacing:.16em;font-size:.72rem;color:#1d0d02;background:#ff8240;padding:4px 12px;border-radius:999px}
      .iso-hero h2{font-size:clamp(2rem,7vw,3rem);font-weight:800;letter-spacing:-.02em;line-height:1;margin:12px 0 8px;padding:0;color:#e9f0f4}
      .iso-hero p{color:#9fb2be;margin:0 0 12px;font-size:1.02rem}
      .iso-tips span{display:inline-block;margin:0 6px 6px 0;padding:5px 12px;border-radius:999px;border:1px solid #243843;background:#0f1920;color:#b9c9d3;font-size:.82rem}
      [data-testid="stFileUploaderDropzone"]{border:2px dashed #ff8240;border-radius:22px;background:rgba(255,130,64,.06);padding:30px 16px;transition:background .25s,box-shadow .25s}
      [data-testid="stFileUploaderDropzone"]:hover{background:rgba(255,130,64,.13);box-shadow:0 0 44px rgba(255,130,64,.28)}
      [data-testid="stFileUploaderDropzoneInstructions"] > div > span{display:none}
      [data-testid="stFileUploaderDropzoneInstructions"] > div::before{content:"Tap to take or choose a photo";font-weight:800;font-size:1.15rem;color:#e9f0f4}
      [data-testid="stFileUploaderDropzoneInstructions"] small{display:none}
      [data-testid="stFileUploaderDropzone"] button{border:2px solid #ff8240;color:#ff8240;border-radius:14px;font-weight:800;background:transparent}
      .stButton > button[kind="primary"],[data-testid="stBaseButton-primary"]{background:linear-gradient(135deg,#ff8a4c,#ff6a13)!important;color:#1d0d02!important;border:0!important;border-radius:18px!important;min-height:3.7rem;font-weight:800;font-size:1.15rem;letter-spacing:.01em;box-shadow:0 12px 36px rgba(255,130,64,.4);transition:transform .15s,box-shadow .15s}
      .stButton > button[kind="primary"]:hover{transform:translateY(-2px);box-shadow:0 16px 44px rgba(255,130,64,.55)}
      [data-testid="stExpander"]{border:1px solid #243843;border-radius:18px;background:#0f1920}
      [data-testid="stFileUploaderFile"]{border-radius:12px}
      .iso-note{color:#7f93a0;font-size:.85rem;text-align:center;margin-top:6px}
    </style>
    """,
    unsafe_allow_html=True,
)

_first = st.session_state.result is None
_box = st.container() if _first else st.expander("Read a different iso", expanded=False)
with _box:
    if _first:
        st.markdown(
            '<div class="iso-hero"><span class="iso-kick">STEP 1</span><h2>Start with your iso</h2>'
            "<p>One clear photo of the drawing. It reads the coordinates and builds your checklist.</p>"
            '<div class="iso-tips"><span>Lay it flat</span><span>Shoot straight down</span><span>Fill the frame</span><span>Even light</span><span>Sharp small print</span></div></div>',
            unsafe_allow_html=True,
        )
    up = st.file_uploader("Photo of the iso", type=["jpg", "jpeg", "png", "webp", "heic", "heif"], label_visibility="collapsed")
    up2 = []
    if st.checkbox("Add a photo of the material list too", help="Use this when the material list is on another part of the sheet or did not fit in the first photo. It is how the circled numbers get their names."):
        up2 = st.file_uploader("Photo of the material list", type=["jpg", "jpeg", "png", "webp", "heic", "heif"], accept_multiple_files=True, label_visibility="collapsed")
    can = True
    if up and can and st.button("Read the iso", type="primary", use_container_width=True):
        ok, msg, _ = S.check_allowed(store(), email, default_monthly=FREE_PER_MONTH, app_daily=MAX_PER_DAY, cooldown_s=COOLDOWN_S, limit=limit if ent["plan"] != "free" else None, group=group)
        if up.size > 15 * 1024 * 1024:
            st.error("That photo is over 15 MB. Take it again at a lower resolution.")
        elif not ok:
            st.error(msg)
        else:
            st.session_state["_acc"] = {"in": 0, "out": 0, "cost": 0.0}
            rid = store().add_read(email)
            try:
                with st.spinner("Claude is reading the iso and zooming in on the small print. This takes about a minute."):
                    st.session_state.result = read_iso(up.getvalue(), ask, [f.getvalue() for f in (up2 or [])][:3])
                acc = st.session_state.get("_acc") or {}
                try:
                    store().finish_read(rid, "ok", acc.get("in"), acc.get("out"), acc.get("cost"))
                except Exception:
                    pass
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
