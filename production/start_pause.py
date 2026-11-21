USERNAME = "x"
PASSWORD = "x"

import os
import sys
import time
import requests


BASE_URL = "https://api.easee.com"

# Observation IDs (Easee Developer Platform)
OBS_STATUS          = 109   # Charger op_status
OBS_AUTH_REQUIRED   = 42    # authorizationRequired (bool)
OBS_CHARGING_SCHED  = 62    # Charging schedule (JSON)
OBS_DYNAMIC_CURRENT = 48    # Current offered to car (A)

# Status enum (simplified)
STATUS_NAMES = {
    0: "Offline", 1: "Idle", 2: "Awaiting Start", 3: "Charging",
    4: "Completed", 5: "Error", 6: "Ready to Charge", 7: "Awaiting Authentication",
    8: "Deauthorizing",
}


def get_session():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/accounts/login",
               json={"userName": USERNAME, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    s.headers.update({"Authorization": f"Bearer {r.json()['accessToken']}"})
    return s


def get_charger_id(session):
    r = session.get(f"{BASE_URL}/api/chargers", timeout=10)
    r.raise_for_status()
    chargers = r.json()
    if not chargers:
        raise SystemExit("No chargers found.")
    return chargers[0]["id"]


def get_obs(session, charger_id, ids):
    r = session.get(
        f"{BASE_URL}/state/{charger_id}/observations",
        params={"ids": ",".join(str(i) for i in ids)}, timeout=10,
    )
    r.raise_for_status()
    return {o["id"]: o.get("value") for o in r.json().get("observations", [])}


def show(obs, label):
    st = obs.get(OBS_STATUS)
    print(f"\n--- {label} ---")
    print(f"Status (109):            {st} ({STATUS_NAMES.get(st, 'unknown')})")
    print(f"Auth required (42):      {obs.get(OBS_AUTH_REQUIRED)}")
    print(f"Dynamic current (48):    {obs.get(OBS_DYNAMIC_CURRENT)} A")
    sched = obs.get(OBS_CHARGING_SCHED)
    print(f"Charging schedule (62):  {sched if sched else 'none'}")


def send_command(session, charger_id, command):
    r = session.post(
        f"{BASE_URL}/api/chargers/{charger_id}/commands/{command}", timeout=10
    )
    r.raise_for_status()
    print(f"→ sent: {command}  (HTTP {r.status_code})")


def ask_action():
    while True:
        a = input("Do you want to pause or start the charging? [start/pause]: ").strip().lower()
        if a in ("start", "s"):
            return "start"
        if a in ("pause", "p"):
            return "pause"
        print("Please type 'start' or 'pause'.")


def main():
    action = ask_action()
    session = get_session()
    charger_id = get_charger_id(session)
    print(f"Using charger {charger_id}")

    ids = [OBS_STATUS, OBS_AUTH_REQUIRED, OBS_CHARGING_SCHED, OBS_DYNAMIC_CURRENT]
    before = get_obs(session, charger_id, ids)
    show(before, "BEFORE")

    auth_required = bool(before.get(OBS_AUTH_REQUIRED))
    status_before = before.get(OBS_STATUS)

    # ---- choose the right command ----
    if action == "start":
        if auth_required and status_before == 7:
            # Awaiting Authentication → start_charging is the correct call
            send_command(session, charger_id, "start_charging")
        else:
            # Paused/Awaiting Start/other → resume_charging is the correct call
            send_command(session, charger_id, "resume_charging")
    else:
        # Pause is the same regardless of auth mode
        send_command(session, charger_id, "pause_charging")

    # ---- verify ----
    print("\nWaiting 8 s for the charger to update…")
    time.sleep(8)
    after = get_obs(session, charger_id, ids)
    show(after, "AFTER")

    st = after.get(OBS_STATUS)
    if action == "start":
        if st == 3:
            print("\n✅ Charger reports Charging.")
        elif st == 6:
            print("\n⚠️  Charger is Ready to Charge but the car isn't drawing power.")
            print("   → Check the car's own charging schedule / limit.")
        elif st == 2:
            print("\n⚠️  Still Awaiting Start. Something (schedule/third-party) is holding it.")
            print("   → Check Observation 62 and any connected services in the Easee app.")
        else:
            print(f"\n⚠️  Unexpected status {st}. Check the Easee app.")
    else:
        if st in (2, 6):
            print("\n✅ Charger is paused/idle as expected.")
        else:
            print(f"\n⚠️  Status is {st} — pause may not have taken effect.")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as exc:
        print(f"API error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)
    except requests.RequestException as exc:
        print(f"Network error: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)
