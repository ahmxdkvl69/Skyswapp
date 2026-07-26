"""Create the SkySwap schema in Neon and seed the demo data.

Run this once against a fresh database, and again any time schema.sql changes
(every statement in it is idempotent, so re-running is safe):

    python init_db.py
"""

from database import (
    ALL_TABLES, ensure_schema, inventory_tickets, seed_admin_user, seed_demo_user,
)
from external_inventory_client import ExternalInventoryClient, SEED_TICKETS
from db import DATABASE_URL, query_one


def describe_target():
    """Show which database we're about to touch, without printing the password."""
    row = query_one("SELECT current_database() AS name, version() AS version")
    host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "?"
    print(f"Connected to {row['name']} on {host}")
    print(f"  {row['version'].split(',')[0]}")


def main():
    describe_target()

    print("\nCreating tables and indexes...")
    ensure_schema()
    for table in ALL_TABLES:
        print(f"  ok  {table.name:<20} ({table.count()} rows)")

    print("\nSeeding the external inventory...")
    if inventory_tickets.count() == 0:
        ExternalInventoryClient.seed_default_tickets()
        print(f"  added {len(SEED_TICKETS)} demo tickets")
    else:
        print("  already has tickets, leaving it alone")

    print("\nSeeding the demo login...")
    created = seed_demo_user()
    print(f"  created {created['email']}" if created else "  already exists")

    print("\nSeeding the admin login...")
    action, admin_email = seed_admin_user()
    print(f"  {action} {admin_email}")

    print("\nDone. Start the app with:  python app.py")


if __name__ == "__main__":
    main()
