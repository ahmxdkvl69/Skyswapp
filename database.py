"""Table definitions and schema bootstrap for SkySwap.

`db.py` knows how to talk to Neon; this module says what lives there. Import
the table objects from here and use them directly:

    user = users.find_one(email=email)
    users.update({"uid": uid}, {"is_blocked": True})
"""

import os
import uuid

import bcrypt
from dotenv import load_dotenv

from db import Table, execute_script, query, query_one, execute, transaction  # noqa: F401

load_dotenv()

SCHEMA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")

users = Table(
    "users",
    columns=(
        "id", "uid", "email", "password", "name", "phone",
        "role", "is_blocked", "created_at",
    ),
)

pending_users = Table(
    "pending_users",
    columns=("id", "temp_id", "email", "password", "name", "otp", "created_at"),
)

# The GDS simulator's store — the "airline side" of the system.
inventory_tickets = Table(
    "inventory_tickets",
    columns=(
        "id", "ticket_id", "flight_number", "airline_name", "departure_city",
        "destination_city", "departure_date", "departure_time", "arrival_time",
        "seat_number", "travel_class", "ticket_status", "original_price",
        "resale_price", "availability_status", "seller_name", "seller_phone",
        "seller_email", "updated_at",
    ),
    date_columns=("departure_date",),
)

# SkySwap's synced copy of the above.
fetched_tickets = Table(
    "fetched_tickets",
    columns=(
        "id", "ticket_id", "flight_number", "airline_name", "departure_city",
        "destination_city", "departure_date", "departure_time", "arrival_time",
        "seat_number", "travel_class", "ticket_status", "original_price",
        "resale_price", "availability_status", "seller_name", "seller_phone",
        "seller_email", "fetched_at",
    ),
    date_columns=("departure_date",),
)

bookings = Table(
    "bookings",
    columns=(
        "id", "booking_id", "user_uid", "user_email", "flight_number", "airline",
        "origin", "destination", "departure", "arrival", "departure_date",
        "seat_number", "travel_class", "price", "status", "created_at",
    ),
    date_columns=("departure_date",),
)

sell_requests = Table(
    "sell_requests",
    columns=(
        "id", "request_id", "booking_id", "seller_uid", "seller_email",
        "buyer_uid", "buyer_email", "flight_number", "origin", "destination",
        "asking_price", "resale_reason", "status", "verified_by",
        "verification_date", "approved_by", "approved_at", "rejected_by",
        "rejected_at", "requested_at", "created_at",
    ),
)

transactions = Table(
    "transactions",
    columns=(
        "id", "transaction_id", "type", "source", "request_id", "booking_id",
        "buyer_uid", "buyer_email", "seller_email", "flight_number", "airline",
        "origin", "destination", "amount", "bank_name", "card_holder", "status",
        "flow_steps", "created_at", "completed_at", "rejected_at",
    ),
    json_columns=("flow_steps",),
)

ALL_TABLES = (
    users, pending_users, inventory_tickets, fetched_tickets,
    bookings, sell_requests, transactions,
)


def ensure_schema():
    """Create every table and index. Idempotent."""
    with open(SCHEMA_FILE, "r", encoding="utf-8") as handle:
        execute_script(handle.read())


def _hash(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def seed_demo_user():
    """Keep the client-demo login working. No-op once the user exists."""
    email = os.getenv("DEMO_USER_EMAIL", "user@gmail.com")
    if users.exists(email=email):
        return None
    password = os.getenv("DEMO_USER_PASSWORD", "12345678")
    return users.insert({
        "uid": str(uuid.uuid4()),
        "email": email,
        "password": _hash(password),
        "name": os.getenv("DEMO_USER_NAME", "Atta Raja"),
        "role": "user",
        "is_blocked": False,
    })


def seed_admin_user():
    """Create (or repair) the admin account from ADMIN_EMAIL / ADMIN_PASSWORD.

    Unlike the demo user this re-applies the password every run, so running
    init_db.py is always a reliable way to get back into the admin panel.
    Returns "created" or "updated" so the caller can report which happened.
    """
    email = os.getenv("ADMIN_EMAIL", "admin@admin.com")
    password = os.getenv("ADMIN_PASSWORD", "admin12345")
    values = {
        "password": _hash(password),
        "name": os.getenv("ADMIN_NAME", "Administrator"),
        "role": "admin",
        "is_blocked": False,
    }

    if users.exists(email=email):
        users.update({"email": email}, values)
        return "updated", email

    users.insert({"uid": str(uuid.uuid4()), "email": email, "phone": "", **values})
    return "created", email
