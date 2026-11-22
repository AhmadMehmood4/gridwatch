
# ⚡ GridWatch — Transmission Grid Monitoring & Alert System

**A Python-based, interactive dashboard for monitoring a simulated high-voltage transmission network, detecting operating abnormalities, and reviewing historical system performance.**

[![Live Demo](https://img.shields.io/badge/Live_Demo-Open_GridWatch-16A6D5?style=for-the-badge)](https://gridwatch-transmission.streamlit.app/)
[![Python](https://img.shields.io/badge/Python-3.x-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)](https://streamlit.io/)
[![SQLite](https://img.shields.io/badge/SQLite-Historical_Data-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://www.sqlite.org/)

**[Launch the live dashboard](https://gridwatch-transmission.streamlit.app/)** · **[View the source code](https://github.com/AhmadMehmood4/gridwatch)**

![GridWatch operations overview](images/01_operations_overview.png)

> **Important:** GridWatch is an educational simulator using synthetic telemetry and a fictional Ontario-inspired transmission layout. It is not connected to a utility's live grid or SCADA/EMS infrastructure. Measurements are simulated and internally consistent; they are not the result of a validated AC power-flow study.

---

## Project Overview

GridWatch was developed to explore how transmission system operating data can be presented in a centralized monitoring interface.

Instead of inspecting individual measurements manually, an operator can use the dashboard to monitor transmission-line loading, identify abnormal conditions, acknowledge alarms, inspect equipment status, and review historical events.

The simulated network contains:

- **8 substations**
- **12 transmission lines**
- **500 kV, 230 kV, and 115 kV voltage classes**
- **Automated fault detection and alarm management**
- **Historical telemetry storage and SQL analytics**

A controllable simulation generates operating measurements and allows users to inject faults for repeatable testing.

## Key Features

### 1. Operations Overview

The main dashboard provides a summary of the simulated transmission network, including:

- Transmission-line loading percentages
- Equipment availability
- Active warnings and critical alarms
- System operating status
- Operational event history
- Highest-loaded transmission lines

This allows users to identify abnormal operating conditions without examining every line individually.

### 2. Interactive Transmission Network Map

The network map displays a schematic representation of the transmission system.

Users can select individual transmission lines to view:

- Nominal and measured voltage
- Current and rated-current limit
- Loading percentage
- Active power (MW)
- Reactive power (MVAr)
- Apparent power (MVA)
- Equipment operating status
- Associated alarms

![Transmission network map](images/02_transmission_network.png)

### 3. Automated Overload Detection

GridWatch monitors transmission-line loading and generates alarms when predefined thresholds are exceeded.

The overload test was performed on **TL-01 (Georgian Bay – Lake Huron, 500 kV)**, which has a rated-current limit of **2,000 A**.

| Severity | Target Loading | Actual Loading | Threshold |
|---|---:|---:|---|
| Warning | 85% | 85.6% | Above 80% |
| High | 95% | 95.6% | Above 90% |
| Critical | 105% | 103.9% | Above 100% |

#### Warning Detection

At 85.6% loading, GridWatch automatically generated a Warning alarm.

![Warning alarm](images/03_overload_warning.png)

#### High Severity Detection

As loading increased to 95.6%, the same alarm escalated from Warning to High.

![High overload alarm](images/04_overload_high.png)

#### Critical Severity Detection

At 103.9% loading, GridWatch classified the condition as Critical.

![Critical overload alarm](images/05_overload_critical.png)

The network map also highlighted TL-01 in red.

At this stage, the line was carrying **2,078 A**, exceeding its rated-current limit of 2,000 A.

![Critical network visualization](images/06_critical_network.png)

The test demonstrated automated threshold monitoring, alarm escalation, and synchronized network visualization.

### 4. Alarm Acknowledgement and Recovery

GridWatch allows operators to acknowledge alarms while maintaining visibility of unresolved equipment conditions.

During testing, the Critical alarm associated with TL-01 was acknowledged.

The system correctly:

- Updated the alarm state to Acknowledged
- Reduced the unacknowledged alarm counter
- Preserved the Critical severity
- Maintained the alarm in the active alarm list
- Kept TL-01 highlighted in red
- Recorded the acknowledgement in alarm history

![Acknowledged alarm](images/07_alarm_acknowledged.png)

![Acknowledged alarm on network map](images/08_acknowledged_network.png)

![Alarm history](images/09_alarm_history.png)

This demonstrates that acknowledging an alarm does not automatically resolve the underlying fault.

#### Fault Recovery

After reducing TL-01 loading to approximately 60%, GridWatch automatically cleared the overload alarm.

The transmission line returned to its normal green appearance, while the alarm history preserved the earlier Critical condition.

![Recovered transmission line](images/10_fault_recovery.png)

### 5. Transmission-Line Outage Detection

GridWatch also supports simulated transmission-line outages.

During testing, TL-01 was taken out of service.

The system automatically:

- Generated a Critical outage alarm
- Marked TL-01 as Out of Service
- Displayed the affected line using a grey dashed connection
- Reduced its simulated current and power to zero
- Updated equipment status and availability

![Outage alarm](images/11_outage_alarm.png)

![Transmission-line outage](images/12_line_outage_network.png)

After restoring the line, GridWatch returned TL-01 to service at approximately 60.1% loading.

The outage alarm was cleared, and the event remained available in historical records.

![Restored transmission line](images/13_restored_network.png)

> The network map illustrates equipment connectivity and operating status. It does not perform contingency power-flow calculations or automatically redistribute transmission flows.

### 6. Undervoltage Detection

GridWatch detects abnormal voltage conditions when measured voltage falls below configured thresholds.

An undervoltage test was performed on TL-01.

The simulated voltage decreased to:

- **451.4 kV**
- **0.903 pu**
- Configured alarm threshold: **0.920 pu**

GridWatch generated a High severity undervoltage alarm.

![Undervoltage alarm](images/14_undervoltage_alarm.png)

The abnormal voltage was also displayed in the selected equipment details.

![Undervoltage network visualization](images/15_undervoltage_network.png)

After restoring the voltage to approximately 499.7 kV, the undervoltage alarm automatically cleared.

![Voltage restored](images/16_voltage_recovered.png)

The line remained connected throughout the test because an undervoltage condition does not necessarily indicate an equipment outage.

### 7. Historical Analytics and SQL Reporting

GridWatch stores simulated operating measurements and alarm records in a SQLite database.

The Historical Analytics dashboard uses SQL queries to calculate equipment performance statistics and identify abnormal operating conditions.

At the end of the test sequence, the system recorded:

| Historical Metric | Result |
|---|---:|
| Telemetry samples analyzed | 3,120 |
| Total alarms raised | 6 |
| Highest loading | 103.9% |
| Fleet average loading | 52.0% |
| Lines exceeding 90% loading | 1 |
| TL-01 equipment availability | 99.6% |
| TL-01 intervals above 90% | 2 |

#### Historical Loading Trends

GridWatch displays a heatmap of peak transmission-line loading across simulated operating hours.

![Historical loading heatmap](images/17_historical_overview.png)

It also compares average and maximum loading across the network.

![Average and peak loading](images/18_loading_comparison.png)

#### Equipment Utilisation

The Utilisation dashboard compares average apparent power against the thermal rating of each transmission line.

![Equipment utilisation](images/19_utilisation.png)

#### Voltage Deviations

Historical voltage analysis identifies measurements outside the configured normal operating band.

The undervoltage event on TL-01 was successfully retained at approximately 0.903 pu.

![Historical voltage deviations](images/20_voltage_deviations.png)

#### Alarm Frequency Analysis

GridWatch groups historical alarms by:

- Alarm type
- Severity
- Equipment
- Simulation time
- Alarm duration

![Alarm frequency analysis](images/21_alarm_frequency.png)

#### SQL Reports and CSV Export

GridWatch includes parameterized SQL reports executed against the SQLite database through SQLAlchemy.

Reports can retrieve operating measurements, equipment information, and historical performance data.

The application also provides a CSV download option for exporting query results.

![SQL reports](images/22_sql_reports.png)

---

## Project Validation

GridWatch was evaluated through seven functional demonstration tests using the same simulation history.

| Test | Description | Result |
|---|---|---|
| 1 | Baseline system monitoring | Completed |
| 2 | Transmission-line overload detection | Passed |
| 3 | Alarm acknowledgement and persistence | Passed |
| 4 | Fault recovery and alarm clearing | Passed |
| 5 | Transmission-line outage and restoration | Passed |
| 6 | Undervoltage detection and recovery | Passed |
| 7 | Historical analytics and SQL reporting | Passed for demonstrated views |

The results demonstrate that GridWatch can identify simulated operating abnormalities, maintain alarm states, record equipment recovery, and preserve historical data for later analysis.

These tests validate the demonstrated dashboard functionality using synthetic telemetry. They do not constitute validation of an actual transmission grid model or production monitoring system.

---

## Technologies Used

| Technology | Purpose |
|---|---|
| Python | Application development and simulation logic |
| Streamlit | Interactive web dashboard |
| SQLite | Historical telemetry and alarm storage |
| SQL | Data retrieval, filtering, and analysis |
| SQLAlchemy | Database access and reporting |
| Git | Version control |
| GitHub | Source code hosting |
| Streamlit Community Cloud | Public application deployment |

## Repository Structure

```text
gridwatch/
├── app.py
├── gridwatch/
│   ├── __init__.py
│   ├── alarms.py
│   ├── analytics.py
│   ├── calculations.py
│   ├── config.py
│   ├── database.py
│   ├── events.py
│   ├── network.py
│   ├── simulator.py
│   ├── telemetry.py
│   └── ui/
├── tests/
├── data/
├── .streamlit/
├── requirements.txt
├── Launch_GridWatch.bat
├── images/
└── README.md
```

---

## Installation and Setup

### 1. Clone the Repository

```bash
git clone https://github.com/AhmadMehmood4/gridwatch.git
cd gridwatch
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv
```

Activate the environment on Windows:

```powershell
.venv\Scripts\activate
```

For macOS or Linux:

```bash
source .venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Run GridWatch

```bash
streamlit run app.py
```

The dashboard will open in your web browser.

Windows users can also launch the application using the included `Launch_GridWatch.bat` file.

### Live Application

GridWatch is publicly deployed using Streamlit Community Cloud.

**[Open GridWatch](https://gridwatch-transmission.streamlit.app/)**

---

## Project Scope and Limitations

GridWatch was developed as an educational engineering and software project.

The following limitations apply:

- The transmission network is fictional and inspired by Ontario geography.
- Measurements are generated using synthetic telemetry rather than collected from operational equipment.
- Electrical measurements use simplified calculations and are not outputs of a validated AC power-flow model.
- The network diagram represents equipment connections but does not perform transmission contingency analysis.
- Fault injection is intended for demonstrating monitoring and alarm-management behaviour.
- The application is not connected to live utility infrastructure or SCADA/EMS systems.

These limitations keep the project focused on monitoring workflows, software development, and historical data analysis.

## What I Learned

Developing GridWatch provided practical experience in several areas of electrical engineering and software development.

**Transmission System Monitoring**

Working with transmission-line loading, current limits, voltage classes, equipment status, and abnormal operating conditions.

**Python Development**

Organizing a larger Python application into reusable modules for simulation, monitoring, alarm processing, and analytics.

**SQL and Database Management**

Storing telemetry and event records, retrieving historical measurements, and calculating equipment performance statistics through SQL queries.

**Automated Alarm Management**

Implementing configurable thresholds, alarm severity escalation, operator acknowledgement, automatic clearing, and historical tracking.

**Data Visualization**

Presenting operational measurements through interactive dashboards, network diagrams, loading charts, and historical reports.

**Software Testing and Deployment**

Performing controlled functional tests, documenting results, using Git for version control, and deploying the application online.

---

## Future Improvements

Potential future enhancements include:

- Integration with sample SCADA-style data feeds
- More advanced network topology analysis
- Power-flow simulations using established engineering libraries
- Automated reporting of operating events
- Additional database queries and performance indicators
- Expanded automated testing
- More advanced fault scenarios and equipment monitoring

---

## Author

**Ahmad Mehmood**

Electrical Engineering  
Toronto Metropolitan University

- **GitHub:** [AhmadMehmood4](https://github.com/AhmadMehmood4)
- **Live Project:** [GridWatch Dashboard](https://gridwatch-transmission.streamlit.app/)

---

*GridWatch is a personal engineering portfolio project developed for educational and technical demonstration purposes.*
