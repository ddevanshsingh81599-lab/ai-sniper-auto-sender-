"""
keep_alive.py
─────────────
Pings the tracker's /health endpoint every 10 minutes.
This prevents Render free-tier cold starts that silently drop
open/click events and break the follow-up loop.

HOW TO USE:
  This is automatically started by email_sender.py.
  You do NOT need to run this manually.

Why 10 minutes?
  Render spins down services after 15 minutes of no inbound HTTP traffic.
  Pinging every 10 min guarantees the process stays warm.
"""

import os
import time
import threading
import requests
from dotenv import load_dotenv

load_dotenv()

PING_INTERVAL_SEC = 10 * 60   # 10 minutes
_stop_event = threading.Event()


def _ping_once(url: str) -> bool:
    """Hit the /health endpoint. Returns True if 200 OK."""
    try:
        resp = requests.get(url, timeout=10)
        ok = resp.status_code == 200
        if ok:
            print(f"  💓 Keep-alive ping OK → {url}")
        else:
            print(f"  ⚠️  Keep-alive ping got {resp.status_code} → {url}")
        return ok
    except Exception as e:
        print(f"  ⚠️  Keep-alive ping failed: {e}")
        return False


def start_keep_alive():
    """
    Start a background daemon thread that pings /health every 10 minutes.
    Call this from email_sender.py at startup.
    """
    base_url = os.getenv("TRACKER_BASE_URL", "").rstrip("/")
    if not base_url or "localhost" in base_url:
        # Don't ping localhost — only useful in production
        print("  💓 Keep-alive: skipped (local/dev environment).")
        return

    health_url = f"{base_url}/health"

    def _loop():
        # Wait 1 minute before first ping so startup isn't noisy
        time.sleep(60)
        while not _stop_event.is_set():
            _ping_once(health_url)
            _stop_event.wait(PING_INTERVAL_SEC)

    t = threading.Thread(target=_loop, daemon=True, name="keep-alive")
    t.start()
    print(f"  💓 Keep-alive started → pinging {health_url} every 10 min.")
