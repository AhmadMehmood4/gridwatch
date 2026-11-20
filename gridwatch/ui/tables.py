"""Readable table presentation helpers (indicator lamps, column formats)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

SEV_LAMP = {"CRITICAL": "\U0001F534 Critical", "HIGH": "\U0001F7E0 High", "WARNING": "\U0001F7E1 Warning"}
STATE_LABEL = {"ACTIVE": "Active", "ACKNOWLEDGED": "Acknowledged", "CLEARED": "Cleared"}
STATUS_LAMP = {"IN_SERVICE": "\U0001F7E2 In service", "OUT_OF_SERVICE": "⚫ Out of service"}
SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "WARNING": 2}
CC = st.column_config


def sort_by_severity(df: pd.DataFrame, col: str = "severity") -> pd.DataFrame:
    if df.empty:
        return df
    return (df.assign(_r=df[col].map(SEV_RANK)).sort_values(["_r", "raised_at"], ascending=[True, False])
              .drop(columns="_r"))


def fmt_alarm_value(row: pd.Series) -> str:
    unit = row["unit"]
    if unit == "status":
        return "Out of service"
    if unit == "pu":
        return f"{row['latest_value']:.3f} pu (worst {row['peak_value']:.3f})"
    return f"{row['latest_value']:.1f}% (peak {row['peak_value']:.1f}%)"


def alarm_display(df: pd.DataFrame, history: bool = False) -> pd.DataFrame:
    out = pd.DataFrame({
        "ID": df["alarm_id"],
        "Raised (sim)": df["raised_at"],
        "Severity": df["severity"].map(SEV_LAMP),
        "Type": df["alarm_type"].str.title(),
        "Line": df["line_code"],
        "kV": df["nominal_kv"],
        "State": df["state"].map(STATE_LABEL),
        "Value": df.apply(fmt_alarm_value, axis=1) if len(df) else pd.Series(dtype=str),
        "Message": df["message"],
    })
    if history:
        out.insert(3, "Peak severity", df["peak_severity"].map(SEV_LAMP))
        out["Acknowledged by"] = df["acknowledged_by"].fillna("")
        out["Acknowledged (sim)"] = df["acknowledged_at"]
        out["Cleared (sim)"] = df["cleared_at"]
        out["Duration (min)"] = df["duration_min"]
    return out


ALARM_CFG = {
    "Raised (sim)": CC.DatetimeColumn(format="MMM DD, HH:mm", width="small"),
    "Acknowledged (sim)": CC.DatetimeColumn(format="MMM DD, HH:mm", width="small"),
    "Cleared (sim)": CC.DatetimeColumn(format="MMM DD, HH:mm", width="small"),
    "ID": CC.NumberColumn(width="small"),
    "kV": CC.NumberColumn(width="small", format="%d"),
    "Message": CC.TextColumn(width="large"),
    "Duration (min)": CC.NumberColumn(format="%.0f"),
}


def show(df: pd.DataFrame, height: int | None = None, cfg: dict | None = None, key: str | None = None) -> None:
    kwargs = {"height": height} if height else {}
    st.dataframe(df, hide_index=True, width="stretch", column_config=cfg or {}, key=key, **kwargs)


def measurements_display(latest: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "Line": latest["line_code"], "Name": latest["line_name"], "kV": latest["nominal_kv"],
        "Status": latest["equipment_status"].map(STATUS_LAMP),
        "Voltage (kV)": latest["voltage_kv"], "Voltage (pu)": latest["voltage_pu"],
        "Current (A)": latest["current_a"], "P (MW)": latest["p_mw"], "Q (MVAr)": latest["q_mvar"],
        "S (MVA)": latest["s_mva"], "PF": latest["power_factor"], "Loading": latest["loading_pct"],
        "Updated (sim)": latest["timestamp"],
    })


MEAS_CFG = {
    "kV": CC.NumberColumn(format="%d", width="small"),
    "Voltage (kV)": CC.NumberColumn(format="%.1f"), "Voltage (pu)": CC.NumberColumn(format="%.3f"),
    "Current (A)": CC.NumberColumn(format="%.0f"), "P (MW)": CC.NumberColumn(format="%.1f"),
    "Q (MVAr)": CC.NumberColumn(format="%.1f"), "S (MVA)": CC.NumberColumn(format="%.1f"),
    "PF": CC.NumberColumn(format="%.3f", width="small"),
    "Loading": CC.ProgressColumn("Loading", min_value=0, max_value=120, format="%.1f%%"),
    "Updated (sim)": CC.DatetimeColumn(format="HH:mm", width="small"),
}
