"""Copy the old file-based data into Neon.

Reads whatever the app left behind before the move to Postgres:

  * local_database.pkl        - users, bookings, sell requests, transactions
  * external_inventory_db.json - the GDS simulator's tickets

and writes it to the matching tables. Safe to re-run: every row is upserted on
its business key, so nothing is duplicated.

    python init_db.py          # first, create the tables
    python migrate_to_neon.py  # then, bring the data across
"""

import datetime
import json
import os
import pickle
import sys
import uuid

from database import (
    bookings, fetched_tickets, inventory_tickets, pending_users,
    sell_requests, transactions, users,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PICKLE_FILE = os.path.join(BASE_DIR, "local_database.pkl")
INVENTORY_FILE = os.path.join(BASE_DIR, "external_inventory_db.json")


def load_pickle():
    if not os.path.exists(PICKLE_FILE):
        return {}
    with open(PICKLE_FILE, "rb") as handle:
        return pickle.load(handle)


def load_inventory_json():
    if not os.path.exists(INVENTORY_FILE):
        return []
    with open(INVENTORY_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def as_utc(value):
    """Old rows stored naive local datetimes; TIMESTAMPTZ wants an offset."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value
    return None


def shape(document, table, defaults=None, timestamps=()):
    """Keep only real columns, drop Mongo's _id, and fix up timestamps."""
    row = dict(defaults or {})
    for key, value in document.items():
        if key in ("_id", "id") or key not in table.columns:
            continue
        row[key] = value
    for field in timestamps:
        if field in row:
            row[field] = as_utc(row[field])
    return row


def migrate(name, documents, table, key, defaults=None, timestamps=(), skip_if=None):
    copied = skipped = 0
    for document in documents:
        row = shape(document, table, defaults, timestamps)
        if not row.get(key):
            skipped += 1
            continue
        if skip_if and skip_if(row):
            skipped += 1
            continue
        try:
            table.upsert(key, row)
            copied += 1
        except Exception as error:
            skipped += 1
            print(f"    skipped {row.get(key)}: {str(error).splitlines()[0]}")
    note = f" ({skipped} skipped)" if skipped else ""
    print(f"  {name:<18} {copied} rows{note}")
    return copied


def main():
    data = load_pickle()
    inventory = load_inventory_json()

    if not data and not inventory:
        print("Nothing to migrate: no local_database.pkl or external_inventory_db.json found.")
        return

    print("Migrating file-based data into Neon...\n")

    # Users first: bookings reference them.
    migrate(
        "users", data.get("users", []), users, "uid",
        defaults={"phone": "", "role": "user", "is_blocked": False},
        timestamps=("created_at",),
    )
    known_uids = {row["uid"] for row in users.find_all()}

    migrate(
        "pending_users", data.get("pending_users", []), pending_users, "email",
        defaults={"temp_id": str(uuid.uuid4())},
        timestamps=("created_at",),
    )

    migrate("inventory_tickets", inventory, inventory_tickets, "ticket_id")

    migrate(
        "fetched_tickets", data.get("fetched_tickets", []), fetched_tickets, "ticket_id",
        timestamps=("fetched_at",),
    )

    # A booking whose owner never made it across would violate the foreign key.
    migrate(
        "bookings", data.get("bookings", []), bookings, "booking_id",
        timestamps=("created_at",),
        skip_if=lambda row: row.get("user_uid") not in known_uids,
    )
    known_bookings = {row["booking_id"] for row in bookings.find_all()}

    migrate(
        "sell_requests", data.get("sell_requests", []), sell_requests, "request_id",
        timestamps=("created_at", "approved_at", "rejected_at", "requested_at"),
        skip_if=lambda row: row.get("booking_id") not in known_bookings,
    )

    migrate(
        "transactions", data.get("transactions", []), transactions, "transaction_id",
        defaults={"flow_steps": []},
        timestamps=("created_at", "completed_at", "rejected_at"),
    )

    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
