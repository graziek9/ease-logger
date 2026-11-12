import csv
import os
import time
from datetime import datetime

import requests

# --- Config (credentials come from environment variables / GitHub Secrets) ---
EASEE_USERNAME = os.environ["EASEE_USERNAME"]
EASEE_PASSWORD = os.environ["EASEE_PASSWORD"]

BASE_URL = "https://api.easee.com"
LOGIN_URL = f"{BASE_URL}/api/accounts/login"
CHARGERS_URL = f"{BASE_URL}/api/chargers"

REFRESH_SECONDS = 60
# Default 14 minutes; leaves a buffer before the 15-minute workflow timeout.
RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "840"))

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
    if not os.path.exists(CSV_FILE) or os.path.getsize(CSV_FILE) == 0:
        with open(CSV_FILE, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def append_csv_row(row):
    with open(CSV_FILE, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(
            {k: row.get(k, "") for k in CSV_FIELDS}
        )


def status_to_text(code):
    mapping = {
        0: "Offline",
        1: "Idle",
        2: "Connected",
        3: "Charging",
        4: "Completed",
        5: "Error",
        6: "Ready",
    }
    return mapping.get(code, f"Unknown ({code})")


def parse_obs(response, obs_id):
    if not response or "observations" not in response:
        return None
    for o in response["observations"]:
        if o.get("id") == obs_id:
            return o.get("value")
    return None


def find_equalizer_id(session):
    try:
        products = session.get(f"{BASE_URL}/api/accounts/products").json()
    except Exception as exc:
        print(f"products fetch error: {exc}")
        return None
    for product in products:
        for eq in product.get("equalizers", []):
            return eq.get("id")
    return None


def main():
    ensure_csv_header()

    session = requests.Session()
    r = session.post(
        LOGIN_URL,
        json={"userName": EASEE_USERNAME, "password": EASEE_PASSWORD},
    )
    r.raise_for_status()
    session.headers.update({"Authorization": f"Bearer {r.json()['accessToken']}"})

    chargers = session.get(CHARGERS_URL).json()
    if not chargers:
        raise SystemExit("No chargers found.")
    charger_serial = chargers[0]["id"]

    equalizer_serial = find_equalizer_id(session)
    print(f"Charger={charger_serial} Equalizer={equalizer_serial}")

    deadline = time.time() + RUN_SECONDS
    while time.time() < deadline:
        try:
            c = session.get(
                f"{BASE_URL}/state/{charger_serial}/observations",
                params={"ids": "109,120,121,124"},
            ).json()

            e = None
            if equalizer_serial:
                e = session.get(
                    f"{BASE_URL}/state/{equalizer_serial}/observations",
                    params={"ids": "40,41,45,46"},
                ).json()

            c_status = parse_obs(c, 109)
            append_csv_row({
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "charger_power_kw": parse_obs(c, 120),
                "session_energy_kwh": parse_obs(c, 121),
                "total_energy_kwh": parse_obs(c, 124),
                "status": status_to_text(c_status) if c_status is not None else "Unknown",
                "import_power_kw": parse_obs(e, 40) if e else None,
                "export_power_kw": parse_obs(e, 41) if e else None,
                "import_energy_kwh": parse_obs(e, 45) if e else None,
                "export_energy_kwh": parse_obs(e, 46) if e else None,
            })
            print(f"logged {datetime.now().isoformat(timespec='seconds')}")
        except Exception as exc:
            print(f"fetch error: {exc}")

        # Sleep in small chunks so we exit promptly near the deadline.
        remaining = deadline - time.time()
        time.sleep(max(0, min(REFRESH_SECONDS, remaining)))


if __name__ == "__main__":
    main()
