from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Response
import os
import uuid
import json
import datetime
from dotenv import load_dotenv
from database import users, pending_users, bookings, sell_requests, fetched_tickets
from database import transactions as txn_table
from db import execute, query, transaction
from utils import (
    generate_otp, send_email_emailjs, send_notification_email_emailjs,
    predict_flight_time, hash_password, check_password, get_lowest_price_suggestion,
    now, to_display_timezone, is_valid_email, is_valid_phone, normalize_phone,
)
from pdf_utils import generate_ticket_pdf, generate_report_pdf
from external_inventory_client import ExternalInventoryClient
from external_inventory_app import inventory_bp

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

# Set this to log in as admin under any email via an emailed OTP. The regular
# admin account (role='admin' in the users table) does not need it.
ADMIN_MASTER_PASSWORD = os.getenv("ADMIN_MASTER_PASSWORD", "StrongPassword@2026").strip()

# The GDS simulator used to be a second Flask process on port 5001. It is now
# a blueprint on this app (served at /inventory) so the whole system deploys as
# one serverless application.
app.register_blueprint(inventory_bp)

# --- Routes ---

def flash_success(message):
    flash(message, "success")


def flash_error(message):
    flash(message, "error")


def password_strength(password):
    score = 0
    score += len(password) >= 8
    score += any(char.isupper() for char in password)
    score += any(char.islower() for char in password)
    score += any(char.isdigit() for char in password)
    score += any(char in "!@#$%^&*" for char in password)

    if score <= 2:
        return "weak"
    if score <= 4:
        return "medium"
    return "strong"


def drop_nulls(row):
    """Hide NULL columns so a template's `row.get('x', 'N/A')` sees its default.

    Nullable columns come back as None rather than being absent, and `.get`
    with a default only fires when the key is missing.
    """
    if not row:
        return row
    return {key: value for key, value in row.items() if value is not None}


def format_transaction(txn):
    created_at = txn.get("created_at")
    if isinstance(created_at, datetime.datetime):
        txn["created_at_display"] = to_display_timezone(created_at).strftime("%d %b %Y, %I:%M %p")
    else:
        txn["created_at_display"] = "Not recorded"
    return txn

@app.route('/')
def index():
    if 'user' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        name = request.form['name']
        if not is_valid_email(email):
            flash_error("This isn't a valid email and cannot be registered.")
            return redirect(url_for('register'))

        if password_strength(password) == "weak":
            flash_error("Please choose a stronger password.")
            return redirect(url_for('register'))

        # Check if user already exists
        if users.exists(email=email):
            flash_error("User already exists")
            return redirect(url_for('register'))

        try:
            # Generate OTP
            otp = generate_otp()
            temp_id = str(uuid.uuid4())

            # Park the signup until the OTP comes back. Asking for a second
            # code replaces the first rather than stacking up rows.
            pending_users.upsert('email', {
                'temp_id': temp_id,
                'email': email,
                'password': hash_password(password),  # Store hashed password
                'name': name,
                'otp': otp,
                'created_at': now()
            })

            # Send OTP
            if send_email_emailjs(email, otp):
                session['pending_email'] = email
                flash_success("Verification code sent successfully")
                return redirect(url_for('verify_otp'))
            else:
                flash_error("Failed to send OTP")
                return redirect(url_for('register'))

        except Exception as e:
            flash_error(f"Error: {e}")
            return redirect(url_for('register'))

    return render_template('register.html')

@app.route('/verify-otp', methods=['GET', 'POST'])
def verify_otp():
    if 'pending_email' not in session:
        return redirect(url_for('register'))

    if request.method == 'POST':
        entered_otp = request.form['otp']
        email = session['pending_email']

        data = pending_users.find_one(email=email)

        if data:
            if data['otp'] == entered_otp:
                # OTP Match -> Create User. Creating the account and clearing
                # the pending row go together or not at all.
                with transaction():
                    users.insert({
                        'uid': str(uuid.uuid4()),
                        'email': data['email'],
                        'password': data['password'],  # Already hashed
                        'name': data['name'],
                        'phone': '',
                        'role': 'user',
                        'is_blocked': False,
                        'created_at': now()
                    })
                    pending_users.delete(email=email)

                session.pop('pending_email', None)

                flash_success("Account created successfully")
                return redirect(url_for('index')) # Login page
            else:
                flash_error("Invalid OTP")
                return redirect(url_for('verify_otp'))
        else:
            flash_error("Session expired")
            return redirect(url_for('register'))

    return render_template('otp.html')

@app.route('/login', methods=['POST'])
def login():
    email = request.form['email']
    password = request.form['password']

    # 1. Legacy admin login: the master password grants admin under any email,
    #    confirmed by OTP. The emptiness check matters — without it, blanking
    #    the setting to disable this path would instead let an empty password
    #    through.
    if ADMIN_MASTER_PASSWORD and password == ADMIN_MASTER_PASSWORD:
        otp = generate_otp()
        session['admin_pending'] = True
        session['admin_email'] = email
        session['admin_otp'] = otp
        session['admin_otp_time'] = datetime.datetime.now().timestamp()

        # Check if the user exists in DB to use their name, otherwise default
        existing_user = users.find_one(email=email)
        admin_name = existing_user.get('name', 'Administrator') if existing_user else 'Administrator'

        session['admin_user_data'] = {
            'uid': str(existing_user.get('uid')) if existing_user else 'admin',
            'email': email,
            'name': admin_name,
            'role': 'admin'
        }

        print(f"\n========================================\n[ADMIN OTP] Code is: {otp}\n========================================\n")

        if send_email_emailjs(email, otp):
            flash_success(f"OTP sent to {email}")
            return redirect(url_for('admin_verify_otp'))
        else:
            flash_success(f"[DEV] OTP sent (logged to terminal): {otp}")
            return redirect(url_for('admin_verify_otp'))

    # 2. Regular User Login
    user_data = users.find_one(email=email)
    if user_data and check_password(user_data['password'], password):
        if user_data.get('is_blocked', False):
            flash_error("Your account is blocked by admin")
            return redirect(url_for('index'))

        session_user = {
            'uid': str(user_data.get('uid')),
            'email': user_data['email'],
            'name': user_data['name'],
            'role': user_data.get('role', 'user')
        }
        session['user'] = session_user
        session['role'] = session_user['role']
        return redirect(url_for('dashboard'))

    print(f"[LOGIN] Failed for: {email}")
    flash_error("Invalid credentials")
    return redirect(url_for('index'))

@app.route('/admin/verify-otp', methods=['GET', 'POST'])
def admin_verify_otp():
    if not session.get('admin_pending'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        entered_otp = request.form['otp']
        stored_otp = session.get('admin_otp')
        otp_time = session.get('admin_otp_time', 0)

        # OTP valid for 60 seconds
        if datetime.datetime.now().timestamp() - otp_time > 60:
            session.pop('admin_pending', None)
            session.pop('admin_otp', None)
            session.pop('admin_user_data', None)
            flash_error("OTP expired. Please login again.")
            return redirect(url_for('index'))

        if entered_otp == stored_otp:
            user_data = session.get('admin_user_data', {
                'uid': 'admin',
                'email': session.get('admin_email'),
                'name': 'Administrator',
                'role': 'admin'
            })
            session['user'] = user_data
            session['role'] = 'admin'
            session.pop('admin_pending', None)
            session.pop('admin_otp', None)
            session.pop('admin_user_data', None)
            return redirect(url_for('admin_dashboard'))
        else:
            flash_error("Invalid OTP")
            return redirect(url_for('admin_verify_otp'))

    return render_template('otp.html')

@app.route('/dashboard', methods=['GET', 'POST'])
def dashboard():
    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    prediction = None
    flights = []
    lowest_flight = None
    origin = ''
    destination = ''

    if request.method == 'POST':
        origin = request.form['origin']
        destination = request.form['destination']
        prediction = predict_flight_time(origin, destination)
        duration = prediction.get("flight_time", "N/A") if (prediction and not prediction.get("error")) else "N/A"

        # Published tickets on this route, cheapest first. The resale price
        # wins when the seller set one, otherwise the airline's price stands.
        rows = query(
            """
            SELECT ticket_id,
                   flight_number,
                   airline_name,
                   departure_city,
                   destination_city,
                   departure_date,
                   departure_time,
                   arrival_time,
                   seat_number,
                   travel_class,
                   ticket_status,
                   CASE WHEN resale_price > 0 THEN resale_price ELSE original_price END AS price
              FROM fetched_tickets
             WHERE ticket_status = 'Published'
               AND LOWER(TRIM(departure_city))   = LOWER(TRIM(%s))
               AND LOWER(TRIM(destination_city)) = LOWER(TRIM(%s))
             ORDER BY price ASC
            """,
            (origin, destination),
        )

        for ticket in rows:
            flights.append({
                "ticket_id": ticket["ticket_id"],
                "flight_number": ticket["flight_number"],
                "airline": ticket["airline_name"],
                "origin": ticket["departure_city"],
                "destination": ticket["destination_city"],
                "departure": ticket["departure_time"],
                "arrival": ticket["arrival_time"],
                "duration": duration,
                "status": ticket["ticket_status"],
                "price": ticket["price"],
                "seat_number": ticket["seat_number"],
                "travel_class": ticket["travel_class"],
                # Sent back to us as JSON when the user books, so keep it a string.
                "departure_date": ticket["departure_date"].isoformat() if ticket["departure_date"] else ""
            })
        lowest_flight = get_lowest_price_suggestion(flights)

    return render_template('dashboard.html', user=session['user'], prediction=prediction, flights=flights, lowest_flight=lowest_flight, origin=origin, destination=destination)


@app.route('/book-flight', methods=['POST'])
def book_flight():
    """Hand the chosen flight to the payment page; /confirm-payment does the work."""
    if 'user' not in session:
        return redirect(url_for('index'))

    flight_data = json.loads(request.form['flight_data'])

    return render_template('payment.html', flight=flight_data)


@app.route('/confirm-payment', methods=['POST'])
def confirm_payment():

    if 'user' not in session:
        return redirect(url_for('index'))

    flight_data = json.loads(request.form['flight_data'])
    ticket_id = flight_data.get('ticket_id')

    with transaction():
        # Claim the seat by flipping Published -> Sold. If no row changed,
        # someone else got there first and nothing below should happen.
        claimed = fetched_tickets.update(
            {"ticket_id": ticket_id, "ticket_status": "Published"},
            {"ticket_status": "Sold", "availability_status": "Unavailable"},
        )
        if not claimed:
            flash_error("Ticket is no longer available.")
            return redirect(url_for('dashboard'))

        ticket = fetched_tickets.find_one(ticket_id=ticket_id)

        bookings.insert({
            "booking_id": ticket_id,
            "user_uid": session['user']['uid'],
            "user_email": session['user']['email'],
            "flight_number": ticket['flight_number'],
            "airline": ticket['airline_name'],
            "origin": ticket['departure_city'],
            "destination": ticket['destination_city'],
            "departure": ticket['departure_time'],
            "arrival": ticket['arrival_time'],
            "departure_date": ticket['departure_date'],
            "seat_number": ticket['seat_number'],
            "travel_class": ticket['travel_class'],
            "price": flight_data['price'],
            "status": "Confirmed",
            "created_at": now()
        })

        txn_table.insert({
            "transaction_id": str(uuid.uuid4()),
            "type": "Direct Flight Purchase",
            "source": "direct",
            "buyer_uid": session['user']['uid'],
            "buyer_email": session['user']['email'],
            "seller_email": ticket.get('seller_email') or 'Airline',
            "booking_id": ticket_id,
            "flight_number": ticket['flight_number'],
            "airline": ticket['airline_name'],
            "origin": ticket['departure_city'],
            "destination": ticket['destination_city'],
            "amount": flight_data['price'],
            "bank_name": request.form.get('bank_name', ''),
            "card_holder": request.form.get('card_holder', ''),
            "status": "Completed",
            "created_at": now(),
            "completed_at": now(),
            "flow_steps": ["Payment received", "Ticket issued", "Booking confirmed"]
        })

    # Sync back to external inventory
    ExternalInventoryClient.update_status(ticket_id, "Sold")

    flash_success("Ticket purchased successfully")

    return redirect(url_for('my_bookings'))


@app.route('/my-bookings')
def my_bookings():
    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    user_bookings = bookings.find_all(
        user_uid=session['user']['uid'],
        order_by="created_at DESC",
    )

    return render_template('my_bookings.html', user=session['user'], bookings=user_bookings)

@app.route('/sky-swap') # Ticket Exchange
def sky_swap():
    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    listings = sell_requests.find_all(
        status="Verified / Approved",
        order_by="created_at DESC",
    )

    return render_template('sky_swap.html', user=session['user'], sell_requests=listings)

@app.route('/sell-ticket', methods=['POST'])
def sell_ticket():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    booking_id = request.form['booking_id']
    try:
        price = int(request.form['price'])
    except ValueError:
        flash_error("Please enter a valid resale price")
        return redirect(url_for('my_bookings'))

    if price <= 0:
        flash_error("Resale price must be greater than zero")
        return redirect(url_for('my_bookings'))

    # Validate ownership
    booking = bookings.find_one(
        booking_id=booking_id,
        user_uid=session['user']['uid'],
    )

    if not booking:
        flash_error("Ticket not found or ownership invalid")
        return redirect(url_for('my_bookings'))

    if booking.get("status") not in {"Confirmed", "Confirmed (Transferred)"}:
        flash_error("Only confirmed tickets can be listed for resale")
        return redirect(url_for('my_bookings'))

    # Seller details come from the profile, not the session, so a renamed
    # account lists under its current name.
    user_db = users.find_one(uid=session['user']['uid']) or {}
    seller_name = user_db.get("name") or session['user']['name']
    seller_phone = user_db.get("phone") or ""
    seller_email = user_db.get("email") or session['user']['email']

    with transaction():
        sell_requests.insert({
            "request_id": str(uuid.uuid4()),
            "booking_id": booking_id,
            "seller_uid": session['user']['uid'],
            "seller_email": session['user']['email'],
            "flight_number": booking['flight_number'],
            "origin": booking['origin'],
            "destination": booking['destination'],
            "asking_price": price,
            "status": "Pending Verification",
            "created_at": now()
        })

        bookings.update({"booking_id": booking_id}, {"status": "On Sale"})

        fetched_tickets.update(
            {"ticket_id": booking_id},
            {
                "ticket_status": "Unpublished",  # Hide from search until verified/approved
                "resale_price": price,
                "seller_name": seller_name,
                "seller_phone": seller_phone,
                "seller_email": seller_email,
            },
        )

    # Sync to external inventory
    ExternalInventoryClient.update_status(
        ticket_id=booking_id,
        status="Unpublished",
        resale_price=price,
        seller_name=seller_name,
        seller_phone=seller_phone,
        seller_email=seller_email
    )

    flash_success("Action completed successfully")
    return redirect(url_for('my_bookings'))


@app.route('/purchase-ticket', methods=['POST'])
def purchase_ticket():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Not logged in"}), 401

    request_id = request.form['request_id']
    sell_req = sell_requests.find_one(request_id=request_id, status="Verified / Approved")

    if not sell_req:
        flash_error("Sell request not found or already pending")
        return redirect(url_for('sky_swap'))

    seller_email = sell_req['seller_email']
    buyer_email = session['user']['email']
    booking_id = sell_req['booking_id']

    with transaction():
        # Re-check the status inside the UPDATE so two buyers can't both claim it.
        claimed = sell_requests.update(
            {"request_id": request_id, "status": "Verified / Approved"},
            {
                "buyer_uid": session['user']['uid'],
                "buyer_email": buyer_email,
                "status": "Pending Admin Approval",
                "requested_at": now(),
            },
        )

        if not claimed:
            flash_error("This ticket is no longer available")
            return redirect(url_for('sky_swap'))

        bookings.update(
            {"booking_id": booking_id, "user_uid": sell_req["seller_uid"]},
            {"status": "Transfer Pending"},
        )

        txn_table.insert({
            "transaction_id": str(uuid.uuid4()),
            "type": "Resale Ticket Purchase",
            "source": "resale",
            "request_id": request_id,
            "buyer_uid": session['user']['uid'],
            "buyer_email": buyer_email,
            "seller_email": seller_email,
            "booking_id": booking_id,
            "flight_number": sell_req.get('flight_number', ''),
            "origin": sell_req.get('origin', ''),
            "destination": sell_req.get('destination', ''),
            "amount": sell_req['asking_price'],
            "status": "Pending Admin Approval",
            "created_at": now(),
            "flow_steps": ["Buyer request submitted", "Waiting for admin approval", "Ownership transfer pending"]
        })

    # 3. NOTIFY
    print(f"NOTIFICATION: Ticket {booking_id} transferred from {seller_email} to {buyer_email}")
       # Notify Seller
    send_notification_email_emailjs(
        email=seller_email,
        subject="Ticket Purchase Request - SkySwap",
        message=f"A buyer ({buyer_email}) has requested to purchase your ticket (Booking ID: {booking_id}). Waiting for admin approval."
    )

    # Notify Buyer
    send_notification_email_emailjs(
        email=buyer_email,
        subject="Purchase Request Submitted - SkySwap",
        message=f"Your purchase request for ticket (Booking ID: {booking_id}) has been submitted. Waiting for admin approval."
    )

    flash_success("Resale ticket request sent and is pending admin approval")
    return redirect(url_for('transactions'))

@app.route('/profile', methods=['GET', 'POST'])
def profile():

    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    email = session['user']['email']
    user_db = users.find_one(email=email)

    if not user_db:
        session.clear()
        flash_error("Please log in again")
        return redirect(url_for('index'))

    if request.method == 'POST':

        name = request.form['name'].strip()
        phone = request.form.get('phone', '').strip()

        if phone and not is_valid_phone(phone):
            flash_error("This isn't a valid number and cannot be registered.")
            return redirect(url_for('profile'))

        phone = normalize_phone(phone) if phone else phone
        current_password = request.form.get('current_password')
        new_pass = request.form.get('password')

        if not name:
            flash_error("Name is required")
            return redirect(url_for('profile'))

        update_data = {
            'name': name,
            'phone': phone
        }

        # Only change password if user entered a new password
        if new_pass:

            # Require current password
            if not current_password:
                flash_error("Please enter your current password")
                return redirect(url_for('profile'))

            # Verify current password
            if not check_password(user_db['password'], current_password):
                flash_error("Current password is incorrect")
                return redirect(url_for('profile'))

            if password_strength(new_pass) == "weak":
                flash_error("Please choose a stronger password")
                return redirect(url_for('profile'))

            # Save new password
            update_data['password'] = hash_password(new_pass)

        users.update({'email': email}, update_data)

        # Update session
        session['user']['name'] = name
        session['user']['phone'] = phone
        session.modified = True

        if new_pass:
            flash_success("Password changed successfully")
        else:
            flash_success("Username / Profile updated successfully")

        return redirect(url_for('profile'))

    profile_user = {
        'uid': str(user_db.get('uid')),
        'email': user_db.get('email', email),
        'name': user_db.get('name', session['user'].get('name', '')),
        'phone': user_db.get('phone', ''),
        'role': user_db.get('role', session['user'].get('role', 'user'))
    }

    return render_template(
        'profile.html',
        user=profile_user
    )

@app.route('/admin/dashboard')
@app.route('/admin/profile', methods=['GET', 'POST'])
def admin_profile():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    email = session['user']['email']
    user_db = users.find_one(email=email)

    if not user_db:
        session.clear()
        flash_error("Please log in again")
        return redirect(url_for('index'))

    if request.method == 'POST':
        name = request.form['name'].strip()
        phone = request.form.get('phone', '').strip()
        current_password = request.form.get('current_password')
        new_pass = request.form.get('password')

        if not name:
            flash_error("Name is required")
            return redirect(url_for('admin_profile'))

        if phone and not is_valid_phone(phone):
            flash_error("This isn't a valid number and cannot be registered.")
            return redirect(url_for('admin_profile'))

        phone = normalize_phone(phone) if phone else phone

        update_data = {
            'name': name,
            'phone': phone
        }

        if new_pass:
            if not current_password:
                flash_error("Please enter your current password")
                return redirect(url_for('admin_profile'))

            if not check_password(user_db['password'], current_password):
                flash_error("Current password is incorrect")
                return redirect(url_for('admin_profile'))

            if password_strength(new_pass) == "weak":
                flash_error("Please choose a stronger password")
                return redirect(url_for('admin_profile'))

            update_data['password'] = hash_password(new_pass)

        users.update({'email': email}, update_data)

        session['user']['name'] = name
        session['user']['phone'] = phone
        session.modified = True

        if new_pass:
            flash_success("Password changed successfully")
        else:
            flash_success("Admin profile updated successfully")

        return redirect(url_for('admin_profile'))

    profile_user = {
        'uid': str(user_db.get('uid')),
        'email': user_db.get('email', email),
        'name': user_db.get('name', session['user'].get('name', '')),
        'phone': user_db.get('phone', ''),
        'role': 'admin'
    }

    return render_template('admin_profile.html', user=profile_user)
def admin_dashboard():
    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') != 'admin':
        return redirect(url_for('index'))

    return render_template(
        'admin_dashboard.html',
        users=users.find_all(role="user", order_by="created_at DESC"),
        pending_transfers=sell_requests.find_all(
            status="Pending Admin Approval",
            order_by="requested_at DESC",
        )
    )

@app.route('/admin/toggle-block/<uid>')
def toggle_block(uid):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    # Flip the flag in one statement rather than read-then-write.
    changed = execute("UPDATE users SET is_blocked = NOT is_blocked WHERE uid = %s", (uid,))
    if changed:
        flash_success("Action completed successfully")

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/approve-transfer/<request_id>')
def approve_transfer(request_id):

    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    sell_req = sell_requests.find_one(request_id=request_id, status="Pending Admin Approval")

    if not sell_req:
        flash_error("Pending resale transaction not found")
        return redirect(url_for('admin_dashboard'))

    # The buyer becomes the ticket's new owner and, for the inventory, its new seller.
    buyer = users.find_one(uid=sell_req['buyer_uid']) or {}
    buyer_name = buyer.get("name") or "Buyer"
    buyer_phone = buyer.get("phone") or ""
    buyer_email = buyer.get("email") or sell_req['buyer_email']
    ticket_id = sell_req['booking_id']

    with transaction():
        # Transfer ticket ownership
        bookings.update(
            {"booking_id": ticket_id},
            {
                "user_uid": sell_req['buyer_uid'],
                "user_email": sell_req['buyer_email'],
                "status": "Confirmed (Transferred)",
            },
        )

        # Mark sell request completed
        sell_requests.update(
            {"request_id": request_id},
            {
                "status": "Sold",
                "approved_at": now(),
                "approved_by": session['user']['email'],
            },
        )

        # Mark transaction completed
        txn_table.update(
            {
                "request_id": request_id,
                "booking_id": ticket_id,
                "status": "Pending Admin Approval",
            },
            {
                "status": "Completed",
                "completed_at": now(),
                "flow_steps": ["Buyer request submitted", "Admin approved", "Ownership transferred"],
            },
        )

        fetched_tickets.update(
            {"ticket_id": ticket_id},
            {
                "ticket_status": "Sold",
                "seller_name": buyer_name,
                "seller_phone": buyer_phone,
                "seller_email": buyer_email,
            },
        )

    ExternalInventoryClient.update_status(
        ticket_id=ticket_id,
        status="Sold",
        seller_name=buyer_name,
        seller_phone=buyer_phone,
        seller_email=buyer_email
    )

    # Notify Seller
    send_notification_email_emailjs(
        email=sell_req['seller_email'],
        subject="Ticket Sold Successfully",
        message="Your ticket has been sold and approved by admin."
    )

    # Notify Buyer
    send_notification_email_emailjs(
        email=sell_req['buyer_email'],
        subject="Ticket Transfer Approved",
        message="Your ticket purchase has been approved and ownership transferred."
    )

    flash_success("Resale transaction approved by admin")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/reject-transfer/<request_id>')
def reject_transfer(request_id):

    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    sell_req = sell_requests.find_one(request_id=request_id, status="Pending Admin Approval")

    if not sell_req:
        flash_error("Pending resale transaction not found")
        return redirect(url_for('admin_dashboard'))

    ticket_id = sell_req['booking_id']

    with transaction():
        sell_requests.update(
            {"request_id": request_id},
            {
                "status": "Rejected",
                "rejected_at": now(),
                "rejected_by": session['user']['email'],
            },
        )

        bookings.update(
            {"booking_id": ticket_id, "user_uid": sell_req['seller_uid']},
            {"status": "Confirmed"},
        )

        txn_table.update(
            {
                "request_id": request_id,
                "booking_id": ticket_id,
                "status": "Pending Admin Approval",
            },
            {
                "status": "Rejected",
                "rejected_at": now(),
                "flow_steps": ["Buyer request submitted", "Admin rejected", "Ticket returned to seller"],
            },
        )

        # Ticket goes back to the original seller, so it is Sold again with no
        # resale price attached.
        fetched_tickets.update(
            {"ticket_id": ticket_id},
            {"ticket_status": "Sold", "resale_price": 0},
        )

    ExternalInventoryClient.update_status(ticket_id, "Sold", resale_price=0)

    send_notification_email_emailjs(
        email=sell_req['seller_email'],
        subject="Ticket Resale Request Rejected",
        message="The resale request for your ticket was rejected by admin. Your ticket remains yours."
    )

    send_notification_email_emailjs(
        email=sell_req['buyer_email'],
        subject="Ticket Transfer Rejected",
        message="Your resale ticket purchase request was rejected by admin."
    )

    flash_success("Action completed successfully")
    return redirect(url_for('admin_dashboard'))

@app.route('/transactions', endpoint='transactions')
def transactions_page():

    if 'user' not in session:
        return redirect(url_for('index'))
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))

    email = session['user']['email']

    # One query for both sides of the trade, newest first.
    rows = query(
        """
        SELECT * FROM transactions
         WHERE buyer_email = %(email)s OR seller_email = %(email)s
         ORDER BY created_at DESC
        """,
        {"email": email},
    )

    return render_template(
        'transactions.html',
        user=session['user'],
        transactions=[format_transaction(row) for row in rows]
    )

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form['email']

        if users.exists(email=email):
            otp = generate_otp()
            session['reset_email'] = email
            session['reset_otp'] = otp

            if send_email_emailjs(email, otp):
                flash_success("Password reset code sent successfully")
                return redirect(url_for('reset_password_verify'))

            flash_error("Unable to send reset code right now")
            return redirect(url_for('forgot_password'))

        flash_error("User not found")
        return redirect(url_for('forgot_password'))

    return render_template('forgot_password.html')

@app.route('/reset-password-verify', methods=['GET', 'POST'])
def reset_password_verify():

    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':

        entered_otp = request.form['otp']

        if entered_otp == session.get('reset_otp'):
            return redirect(url_for('reset_new_password'))

        flash_error("Invalid OTP")
        return redirect(url_for('reset_password_verify'))

    return render_template('otp.html')

@app.route('/reset-new-password', methods=['GET', 'POST'])
def reset_new_password():

    if 'reset_email' not in session:
        return redirect(url_for('forgot_password'))

    if request.method == 'POST':

        password = request.form['password']
        email = session['reset_email']

        if password_strength(password) == "weak":
            flash_error("Please choose a stronger password")
            return redirect(url_for('reset_new_password'))

        users.update({'email': email}, {'password': hash_password(password)})

        session.pop('reset_email', None)
        session.pop('reset_otp', None)

        flash_success("Password reset successfully")
        return redirect(url_for('index'))

    return render_template('reset_new_password.html')

@app.route('/admin/tickets')
def admin_tickets():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')

    # Flight details live on the booking, so join it in rather than looking up
    # each booking one at a time.
    tickets = query(
        """
        SELECT s.request_id,
               s.booking_id,
               s.flight_number,
               s.seller_email,
               s.asking_price,
               s.status,
               s.origin,
               COALESCE(b.airline, 'N/A')                        AS airline,
               COALESCE(b.departure_date::text, 'N/A')           AS departure_date,
               COALESCE(NULLIF(b.departure, ''), 'N/A')          AS departure_time,
               COALESCE(NULLIF(b.destination, ''), s.destination) AS destination
          FROM sell_requests s
          LEFT JOIN bookings b ON b.booking_id = s.booking_id
         WHERE (%(status)s = '' OR s.status = %(status)s)
           AND (
                %(search)s = ''
                OR s.request_id    ILIKE %(like)s
                OR s.flight_number ILIKE %(like)s
                OR s.seller_email  ILIKE %(like)s
                OR b.airline       ILIKE %(like)s
           )
         ORDER BY s.created_at DESC
        """,
        {"status": status_filter, "search": search, "like": f"%{search}%"},
    )

    return render_template('admin_tickets.html', tickets=tickets)

@app.route('/admin/ticket/<request_id>')
def admin_ticket_detail(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    sell_req = sell_requests.find_one(request_id=request_id)
    if not sell_req:
        flash_error("Ticket request not found")
        return redirect(url_for('admin_tickets'))

    return render_template(
        'admin_ticket_detail.html',
        ticket=drop_nulls(sell_req),
        booking=drop_nulls(bookings.find_one(booking_id=sell_req['booking_id'])),
        seller=drop_nulls(users.find_one(uid=sell_req['seller_uid'])),
    )

@app.route('/admin/verify-ticket/<request_id>')
def verify_ticket(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    status = request.args.get('status', '')
    if status not in ['Verified / Approved', 'Rejected']:
        flash_error("Invalid status")
        return redirect(url_for('admin_ticket_detail', request_id=request_id))

    sell_req = sell_requests.find_one(request_id=request_id)
    if not sell_req:
        flash_error("Ticket request not found")
        return redirect(url_for('admin_tickets'))

    ticket_id = sell_req['booking_id']

    with transaction():
        sell_requests.update(
            {"request_id": request_id},
            {
                "status": status,
                # Shown to the admin as-is, so record it in the display zone.
                "verification_date": to_display_timezone(now()).strftime("%Y-%m-%d %H:%M:%S"),
                "verified_by": session['user']['name'],
            },
        )

        if status == 'Verified / Approved':
            fetched_tickets.update({"ticket_id": ticket_id}, {"ticket_status": "Published"})
        else:
            fetched_tickets.update(
                {"ticket_id": ticket_id},
                {"ticket_status": "Sold", "resale_price": 0},
            )
            bookings.update({"booking_id": ticket_id}, {"status": "Confirmed"})

    # Sync with the external inventory
    if status == 'Verified / Approved':
        ExternalInventoryClient.update_status(ticket_id, "Published")
    else:
        ExternalInventoryClient.update_status(ticket_id, "Sold", resale_price=0)

    flash_success(f"Ticket has been {status.lower()}")
    return redirect(url_for('admin_ticket_detail', request_id=request_id))


@app.route('/admin/ticket/<request_id>/pdf')
def admin_ticket_pdf(request_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    sell_req = sell_requests.find_one(request_id=request_id)
    if not sell_req:
        return "Not found", 404

    booking = bookings.find_one(booking_id=sell_req['booking_id']) or {}
    seller = users.find_one(uid=sell_req['seller_uid']) or {}

    data = {
        'ticket_id': request_id,
        'flight_number': sell_req.get('flight_number'),
        'airline': booking.get('airline') or 'N/A',
        'origin': sell_req.get('origin'),
        'destination': sell_req.get('destination'),
        'departure_date': booking.get('departure_date') or 'N/A',
        'departure_time': booking.get('departure') or 'N/A',
        'arrival_time': booking.get('arrival') or 'N/A',
        'duration': predict_flight_time(sell_req.get('origin', ''), sell_req.get('destination', '')).get('flight_time', 'N/A'),
        'seat_number': booking.get('seat_number') or 'N/A',
        'travel_class': booking.get('travel_class') or 'N/A',

        'seller_name': seller.get('name') or 'N/A',
        'seller_uid': sell_req.get('seller_uid'),
        'seller_phone': seller.get('phone') or 'N/A',
        'seller_email': sell_req.get('seller_email'),

        'original_price': booking.get('price', 0),
        'resale_price': sell_req.get('asking_price', 0),
        'resale_reason': sell_req.get('resale_reason') or 'N/A',

        'verification_status': sell_req.get('status') or 'Pending Verification',
        'verified_by': sell_req.get('verified_by') or 'N/A',
        'verification_date': sell_req.get('verification_date') or 'N/A'
    }

    pdf_bytes = generate_ticket_pdf(data)

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": f"attachment; filename=ticket_{request_id}.pdf"}
    )

@app.route('/admin/reports')
def admin_reports():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    reports_data = query(
        """
        SELECT COALESCE(b.departure_date::text, 'N/A')  AS departure_date,
               s.flight_number,
               s.origin,
               s.destination,
               COALESCE(b.departure, '')                AS departure_time,
               COALESCE(b.arrival, '')                  AS arrival_time,
               s.seller_email,
               COALESCE(s.status, 'Unknown')            AS status
          FROM sell_requests s
          LEFT JOIN bookings b ON b.booking_id = s.booking_id
         ORDER BY s.created_at DESC
        """
    )

    pdf_bytes = generate_report_pdf(reports_data)

    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-disposition": "attachment; filename=tickets_report.pdf"}
    )

# Only these sort options reach the ORDER BY, and each maps to a fixed
# fragment, so nothing from the query string is ever interpolated into SQL.
INVENTORY_SORTS = {
    'price_asc': "CASE WHEN resale_price > 0 THEN resale_price ELSE original_price END ASC",
    'price_desc': "CASE WHEN resale_price > 0 THEN resale_price ELSE original_price END DESC",
    'date_asc': "departure_date ASC NULLS LAST",
    'date_desc': "departure_date DESC NULLS LAST",
}
DEFAULT_INVENTORY_SORT = "ticket_id ASC"

@app.route('/admin/inventory')
def admin_inventory():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    order_by = INVENTORY_SORTS.get(request.args.get('sort', ''), DEFAULT_INVENTORY_SORT)

    tickets = query(
        f"""
        SELECT * FROM fetched_tickets
         WHERE (%(status)s = '' OR ticket_status = %(status)s)
           AND (
                %(search)s = ''
                OR ticket_id        ILIKE %(like)s
                OR flight_number    ILIKE %(like)s
                OR airline_name     ILIKE %(like)s
                OR departure_city   ILIKE %(like)s
                OR destination_city ILIKE %(like)s
                OR seller_email     ILIKE %(like)s
           )
         ORDER BY {order_by}
        """,
        {"status": status_filter, "search": search, "like": f"%{search}%"},
    )

    return render_template('admin_inventory.html', tickets=tickets)


# Columns SkySwap copies straight from the external inventory on every sync.
SYNCED_TICKET_FIELDS = (
    "flight_number", "airline_name", "departure_city", "destination_city",
    "departure_date", "departure_time", "arrival_time", "seat_number",
    "travel_class", "original_price", "resale_price", "seller_name",
    "seller_phone", "seller_email",
)

@app.route('/admin/inventory/fetch', methods=['POST'])
def admin_fetch_inventory():
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    external_tickets = ExternalInventoryClient.get_all_tickets()
    if external_tickets is None:
        flash_error("Failed to fetch tickets from the external inventory service.")
        return redirect(url_for('admin_inventory'))

    imported_count = 0
    updated_count = 0
    invalid_count = 0
    duplicate_count = 0

    fetched_ids = set()

    for ticket_data in external_tickets:
        ticket_id = ticket_data.get('ticket_id')

        # Basic validation checks
        if not ticket_id or not ticket_data.get('flight_number') or not ticket_data.get('departure_city') or not ticket_data.get('destination_city'):
            invalid_count += 1
            continue

        original_price = ticket_data.get('original_price', 0)
        resale_price = ticket_data.get('resale_price', 0)
        if original_price < 0 or resale_price < 0:
            invalid_count += 1
            continue

        # Prevent duplicate handling inside the same imported batch
        if ticket_id in fetched_ids:
            duplicate_count += 1
            continue
        fetched_ids.add(ticket_id)

        payload = {field: ticket_data.get(field) for field in SYNCED_TICKET_FIELDS}
        payload['ticket_id'] = ticket_id
        payload['availability_status'] = ticket_data.get('availability_status', 'Available')

        existing = fetched_tickets.find_one(ticket_id=ticket_id)
        ext_status = ticket_data.get('ticket_status', 'Available')

        if existing:
            # Preserve the local visibility decision (Published / Unpublished)
            # unless the airline says the seat is gone.
            payload['ticket_status'] = (
                ext_status if ext_status in ('Sold', 'Reserved', 'Expired')
                else existing.get('ticket_status', 'Unpublished')
            )
            fetched_tickets.update({"ticket_id": ticket_id}, payload)
            updated_count += 1
        else:
            # "No automatic publishing should occur unless specifically enabled
            # by the admin", so newly fetched tickets start Unpublished.
            payload['ticket_status'] = 'Unpublished'
            payload['fetched_at'] = now()
            fetched_tickets.insert(payload)
            imported_count += 1

    # Anything the airline dropped should stop being offered here too.
    deleted_count = 0
    for local_ticket in fetched_tickets.find_all():
        if local_ticket['ticket_id'] not in fetched_ids:
            fetched_tickets.delete(ticket_id=local_ticket['ticket_id'])
            deleted_count += 1

    flash_success(f"Sync complete. Imported: {imported_count}, Updated: {updated_count}, Removed: {deleted_count}. (Skipped {invalid_count} invalid, {duplicate_count} duplicates).")
    return redirect(url_for('admin_inventory'))

@app.route('/admin/inventory/update-status/<ticket_id>')
def admin_inventory_update_status(ticket_id):
    if 'user' not in session or session.get('role') != 'admin':
        return redirect(url_for('index'))

    action = request.args.get('action')
    ticket = fetched_tickets.find_one(ticket_id=ticket_id)
    if not ticket:
        flash_error("Ticket not found")
        return redirect(url_for('admin_inventory'))

    new_status = {
        'publish': 'Published',
        'unpublish': 'Unpublished',
        'sold': 'Sold',
        'unavailable': 'Expired',
    }.get(action, ticket.get('ticket_status', 'Unpublished'))

    fetched_tickets.update(
        {"ticket_id": ticket_id},
        {
            "ticket_status": new_status,
            "availability_status": "Unavailable" if new_status in ("Sold", "Reserved", "Expired", "Unpublished") else "Available",
        },
    )

    # Sync back to the external inventory
    ExternalInventoryClient.update_status(ticket_id, new_status)

    flash_success(f"Ticket status successfully updated to {new_status}")
    return redirect(url_for('admin_inventory'))

if __name__ == '__main__':
    app.run(debug=True, use_reloader=False)
