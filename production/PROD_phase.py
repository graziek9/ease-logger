USERNAME = "x"
PASSWORD = "x"


import os
import sys
import requests

# --- Configuration ---
BASE_URL = "https://api.easee.com"

# Phase mode mapping (per Easee API docs)
PHASE_MODES = {
    "1": 1,      # Locked to 1 phase
    "auto": 2,   # Automatic (firmware decides)
    "3": 3,      # Locked to 3 phases
}


def get_session():
    """Log in and return an authenticated session."""
    if not USERNAME or not PASSWORD:
        raise SystemExit("Error: EASEE_USERNAME and EASEE_PASSWORD must be set.")
    session = requests.Session()
    resp = session.post(
        f"{BASE_URL}/api/accounts/login",
        json={"userName": USERNAME, "password": PASSWORD},
        timeout=10,
    )
    resp.raise_for_status()
    session.headers.update({"Authorization": f"Bearer {resp.json()['accessToken']}"})
    return session


def get_charger_id(session):
    """Fetch the first charger's serial ID."""
    resp = session.get(f"{BASE_URL}/api/chargers", timeout=10)
    resp.raise_for_status()
    chargers = resp.json()
    if not chargers:
        raise SystemExit("No chargers found on this account.")
    return chargers[0]["id"]


def set_phase_mode(session, charger_id, phase_mode):
    """
    Send the set_phase_mode command.
    phase_mode: 1 (1-phase), 2 (auto), 3 (3-phase)
    """
    url = f"{BASE_URL}/api/chargers/{charger_id}/commands/set_phase_mode"
    payload = {"phaseMode": phase_mode}
    resp = session.post(url, json=payload, timeout=10)
    resp.raise_for_status()
    return resp


def ask_phase_mode():
    """Prompt the user for the desired phase mode."""
    print("Select phase mode:")
    print("  1    = Locked to 1 phase")
    print("  auto = Automatic (firmware decides)")
    print("  3    = Locked to 3 phases")
    while True:
        choice = input("Enter choice [1/auto/3]: ").strip().lower()
        if choice in PHASE_MODES:
            return PHASE_MODES[choice]
        print("Invalid choice. Please enter 1, auto, or 3.")


def main():
    phase_mode = ask_phase_mode()

    session = get_session()
    charger_id = get_charger_id(session)
    print(f"Using charger {charger_id}")

    try:
        set_phase_mode(session, charger_id, phase_mode)
        mode_name = {1: "1-phase", 2: "auto", 3: "3-phase"}[phase_mode]
        print(f"✅ Phase mode command sent: {mode_name}")
    except requests.HTTPError as exc:
        print(f"❌ API error: {exc.response.status_code} {exc.response.text}")
        sys.exit(1)


if __name__ == "__main__":
    main()