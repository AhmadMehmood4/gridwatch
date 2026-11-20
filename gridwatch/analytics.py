"""SQL-powered analytics and reports.

Every report is a plain, parameterised SQL statement (JOIN / GROUP BY / ORDER BY /
aggregates, plus CTEs and portable SQLite syntax). The dashboard shows the exact
SQL next to each result.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

_DATE_COLUMNS = ("timestamp", "raised_at", "acknowledged_at", "cleared_at", "last_seen",
                 "outage_since", "hour", "first_seen", "applied_at")


@dataclass(frozen=True)
class Query:
    key: str
    title: str
    description: str
    sql: str
    defaults: dict = field(default_factory=dict)


_SINCE = "1900-01-01 00:00:00"

QUERIES: dict[str, Query] = {q.key: q for q in (
    Query("latest_measurements", "Latest measurement per line",
          "Most recent telemetry sample for every line, joined to its terminal substations.",
          """
WITH latest AS (
    SELECT line_id, MAX(tick) AS tick FROM measurements GROUP BY line_id
)
SELECT l.id AS line_id, l.code AS line_code, l.name AS line_name, l.nominal_kv,
       fs.code AS from_sub, ts.code AS to_sub, l.status AS equipment_status,
       m.timestamp, m.tick, m.voltage_kv, m.voltage_pu, m.current_a, m.power_factor,
       m.p_mw, m.q_mvar, m.s_mva, m.loading_pct, l.rated_current_a,
       ROUND(1.7320508 * l.nominal_kv * l.rated_current_a / 1000.0, 1) AS rated_mva
FROM latest
JOIN measurements m        ON m.line_id = latest.line_id AND m.tick = latest.tick
JOIN transmission_lines l  ON l.id = m.line_id
JOIN substations fs        ON fs.id = l.from_substation_id
JOIN substations ts        ON ts.id = l.to_substation_id
ORDER BY l.nominal_kv DESC, l.code;"""),

    Query("lines_over_loading_now", "Lines currently above a loading threshold",
          "Latest sample per line where loading exceeds the chosen percentage.",
          """
WITH latest AS (
    SELECT line_id, MAX(tick) AS tick FROM measurements GROUP BY line_id
)
SELECT l.code AS line_code, l.name AS line_name, l.nominal_kv,
       ROUND(m.loading_pct, 1) AS loading_pct, ROUND(m.current_a, 0) AS current_a,
       l.rated_current_a, m.timestamp
FROM latest
JOIN measurements m       ON m.line_id = latest.line_id AND m.tick = latest.tick
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.loading_pct > :threshold_pct
ORDER BY m.loading_pct DESC;""", {"threshold_pct": 90.0}),

    Query("lines_over_loading_history", "Lines that exceeded a loading threshold",
          "Historical exceedances: how often and how high each line went over the threshold.",
          """
SELECT l.code AS line_code, l.name AS line_name, l.nominal_kv,
       COUNT(*)                         AS intervals_over,
       ROUND(MAX(m.loading_pct), 1)     AS peak_loading_pct,
       ROUND(AVG(m.loading_pct), 1)     AS avg_loading_when_over_pct,
       MIN(m.timestamp)                 AS first_seen,
       MAX(m.timestamp)                 AS last_seen
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.loading_pct > :threshold_pct AND m.timestamp >= :since
GROUP BY l.id, l.code, l.name, l.nominal_kv
ORDER BY peak_loading_pct DESC;""", {"threshold_pct": 90.0, "since": _SINCE}),

    Query("loading_by_line", "Average and maximum loading by line",
          "Loading statistics and availability per line over the selected window (in-service samples only for loading).",
          """
SELECT l.code AS line_code, l.name AS line_name, l.nominal_kv,
       COUNT(*)                                                   AS samples,
       ROUND(AVG(CASE WHEN m.status = 'IN_SERVICE' THEN m.loading_pct END), 1) AS avg_loading_pct,
       ROUND(MAX(m.loading_pct), 1)                               AS max_loading_pct,
       ROUND(MIN(CASE WHEN m.status = 'IN_SERVICE' THEN m.loading_pct END), 1) AS min_loading_pct,
       ROUND(100.0 * SUM(CASE WHEN m.status = 'IN_SERVICE' THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                                  AS availability_pct
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.timestamp >= :since
GROUP BY l.id, l.code, l.name, l.nominal_kv
ORDER BY max_loading_pct DESC;""", {"since": _SINCE}),

    Query("utilization", "Equipment utilisation",
          "Average apparent power versus thermal capacity (sqrt(3) x kV x rated A).",
          """
SELECT l.code AS line_code, l.nominal_kv,
       ROUND(1.7320508 * l.nominal_kv * l.rated_current_a / 1000.0, 1) AS rated_mva,
       ROUND(AVG(m.s_mva), 1)  AS avg_mva,
       ROUND(MAX(m.s_mva), 1)  AS peak_mva,
       ROUND(100.0 * AVG(m.s_mva) / (1.7320508 * l.nominal_kv * l.rated_current_a / 1000.0), 1)
                               AS avg_utilization_pct
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.status = 'IN_SERVICE' AND m.timestamp >= :since
GROUP BY l.id, l.code, l.nominal_kv, l.rated_current_a
ORDER BY avg_utilization_pct DESC;""", {"since": _SINCE}),

    Query("active_alarms_by_severity", "Active alarms by severity",
          "Open (not cleared) alarms grouped by severity, with acknowledgement counts.",
          """
SELECT severity,
       COUNT(*) AS alarm_count,
       SUM(CASE WHEN state = 'ACKNOWLEDGED' THEN 1 ELSE 0 END) AS acknowledged,
       SUM(CASE WHEN state = 'ACTIVE' THEN 1 ELSE 0 END)       AS unacknowledged
FROM alarms
WHERE state <> 'CLEARED'
GROUP BY severity
ORDER BY CASE severity WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 ELSE 3 END;"""),

    Query("equipment_alarm_counts", "Equipment with the most alarms",
          "Alarm totals per line (LEFT JOIN keeps lines with zero alarms).",
          """
SELECT l.code AS line_code, l.name AS line_name, l.nominal_kv,
       COUNT(a.id) AS total_alarms,
       SUM(CASE WHEN a.peak_severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical,
       SUM(CASE WHEN a.peak_severity = 'HIGH' THEN 1 ELSE 0 END)     AS high,
       SUM(CASE WHEN a.peak_severity = 'WARNING' THEN 1 ELSE 0 END)  AS warning
FROM transmission_lines l
LEFT JOIN alarms a ON a.line_id = l.id AND a.raised_at >= :since
GROUP BY l.id, l.code, l.name, l.nominal_kv
ORDER BY total_alarms DESC, critical DESC, l.code
LIMIT :limit;""", {"since": _SINCE, "limit": 12}),

    Query("alarm_frequency", "Alarm frequency by type and severity",
          "Count and mean duration (simulated minutes) of alarms, grouped by type and the worst severity each reached.",
          """
SELECT alarm_type, peak_severity AS severity,
       COUNT(*) AS alarm_count,
       ROUND(AVG(CASE WHEN cleared_at IS NOT NULL
                      THEN (julianday(cleared_at) - julianday(raised_at)) * 1440.0 END), 1)
                AS avg_duration_min
FROM alarms
WHERE raised_at >= :since
GROUP BY alarm_type, peak_severity
ORDER BY alarm_count DESC, alarm_type;""", {"since": _SINCE}),

    Query("alarms_by_hour", "Alarms raised per simulated hour",
          "Alarm frequency over time, bucketed by hour with strftime().",
          """
SELECT strftime('%Y-%m-%d %H:00:00', raised_at) AS hour,
       COUNT(*) AS alarms_raised,
       SUM(CASE WHEN peak_severity = 'CRITICAL' THEN 1 ELSE 0 END) AS critical
FROM alarms
WHERE raised_at >= :since
GROUP BY hour
ORDER BY hour;""", {"since": _SINCE}),

    Query("voltage_abnormalities", "Historical voltage abnormalities",
          "In-service samples outside the warning voltage band (limits come from the alarm thresholds).",
          """
SELECT m.timestamp, l.code AS line_code, l.nominal_kv,
       ROUND(m.voltage_kv, 2) AS voltage_kv, ROUND(m.voltage_pu, 4) AS voltage_pu,
       CASE WHEN m.voltage_pu < :v_low THEN 'UNDERVOLTAGE' ELSE 'OVERVOLTAGE' END AS condition
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.status = 'IN_SERVICE'
  AND (m.voltage_pu < :v_low OR m.voltage_pu > :v_high)
  AND m.timestamp >= :since
ORDER BY m.timestamp DESC
LIMIT :limit;""", {"v_low": 0.95, "v_high": 1.05, "since": _SINCE, "limit": 500}),

    Query("voltage_deviation", "Voltage deviation by line",
          "Per-unit voltage statistics and worst deviation from 1.00 pu (in-service samples).",
          """
SELECT l.code AS line_code, l.nominal_kv,
       ROUND(AVG(m.voltage_pu), 4) AS avg_pu,
       ROUND(MIN(m.voltage_pu), 4) AS min_pu,
       ROUND(MAX(m.voltage_pu), 4) AS max_pu,
       ROUND(MAX(ABS(m.voltage_pu - 1.0)) * 100.0, 2) AS max_deviation_pct
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.status = 'IN_SERVICE' AND m.timestamp >= :since
GROUP BY l.id, l.code, l.nominal_kv
ORDER BY max_deviation_pct DESC;""", {"since": _SINCE}),

    Query("offline_equipment", "Offline equipment",
          "Lines currently out of service, their terminals, and when the outage began.",
          """
SELECT l.code AS line_code, l.name AS line_name, l.nominal_kv,
       fs.name AS from_substation, ts.name AS to_substation,
       (SELECT MAX(e.timestamp) FROM simulation_events e
         WHERE e.line_id = l.id AND e.event_type = 'LINE_TRIPPED') AS outage_since
FROM transmission_lines l
JOIN substations fs ON fs.id = l.from_substation_id
JOIN substations ts ON ts.id = l.to_substation_id
WHERE l.status = 'OUT_OF_SERVICE'
ORDER BY l.nominal_kv DESC, l.code;"""),

    Query("substation_summary", "Substation connectivity summary",
          "Lines terminating at each substation with total thermal capacity (GROUP BY ... HAVING).",
          """
SELECT s.code AS substation, s.name, s.voltage_levels_kv AS levels_kv,
       COUNT(l.id) AS connected_lines,
       SUM(CASE WHEN l.status = 'OUT_OF_SERVICE' THEN 1 ELSE 0 END) AS lines_out,
       ROUND(SUM(1.7320508 * l.nominal_kv * l.rated_current_a / 1000.0), 0) AS connected_capacity_mva
FROM substations s
JOIN transmission_lines l ON l.from_substation_id = s.id OR l.to_substation_id = s.id
GROUP BY s.id, s.code, s.name, s.voltage_levels_kv
HAVING COUNT(l.id) >= 1
ORDER BY connected_capacity_mva DESC;"""),

    Query("substations", "Substation register",
          "Substation reference data (layout coordinates are schematic).",
          """
SELECT id, code, name, region, voltage_levels_kv, max_voltage_kv, map_x, map_y
FROM substations
ORDER BY max_voltage_kv DESC, code;"""),

    Query("active_overrides", "Injected abnormal conditions",
          "Operator-injected overloads / voltage abnormalities currently applied to lines.",
          """
SELECT l.code AS line_code, o.kind AS condition,
       CASE o.kind WHEN 'OVERLOAD' THEN ROUND(o.magnitude, 1) || ' % loading'
                   ELSE ROUND(o.magnitude, 3) || ' pu voltage' END AS target,
       o.applied_at
FROM simulation_overrides o
JOIN transmission_lines l ON l.id = o.line_id
ORDER BY o.applied_at DESC, l.code;"""),

    Query("recent_events", "Operational event log",
          "Most recent operational events with the affected line.",
          """
SELECT e.timestamp, e.event_type, COALESCE(l.code, '-') AS line_code, e.description
FROM simulation_events e
LEFT JOIN transmission_lines l ON l.id = e.line_id
ORDER BY e.id DESC
LIMIT :limit;""", {"limit": 50}),

    Query("alarm_list", "Alarm register",
          "All alarms with line context, newest first.",
          """
SELECT a.id AS alarm_id, a.raised_at, a.severity, a.peak_severity, a.alarm_type, a.state,
       l.code AS line_code, l.nominal_kv, a.message,
       a.trigger_value, a.peak_value, a.latest_value, a.threshold_value, a.unit,
       a.acknowledged_by, a.acknowledged_at, a.cleared_at,
       ROUND(CASE WHEN a.cleared_at IS NOT NULL
                  THEN (julianday(a.cleared_at) - julianday(a.raised_at)) * 1440.0 END, 1) AS duration_min
FROM alarms a
JOIN transmission_lines l ON l.id = a.line_id
ORDER BY a.id DESC
LIMIT :limit;""", {"limit": 2000}),

    Query("loading_trend_hourly", "Hourly loading trend",
          "Average and maximum loading per line per simulated hour.",
          """
SELECT strftime('%Y-%m-%d %H:00:00', m.timestamp) AS hour, l.code AS line_code,
       ROUND(AVG(m.loading_pct), 2) AS avg_loading_pct,
       ROUND(MAX(m.loading_pct), 2) AS max_loading_pct
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE m.status = 'IN_SERVICE' AND m.timestamp >= :since
GROUP BY hour, l.id, l.code
ORDER BY hour, l.code;""", {"since": _SINCE}),

    Query("measurement_series", "Telemetry time series",
          "Raw telemetry for selected lines (ordered by time).",
          """
SELECT m.tick, m.timestamp, l.code AS line_code, l.nominal_kv, m.status,
       m.voltage_kv, m.voltage_pu, m.current_a, m.power_factor,
       m.p_mw, m.q_mvar, m.s_mva, m.loading_pct, m.data_source
FROM measurements m
JOIN transmission_lines l ON l.id = m.line_id
WHERE l.code IN :codes AND m.tick > :min_tick
ORDER BY m.tick, l.code;""", {"min_tick": -1}),
)}


def run_query(engine: Engine, key: str, **params) -> pd.DataFrame:
    """Execute a named report. Unknown keys raise KeyError; empty results give an empty frame."""
    q = QUERIES[key]
    merged = {**q.defaults, **params}
    wanted = set(re.findall(r":(\w+)", q.sql))
    merged = {k: v for k, v in merged.items() if k in wanted}
    for k, v in list(merged.items()):                 # datetimes -> sortable strings
        if isinstance(v, datetime):
            merged[k] = v.strftime("%Y-%m-%d %H:%M:%S")
    stmt = text(q.sql)
    if "codes" in wanted:
        merged["codes"] = list(merged.get("codes") or [""])
        stmt = stmt.bindparams(bindparam("codes", expanding=True))
    with engine.connect() as conn:
        df = pd.read_sql_query(stmt, conn, params=merged)
    for col in _DATE_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df


# ------------------------------------------------------------------ exports
def measurements_csv(engine: Engine, line_codes: list[str] | None = None,
                     since_tick: int = -1) -> str:
    """CSV text of telemetry (all lines when line_codes is None)."""
    if line_codes is None:
        line_codes = list(run_query(engine, "latest_measurements")["line_code"])
    df = run_query(engine, "measurement_series", codes=line_codes, min_tick=since_tick)
    buf = io.StringIO()
    df.to_csv(buf, index=False, float_format="%.6g")
    return buf.getvalue()


def alarms_csv(engine: Engine) -> str:
    df = run_query(engine, "alarm_list", limit=1_000_000)
    buf = io.StringIO()
    df.to_csv(buf, index=False, float_format="%.6g")
    return buf.getvalue()


def events_csv(engine: Engine) -> str:
    df = run_query(engine, "recent_events", limit=1_000_000)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()
