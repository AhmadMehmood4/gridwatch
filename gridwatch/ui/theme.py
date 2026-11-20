"""Visual identity: dark navy control-room palette, CSS, Plotly styling, HTML snippets."""
from __future__ import annotations

import html

import plotly.graph_objects as go
import streamlit as st

from gridwatch.alarms import Thresholds

C = {
    "bg": "#0A1628", "panel": "#0F2038", "panel_alt": "#13294B", "border": "#22406A",
    "grid": "#1A3050", "text": "#E6EDF7", "muted": "#8DA2C0", "accent": "#38BDF8",
    "ok": "#2FBF71", "warn": "#F5B301", "high": "#FF7A1A", "crit": "#F0384A", "offline": "#6B7A90",
}
SEVERITY_COLOR = {"WARNING": C["warn"], "HIGH": C["high"], "CRITICAL": C["crit"]}
KV_COLOR = {500: "#B794F6", 230: "#38BDF8", 115: "#7C93B8"}
KV_WIDTH = {500: 7, 230: 4.5, 115: 2.8}
SERIES = ["#38BDF8", "#B794F6", "#34D399", "#F472B6", "#FBBF24", "#60A5FA",
          "#FB923C", "#2DD4BF", "#C4B5FD", "#94A3B8", "#F87171", "#A3E635"]

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:wght@500;600;700&family=IBM+Plex+Sans:wght@400;500;600&display=swap');
:root{--bg:%(bg)s;--panel:%(panel)s;--alt:%(panel_alt)s;--border:%(border)s;--text:%(text)s;--muted:%(muted)s;
--accent:%(accent)s;--ok:%(ok)s;--warn:%(warn)s;--high:%(high)s;--crit:%(crit)s;--off:%(offline)s}
html,body,[class*="css"],.stApp{font-family:'IBM Plex Sans',system-ui,'Segoe UI',sans-serif;font-variant-numeric:tabular-nums}
.stApp{background:var(--bg)}
header[data-testid="stHeader"]{background:transparent}
#MainMenu,footer{visibility:hidden}
.block-container{padding-top:2.6rem;padding-bottom:3rem;max-width:1600px}
[data-testid="stSidebar"]{background:#081222;border-right:1px solid var(--border)}
h1,h2,h3,h4{font-family:'Barlow Semi Condensed','IBM Plex Sans',sans-serif;letter-spacing:.2px}
.gw-brand{font-family:'Barlow Semi Condensed',sans-serif;font-weight:700;font-size:30px;line-height:1;color:var(--text)}
.gw-brand span{color:var(--accent)}
.gw-brand-sub{color:var(--muted);font-size:12.5px;margin-top:4px}
.gw-title{font-family:'Barlow Semi Condensed',sans-serif;font-weight:700;font-size:32px;line-height:1.1;margin:0}
.gw-subtitle{color:var(--muted);font-size:14px;margin:2px 0 14px 0;max-width:900px}
.gw-section{font-family:'Barlow Semi Condensed',sans-serif;font-weight:600;font-size:19px;margin:18px 0 8px 0;
  padding-bottom:5px;border-bottom:1px solid var(--border)}
.gw-strip{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px 0}
.gw-chip{display:inline-flex;align-items:center;gap:6px;background:var(--panel);border:1px solid var(--border);
  border-radius:4px;padding:3px 10px;font-size:12.5px;color:var(--muted)}
.gw-chip b{color:var(--text);font-weight:500}
.gw-chip.run{border-color:var(--ok);color:var(--ok)}
.gw-chip.paused{border-color:var(--warn);color:var(--warn)}
.gw-chip.synth{border-style:dashed;color:var(--accent);border-color:var(--accent)}
.gw-kpi{background:var(--panel);border:1px solid var(--border);border-left:4px solid var(--tone,var(--accent));
  border-radius:6px;padding:11px 16px 10px 16px;height:100%%}
.gw-kpi-label{color:var(--muted);font-size:13px}
.gw-kpi-value{font-family:'Barlow Semi Condensed',sans-serif;font-weight:700;font-size:38px;line-height:1.1;color:var(--text)}
.gw-kpi-value small{font-size:16px;color:var(--muted);font-weight:500;margin-left:3px}
.gw-kpi-sub{color:var(--muted);font-size:12.5px;min-height:17px}
.gw-kpi.compact{padding:7px 12px 6px 12px}
.gw-kpi.compact .gw-kpi-value{font-size:25px}
.gw-kpi.tone-ok{--tone:var(--ok)} .gw-kpi.tone-warn{--tone:var(--warn)} .gw-kpi.tone-high{--tone:var(--high)}
.gw-kpi.tone-crit{--tone:var(--crit)} .gw-kpi.tone-off{--tone:var(--off)}
.gw-kpi.pulse{animation:gwpulse 1.8s ease-in-out infinite}
@keyframes gwpulse{0%%,100%%{border-color:var(--border);border-left-color:var(--crit)}50%%{border-color:var(--crit)}}
@media (prefers-reduced-motion:reduce){.gw-kpi.pulse{animation:none}}
.gw-legend{display:flex;flex-wrap:wrap;gap:16px;color:var(--muted);font-size:12.5px;margin:2px 0 6px 2px}
.gw-legend i{display:inline-block;width:22px;height:4px;border-radius:2px;margin-right:6px;vertical-align:middle}
.gw-note{background:var(--panel);border:1px solid var(--border);border-left:3px solid var(--accent);border-radius:4px;
  padding:8px 12px;color:var(--muted);font-size:12.5px;line-height:1.45}
.gw-empty{border:1px dashed var(--border);border-radius:6px;padding:22px;text-align:center;color:var(--muted)}
.gw-sev{font-weight:600}
div[data-testid="stTabs"] button[role="tab"]{font-size:15px}
div[data-testid="stDataFrame"]{border:1px solid var(--border);border-radius:6px}
div.stButton>button,div.stDownloadButton>button{border-radius:4px;border:1px solid var(--border);font-weight:500}
div.stButton>button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.stCodeBlock{border:1px solid var(--border);border-radius:6px}
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS % C, unsafe_allow_html=True)


# ------------------------------------------------------------------ colour logic
def loading_color(pct: float | None, th: Thresholds, in_service: bool = True) -> str:
    if not in_service or pct is None:
        return C["offline"]
    if pct > th.loading_critical:
        return C["crit"]
    if pct > th.loading_high:
        return C["high"]
    if pct > th.loading_warning:
        return C["warn"]
    return C["ok"]


def style_fig(fig: go.Figure, height: int = 340, legend: bool = True) -> go.Figure:
    fig.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=C["panel"],
        font=dict(family="IBM Plex Sans, Segoe UI, sans-serif", color=C["text"], size=12),
        margin=dict(l=48, r=18, t=34, b=38), showlegend=legend,
        legend=dict(orientation="h", y=1.14, x=0, font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor=C["panel_alt"], bordercolor=C["border"], font=dict(color=C["text"])),
        title=dict(font=dict(family="Barlow Semi Condensed, sans-serif", size=16), x=0.0, y=0.97),
    )
    fig.update_xaxes(gridcolor=C["grid"], linecolor=C["border"], zeroline=False)
    fig.update_yaxes(gridcolor=C["grid"], linecolor=C["border"], zeroline=False)
    return fig


# ------------------------------------------------------------------ HTML snippets
def esc(x: object) -> str:
    return html.escape(str(x))


def page_title(title: str, subtitle: str = "") -> None:
    st.markdown(f'<h1 class="gw-title">{esc(title)}</h1><div class="gw-subtitle">{esc(subtitle)}</div>',
                unsafe_allow_html=True)


def section(title: str) -> None:
    st.markdown(f'<div class="gw-section">{esc(title)}</div>', unsafe_allow_html=True)


def kpi_html(label: str, value: object, sub: str = "", tone: str = "accent",
             unit: str = "", compact: bool = False, pulse: bool = False) -> str:
    cls = f"gw-kpi tone-{tone}" + (" compact" if compact else "") + (" pulse" if pulse else "")
    u = f"<small>{esc(unit)}</small>" if unit else ""
    return (f'<div class="{cls}"><div class="gw-kpi-label">{esc(label)}</div>'
            f'<div class="gw-kpi-value">{esc(value)}{u}</div><div class="gw-kpi-sub">{esc(sub)}</div></div>')


def kpi_row(items: list[dict]) -> None:
    cols = st.columns(len(items))
    for col, it in zip(cols, items):
        col.markdown(kpi_html(**it), unsafe_allow_html=True)


def status_strip(snap) -> None:
    state = ('<span class="gw-chip run">&#9679; <b>Running</b></span>' if snap.running
             else '<span class="gw-chip paused">&#10074;&#10074; <b>Paused</b></span>')
    scen = f'<span class="gw-chip">Scenario <b>{esc(snap.scenario)}</b></span>' if snap.scenario else ""
    st.markdown(
        f'<div class="gw-strip">{state}'
        f'<span class="gw-chip">Simulation clock <b>{snap.sim_time:%Y-%m-%d %H:%M}</b></span>'
        f'<span class="gw-chip">Tick <b>{snap.tick}</b></span>'
        f'<span class="gw-chip">Step <b>{snap.step_minutes} min</b></span>'
        f'<span class="gw-chip">Seed <b>{snap.seed}</b></span>{scen}'
        f'<span class="gw-chip synth">Synthetic telemetry</span></div>', unsafe_allow_html=True)


def empty_state(message: str) -> None:
    st.markdown(f'<div class="gw-empty">{esc(message)}</div>', unsafe_allow_html=True)


def note(message: str) -> None:
    st.markdown(f'<div class="gw-note">{esc(message)}</div>', unsafe_allow_html=True)
