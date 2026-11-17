# `easee_simulation.csv` — Column Reference

Every column in the output CSV, grouped by what they represent.

---

## 1) Original columns (from the Easee log)

| Column | Unit | Meaning |
|---|---|---|
| `timestamp` | datetime | When the log sample was taken. Local Mac time (no timezone). |
| `charger_power_kw` | kW | Actual charger output at that moment, as reported by Easee. `0` = not charging. |
| `session_energy_kwh` | kWh | Energy delivered in the current charging session (resets when a new session starts). |
| `total_energy_kwh` | kWh | Lifetime energy delivered by this charger (never resets). |
| `status` | text | Easee charger state: `Idle`, `Connected`, `Charging`, `Completed`, `Error`, `Offline`, `Ready`. |
| `import_power_kw` | kW | Power drawn from the grid at that moment (whole house, including the charger). |
| `export_power_kw` | kW | Power pushed to the grid at that moment (whole house). |
| `import_energy_kwh` | kWh | Cumulative grid-import meter reading. |
| `export_energy_kwh` | kWh | Cumulative grid-export meter reading. |

---

## 2) Surplus columns

| Column | Unit | Formula | Meaning |
|---|---|---|---|
| `surplus_kw` | kW | `charger_power_kw + export_power_kw − import_power_kw` | Instantaneous solar surplus available for charging. **Invariant to the charger's own draw** — if the charger takes 1 kW, import goes up or export goes down by 1 kW, and the sum stays the same. `0` = no surplus, `> 0` = solar excess, `< 0` = you're importing (no solar spare). |
| `surplus_amps` | A | `surplus_kw × 1000 / (230 × PHASES)` | Surplus converted to amps on 3 phases. `4.14 A` at 3 phases = 2.86 kW. Can be negative when importing. |
| `surplus_amps_clipped` | A | `surplus_amps` clipped to `[MIN_A, MAX_A]` | Surplus amps but bounded to the charger's controllable range (`6–16 A`). Useful for "how many amps could the car actually take right now?" — but note it clips `0` up to `6`, so don't read it as "on/off". |
| `surplus_ema_kw` | kW | Exponential moving average of `surplus_kw` with half-life `EMA_HALFLIFE_S` (60 s), **time-aware** (`times=` argument) | What the controller actually reacts to. Smooths out 10-second noise and correctly decays across gaps in the log. Peaks are flattened and short dips are bridged. |
| `surplus_ema_amps` | A | `surplus_ema_kw × 1000 / (230 × PHASES)` | Same EMA expressed in amps. Used internally by the hysteresis logic (`usable_amps` = this minus `BUFFER_KW` converted to amps). |

---

## 3) Commanded setpoint columns (one per mode)

For each mode in `MODES`, three columns are created:

| Column pattern | Unit | Meaning |
|---|---|---|
| `cmd_a_<mode>` | A | Commanded charging current at that moment (0 = off, 6–16 = charging). This is the setpoint the controller wants the charger to apply. |
| `cmd_kw_<mode>` | kW | Same, converted to power: `cmd_a × 230 × PHASES / 1000`. At 3 phases, `6 A = 4.14 kW`, `16 A = 11.04 kW`. |
| `kwh_<mode>` | kWh | Energy delivered during this sample interval: `cmd_kw × dt_s / 3600`. Summing this column gives total energy delivered in that mode over the whole log. |

The `<mode>` values depend on your `MODES` list (default: all five):

| Mode | Behaviour |
|---|---|
| `solar_only` | Charge only when smoothed surplus exceeds minimum. Ramps amps up/down to track surplus with 0.2 kW buffer. Zero grid import targeted. |
| `min_solar` | Always charges at `MIN_A` (6 A), plus any solar surplus on top. Pulls `4.14 kW` from the grid when there is no sun. |
| `fast` | Always at `MAX_A` (16 A) — ignores solar entirely, always pulls from grid. Bypasses hysteresis (no stop timer). |
| `cheap_tariff` | At `MAX_A` during `CHEAP_HOURS` (01–05, 13–15), falls back to `solar_only` behaviour outside those hours. |
| `deadline` | Falls back to `solar_only` if `DEADLINE_TS` / `DEADLINE_KWH` are `None`. If set, ramps up from solar to grid-charging as the deadline approaches and kWh-delivered falls short. |

---

## 4) Default / convenience columns

| Column | Unit | Formula | Meaning |
|---|---|---|---|
| `cmd_a_default` | A | Copy of `cmd_a_solar_only` (or the first mode if `solar_only` isn't in `MODES`) | The "primary" commanded current. Used by downstream convenience columns. |
| `cmd_kw_default` | kW | `cmd_a_default × 230 × PHASES / 1000` | Same, in kW. |
| `car_charging` | 0/1 | `1 if cmd_a_default > 0 else 0` | Binary indicator: is the simulated car charging right now? Useful for `groupby` in analysis. |
| `grid_part_kw` | kW | `max(0, cmd_kw_default − max(0, surplus_kw))` | Of the charger's draw, how much comes from the grid. Everything above the instantaneous solar surplus is imported. |
| `solar_part_kw` | kW | `cmd_kw_default − grid_part_kw` | Of the charger's draw, how much comes from solar. `solar_part + grid_part = total` at every row. |

---

## Quick example reading a row

Take a hypothetical row where:
