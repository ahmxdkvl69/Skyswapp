"""SkySwap's client for the external ticket inventory (the GDS simulator).

The simulator used to be a second Flask process on port 5001 that SkySwap
called over HTTP. Serverless hosting can't run a second listener, so the
simulator is now a blueprint in the same app with its own `inventory_tickets`
table, and this client reads and writes that table directly.

The public methods keep the shapes they had over HTTP — a list of ticket dicts
from `get_all_tickets`, the updated ticket or None from the update calls — so
the sync logic in app.py did not have to change.
"""

import datetime
import logging

from database import inventory_tickets

logger = logging.getLogger("external_inventory_client")

# Statuses that mean "nobody else can take this seat".
UNAVAILABLE_STATUSES = {"Sold", "Reserved", "Expired", "Unpublished"}

# What the simulator ships with, and what "Re-Seed Default Tickets" restores.
SEED_TICKETS = [
    {
        "ticket_id": "TKT-1001", "flight_number": "EK-622", "airline_name": "Emirates",
        "departure_city": "Lahore", "destination_city": "Dubai",
        "departure_date": "2026-07-20", "departure_time": "10:30", "arrival_time": "13:20",
        "seat_number": "12A", "travel_class": "Economy", "ticket_status": "Available",
        "original_price": 55000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "Emirates Airlines", "seller_phone": "+923001234567",
        "seller_email": "inventory@emirates.com",
    },
    {
        "ticket_id": "TKT-1002", "flight_number": "EK-623", "airline_name": "Emirates",
        "departure_city": "Dubai", "destination_city": "Lahore",
        "departure_date": "2026-07-25", "departure_time": "15:45", "arrival_time": "19:15",
        "seat_number": "14C", "travel_class": "Economy", "ticket_status": "Available",
        "original_price": 58000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "Emirates Airlines", "seller_phone": "+923001234567",
        "seller_email": "inventory@emirates.com",
    },
    {
        "ticket_id": "TKT-1003", "flight_number": "PK-302", "airline_name": "PIA",
        "departure_city": "Karachi", "destination_city": "Islamabad",
        "departure_date": "2026-07-15", "departure_time": "08:00", "arrival_time": "10:00",
        "seat_number": "22D", "travel_class": "Economy", "ticket_status": "Available",
        "original_price": 28000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "PIA", "seller_phone": "+923219876543",
        "seller_email": "inventory@pia.com.pk",
    },
    {
        "ticket_id": "TKT-1004", "flight_number": "PK-303", "airline_name": "PIA",
        "departure_city": "Islamabad", "destination_city": "Karachi",
        "departure_date": "2026-07-18", "departure_time": "18:30", "arrival_time": "20:30",
        "seat_number": "10B", "travel_class": "Business", "ticket_status": "Available",
        "original_price": 45000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "PIA", "seller_phone": "+923219876543",
        "seller_email": "inventory@pia.com.pk",
    },
    {
        "ticket_id": "TKT-1005", "flight_number": "QR-240", "airline_name": "Qatar Airways",
        "departure_city": "London", "destination_city": "New York",
        "departure_date": "2026-08-05", "departure_time": "14:00", "arrival_time": "16:45",
        "seat_number": "04F", "travel_class": "Business", "ticket_status": "Available",
        "original_price": 140000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "Qatar Airways", "seller_phone": "+97444444444",
        "seller_email": "inventory@qatarairways.com",
    },
    {
        "ticket_id": "TKT-1006", "flight_number": "TK-715", "airline_name": "Turkish Airlines",
        "departure_city": "Lahore", "destination_city": "Istanbul",
        "departure_date": "2026-07-28", "departure_time": "06:15", "arrival_time": "10:30",
        "seat_number": "31A", "travel_class": "Economy", "ticket_status": "Available",
        "original_price": 75000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "Turkish Airlines", "seller_phone": "+902124636363",
        "seller_email": "inventory@thy.com",
    },
    {
        "ticket_id": "TKT-1007", "flight_number": "EY-231", "airline_name": "Etihad Airways",
        "departure_city": "Abu Dhabi", "destination_city": "London",
        "departure_date": "2026-08-12", "departure_time": "09:00", "arrival_time": "13:30",
        "seat_number": "15C", "travel_class": "Economy", "ticket_status": "Available",
        "original_price": 95000, "resale_price": 0, "availability_status": "Available",
        "seller_name": "Etihad Airways", "seller_phone": "+97125990000",
        "seller_email": "inventory@etihad.com",
    },
]


def availability_for(status):
    return "Unavailable" if status in UNAVAILABLE_STATUSES else "Available"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


class ExternalInventoryClient:
    @staticmethod
    def get_all_tickets():
        """Every ticket the airline side is holding, oldest first."""
        try:
            return inventory_tickets.find_all(order_by="ticket_id")
        except Exception as error:
            logger.error(f"Error reading the external inventory: {error}")
            return None

    @staticmethod
    def update_status(ticket_id, status, resale_price=None, seller_name=None,
                      seller_phone=None, seller_email=None):
        """Push a status change (and optionally resale/seller details) upstream.

        Returns the updated ticket, or None if there is no such ticket.
        """
        values = {
            "ticket_status": status,
            "availability_status": availability_for(status),
            "updated_at": _now(),
        }
        if resale_price is not None:
            values["resale_price"] = int(resale_price)
        if seller_name:
            values["seller_name"] = seller_name
        if seller_phone:
            values["seller_phone"] = seller_phone
        if seller_email:
            values["seller_email"] = seller_email

        try:
            changed = inventory_tickets.update({"ticket_id": ticket_id}, values)
            if not changed:
                logger.error(f"External inventory has no ticket {ticket_id}")
                return None
            return inventory_tickets.find_one(ticket_id=ticket_id)
        except Exception as error:
            logger.error(f"Error updating status in the external inventory: {error}")
            return None

    @staticmethod
    def update_ticket(ticket_id, ticket_data):
        """Replace a ticket's details upstream. Returns the updated ticket."""
        values = {
            key: value for key, value in ticket_data.items()
            if key in inventory_tickets.columns and key not in ("id", "ticket_id")
        }
        if "ticket_status" in values:
            values["availability_status"] = availability_for(values["ticket_status"])
        values["updated_at"] = _now()

        try:
            changed = inventory_tickets.update({"ticket_id": ticket_id}, values)
            if not changed:
                logger.error(f"External inventory has no ticket {ticket_id}")
                return None
            return inventory_tickets.find_one(ticket_id=ticket_id)
        except Exception as error:
            logger.error(f"Error updating ticket in the external inventory: {error}")
            return None

    @staticmethod
    def seed_default_tickets():
        """Restore the demo inventory, overwriting anything with the same id."""
        for ticket in SEED_TICKETS:
            inventory_tickets.upsert("ticket_id", dict(ticket))
        return len(SEED_TICKETS)
