import pandas as pd

import dash
from dash import dcc, html, Input, Output
import plotly.graph_objs as go

CSV_FILE = "easee_log.csv"
REFRESH_INTERVAL_MS = 10_000  # 10 seconds

app = dash.Dash(__name__)
app.title = "Easee Log Viewer"

app.layout = html.Div([
    html.H1("⚡ Easee Log Viewer", style={"textAlign": "center"}),
    html.Div(id="kpi-cards", style={
        "display": "flex", "justifyContent": "space-around",
        "flexWrap": "wrap", "marginBottom": "20px"
    }),
    dcc.Graph(id="power-chart"),
    dcc.Graph(id="grid-chart"),
    dcc.Interval(id="interval-component",
                 interval=REFRESH_INTERVAL_MS, n_intervals=0),
], style={"maxWidth": "1200px", "margin": "0 auto", "padding": "20px"})


def dynamic_y_range(series_list, pad_ratio=0.05, fallback=(0, 1)):
    """Tight y-axis range from one or more series; ignores NaN/None."""
    vals = [v for s in series_list for v in s if pd.notna(v)]
    if not vals:
        return list(fallback)
    lo, hi = min(vals), max(vals)
    if lo == hi:
        d = max(abs(lo) * 0.1, 0.01)
        return [lo - d, hi + d]
    pad = (hi - lo) * pad_ratio
    return [lo - pad, hi + pad]


@app.callback(
    [Output("kpi-cards", "children"),
     Output("power-chart", "figure"),
     Output("grid-chart", "figure")],
    [Input("interval-component", "n_intervals")]
)
def update(_):
    try:
        df = pd.read_csv(CSV_FILE)
    except Exception:
        df = pd.DataFrame()

    def card(label, value, unit=""):
        return html.Div([
            html.Div(label, style={"fontSize": "14px", "color": "#888"}),
            html.Div(f"{value} {unit}",
                     style={"fontSize": "24px", "fontWeight": "bold"}),
        ], style={
            "background": "#f8f9fa", "borderRadius": "8px",
            "padding": "12px 20px", "margin": "8px",
            "minWidth": "160px", "textAlign": "center",
            "boxShadow": "0 1px 3px rgba(0,0,0,0.1)"
        })

    if df.empty:
        empty = go.Figure()
        empty.update_layout(title="No data yet — waiting for the first log rows.",
                            template="plotly_white")
        return [], empty, empty

    last = df.iloc[-1]

    def fmt(key, digits):
        v = last.get(key)
        try:
            return f"{float(v):.{digits}f}"
        except (TypeError, ValueError):
            return "—"

    kpi_cards = [
        card("Status", last.get("status", "Unknown")),
        card("Session Energy", fmt("session_energy_kwh", 1), "kWh"),
        card("Total Charged", fmt("total_energy_kwh", 0), "kWh"),
        card("Imported Energy", fmt("import_energy_kwh", 0), "kWh"),
        card("Exported Energy", fmt("export_energy_kwh", 0), "kWh"),
    ]

    tail = df.tail(720)  # last ~2 hours at 10 s cadence
    x = tail["timestamp"]

    power_fig = go.Figure()
    power_fig.add_trace(go.Scatter(
        x=x, y=tail["charger_power_kw"],
        mode="lines+markers", name="Charging Power (kW)",
        line=dict(color="#2ecc71", width=2)
    ))
    power_fig.update_layout(
        title="Charging Power Trend",
        yaxis_title="kW", xaxis_title="Time",
        margin=dict(l=40, r=20, t=40, b=30),
        template="plotly_white",
        yaxis=dict(range=dynamic_y_range([tail["charger_power_kw"]])),
        uirevision="keep-zoom"
    )

    grid_fig = go.Figure()
    grid_fig.add_trace(go.Scatter(
        x=x, y=tail["import_power_kw"],
        mode="lines", name="Import (kW)",
        line=dict(color="#3498db")
    ))
    grid_fig.add_trace(go.Scatter(
        x=x, y=tail["export_power_kw"],
        mode="lines", name="Export (kW)",
        line=dict(color="#e67e22")
    ))
    grid_fig.update_layout(
        title="Grid Import / Export Power",
        yaxis_title="kW", xaxis_title="Time",
        margin=dict(l=40, r=20, t=40, b=30),
        template="plotly_white",
        yaxis=dict(range=dynamic_y_range(
            [tail["import_power_kw"], tail["export_power_kw"]]
        )),
        uirevision="keep-zoom"
    )

    return kpi_cards, power_fig, grid_fig


if __name__ == "__main__":
    print("Dashboard running at http://127.0.0.1:8050")
    app.run(debug=False, host="127.0.0.1", port=8050)