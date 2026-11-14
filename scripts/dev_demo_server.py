"""
Offline demo server for UI work. NOT for production.

Runs the real application with two stand-ins so the interface can be exercised
without any accounts or API keys:

* Supabase is replaced by the in-memory implementation used by the test suite
  (everything is lost when the process exits);
* the agents run in mock mode with a short artificial delay.

Two accounts are created at start-up (password for both: demo-password-123):
    admin@example.com   administrator
    user@example.com    regular user

    python scripts/dev_demo_server.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("MOCK_LATENCY_MS", "900")
os.environ.setdefault("MOCK_SCENARIO", "self_heal")

import uvicorn  # noqa: E402

from config.settings import settings  # noqa: E402
from tests.fake_supabase import ANON_KEY, SERVICE_KEY, URL, FakeSupabase  # noqa: E402
from utils.supabase import supabase  # noqa: E402

PASSWORD = "demo-password-123"


def main() -> None:
    fake = FakeSupabase(autoconfirm=False)
    settings.supabase_url = URL
    settings.supabase_anon_key = ANON_KEY
    settings.supabase_service_role_key = SERVICE_KEY
    settings.admin_emails = "admin@example.com"
    supabase._transport = fake.transport

    import httpx

    with httpx.Client(transport=fake.transport, base_url=URL) as http:
        for email, name in (("admin@example.com", "Demo Admin"), ("user@example.com", "Demo User")):
            http.post("/auth/v1/signup", headers={"apikey": ANON_KEY},
                      json={"email": email, "password": PASSWORD, "data": {"full_name": name}})
            fake.confirm(email)

    print("DEMO SERVER: in-memory database, mock agents. Nothing here is real or persistent.")
    uvicorn.run("api.main:app", host="127.0.0.1", port=int(os.environ.get("PORT", "8770")))


if __name__ == "__main__":
    main()
