"""The external ticket inventory (GDS simulator), as a blueprint.

This stands in for the airline-side system SkySwap pulls tickets from. It used
to be a second Flask process on port 5001 backed by a JSON file; it is now
mounted at /inventory inside the main app and backed by the `inventory_tickets`
table, so it deploys as one serverless application.

Its REST endpoints are still here (/inventory/api/tickets) for anyone wanting
to drive it over HTTP, but SkySwap itself now calls it in-process through
`ExternalInventoryClient`.
"""

from flask import Blueprint, jsonify, redirect, render_template_string, request, url_for

from database import inventory_tickets
from external_inventory_client import ExternalInventoryClient, availability_for

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventory")

# Fields the panel form and the REST API are allowed to write.
EDITABLE_FIELDS = (
    "flight_number", "airline_name", "travel_class", "departure_city",
    "destination_city", "departure_date", "seat_number", "departure_time",
    "arrival_time", "original_price", "resale_price", "seller_name",
    "seller_phone", "seller_email", "ticket_status",
)


def load_tickets():
    return inventory_tickets.find_all(order_by="ticket_id")


# --- Web Interface (External Inventory Control Panel) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>External Ticket Inventory Panel</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Outfit', sans-serif; background-color: #0f172a; color: #f8fafc; }
        .glass-panel { background: rgba(30, 41, 59, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.05); }
    </style>
</head>
<body class="min-h-screen p-8">
    <div class="max-w-6xl mx-auto space-y-8">

        <!-- Header -->
        <div class="glass-panel rounded-3xl p-6 shadow-2xl flex flex-col md:flex-row md:items-center md:justify-between border-l-4 border-indigo-500">
            <div>
                <span class="text-xs font-bold text-indigo-400 uppercase tracking-widest">Global Distribution System (GDS) Simulator</span>
                <h1 class="text-3xl font-extrabold tracking-tight">Dummy Ticket Inventory</h1>
                <p class="text-slate-400 mt-1">Manage flight tickets at the source. Any changes here represent updates in the external airline database.</p>
            </div>
            <div class="mt-4 md:mt-0 flex items-center space-x-3 bg-slate-800 px-4 py-2 rounded-full border border-slate-700">
                <span class="relative flex h-3 w-3">
                  <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                  <span class="relative inline-flex rounded-full h-3 w-3 bg-emerald-500"></span>
                </span>
                <span class="text-sm font-semibold">Simulator Active</span>
            </div>
        </div>

        <!-- Add/Edit Ticket Card -->
        <div class="glass-panel rounded-3xl p-6 shadow-2xl">
            <h2 class="text-xl font-bold mb-4 flex items-center gap-2 text-indigo-300">
                <i class="fas fa-edit"></i>
                <span>Add / Edit Ticket in Inventory</span>
            </h2>
            <form action="{{ url_for('inventory.web_save_ticket') }}" method="POST" class="grid grid-cols-1 md:grid-cols-4 gap-4">
                <input type="hidden" name="action_type" id="form-action" value="create">

                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Ticket ID</label>
                    <input type="text" name="ticket_id" id="form-ticket-id" required placeholder="e.g. TKT-1008" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Flight Number</label>
                    <input type="text" name="flight_number" id="form-flight-number" required placeholder="e.g. EK-624" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Airline Name</label>
                    <input type="text" name="airline_name" id="form-airline-name" required placeholder="e.g. Emirates" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Travel Class</label>
                    <select name="travel_class" id="form-travel-class" required class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                        <option value="Economy">Economy</option>
                        <option value="Business">Business</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Departure City</label>
                    <input type="text" name="departure_city" id="form-departure-city" required placeholder="e.g. Lahore" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Destination City</label>
                    <input type="text" name="destination_city" id="form-destination-city" required placeholder="e.g. Dubai" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Departure Date</label>
                    <input type="date" name="departure_date" id="form-departure-date" required class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Seat Number</label>
                    <input type="text" name="seat_number" id="form-seat-number" required placeholder="e.g. 12A" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Departure Time</label>
                    <input type="text" name="departure_time" id="form-departure-time" required placeholder="e.g. 10:30" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Arrival Time</label>
                    <input type="text" name="arrival_time" id="form-arrival-time" required placeholder="e.g. 13:20" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Original Price (PKR)</label>
                    <input type="number" name="original_price" id="form-original-price" required placeholder="55000" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Resale Price (PKR)</label>
                    <input type="number" name="resale_price" id="form-resale-price" value="0" placeholder="0" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Seller Name</label>
                    <input type="text" name="seller_name" id="form-seller-name" required placeholder="Emirates Airlines" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Seller Phone</label>
                    <input type="text" name="seller_phone" id="form-seller-phone" required placeholder="+923001234567" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Seller Email</label>
                    <input type="email" name="seller_email" id="form-seller-email" required placeholder="inventory@airline.com" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 uppercase tracking-wider mb-1">Status</label>
                    <select name="ticket_status" id="form-ticket-status" class="w-full px-3 py-2 bg-slate-800 border border-slate-700 rounded-xl focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none text-white transition-all">
                        <option value="Available">Available</option>
                        <option value="Published">Published</option>
                        <option value="Unpublished">Unpublished</option>
                        <option value="Sold">Sold</option>
                        <option value="Reserved">Reserved</option>
                        <option value="Expired">Expired</option>
                    </select>
                </div>

                <div class="md:col-span-4 flex justify-end gap-3 mt-2">
                    <button type="button" onclick="resetForm()" class="px-6 py-2 rounded-xl bg-slate-750 border border-slate-700 hover:bg-slate-700 font-semibold text-sm transition-all">
                        Reset
                    </button>
                    <button type="submit" class="px-6 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-700 font-bold text-sm shadow-lg shadow-indigo-600/30 transition-all">
                        Save Inventory Ticket
                    </button>
                </div>
            </form>
            {% if error %}
            <p class="mt-4 text-sm font-semibold text-red-400"><i class="fas fa-circle-exclamation mr-1"></i>{{ error }}</p>
            {% endif %}
        </div>

        <!-- Inventory List -->
        <div class="glass-panel rounded-3xl p-6 shadow-2xl overflow-hidden">
            <div class="flex items-center justify-between mb-6">
                <h2 class="text-xl font-bold flex items-center gap-2 text-indigo-300">
                    <i class="fas fa-list"></i>
                    <span>All Inventory Tickets ({{ tickets|length }})</span>
                </h2>
                <a href="{{ url_for('inventory.web_seed') }}" class="px-4 py-2 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-xl text-sm font-semibold transition-all">
                    <i class="fas fa-sync mr-1"></i> Re-Seed Default Tickets
                </a>
            </div>

            <div class="overflow-x-auto">
                <table class="min-w-full divide-y divide-slate-800 text-left">
                    <thead class="bg-slate-800/50">
                        <tr>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">ID</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Flight</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Route</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Seat &amp; Class</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Price (Original / Resale)</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Seller Info</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Status</th>
                            <th class="px-4 py-3 text-xs font-bold text-slate-400 uppercase">Actions</th>
                        </tr>
                    </thead>
                    <tbody class="divide-y divide-slate-800 bg-slate-900/20">
                        {% for ticket in tickets %}
                        <tr class="hover:bg-slate-800/40 transition-colors">
                            <td class="px-4 py-4 whitespace-nowrap text-sm font-mono font-bold text-indigo-400">{{ ticket.ticket_id }}</td>
                            <td class="px-4 py-4 whitespace-nowrap">
                                <span class="font-bold text-sm text-slate-200">{{ ticket.flight_number }}</span><br>
                                <span class="text-xs text-slate-400">{{ ticket.airline_name }}</span>
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-sm text-slate-200">
                                <div>{{ ticket.departure_city }} &rarr; {{ ticket.destination_city }}</div>
                                <div class="text-xs text-slate-400">{{ ticket.departure_date }} at {{ ticket.departure_time }}</div>
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-sm text-slate-200">
                                <span class="px-2 py-0.5 rounded bg-slate-800 text-xs font-bold font-mono text-slate-300">{{ ticket.seat_number }}</span><br>
                                <span class="text-xs text-slate-400">{{ ticket.travel_class }}</span>
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-sm text-slate-200">
                                <span class="font-semibold text-slate-300">{{ ticket.original_price }} PKR</span><br>
                                {% if ticket.resale_price > 0 %}
                                <span class="text-xs text-indigo-400">Resale: {{ ticket.resale_price }} PKR</span>
                                {% else %}
                                <span class="text-xs text-slate-500">No resale</span>
                                {% endif %}
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-xs text-slate-400">
                                <span class="font-bold text-slate-300">{{ ticket.seller_name }}</span><br>
                                <span>{{ ticket.seller_email }}</span><br>
                                <span>{{ ticket.seller_phone }}</span>
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-sm">
                                <span class="px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider
                                    {% if ticket.ticket_status == 'Available' or ticket.ticket_status == 'Published' %}
                                        bg-emerald-500/10 text-emerald-400 border border-emerald-500/20
                                    {% elif ticket.ticket_status == 'Sold' %}
                                        bg-blue-500/10 text-blue-400 border border-blue-500/20
                                    {% elif ticket.ticket_status == 'Reserved' %}
                                        bg-amber-500/10 text-amber-400 border border-amber-500/20
                                    {% else %}
                                        bg-slate-700/30 text-slate-400 border border-slate-600/20
                                    {% endif %}">
                                    {{ ticket.ticket_status }}
                                </span>
                            </td>
                            <td class="px-4 py-4 whitespace-nowrap text-xs font-bold space-x-2">
                                <button onclick="editTicket({{ ticket|tojson|safe }})" class="px-2 py-1 rounded bg-indigo-600/20 text-indigo-400 hover:bg-indigo-600/30">
                                    Edit
                                </button>
                                <a href="{{ url_for('inventory.web_delete_ticket', ticket_id=ticket.ticket_id) }}" onclick="return confirm('Are you sure you want to delete this ticket?')" class="px-2 py-1 rounded bg-red-500/15 text-red-400 hover:bg-red-500/25">
                                    Delete
                                </a>
                            </td>
                        </tr>
                        {% else %}
                        <tr>
                            <td colspan="8" class="px-4 py-8 text-center text-slate-500">No tickets inside the inventory. Seed or add some.</td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        function editTicket(t) {
            document.getElementById('form-action').value = 'update';

            document.getElementById('form-ticket-id').value = t.ticket_id;
            document.getElementById('form-ticket-id').readOnly = true;
            document.getElementById('form-ticket-id').classList.add('opacity-50');

            document.getElementById('form-flight-number').value = t.flight_number;
            document.getElementById('form-airline-name').value = t.airline_name;
            document.getElementById('form-travel-class').value = t.travel_class;
            document.getElementById('form-departure-city').value = t.departure_city;
            document.getElementById('form-destination-city').value = t.destination_city;
            document.getElementById('form-departure-date').value = t.departure_date;
            document.getElementById('form-seat-number').value = t.seat_number;
            document.getElementById('form-departure-time').value = t.departure_time;
            document.getElementById('form-arrival-time').value = t.arrival_time;
            document.getElementById('form-original-price').value = t.original_price;
            document.getElementById('form-resale-price').value = t.resale_price;
            document.getElementById('form-seller-name').value = t.seller_name;
            document.getElementById('form-seller-phone').value = t.seller_phone;
            document.getElementById('form-seller-email').value = t.seller_email;
            document.getElementById('form-ticket-status').value = t.ticket_status;
        }

        function resetForm() {
            document.getElementById('form-action').value = 'create';

            document.getElementById('form-ticket-id').value = '';
            document.getElementById('form-ticket-id').readOnly = false;
            document.getElementById('form-ticket-id').classList.remove('opacity-50');

            document.getElementById('form-flight-number').value = '';
            document.getElementById('form-airline-name').value = '';
            document.getElementById('form-travel-class').value = 'Economy';
            document.getElementById('form-departure-city').value = '';
            document.getElementById('form-destination-city').value = '';
            document.getElementById('form-departure-date').value = '';
            document.getElementById('form-seat-number').value = '';
            document.getElementById('form-departure-time').value = '';
            document.getElementById('form-arrival-time').value = '';
            document.getElementById('form-original-price').value = '';
            document.getElementById('form-resale-price').value = '0';
            document.getElementById('form-seller-name').value = '';
            document.getElementById('form-seller-phone').value = '';
            document.getElementById('form-seller-email').value = '';
            document.getElementById('form-ticket-status').value = 'Available';
        }
    </script>
</body>
</html>
"""


@inventory_bp.route("/")
def web_index():
    return render_template_string(HTML_TEMPLATE, tickets=load_tickets(), error=None)


@inventory_bp.route("/web/seed")
def web_seed():
    ExternalInventoryClient.seed_default_tickets()
    return redirect(url_for("inventory.web_index"))


@inventory_bp.route("/web/save", methods=["POST"])
def web_save_ticket():
    ticket_id = request.form["ticket_id"].strip()
    action = request.form.get("action_type", "create")
    status = request.form["ticket_status"].strip()

    ticket_data = {
        "ticket_id": ticket_id,
        "flight_number": request.form["flight_number"].strip(),
        "airline_name": request.form["airline_name"].strip(),
        "travel_class": request.form["travel_class"].strip(),
        "departure_city": request.form["departure_city"].strip(),
        "destination_city": request.form["destination_city"].strip(),
        "departure_date": request.form["departure_date"].strip(),
        "seat_number": request.form["seat_number"].strip(),
        "departure_time": request.form["departure_time"].strip(),
        "arrival_time": request.form["arrival_time"].strip(),
        "original_price": int(request.form["original_price"] or 0),
        "resale_price": int(request.form.get("resale_price", 0) or 0),
        "seller_name": request.form["seller_name"].strip(),
        "seller_phone": request.form["seller_phone"].strip(),
        "seller_email": request.form["seller_email"].strip(),
        "ticket_status": status,
        "availability_status": availability_for(status),
    }

    if action == "create":
        if inventory_tickets.exists(ticket_id=ticket_id):
            return render_template_string(
                HTML_TEMPLATE,
                tickets=load_tickets(),
                error=f"Ticket ID {ticket_id} already exists in the inventory.",
            ), 400
        inventory_tickets.insert(ticket_data)
    else:
        inventory_tickets.update({"ticket_id": ticket_id}, ticket_data)

    return redirect(url_for("inventory.web_index"))


@inventory_bp.route("/web/delete/<ticket_id>")
def web_delete_ticket(ticket_id):
    inventory_tickets.delete(ticket_id=ticket_id)
    return redirect(url_for("inventory.web_index"))


# --- API Endpoints ---
@inventory_bp.route("/api/tickets", methods=["GET"])
def api_get_tickets():
    return jsonify(load_tickets())


@inventory_bp.route("/api/tickets/<ticket_id>", methods=["PUT"])
def api_update_ticket(ticket_id):
    payload = {
        key: value for key, value in (request.json or {}).items()
        if key in EDITABLE_FIELDS
    }
    ticket = ExternalInventoryClient.update_ticket(ticket_id, payload)
    if ticket is None:
        return jsonify({"success": False, "message": "Ticket not found"}), 404
    return jsonify({"success": True, "ticket": ticket})


@inventory_bp.route("/api/tickets/<ticket_id>/status", methods=["POST"])
def api_update_status(ticket_id):
    payload = request.json or {}
    status = payload.get("ticket_status")
    if not status:
        return jsonify({"success": False, "message": "Status not provided"}), 400

    ticket = ExternalInventoryClient.update_status(
        ticket_id,
        status,
        resale_price=payload.get("resale_price"),
        seller_name=payload.get("seller_name"),
        seller_phone=payload.get("seller_phone"),
        seller_email=payload.get("seller_email"),
    )
    if ticket is None:
        return jsonify({"success": False, "message": "Ticket not found"}), 404
    return jsonify({"success": True, "ticket": ticket})
