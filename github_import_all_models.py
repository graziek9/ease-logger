import pandas as pd
import numpy as np
from collections import deque

# ============================================================================
# ==============================  CONFIG  ====================================
# ============================================================================
# --- Data source ------------------------------------------------------------
URL = "https://raw.githubusercontent.com/graziek9/ease-logger/main/easee_log.csv"

# --- Electrical model -------------------------------------------------------
PHASES         = 3          # 1 or 3
VOLTAGE        = 230.0      # V per phase
MIN_A          = 6          # minimum charging current (A) — 6 A is the IEC minimum
MAX_A          = 16         # maximum charging current (A)

# --- Smoothing & buffer -----------------------------------------------------
EMA_HALFLIFE_S = 60.0       # exponential-moving-average half-life (seconds)
BUFFER_KW      = 0.2        # safety margin subtracted from surplus (kW)

# --- Hysteresis & timing ----------------------------------------------------
START_ABOVE_S  = 150        # surplus must stay above MIN for this long to START (s)
STOP_BELOW_S   = 300        # surplus must stay below MIN for this long to STOP  (s)
UPDATE_EVERY_S = 60         # minimum time between setpoint changes           (s)
RAMP_MAX_A     = 1          # max |ΔA| per setpoint update                    (A)
MIN_ON_S       = 300        # minimum continuous run time                     (s)
MIN_OFF_S      = 300        # minimum pause after a stop                      (s)

# --- Modes to simulate ------------------------------------------------------
# Options: "solar_only", "min_solar", "fast", "cheap_tariff", "deadline"
MODES = ["solar_only", "min_solar", "fast", "cheap_tariff", "deadline"]

# --- Cheap-tariff mode ------------------------------------------------------
CHEAP_HOURS = list(range(1, 6)) + list(range(13, 16))   # 01–05 and 13–15

# --- Deadline mode ----------------------------------------------------------
DEADLINE_TS  = None          # e.g. pd.Timestamp("2026-11-18 07:00:00")
DEADLINE_KWH = None          # e.g. 20.0

# --- Output -----------------------------------------------------------------
CSV_OUT        = "easee_simulation.csv"   # always written
EXPORT_EXCEL   = False                    # optional extra Excel file
EXCEL_BASENAME = "easee_simulation"

# ============================================================================
# ==============================  END CONFIG  ================================
# ============================================================================


# ---- Derived constants (do not edit) ---------------------------------------
W_PER_A        = VOLTAGE * PHASES                    # 690 W on 3 phases
CHARGER_KW_MAX = MAX_A * W_PER_A / 1000.0            # 11.04 kW


# ---- Load data -------------------------------------------------------------
df = pd.read_csv(URL, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

# ============================================================================
# 1) SURPLUS
# ============================================================================
df["surplus_kw"] = df["charger_power_kw"] + df["export_power_kw"] - df["import_power_kw"]

# ============================================================================
# 2) AMPS from surplus
# ============================================================================
df["surplus_amps"]         = df["surplus_kw"] * 1000.0 / W_PER_A
df["surplus_amps_clipped"] = df["surplus_amps"].clip(lower=MIN_A, upper=MAX_A)

# ============================================================================
# 3) STABILITY: EMA smoothing (time-aware)
# ============================================================================
df["surplus_ema_kw"] = (
    df.set_index("timestamp")["surplus_kw"]
      .ewm(halflife=pd.Timedelta(seconds=EMA_HALFLIFE_S),
           times=df["timestamp"])
      .mean()
      .values
)
df["surplus_ema_amps"] = df["surplus_ema_kw"] * 1000.0 / W_PER_A


# ---- Helper: cheap-hours check ---------------------------------------------
def is_cheap(ts):
    return ts.hour in CHEAP_HOURS


# ---- Stateful simulation ---------------------------------------------------
state = {
    "on": False, "amps": 0, "t_changed": None,
    "above_since": None, "below_since": None,
    "last_update": None, "kwh_delivered": 0.0,
}


def step(ts, surplus_ema_kw, mode="solar_only"):
    since_change = (ts - state["t_changed"]).total_seconds() if state["t_changed"] else 1e9
    usable_kw    = surplus_ema_kw - BUFFER_KW
    usable_amps  = usable_kw * 1000.0 / W_PER_A

    # ---- mode-specific target amps ----
    if mode == "fast":
        target = MAX_A
    elif mode == "min_solar":
        target = max(MIN_A, min(MAX_A, np.floor(usable_amps)))
    elif mode == "cheap_tariff":
        target = MAX_A if is_cheap(ts) else max(0, min(MAX_A, np.floor(usable_amps)))
    elif mode == "deadline":
        if DEADLINE_TS is None or DEADLINE_KWH is None:
            target = max(0, min(MAX_A, np.floor(usable_amps)))
        else:
            remaining_s      = (DEADLINE_TS - ts).total_seconds()
            remaining_kwh    = max(0.0, DEADLINE_KWH - state["kwh_delivered"])
            if remaining_s <= 0:
                target = 0
            else:
                required_kw   = remaining_kwh * 3600.0 / remaining_s
                solar_amps    = max(0, min(MAX_A, np.floor(usable_amps)))
                required_amps = min(MAX_A, np.ceil(required_kw * 1000.0 / W_PER_A))
                target        = max(solar_amps, required_amps)
    else:  # "solar_only"
        target = max(0, min(MAX_A, np.floor(usable_amps)))

    if 0 < target < MIN_A:
        target = MIN_A

    # ---- on/off hysteresis ----
    start_ok = usable_amps >= MIN_A
    stay_ok  = usable_amps >= MIN_A - (BUFFER_KW * 1000.0 / W_PER_A)

    state["above_since"] = (state["above_since"] or ts) if start_ok else None
    state["below_since"] = None if stay_ok else (state["below_since"] or ts)

    if not state["on"]:
        forced = (mode in ("fast", "min_solar", "cheap_tariff", "deadline")
                  and target >= MIN_A and since_change >= MIN_OFF_S)
        timed  = (state["above_since"]
                  and (ts - state["above_since"]).total_seconds() >= START_ABOVE_S
                  and since_change >= MIN_OFF_S)
        if forced or timed:
            state["on"], state["amps"], state["t_changed"], state["last_update"] = True, MIN_A, ts, ts
    else:
        if (state["below_since"]
                and (ts - state["below_since"]).total_seconds() >= STOP_BELOW_S
                and since_change >= MIN_ON_S
                and mode != "fast"):
            state["on"], state["amps"], state["t_changed"] = False, 0, ts

    # ---- setpoint updates ----
    if state["on"] and state["last_update"] and (ts - state["last_update"]).total_seconds() >= UPDATE_EVERY_S:
        step_a   = max(-RAMP_MAX_A, min(RAMP_MAX_A, target - state["amps"]))
        new_amps = max(0, min(MAX_A, int(state["amps"] + step_a)))
        if 0 < new_amps < MIN_A:
            new_amps = MIN_A
        state["amps"] = new_amps
        state["last_update"] = ts
    elif state["on"] and state["last_update"] is None:
        state["last_update"] = ts

    return state["amps"] if state["on"] else 0


def run_mode(mode):
    for k, v in {"on": False, "amps": 0, "t_changed": None,
                 "above_since": None, "below_since": None,
                 "last_update": None, "kwh_delivered": 0.0}.items():
        state[k] = v
    return pd.Series([step(ts, s, mode=mode)
                      for ts, s in zip(df["timestamp"], df["surplus_ema_kw"])],
                     index=df.index)


# ---- Run each mode ---------------------------------------------------------
for mode in MODES:
    cmd = run_mode(mode)
    df[f"cmd_a_{mode}"]  = cmd
    df[f"cmd_kw_{mode}"] = cmd * W_PER_A / 1000.0
    df[f"kwh_{mode}"]    = df[f"cmd_kw_{mode}"] * df["timestamp"].diff().dt.total_seconds().fillna(0) / 3600.0

# Default = solar_only for the unsuffixed convenience columns
if "solar_only" in MODES:
    df["cmd_a_default"]  = df["cmd_a_solar_only"]
    df["cmd_kw_default"] = df["cmd_kw_solar_only"]
else:
    df["cmd_a_default"]  = df[f"cmd_a_{MODES[0]}"]
    df["cmd_kw_default"] = df[f"cmd_kw_{MODES[0]}"]

df["car_charging"]  = (df["cmd_a_default"] > 0).astype(int)
df["grid_part_kw"]  = (df["cmd_kw_default"] - df["surplus_kw"].clip(lower=0)).clip(lower=0)
df["solar_part_kw"] = df["cmd_kw_default"] - df["grid_part_kw"]

# ---- Preview ---------------------------------------------------------------
print(df[[
    "timestamp",
    "surplus_kw", "surplus_ema_kw",
    "surplus_amps", "surplus_amps_clipped",
    "cmd_a_default", "cmd_kw_default",
    "grid_part_kw", "solar_part_kw",
]].head(20).to_string(index=False))

print("\nColumns in df:")
print(list(df.columns))

# ---- Always write CSV ------------------------------------------------------
df.round(4).to_csv(CSV_OUT, index=False)
print(f"\nWrote {CSV_OUT}")

