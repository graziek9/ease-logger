"""
easee_set_amps.py - Standalone script to set charging amps on an Easee charger,
                    with built-in verification that the setting was applied.

Usage:
    python easee_set_amps.py <amps> [ttl_minutes]

Example:
    python easee_set_amps.py 10 5    # limit to 10A for 5 minutes
    python easee_set_amps.py 0 2     # pause charging (0A) for 2 minutes
    


The command uses SetDynamicChargerCurrent (Command ID 48).
This is a volatile setting: it is cleared when the charger reboots or
the cable is unplugged.
"""

EASEE_USERNAME = ""  # Include country code for phone
EASEE_PASSWORD = ""


import sys
import time
import requests

# ============================================================================
# CONFIG - fill in your credentials
# ============================================================================

CHARGER_SERIAL = "EUHUMAJY"               # your charger's serial number

BASE_URL = "https://api.easee.com"
LOGIN_URL = f"{BASE_URL}/api/accounts/login"
# ============================================================================


def login(session: requests.Session, username: str, password: str) -> None:
    """Authenticate and set the Bearer token on the session."""
    resp = session.post(LOGIN_URL, json={
        "userName": username,
        "password": password,
    })
    resp.raise_for_status()
    token = resp.json()["accessToken"]
    session.headers.update({"Authorization": f"Bearer {token}"})
    print("✅ Login OK")


def get_observations(session: requests.Session, serial: str, obs_ids: str) -> dict:
    """Fetch a set of observations for a device (same as in the dashboard)."""
    url = f"{BASE_URL}/state/{serial}/observations"
    resp = session.get(url, params={"ids": obs_ids})
    resp.raise_for_status()
    return resp.json()


def parse_observation(response: dict, obs_id: int):
    """Extract one observation value from the response, or None."""
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


def get_actual_current(session: requests.Session, charger_serial: str):
    """Read the current (A) the charger is actually using right now.
    Observation 11 = actual current per phase (Amps)."""
    resp = get_observations(session, charger_serial, "11")
    return parse_observation(resp, 11)


def get_charger_state(session: requests.Session, charger_serial: str) -> dict:
    """Read status + current in one call for verification."""
    # 109 = status code, 11 = actual current (A)
    resp = get_observations(session, charger_serial, "109,11")
    status_code = parse_observation(resp, 109)
    current = parse_observation(resp, 11)
    return {
        "status_code": status_code,
        "status": status_to_text(status_code) if status_code is not None else "Unknown",
        "current_a": current,
    }


def set_dynamic_current(session: requests.Session, charger_serial: str,
                        amps: int, ttl_minutes: int = 5) -> dict:
    """
    Send SetDynamicChargerCurrent (Command ID 48) to the charger.

    Args:
        amps:        Current limit in Amperes (integer). 0 = pause charging.
        ttl_minutes: How long the limit stays valid, in minutes.
                     0 = valid until changed or cable unplugged.
    """
    url = f"{BASE_URL}/api/chargers/{charger_serial}/commands/set_dynamic_charger_current"
    payload = {
        "current": int(amps),
        "ttl": int(ttl_minutes),
    }
    resp = session.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def verify_command(session: requests.Session, charger_serial: str,
                   target_amps: int, wait_s: float = 10.0,
                   tolerance_a: float = 0.5) -> bool:
    """
    Verify that the charger actually applied the target current.

    Waits `wait_s` seconds (the charger needs time to react), then reads the
    actual current back from the API. Prints a clear PASS/FAIL.

    Returns True if the actual current matches the target within tolerance,
    or if the charger is intentionally idle/paused (target 0).
    """
    print(f"\n⏳ Waiting {wait_s:.0f}s for the charger to apply the new limit...")
    time.sleep(wait_s)

    state = get_charger_state(session, charger_serial)
    actual = state["current_a"]
    status = state["status"]

    print(f"   Status : {status}")
    print(f"   Current: {actual} A  (target: {target_amps} A)")

    # If charger is paused / off, current may be 0 or None - that's fine for target 0
    if target_amps == 0:
        if actual is None or actual < tolerance_a:
            print("✅ PASS: charging is paused (current ≈ 0)")
            return True
        print(f"⚠️  WARN: expected pause but charger reports {actual} A")
        return False

    # If charger is idle / not connected, it can't draw current - don't fail on that
    if status in ("Idle", "Offline", "Completed"):
        print(f"ℹ️  INFO: charger is '{status}' - not drawing current right now.")
        print("   (Command was accepted; verification is only meaningful while charging.)")
        return True

    # Normal case: charger is connected/charging, compare actual vs target
    if actual is None:
        print("⚠️  WARN: could not read current from charger")
        return False

    if abs(actual - target_amps) <= tolerance_a:
        print(f"✅ PASS: charger is at {actual} A (target {target_amps} A)")
        return True

    print(f"❌ FAIL: charger is at {actual} A, expected ~{target_amps} A")
    return False


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python easee_set_amps.py <amps> [ttl_minutes]")
        print("Example: python easee_set_amps.py 10 5")
        sys.exit(1)

    try:
        amps = int(sys.argv[1])
        ttl = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    except ValueError:
        print("Error: amps and ttl_minutes must be integers")
        sys.exit(1)

    if amps < 0 or amps > 32:
        print(f"Error: amps must be between 0 and 32 (got {amps})")
        sys.exit(1)
    if ttl < 0:
        print("Error: ttl must be >= 0")
        sys.exit(1)

    session = requests.Session()

    try:
        # --- 1. Login ---
        login(session, EASEE_USERNAME, EASEE_PASSWORD)

        # --- 2. Read state BEFORE the command (nice for comparison) ---
        print(f"\n📋 Charger state BEFORE command:")
        before = get_charger_state(session, CHARGER_SERIAL)
        print(f"   Status : {before['status']}")
        print(f"   Current: {before['current_a']} A")

        # --- 3. Send the command ---
        print(f"\n📤 Sending {amps} A (TTL {ttl} min) to charger {CHARGER_SERIAL}...")
        result = set_dynamic_current(session, CHARGER_SERIAL, amps, ttl)
        print(f"✅ Command accepted: {result}")

        # --- 4. Verify by reading back ---
        ok = verify_command(session, CHARGER_SERIAL, amps, wait_s=10.0, tolerance_a=0.5)

        # --- 5. Exit code based on verification ---
        sys.exit(0 if ok else 2)

    except requests.HTTPError as e:
        print(f"\n❌ API error: {e}")
        if e.response is not None:
            print(f"   Response body: {e.response.text}")
        sys.exit(1)
    except requests.RequestException as e:
        print(f"\n❌ Network error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)


if __name__ == "__main__":
    main()