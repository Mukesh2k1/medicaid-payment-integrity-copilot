"""Visual identity: an audit ledger for payment-integrity analysts."""
import html as _html
import re

import streamlit as st

INK = "#17324D"
TEAL = "#0F766E"
RED = "#B3261E"
GREEN = "#2E7D4F"
AMBER = "#B7791F"
GREY = "#6B7785"
LINE = "#DDE3EA"
PAPER = "#F3F5F7"
FONT = "'IBM Plex Sans', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif"

CHANGE_COLORS = {
    "Rate increase": RED,
    "Rate decrease": GREEN,
    "New code": TEAL,
    "Deleted code": GREY,
    "Became not priced": AMBER,
    "Became priced": INK,
    "No change": "#C9D1DA",
}
TONES = {"neutral": INK, "cost": RED, "saving": GREEN, "caution": AMBER, "accent": TEAL,
         "muted": GREY}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
html, body, p, li, label, h1, h2, h3, h4, h5, h6, td, th, input, textarea, button,
[data-testid="stMarkdownContainer"], [data-testid="stCaptionContainer"],
[data-testid="stMetricValue"], [data-testid="stWidgetLabel"] {{
  font-family: {FONT};
  font-variant-numeric: tabular-nums;
}}
.block-container {{ padding-top: 3.2rem; max-width: 1320px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stAppDeployButton"], .stDeployButton {{ display: none; }}
section[data-testid="stSidebar"] {{ background: {PAPER}; border-right: 1px solid {LINE}; }}
h3 {{ font-size: 1.3rem !important; color: {INK}; font-weight: 600 !important; }}
[data-testid="stVerticalBlockBorderWrapper"] h3 {{ font-size: 1.08rem !important; }}
section[data-testid="stSidebar"] h3 {{ font-size: 0.95rem !important; }}
.stTabs [data-baseweb="tab"] p {{ font-size: 1rem; color: {GREY}; }}
.stTabs [aria-selected="true"] p {{ color: {INK}; font-weight: 600; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {TEAL}; height: 3px; }}
.mpic-num {{ font-variant-numeric: tabular-nums; }}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def _dollar(text):
    """Stop Streamlit from reading $...$ as LaTeX inside HTML components."""
    return str(text).replace("$", "&#36;")


def _html_out(markup):
    st.markdown(_dollar(markup), unsafe_allow_html=True)


def _bold(text):
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def esc(x):
    return _html.escape(str(x))


# ----------------------------------------------------------------------------- components
def header(meta=None):
    if meta:
        right = (f"<div style='font-weight:600'>{esc(meta.get('name', ''))}</div>"
                 f"<div>{esc(meta.get('old_label', ''))} compared with "
                 f"{esc(meta.get('new_label', ''))}</div>"
                 f"<div style='opacity:.75'>{esc(meta.get('source', ''))}</div>")
    else:
        right = ("<div style='font-weight:600'>No data loaded</div>"
                 "<div style='opacity:.75'>Run the agent on the Load tab</div>")
    _html_out(f"""
<div style="background:{INK};border-bottom:4px solid {TEAL};border-radius:6px;padding:20px 26px;
display:flex;justify-content:space-between;align-items:center;gap:24px;margin-bottom:14px;">
  <div>
    <div style="color:#fff;font-size:1.85rem;font-weight:600;line-height:1.2;font-family:{FONT}">
      Medicaid Payment Integrity Copilot</div>
    <div style="color:#D6E0EA;font-size:.95rem;margin-top:6px;max-width:720px;font-family:{FONT}">
      Compares two versions of a state fee schedule, prices the change against claims volume,
      and finds codes the claims system would pay wrongly.</div>
  </div>
  <div style="color:#E4EBF2;font-size:.82rem;text-align:right;min-width:220px;font-family:{FONT}">
    {right}</div>
</div>""")


def kpis(items):
    """items: list of dicts label, value, note, tone."""
    cards = []
    for it in items:
        c = TONES.get(it.get("tone", "neutral"), INK)
        note = it.get("note", "")
        cards.append(f"""
<div style="background:#fff;border:1px solid {LINE};border-top:3px solid {c};border-radius:4px;
padding:12px 14px;font-family:{FONT}">
  <div style="color:{GREY};font-size:.8rem">{esc(it['label'])}</div>
  <div class="mpic-num" style="color:{INK};font-size:1.6rem;font-weight:600;margin-top:2px">
    {esc(it['value'])}</div>
  <div style="color:{c};font-size:.78rem;margin-top:2px">{esc(note)}</div>
</div>""")
    _html_out(f"""<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
gap:12px;margin:6px 0 16px">{''.join(cards)}</div>""")


def alarm(big, html_text, verify=None):
    v = (f"<div style='color:{GREEN};font-weight:600;margin-top:8px'>&#10003; {esc(verify)}</div>"
         if verify else "")
    _html_out(f"""
<div style="background:#FBEDEC;border-left:6px solid {RED};border-radius:4px;padding:16px 22px;
margin:6px 0 18px;font-family:{FONT}">
  <div class="mpic-num" style="color:{RED};font-size:2.6rem;font-weight:700;line-height:1.1">
    {esc(big)}</div>
  <div style="color:#1F2A36;margin-top:6px;font-size:1rem">{html_text}</div>{v}
</div>""")


def insight(html_text):
    _html_out(f"""<div style="background:#EAF4F3;border-left:4px solid {TEAL};border-radius:4px;
padding:12px 18px;margin:4px 0 16px;color:#1F2A36;font-family:{FONT}">{html_text}</div>""")


def agent_log(log, elapsed):
    rows = []
    for e in log:
        rows.append(f"""
<div style="display:flex;gap:12px;align-items:flex-start;padding:9px 0;border-bottom:1px solid {LINE}">
  <div style="flex:0 0 26px;height:26px;border-radius:50%;background:{TEAL};color:#fff;
  font-weight:600;font-size:.85rem;display:flex;align-items:center;justify-content:center">
  {e['step']}</div>
  <div><b style="color:{INK}">{esc(e['name'])}</b><br>
  <span style="color:#1F2A36">{esc(e['detail'])}</span></div>
</div>""")
    _html_out(f"""
<div style="border:1px solid {LINE};border-radius:6px;padding:4px 16px 10px;margin-bottom:12px;
font-family:{FONT}">{''.join(rows)}
<div style="color:{GREY};font-size:.82rem;padding-top:8px">Agent finished in {elapsed:.1f} seconds.
Every figure is computed by code; AI only writes summary wording.</div></div>""")


def recs_panel(recs):
    items = []
    for i, r in enumerate(recs):
        last = i == len(recs) - 1
        colour = GREY if last else "#1F2A36"
        items.append(f"<li style='margin:0 0 10px;color:{colour}'>{_bold(esc(r))}</li>")
    _html_out(f"""<div style="border:1px solid {LINE};border-radius:6px;padding:14px 16px 4px;
font-family:{FONT}"><ul style="padding-left:18px;margin:0">{''.join(items)}</ul></div>""")


def sidebar_steps(done):
    from pipeline import STEP_NAMES
    rows = []
    for i, n in enumerate(STEP_NAMES):
        fill = TEAL if done else "transparent"
        rows.append(f"""<div style="display:flex;gap:10px;align-items:center;margin:6px 0;
font-size:.9rem;color:{INK}"><span style="display:inline-block;width:11px;height:11px;
border-radius:50%;border:2px solid {TEAL};background:{fill}"></span>{i + 1}. {esc(n)}</div>""")
    st.sidebar.markdown(_dollar("".join(rows)), unsafe_allow_html=True)


def sidebar_meta(lines):
    st.sidebar.markdown(_dollar("".join(
        f"<div style='font-size:.85rem;color:#1F2A36;margin:3px 0'>{esc(x)}</div>" for x in lines)),
        unsafe_allow_html=True)


def empty_state():
    st.info("Run the agent on the Load tab to see results here.")


def style_fig(fig, height=360, title=None):
    fig.update_layout(
        template="simple_white", height=height, showlegend=False,
        font=dict(family=FONT, color="#1F2A36", size=13),
        title=dict(text=title or "", x=0, xanchor="left", font=dict(color=INK, size=16)),
        margin=dict(l=10, r=20, t=50 if title else 20, b=10),
        plot_bgcolor="#fff", paper_bgcolor="#fff",
    )
    fig.update_xaxes(showgrid=False, linecolor=LINE, tickcolor=LINE)
    fig.update_yaxes(showgrid=False, linecolor=LINE, tickcolor=LINE)
    return fig
