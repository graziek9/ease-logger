import pandas as pd
import numpy as np

# ============================================================================
# ==============================  CONFIG  ====================================
# ============================================================================
# --- Data source ------------------------------------------------------------
URL = "https://raw.githubusercontent.com/graziek9/ease-logger/main/easee_log.csv"

# --- Electrical model -------------------------------------------------------
PHASES         = 3          # 1 or 3
VOLTAGE        = 230.0      # V per phase
MIN_A          = 6          # minimum charging current (A)
MAX_A          = 16         # maximum charging current (A)

# --- Smoothing & buffer -----------------------------------------------------
EMA_HALFLIFE_S = 60.0       # EMA half-life (s)
BUFFER_KW      = 0.2        # safety margin on the solar surplus (kW)

# --- Grid tolerance ---------------------------------------------------------
# How much grid import (kW) is tolerated to START / STAY charging.
# IMPORTANT: this only affects the start/stay decision, NOT the target amps.
# Once charging, the setpoint tracks the actual solar surplus, so grid import
# fades toward zero as surplus grows.
#   0.0  -> pure solar only (never import)
#   2.5  -> start/keep charging when surplus >= MIN_KW - 2.5 kW
ALLOWED_IMPORT_KW = 2.5

# --- Hysteresis & timing ----------------------------------------------------
START_ABOVE_S  = 150        # must stay above start threshold this long to START (s)
STOP_BELOW_S   = 300        # must stay below stay threshold this long to STOP  (s)
UPDATE_EVERY_S = 60         # min time between setpoint changes                (s)
RAMP_MAX_A     = 1          # max |ΔA| per update                              (A)
MIN_ON_S       = 300        # min run time                                     (s)
MIN_OFF_S      = 300        # min pause after stop                             (s)

# --- Output -----------------------------------------------------------------
CSV_OUT = "easee_simulation.csv"

# ============================================================================
# ==============================  END CONFIG  ================================
# ============================================================================


# ---- Derived constants -----------------------------------------------------
W_PER_A  = VOLTAGE * PHASES                              # 690 W/A on 3 phases
MIN_KW   = MIN_A * W_PER_A / 1000.0                      # 4.14 kW physical minimum
START_KW = MIN_KW + BUFFER_KW - ALLOWED_IMPORT_KW        # effective start threshold
STAY_KW  = MIN_KW - BUFFER_KW - ALLOWED_IMPORT_KW        # effective stay threshold


# ---- Load data -------------------------------------------------------------
df = pd.read_csv(URL, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


# ---- Surplus ---------------------------------------------------------------
df["surplus_kw"]           = df["charger_power_kw"] + df["export_power_kw"] - df["import_power_kw"]
df["surplus_amps"]         = df["surplus_kw"] * 1000.0 / W_PER_A
df["surplus_amps_clipped"] = df["surplus_amps"].clip(lower=MIN_A, upper=MAX_A)


# ---- EMA smoothing (time-aware) --------------------------------------------
df["surplus_ema_kw"] = (
    df.set_index("timestamp")["surplus_kw"]
      .ewm(halflife=pd.Timedelta(seconds=EMA_HALFLIFE_S),
           times=df["timestamp"])
      .mean()
      .values
)
df["surplus_ema_amps"] = df["surplus_ema_kw"] * 1000.0 / W_PER_A


# ---- Solar-only controller with allowed grid import -----------------------
state = {
    "on": False, "amps": 0, "t_changed": None,
    "above_since": None, "below_since": None, "last_update": None,
}


def step(ts, surplus_ema_kw):
    since_change = (ts - state["t_changed"]).total_seconds() if state["t_changed"] else 1e9

    # ---- (1) THRESHOLD check: uses ALLOWED_IMPORT_KW ----
    usable_kw = surplus_ema_kw + ALLOWED_IMPORT_KW - BUFFER_KW
    start_ok  = usable_kw >= MIN_KW
    stay_ok   = usable_kw >= MIN_KW - BUFFER_KW

    # ---- (2) TARGET amps: raw surplus only (no ALLOWED_IMPORT padding) ----
    target_kw   = surplus_ema_kw - BUFFER_KW
    target_amps = max(0, min(MAX_A, np.floor(target_kw * 1000.0 / W_PER_A)))
    if 0 < target_amps < MIN_A:
        target_amps = MIN_A

    # ---- on/off hysteresis ----
    state["above_since"] = (state["above_since"] or ts) if start_ok else None
    state["below_since"] = None if stay_ok else (state["below_since"] or ts)

    if not state["on"]:
        timed = (state["above_since"]
                 and (ts - state["above_since"]).total_seconds() >= START_ABOVE_S
                 and since_change >= MIN_OFF_S)
        if timed:
            state["on"], state["amps"], state["t_changed"], state["last_update"] = True, MIN_A, ts, ts
    else:
        if (state["below_since"]
                and (ts - state["below_since"]).total_seconds() >= STOP_BELOW_S
                and since_change >= MIN_ON_S):
            state["on"], state["amps"], state["t_changed"] = False, 0, ts

    # ---- rate-limited setpoint updates ----
    if state["on"] and state["last_update"] and (ts - state["last_update"]).total_seconds() >= UPDATE_EVERY_S:
        step_a   = max(-RAMP_MAX_A, min(RAMP_MAX_A, target_amps - state["amps"]))
        new_amps = max(0, min(MAX_A, int(state["amps"] + step_a)))
        if 0 < new_amps < MIN_A:
            new_amps = MIN_A
        state["amps"] = new_amps
        state["last_update"] = ts
    elif state["on"] and state["last_update"] is None:
        state["last_update"] = ts

    return state["amps"] if state["on"] else 0


# ---- Run the controller ----------------------------------------------------
cmd = pd.Series(
    [step(ts, s) for ts, s in zip(df["timestamp"], df["surplus_ema_kw"])],
    index=df.index,
)
df["cmd_a"]  = cmd
df["cmd_kw"] = cmd * W_PER_A / 1000.0
df["kwh"]    = df["cmd_kw"] * df["timestamp"].diff().dt.total_seconds().fillna(0) / 3600.0


# ---- Solar / grid split ----------------------------------------------------
df["car_charging"]  = (df["cmd_a"] > 0).astype(int)
df["grid_part_kw"]  = (df["cmd_kw"] - df["surplus_kw"].clip(lower=0)).clip(lower=0)
df["solar_part_kw"] = df["cmd_kw"] - df["grid_part_kw"]


# ---- Preview ---------------------------------------------------------------
print(f"MIN_A              = {MIN_A} A ({MIN_KW:.2f} kW)")
print(f"BUFFER_KW          = {BUFFER_KW} kW")
print(f"ALLOWED_IMPORT_KW  = {ALLOWED_IMPORT_KW} kW")
print(f"-> START threshold (smoothed surplus): {START_KW:.2f} kW")
print(f"-> STAY  threshold (smoothed surplus): {STAY_KW:.2f} kW")
print(f"-> Target amps track (surplus - buffer); no ALLOWED_IMPORT padding")
print()

print(df[[
    "timestamp", "surplus_kw", "surplus_ema_kw",
    "cmd_a", "cmd_kw", "grid_part_kw", "solar_part_kw",
]].head(20).to_string(index=False))

print("\nColumns in df:")
print(list(df.columns))


# ---- Write CSV -------------------------------------------------------------
df.round(4).to_csv(CSV_OUT, index=False)
print(f"\nWrote {CSV_OUT}")