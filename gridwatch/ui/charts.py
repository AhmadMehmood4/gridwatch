"""Plotly figure builders. Pure functions: DataFrames in, figures out."""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

from gridwatch.alarms import Thresholds
from gridwatch.ui.theme import (C, KV_COLOR, KV_WIDTH, SERIES, SEVERITY_COLOR, loading_color,
                                style_fig)


def _hex_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def series_color(codes: list[str]) -> dict[str, str]:
    return {c: SERIES[i % len(SERIES)] for i, c in enumerate(sorted(codes))}


# ------------------------------------------------------------------ overview
def loading_bars(latest: pd.DataFrame, th: Thresholds, height: int = 420) -> go.Figure:
    df = latest.sort_values("line_code", ascending=False)
    in_service = df["equipment_status"] == "IN_SERVICE"
    colors = [loading_color(p, th, s) for p, s in zip(df["loading_pct"], in_service)]
    labels = [f"{p:.1f}%" if s else "OUT" for p, s in zip(df["loading_pct"], in_service)]
    y = [f"{c}  ·  {int(k)} kV" for c, k in zip(df["line_code"], df["nominal_kv"])]
    fig = go.Figure(go.Bar(
        x=df["loading_pct"], y=y, orientation="h", marker=dict(color=colors), text=labels,
        textposition="outside", cliponaxis=False, textfont=dict(color=C["text"], size=11),
        customdata=df[["line_name", "current_a", "rated_current_a"]],
        hovertemplate="<b>%{y}</b><br>%{customdata[0]}<br>Loading %{x:.1f}%<br>"
                      "%{customdata[1]:.0f} A of %{customdata[2]:.0f} A rated<extra></extra>"))
    top = max(120.0, float(df["loading_pct"].max()) + 18)
    for val, col in ((th.loading_warning, C["warn"]), (th.loading_high, C["high"]), (th.loading_critical, C["crit"])):
        fig.add_vline(x=val, line=dict(color=col, width=1, dash="dot"))
    fig.update_xaxes(range=[0, top], title="Loading (% of rated current)")
    fig.update_yaxes(tickfont=dict(size=11))
    return style_fig(fig, height, legend=False)


# ------------------------------------------------------------------ network map
def _offset_lines(lines: pd.DataFrame, coords: dict[str, tuple[float, float]]) -> dict[str, tuple]:
    """Endpoints per line; parallel lines between the same pair are drawn side by side."""
    groups: dict[frozenset, list[str]] = {}
    for _, r in lines.iterrows():
        groups.setdefault(frozenset((r["from_sub"], r["to_sub"])), []).append(r["line_code"])
    out = {}
    rows = lines.set_index("line_code")
    for pair, codes in groups.items():
        for i, code in enumerate(sorted(codes)):
            r = rows.loc[code]
            (x0, y0), (x1, y1) = coords[r["from_sub"]], coords[r["to_sub"]]
            dx, dy = x1 - x0, y1 - y0
            norm = math.hypot(dx, dy) or 1.0
            shift = (i - (len(codes) - 1) / 2.0) * 0.34
            ox, oy = -dy / norm * shift, dx / norm * shift
            out[code] = ((x0 + ox, y0 + oy), (x1 + ox, y1 + oy))
    return out


def network_figure(lines: pd.DataFrame, subs: pd.DataFrame, th: Thresholds,
                   selected: str | None, color_by: str = "Loading status", height: int = 600) -> go.Figure:
    coords = {r.code: (r.map_x, r.map_y) for r in subs.itertuples()}
    geom = _offset_lines(lines, coords)
    fig = go.Figure()
    mid_x, mid_y, mid_text, mid_col, mid_size, mid_line, mid_code, mid_hover = [], [], [], [], [], [], [], []

    for r in lines.itertuples():
        (x0, y0), (x1, y1) = geom[r.line_code]
        online = r.equipment_status == "IN_SERVICE"
        if color_by == "Voltage class":
            col = KV_COLOR[int(r.nominal_kv)] if online else C["offline"]
        else:
            col = loading_color(r.loading_pct, th, online)
        width = KV_WIDTH[int(r.nominal_kv)]
        if online and r.loading_pct > th.loading_warning and color_by != "Voltage class":
            fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines", hoverinfo="skip", showlegend=False,
                                     line=dict(color=_hex_rgba(col, 0.22), width=width + 9)))
        fig.add_trace(go.Scatter(x=[x0, x1], y=[y0, y1], mode="lines", hoverinfo="skip", showlegend=False,
                                 line=dict(color=col, width=width, dash="solid" if online else "dash")))
        mid_x.append((x0 + x1) / 2)
        mid_y.append((y0 + y1) / 2)
        mid_code.append(r.line_code)
        mid_col.append(col)
        sel = r.line_code == selected
        mid_size.append(40 if sel else 31)
        mid_line.append(C["text"] if sel else col)
        mid_text.append(f"{r.loading_pct:.0f}%" if online else "OUT")
        mid_hover.append(
            f"<b>{r.line_code}</b> · {int(r.nominal_kv)} kV<br>{r.line_name}<br>"
            + ("Out of service" if not online else
               f"Loading {r.loading_pct:.1f}%<br>{r.current_a:.0f} A · {r.p_mw:.0f} MW · {r.q_mvar:.0f} MVAr"
               f"<br>{r.voltage_kv:.1f} kV ({r.voltage_pu:.3f} pu)"))

    fig.add_trace(go.Scatter(
        x=mid_x, y=mid_y, mode="markers+text", text=mid_text, customdata=mid_code, hovertext=mid_hover,
        hovertemplate="%{hovertext}<extra></extra>", name="lines",
        textfont=dict(color=C["text"], size=10.5, family="IBM Plex Sans"),
        marker=dict(size=mid_size, color=C["bg"], line=dict(color=mid_line, width=3), symbol="circle"),
        selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)), showlegend=False))

    s = subs.copy()
    short = s["name"].str.replace(r"\s+(TS|GS)$", "", regex=True)
    fig.add_trace(go.Scatter(
        x=s["map_x"], y=s["map_y"], mode="markers+text", customdata=s["code"],
        text=[f"<b>{c}</b><br><span style='font-size:10px;color:{C['muted']}'>{n}</span>" for c, n in zip(s["code"], short)],
        textposition="bottom center", textfont=dict(color=C["text"], size=12),
        marker=dict(symbol="square", size=[30 if k == 500 else 25 if k == 230 else 20 for k in s["max_voltage_kv"]],
                    color=[KV_COLOR[int(k)] for k in s["max_voltage_kv"]], line=dict(color=C["bg"], width=3)),
        hovertext=[f"<b>{n}</b><br>{r} · {lv} kV" for n, r, lv in zip(s["name"], s["region"], s["voltage_levels_kv"])],
        hovertemplate="%{hovertext}<extra></extra>", showlegend=False,
        selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1))))

    fig.update_xaxes(visible=False, range=[-0.9, 11.3], fixedrange=True)
    fig.update_yaxes(visible=False, range=[-0.7, 10.2], fixedrange=True, scaleanchor="x", scaleratio=1)
    fig.update_layout(clickmode="event+select", dragmode=False, margin=dict(l=4, r=4, t=4, b=4))
    return style_fig(fig, height, legend=False).update_layout(plot_bgcolor=C["bg"], margin=dict(l=4, r=4, t=4, b=4))


# ------------------------------------------------------------------ live trends
def trend_chart(df: pd.DataFrame, column: str, title: str, y_title: str,
                hlines: list[tuple[float, str, str]] | None = None,
                band: tuple[float, float] | None = None, extra: str | None = None,
                height: int = 300) -> go.Figure:
    """One trace per line. `extra` adds a dashed companion series (e.g. MVAr alongside MW)."""
    fig = go.Figure()
    colors = series_color(list(df["line_code"].unique()))
    for code, g in df.groupby("line_code"):
        y = g[column].where(g["status"] == "IN_SERVICE") if column == "voltage_pu" else g[column]
        fig.add_trace(go.Scatter(x=g["timestamp"], y=y, mode="lines", name=code, legendgroup=code,
                                 line=dict(color=colors[code], width=2), connectgaps=False,
                                 hovertemplate=f"{code}: %{{y:.3f}}<extra></extra>" if column == "voltage_pu"
                                 else f"{code}: %{{y:.1f}}<extra></extra>"))
        if extra:
            fig.add_trace(go.Scatter(x=g["timestamp"], y=g[extra], mode="lines", name=f"{code} (MVAr)",
                                     legendgroup=code, showlegend=False,
                                     line=dict(color=colors[code], width=1.4, dash="dot"),
                                     hovertemplate=f"{code} MVAr: %{{y:.1f}}<extra></extra>"))
    if band:
        fig.add_hrect(y0=band[0], y1=band[1], fillcolor=_hex_rgba(C["ok"], 0.07), line_width=0)
    for val, col, label in hlines or []:
        fig.add_hline(y=val, line=dict(color=col, width=1, dash="dot"),
                      annotation_text=label, annotation_font=dict(size=10, color=col), annotation_position="top right")
    fig.update_yaxes(title=y_title)
    fig.update_layout(title=title, hovermode="x unified")
    return style_fig(fig, height)


def mini_loading_trend(df: pd.DataFrame, th: Thresholds, height: int = 200) -> go.Figure:
    fig = go.Figure(go.Scatter(x=df["timestamp"], y=df["loading_pct"], mode="lines", fill="tozeroy",
                               line=dict(color=C["accent"], width=2), fillcolor=_hex_rgba(C["accent"], 0.12),
                               hovertemplate="%{y:.1f}%<extra></extra>"))
    for val, col in ((th.loading_warning, C["warn"]), (th.loading_critical, C["crit"])):
        fig.add_hline(y=val, line=dict(color=col, width=1, dash="dot"))
    fig.update_yaxes(range=[0, max(115, float(df["loading_pct"].max()) + 5)], title="Loading %")
    fig.update_layout(margin=dict(l=44, r=8, t=8, b=28))
    return style_fig(fig, height, legend=False).update_layout(margin=dict(l=44, r=8, t=8, b=28))


# ------------------------------------------------------------------ analytics
def loading_heatmap(hourly: pd.DataFrame, th: Thresholds, height: int = 420) -> go.Figure:
    pivot = hourly.pivot(index="line_code", columns="hour", values="max_loading_pct").sort_index(ascending=False)
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=pivot.columns, y=pivot.index, zmin=0, zmax=max(110, th.loading_critical + 10),
        colorscale=[[0, "#0F2038"], [0.45, "#1F6FA8"], [0.68, C["ok"]], [0.8, C["warn"]], [0.9, C["high"]], [1, C["crit"]]],
        colorbar=dict(title="Max %", thickness=12), xgap=1, ygap=1,
        hovertemplate="%{y} · %{x|%b %d %H:00}<br>Peak loading %{z:.1f}%<extra></extra>"))
    fig.update_layout(title="Peak loading per line per simulated hour")
    return style_fig(fig, height, legend=False)


def loading_stats_chart(stats: pd.DataFrame, th: Thresholds, height: int = 340) -> go.Figure:
    df = stats.sort_values("line_code")
    fig = go.Figure()
    fig.add_bar(x=df["line_code"], y=df["avg_loading_pct"], name="Average", marker_color=C["accent"])
    fig.add_bar(x=df["line_code"], y=df["max_loading_pct"], name="Peak", marker_color=C["high"])
    for val, col in ((th.loading_high, C["high"]), (th.loading_critical, C["crit"])):
        fig.add_hline(y=val, line=dict(color=col, width=1, dash="dot"))
    fig.update_layout(barmode="group", title="Average vs peak loading", yaxis_title="Loading %")
    return style_fig(fig, height)


def utilization_chart(util: pd.DataFrame, height: int = 340) -> go.Figure:
    df = util.sort_values("avg_utilization_pct")
    fig = go.Figure(go.Bar(
        x=df["avg_utilization_pct"], y=df["line_code"], orientation="h",
        marker=dict(color=[KV_COLOR[int(k)] for k in df["nominal_kv"]]),
        text=[f"{v:.0f}%  ({m:.0f} MVA avg)" for v, m in zip(df["avg_utilization_pct"], df["avg_mva"])],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{y}: %{x:.1f}% of %{customdata:.0f} MVA rating<extra></extra>", customdata=df["rated_mva"]))
    fig.update_xaxes(title="Average utilisation (% of thermal MVA)", range=[0, max(100, float(df["avg_utilization_pct"].max()) + 30)])
    fig.update_layout(title="Equipment utilisation (colour = voltage class)")
    return style_fig(fig, height, legend=False)


def voltage_range_chart(dev: pd.DataFrame, th: Thresholds, height: int = 340) -> go.Figure:
    df = dev.sort_values("line_code")
    fig = go.Figure()
    fig.add_hrect(y0=th.v_low_warning, y1=th.v_high_warning, fillcolor=_hex_rgba(C["ok"], 0.08), line_width=0)
    for val, col in ((th.v_low_warning, C["warn"]), (th.v_high_warning, C["warn"]),
                     (th.v_low_critical, C["crit"]), (th.v_high_critical, C["crit"])):
        fig.add_hline(y=val, line=dict(color=col, width=1, dash="dot"))
    fig.add_trace(go.Scatter(x=df["line_code"], y=df["max_pu"], mode="markers", name="Max",
                             marker=dict(color=C["high"], size=9, symbol="triangle-up")))
    fig.add_trace(go.Scatter(x=df["line_code"], y=df["min_pu"], mode="markers", name="Min",
                             marker=dict(color=C["accent"], size=9, symbol="triangle-down")))
    fig.add_trace(go.Scatter(x=df["line_code"], y=df["avg_pu"], mode="markers", name="Average",
                             marker=dict(color=C["text"], size=7, symbol="line-ew", line=dict(width=2, color=C["text"]))))
    for _, r in df.iterrows():
        fig.add_shape(type="line", x0=r["line_code"], x1=r["line_code"], y0=r["min_pu"], y1=r["max_pu"],
                      line=dict(color=C["muted"], width=2))
    lo = min(float(df["min_pu"].min()), th.v_low_critical) - 0.01
    hi = max(float(df["max_pu"].max()), th.v_high_warning) + 0.01
    fig.update_yaxes(title="Voltage (pu)", range=[lo, hi])
    fig.update_layout(title="Voltage range per line (shaded = normal band)")
    return style_fig(fig, height)


def alarm_frequency_chart(freq: pd.DataFrame, height: int = 320) -> go.Figure:
    fig = go.Figure()
    for sev in ("CRITICAL", "HIGH", "WARNING"):
        g = freq[freq["severity"] == sev]
        fig.add_bar(x=g["alarm_type"], y=g["alarm_count"], name=sev, marker_color=SEVERITY_COLOR[sev])
    fig.update_layout(barmode="stack", title="Alarms by type (worst severity reached)", yaxis_title="Alarms")
    return style_fig(fig, height)


def alarms_by_hour_chart(by_hour: pd.DataFrame, height: int = 320) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=by_hour["hour"], y=by_hour["alarms_raised"], name="Alarms raised", marker_color=C["accent"])
    fig.add_bar(x=by_hour["hour"], y=by_hour["critical"], name="Of which critical", marker_color=C["crit"])
    fig.update_layout(barmode="overlay", title="Alarms raised per simulated hour", yaxis_title="Alarms")
    return style_fig(fig, height)


def equipment_alarm_chart(counts: pd.DataFrame, height: int = 320) -> go.Figure:
    df = counts[counts["total_alarms"] > 0].sort_values("total_alarms")
    fig = go.Figure()
    for sev, col in (("warning", C["warn"]), ("high", C["high"]), ("critical", C["crit"])):
        fig.add_bar(y=df["line_code"], x=df[sev], name=sev.upper(), orientation="h", marker_color=col)
    fig.update_layout(barmode="stack", title="Equipment with the most alarms", xaxis_title="Alarms")
    return style_fig(fig, height)
