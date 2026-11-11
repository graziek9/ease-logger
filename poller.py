EASEE_USERNAME = "raf.gerits@telenet.be"  # Include country code for phone
EASEE_PASSWORD = "Rg17101969"


import csv
import os
import requests
from collections import deque
from datetime import datetime

import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go

# --- Configuration ---
#EASEE_USERNAME = "email"  # Include country code for phone
#EASEE_PASSWORD = "password"
REFRESH_INTERVAL_MS = 10000  # 10 seconds

BASE_URL = "https://api.easee.com"
LOGIN_URL = f"{BASE_URL}/api/accounts/login"
CHARGERS_URL = f"{BASE_URL}/api/chargers"

# History buffers
MAX_POINTS = 720  # 10s * 720 = 2 hours
time_labels = deque(maxlen=MAX_POINTS)
charger_power_history = deque(maxlen=MAX_POINTS)
import_power_history = deque(maxlen=MAX_POINTS)
export_power_history = deque(maxlen=MAX_POINTS)

latest = {
    "session_energy": 0.0,
    "total_energy": 0.0,
    "import_energy": 0.0,
    "export_energy": 0.0,
    "status": "Unknown",
}

# --- CSV Logging Configuration ---
CSV_FILE = "easee_log.csv"
CSV_FIELDS = [
    "timestamp",
    "charger_power_kw",
    "session_energy_kwh",
    "total_energy_kwh",
    "status",
    "import_power_kw",
    "export_power_kw",
    "import_energy_kwh",
    "export_energy_kwh",
]


def ensure_csv_header():
    """Create the CSV file with a header row if it doesn't exist yet."""
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writeheader()


def append_csv_row(row: dict):
    """Append one row to the CSV. Missing keys are written as empty strings."""
    with open(CSV_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})


class EaseeClient:
    def __init__(self, username, password):
        self.session = requests.Session()
        self.access_token = None
        self.username = username
        self.password = password

    def login(self):
        resp = self.session.post(LOGIN_URL, json={
            "userName": self.username,
            "password": self.password
        })
        resp.raise_for_status()
        data = resp.json()
        self.access_token = data["accessToken"]
        self.session.headers.update({"Authorization": f"Bearer {self.access_token}"})

    def get_chargers(self):
        resp = self.session.get(CHARGERS_URL)
        resp.raise_for_status()
        return resp.json()

    def get_observations(self, serial_number, obs_ids):
        url = f"{BASE_URL}/state/{serial_number}/observations"
        params = {"ids": obs_ids}
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


def get_products(client):
    resp = client.session.get(f"{BASE_URL}/api/accounts/products")
    resp.raise_for_status()
    return resp.json()


def find_equalizer_id(products):
    for product in products:
        if "equalizers" in product:
            for eq in product["equalizers"]:
                return eq.get("id")
    return None


def parse_observation(response, obs_id):
    if not response or "observations" not in response:
        return None
    for obs in response["observations"]:
        if obs.get("id") == obs_id:
            return obs.get("value")
    return None


def status_to_text(code):
    mapping = {0: "Offline", 1: "Idle", 2: "Connected", 3: "Charging",
               4: "Completed", 5: "Error", 6: "Ready"}
    return mapping.get(code, f"Unknown ({code})")


def dynamic_y_range(series_list, pad_ratio=0.05, fallback=(0, 1)):
    """Compute a tight y-axis range from one or more data series.
    Adds a small padding so points don't touch the edges."""
    values = [v for series in series_list for v in series if v is not None]
    if not values:
        return list(fallback)
    lo, hi = min(values), max(values)
    if lo == hi:
        # All values identical -> give a small window around it
        delta = max(abs(lo) * 0.1, 0.01)
        return [lo - delta, hi + delta]
    pad = (hi - lo) * pad_ratio
    return [lo - pad, hi + pad]


# --- Initialize client and discover devices (once) ---
print("Logging into Easee...")
ensure_csv_header()
client = EaseeClient(EASEE_USERNAME, EASEE_PASSWORD)
client.login()

chargers = client.get_chargers()
if not chargers:
    raise SystemExit("No chargers found.")
charger_serial = chargers[0]["id"]
charger_name = chargers[0].get("name", charger_serial)

products = get_products(client)
equalizer_serial = find_equalizer_id(products)
print(f"Charger: {charger_name} ({charger_serial}), Equalizer: {equalizer_serial}")


# --- Dash App ---
app = dash.Dash(__name__)
app.title = "Easee Live Dashboard"

app.layout = html.Div([
    html.H1("⚡ Easee Live Dashboard", style={"textAlign": "center"}),
    html.Div(id="kpi-cards", style={
        "display": "flex", "justifyContent": "space-around",
        "flexWrap": "wrap", "marginBottom": "20px"
    }),
    dcc.Graph(id="power-chart"),
    dcc.Graph(id="grid-chart"),
    dcc.Interval(id="interval-component",
                 interval=REFRESH_INTERVAL_MS, n_intervals=0),
], style={"maxWidth": "1200px", "margin": "0 auto", "padding": "20px"})


@app.callback(
    [Output("kpi-cards", "children"),
     Output("power-chart", "figure"),
     Output("grid-chart", "figure")],
    [Input("interval-component", "n_intervals")]
)
def update_dashboard(n):
    try:
        charger_obs = client.get_observations(charger_serial, "109,120,121,124")
        equalizer_obs = client.get_observations(equalizer_serial, "40,41,45,46") if equalizer_serial else None

        c_power = parse_observation(charger_obs, 120)
        c_session = parse_observation(charger_obs, 121)
        c_total = parse_observation(charger_obs, 124)
        c_status = parse_observation(charger_obs, 109)

        e_import_p = parse_observation(equalizer_obs, 40) if equalizer_obs else None
        e_export_p = parse_observation(equalizer_obs, 41) if equalizer_obs else None
        e_import_e = parse_observation(equalizer_obs, 45) if equalizer_obs else None
        e_export_e = parse_observation(equalizer_obs, 46) if equalizer_obs else None

        if c_power is not None:
            charger_power_history.append(c_power)
        if e_import_p is not None:
            import_power_history.append(e_import_p)
        if e_export_p is not None:
            export_power_history.append(e_export_p)
        time_labels.append(datetime.now().strftime("%H:%M:%S"))

        latest["session_energy"] = c_session or 0
        latest["total_energy"] = c_total or 0
        latest["import_energy"] = e_import_e or 0
        latest["export_energy"] = e_export_e or 0
        latest["status"] = status_to_text(c_status) if c_status is not None else "Unknown"

        # --- Log to CSV ---
        append_csv_row({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "charger_power_kw": c_power,
            "session_energy_kwh": c_session,
            "total_energy_kwh": c_total,
            "status": latest["status"],
            "import_power_kw": e_import_p,
            "export_power_kw": e_export_p,
            "import_energy_kwh": e_import_e,
            "export_energy_kwh": e_export_e,
        })

    except Exception as e:
        print(f"Fetch error: {e}")

    # KPI cards
    def card(label, value, unit=""):
        return html.Div([
            html.Div(label, style={"fontSize": "14px", "color": "#888"}),
            html.Div(f"{value} {unit}", style={"fontSize": "24px", "fontWeight": "bold"}),
        ], style={
            "background": "#f8f9fa", "borderRadius": "8px",
            "padding": "12px 20px", "margin": "8px",
            "minWidth": "160px", "textAlign": "center",
            "boxShadow": "0 1px 3px rgba(0,0,0,0.1)"
        })

    kpi_cards = [
        card("Status", latest["status"]),
        card("Session Energy", f"{latest['session_energy']:.1f}", "kWh"),
        card("Total Charged", f"{latest['total_energy']:.0f}", "kWh"),
        card("Imported Energy", f"{latest['import_energy']:.0f}", "kWh"),
        card("Exported Energy", f"{latest['export_energy']:.0f}", "kWh"),
    ]

    x_labels = list(time_labels)
    y_charger = list(charger_power_history)
    y_import = list(import_power_history)
    y_export = list(export_power_history)

    # --- Charging power chart ---
    power_fig = go.Figure()
    power_fig.add_trace(go.Scatter(
        x=x_labels, y=y_charger,
        mode="lines+markers", name="Charging Power (kW)",
        line=dict(color="#2ecc71", width=2)
    ))
    power_fig.update_layout(
        title="Charging Power Trend",
        yaxis_title="kW", xaxis_title="Time",
        margin=dict(l=40, r=20, t=40, b=30),
        template="plotly_white",
        yaxis=dict(range=dynamic_y_range([y_charger])),
        uirevision="keep-zoom"  # keep user zoom/pan across refreshes
    )

    # --- Grid chart ---
    grid_fig = go.Figure()
    grid_fig.add_trace(go.Scatter(
        x=x_labels, y=y_import,
        mode="lines", name="Import (kW)",
        line=dict(color="#3498db")
    ))
    grid_fig.add_trace(go.Scatter(
        x=x_labels, y=y_export,
        mode="lines", name="Export (kW)",
        line=dict(color="#e67e22")
    ))
    grid_fig.update_layout(
        title="Grid Import / Export Power",
        yaxis_title="kW", xaxis_title="Time",
        margin=dict(l=40, r=20, t=40, b=30),
        template="plotly_white",
        yaxis=dict(range=dynamic_y_range([y_import, y_export])),
        uirevision="keep-zoom"
    )

    return kpi_cards, power_fig, grid_fig


if __name__ == "__main__":
    print("Dashboard running at http://127.0.0.1:8050")
    app.run(debug=False, host="127.0.0.1", port=8050)
