import re
import random
import string
import requests
import os
import json
import math
import bcrypt
import uuid
import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from database import users
from dotenv import load_dotenv

load_dotenv()

# Timestamps are stored in UTC (TIMESTAMPTZ) so they mean the same thing from a
# laptop in Lahore and a Vercel function in Washington. They are converted back
# to a local zone only for display.
DISPLAY_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Karachi")

try:
    _display_tz = ZoneInfo(DISPLAY_TIMEZONE)
except (ZoneInfoNotFoundError, ValueError):
    print(f"WARNING: unknown APP_TIMEZONE {DISPLAY_TIMEZONE!r}, showing times in UTC")
    _display_tz = datetime.timezone.utc


def now():
    """Current time as an aware UTC datetime, ready for a TIMESTAMPTZ column."""
    return datetime.datetime.now(datetime.timezone.utc)


def to_display_timezone(value):
    """Move a stored timestamp into the timezone the app shows times in."""
    if not isinstance(value, datetime.datetime):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(_display_tz)

# EmailJS Configuration (Load from Env)
EMAILJS_SERVICE_ID = os.getenv('EMAILJS_SERVICE_ID', '').strip()
EMAILJS_TEMPLATE_ID = os.getenv('EMAILJS_TEMPLATE_ID', '').strip()
EMAILJS_USER_ID = os.getenv('EMAILJS_USER_ID', '').strip() # Public Key
FIREBASE_API_KEY = os.getenv('FIREBASE_API_KEY', '').strip()

EMAILJS_PRIVATE_KEY = os.getenv('EMAILJS_PRIVATE_KEY', '').strip()

# ============================================================
# Airport Database with coordinates (lat, lon) and IATA codes
# ============================================================
AIRPORTS = {
    # Pakistan
    "lahore":       {"name": "Allama Iqbal International Airport", "iata": "LHE", "city": "Lahore",       "country": "Pakistan", "lat": 31.5216, "lon": 74.4036},
    "karachi":      {"name": "Jinnah International Airport",      "iata": "KHI", "city": "Karachi",      "country": "Pakistan", "lat": 24.9065, "lon": 67.1609},
    "islamabad":    {"name": "Islamabad International Airport",   "iata": "ISB", "city": "Islamabad",    "country": "Pakistan", "lat": 33.5491, "lon": 72.8296},
    "peshawar":     {"name": "Bacha Khan International Airport",  "iata": "PEW", "city": "Peshawar",     "country": "Pakistan", "lat": 33.9940, "lon": 71.5146},
    "quetta":       {"name": "Quetta International Airport",      "iata": "UET", "city": "Quetta",       "country": "Pakistan", "lat": 30.2514, "lon": 66.9375},
    "faisalabad":   {"name": "Faisalabad International Airport",  "iata": "LYP", "city": "Faisalabad",   "country": "Pakistan", "lat": 31.3650, "lon": 72.9948},
    "multan":       {"name": "Multan International Airport",      "iata": "MUX", "city": "Multan",       "country": "Pakistan", "lat": 30.2032, "lon": 71.4191},
    "sialkot":      {"name": "Sialkot International Airport",     "iata": "SKT", "city": "Sialkot",      "country": "Pakistan", "lat": 32.5356, "lon": 74.3639},

    # Middle East
    "dubai":        {"name": "Dubai International Airport",       "iata": "DXB", "city": "Dubai",        "country": "UAE",           "lat": 25.2532, "lon": 55.3657},
    "abu dhabi":    {"name": "Abu Dhabi International Airport",   "iata": "AUH", "city": "Abu Dhabi",    "country": "UAE",           "lat": 24.4330, "lon": 54.6511},
    "sharjah":      {"name": "Sharjah International Airport",     "iata": "SHJ", "city": "Sharjah",      "country": "UAE",           "lat": 25.3286, "lon": 55.5172},
    "jeddah":       {"name": "King Abdulaziz International Airport","iata": "JED","city": "Jeddah",       "country": "Saudi Arabia",  "lat": 21.6796, "lon": 39.1565},
    "riyadh":       {"name": "King Khalid International Airport", "iata": "RUH", "city": "Riyadh",       "country": "Saudi Arabia",  "lat": 24.9576, "lon": 46.6988},
    "medina":       {"name": "Prince Mohammad Bin Abdulaziz Airport","iata":"MED","city": "Medina",       "country": "Saudi Arabia",  "lat": 24.5534, "lon": 39.7051},
    "doha":         {"name": "Hamad International Airport",       "iata": "DOH", "city": "Doha",         "country": "Qatar",         "lat": 25.2731, "lon": 51.6081},
    "muscat":       {"name": "Muscat International Airport",      "iata": "MCT", "city": "Muscat",       "country": "Oman",          "lat": 23.5933, "lon": 58.2844},
    "bahrain":      {"name": "Bahrain International Airport",     "iata": "BAH", "city": "Bahrain",      "country": "Bahrain",       "lat": 26.2708, "lon": 50.6336},
    "kuwait":       {"name": "Kuwait International Airport",      "iata": "KWI", "city": "Kuwait City",  "country": "Kuwait",        "lat": 29.2266, "lon": 47.9689},

    # South Asia
    "delhi":        {"name": "Indira Gandhi International Airport","iata": "DEL", "city": "New Delhi",    "country": "India",    "lat": 28.5562, "lon": 77.1000},
    "mumbai":       {"name": "Chhatrapati Shivaji Maharaj Airport","iata": "BOM","city": "Mumbai",       "country": "India",    "lat": 19.0896, "lon": 72.8656},
    "dhaka":        {"name": "Hazrat Shahjalal International Airport","iata":"DAC","city":"Dhaka",        "country": "Bangladesh","lat": 23.8433, "lon": 90.3978},
    "colombo":      {"name": "Bandaranaike International Airport","iata": "CMB", "city": "Colombo",      "country": "Sri Lanka","lat": 7.1808,  "lon": 79.8841},
    "kathmandu":    {"name": "Tribhuvan International Airport",   "iata": "KTM", "city": "Kathmandu",    "country": "Nepal",    "lat": 27.6966, "lon": 85.3591},

    # Europe
    "london":       {"name": "Heathrow Airport",                  "iata": "LHR", "city": "London",       "country": "UK",       "lat": 51.4700, "lon": -0.4543},
    "paris":        {"name": "Charles de Gaulle Airport",         "iata": "CDG", "city": "Paris",        "country": "France",   "lat": 49.0097, "lon": 2.5479},
    "frankfurt":    {"name": "Frankfurt Airport",                 "iata": "FRA", "city": "Frankfurt",    "country": "Germany",  "lat": 50.0379, "lon": 8.5622},
    "istanbul":     {"name": "Istanbul Airport",                  "iata": "IST", "city": "Istanbul",     "country": "Turkey",   "lat": 41.2753, "lon": 28.7519},
    "rome":         {"name": "Leonardo da Vinci Airport",         "iata": "FCO", "city": "Rome",         "country": "Italy",    "lat": 41.8003, "lon": 12.2389},
    "amsterdam":    {"name": "Amsterdam Schiphol Airport",        "iata": "AMS", "city": "Amsterdam",    "country": "Netherlands","lat": 52.3105,"lon": 4.7683},
    "madrid":       {"name": "Adolfo Suárez Madrid–Barajas Airport","iata":"MAD","city": "Madrid",       "country": "Spain",    "lat": 40.4983, "lon": -3.5676},
    "moscow":       {"name": "Sheremetyevo International Airport","iata": "SVO", "city": "Moscow",       "country": "Russia",   "lat": 55.9726, "lon": 37.4146},
    "zurich":       {"name": "Zurich Airport",                    "iata": "ZRH", "city": "Zurich",       "country": "Switzerland","lat":47.4647, "lon": 8.5492},
    "munich":       {"name": "Munich Airport",                    "iata": "MUC", "city": "Munich",       "country": "Germany",  "lat": 48.3538, "lon": 11.7861},
    "barcelona":    {"name": "Josep Tarradellas Barcelona–El Prat Airport","iata":"BCN","city":"Barcelona","country":"Spain",    "lat": 41.2971, "lon": 2.0785},
    "manchester":   {"name": "Manchester Airport",                "iata": "MAN", "city": "Manchester",   "country": "UK",       "lat": 53.3537, "lon": -2.2750},

    # North America
    "new york":     {"name": "John F. Kennedy International Airport","iata":"JFK","city": "New York",    "country": "USA",      "lat": 40.6413, "lon": -73.7781},
    "los angeles":  {"name": "Los Angeles International Airport", "iata": "LAX", "city": "Los Angeles",  "country": "USA",      "lat": 33.9425, "lon": -118.4081},
    "chicago":      {"name": "O'Hare International Airport",      "iata": "ORD", "city": "Chicago",      "country": "USA",      "lat": 41.9742, "lon": -87.9073},
    "toronto":      {"name": "Toronto Pearson International Airport","iata":"YYZ","city": "Toronto",     "country": "Canada",   "lat": 43.6777, "lon": -79.6248},
    "san francisco":{"name": "San Francisco International Airport","iata":"SFO", "city": "San Francisco","country": "USA",      "lat": 37.6213, "lon": -122.3790},
    "miami":        {"name": "Miami International Airport",       "iata": "MIA", "city": "Miami",        "country": "USA",      "lat": 25.7959, "lon": -80.2870},
    "washington":   {"name": "Washington Dulles International Airport","iata":"IAD","city":"Washington DC","country":"USA",      "lat": 38.9531, "lon": -77.4565},
    "houston":      {"name": "George Bush Intercontinental Airport","iata":"IAH","city": "Houston",      "country": "USA",      "lat": 29.9902, "lon": -95.3368},

    # East Asia & Pacific
    "tokyo":        {"name": "Narita International Airport",      "iata": "NRT", "city": "Tokyo",        "country": "Japan",    "lat": 35.7647, "lon": 140.3864},
    "beijing":      {"name": "Beijing Capital International Airport","iata":"PEK","city": "Beijing",     "country": "China",    "lat": 40.0799, "lon": 116.6031},
    "shanghai":     {"name": "Shanghai Pudong International Airport","iata":"PVG","city": "Shanghai",    "country": "China",    "lat": 31.1443, "lon": 121.8083},
    "hong kong":    {"name": "Hong Kong International Airport",   "iata": "HKG", "city": "Hong Kong",    "country": "China",    "lat": 22.3080, "lon": 113.9185},
    "singapore":    {"name": "Changi Airport",                    "iata": "SIN", "city": "Singapore",    "country": "Singapore","lat": 1.3644,  "lon": 103.9915},
    "bangkok":      {"name": "Suvarnabhumi Airport",              "iata": "BKK", "city": "Bangkok",      "country": "Thailand", "lat": 13.6900, "lon": 100.7501},
    "kuala lumpur": {"name": "Kuala Lumpur International Airport","iata": "KUL", "city": "Kuala Lumpur", "country": "Malaysia", "lat": 2.7456,  "lon": 101.7099},
    "seoul":        {"name": "Incheon International Airport",     "iata": "ICN", "city": "Seoul",        "country": "South Korea","lat":37.4602,"lon": 126.4407},
    "sydney":       {"name": "Sydney Kingsford Smith Airport",    "iata": "SYD", "city": "Sydney",       "country": "Australia","lat": -33.9461,"lon": 151.1772},
    "melbourne":    {"name": "Melbourne Airport",                 "iata": "MEL", "city": "Melbourne",    "country": "Australia","lat": -37.6690,"lon": 144.8410},

    # Africa
    "cairo":        {"name": "Cairo International Airport",       "iata": "CAI", "city": "Cairo",        "country": "Egypt",        "lat": 30.1219, "lon": 31.4056},
    "johannesburg": {"name": "OR Tambo International Airport",    "iata": "JNB", "city": "Johannesburg", "country": "South Africa", "lat": -26.1392,"lon": 28.2460},
    "nairobi":      {"name": "Jomo Kenyatta International Airport","iata":"NBO", "city": "Nairobi",      "country": "Kenya",        "lat": -1.3192, "lon": 36.9278},
}


def _haversine(lat1, lon1, lat2, lon2):
    """Calculate great-circle distance between two points in km."""
    R = 6371  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def _find_airport(city_name):
    """Find airport by city name (case-insensitive, partial match)."""
    city = city_name.strip().lower()
    # Exact match first
    if city in AIRPORTS:
        return AIRPORTS[city]
    # Partial match
    for key, data in AIRPORTS.items():
        if city in key or key in city:
            return data
    return None


def predict_flight_time(origin, destination):
    """
    Predict flight time between two cities using real airport coordinates.
    Returns a dict with flight time, distance, airport info, and Google Maps links.
    """
    origin_airport = _find_airport(origin)
    destination_airport = _find_airport(destination)

    if not origin_airport or not destination_airport:
        missing = []
        if not origin_airport:
            missing.append(origin)
        if not destination_airport:
            missing.append(destination)
        return {
            "error": True,
            "message": f"Airport not found for: {', '.join(missing)}. Please try major city names like Lahore, Dubai, London, New York, etc."
        }

    # Calculate distance
    distance_km = _haversine(
        origin_airport["lat"], origin_airport["lon"],
        destination_airport["lat"], destination_airport["lon"]
    )
    distance_miles = distance_km * 0.621371

    # Estimate flight time
    # Average cruise speed: ~840 km/h for commercial jets
    # Add 30 min for taxi, takeoff, landing overhead
    cruise_speed_kmh = 840
    flight_hours = distance_km / cruise_speed_kmh
    total_hours = flight_hours + 0.5  # Add 30 min overhead

    hours = int(total_hours)
    minutes = int((total_hours - hours) * 60)

    # Google Maps links
    origin_maps_link = (
        f"https://www.google.com/maps/search/{origin_airport['name'].replace(' ', '+')}+"
        f"{origin_airport['city'].replace(' ', '+')}/@{origin_airport['lat']},{origin_airport['lon']},14z"
    )
    dest_maps_link = (
        f"https://www.google.com/maps/search/{destination_airport['name'].replace(' ', '+')}+"
        f"{destination_airport['city'].replace(' ', '+')}/@{destination_airport['lat']},{destination_airport['lon']},14z"
    )
    # Directions link (flight route on map)
    directions_link = (
        f"https://www.google.com/maps/dir/{origin_airport['lat']},{origin_airport['lon']}/"
        f"{destination_airport['lat']},{destination_airport['lon']}"
    )

    return {
        "error": False,
        "origin": origin_airport,
        "destination": destination_airport,
        "distance_km": round(distance_km),
        "distance_miles": round(distance_miles),
        "flight_time": f"{hours}h {minutes:02d}m",
        "hours": hours,
        "minutes": minutes,
        "origin_maps_link": origin_maps_link,
        "destination_maps_link": dest_maps_link,
        "directions_link": directions_link,
    }


def generate_otp(length=6):
    """Generate a 6-digit numeric OTP."""
    return ''.join(random.choices(string.digits, k=length))


def send_email_emailjs(email, otp):
    """Send OTP via EmailJS HTTP API."""
    url = "https://api.emailjs.com/api/v1.0/email/send"
    data = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id": EMAILJS_USER_ID,
        "template_params": {
            "email": email,
            "passcode": otp,
            "message": f"Your verification code is {otp}"
        }
    }

    if EMAILJS_PRIVATE_KEY:
        data['accessToken'] = EMAILJS_PRIVATE_KEY

    try:
        response = requests.post(url, json=data)

        print("Status Code:", response.status_code)
        print("Response:", response.text)

        if response.status_code == 200:
            return True
        else:
            return False

    except Exception as e:
        print(f"EmailJS Exception: {e}")
        return False


def send_notification_email_emailjs(email, subject, message):
    """Send Notification via EmailJS HTTP API."""
    url = "https://api.emailjs.com/api/v1.0/email/send"
    data = {
        "service_id": EMAILJS_SERVICE_ID,
        "template_id": EMAILJS_TEMPLATE_ID,
        "user_id": EMAILJS_USER_ID,
        "template_params": {
            "email": email,       
            "passcode": "N/A",      
            "message": f"Subject: {subject}\n\n{message}"
        }
    }
    
    if EMAILJS_PRIVATE_KEY:
        data['accessToken'] = EMAILJS_PRIVATE_KEY
    
    try:
        response = requests.post(url, json=data)
        if response.status_code == 200:
            return True
        else:
            print(f"EmailJS Notification Error: {response.text}")
            return False
    except Exception as e:
        print(f"EmailJS Notification Exception: {e}")
        return False


# Airlines list
AIRLINES = [
    {"name": "PIA", "code": "PK"},
    {"name": "Emirates", "code": "EK"},
    {"name": "Qatar Airways", "code": "QR"},
    {"name": "Etihad Airways", "code": "EY"},
    {"name": "Turkish Airlines", "code": "TK"},
    {"name": "British Airways", "code": "BA"},
    {"name": "Airblue", "code": "PA"},
    {"name": "SereneAir", "code": "ER"},
    {"name": "FlyJinnah", "code": "9P"},
]

# get_mock_flights has been removed to comply with centralized inventory system requirements.


def get_lowest_price_suggestion(flights):
    """Returns the flight with the lowest price."""
    if not flights:
        return None
    return min(flights, key=lambda x: x['price'])

def hash_password(password):
    """Hash a password for storing."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
# --- Validation Helpers ---

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Providers that reliably block AbstractAPI's mailbox verification checks,
# so a real user's address here would otherwise come back "UNKNOWN" and risk
# being wrongly rejected. Their domains are always genuine, so just trust the
# domain and skip mailbox-level verification for these.
MAJOR_EMAIL_PROVIDERS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "icloud.com", "live.com", "msn.com",
}

ABSTRACT_EMAIL_API_KEY = os.getenv('ABSTRACT_EMAIL_API_KEY', '').strip()


def is_valid_email(email):
    """
    Validates email format, then verifies deliverability:
    - Major providers (Gmail, Yahoo, Outlook, etc.): domain-only MX check,
      since AbstractAPI can't reliably confirm mailboxes on these anyway.
    - Everything else: strict mailbox-level verification via AbstractAPI,
      only accepting a confirmed "DELIVERABLE" result.
    """
    email = (email or "").strip()
    if not EMAIL_REGEX.match(email):
        return False

    domain = email.rsplit('@', 1)[-1].lower()

    if domain in MAJOR_EMAIL_PROVIDERS:
        return _is_valid_email_domain_only(email)

    if not ABSTRACT_EMAIL_API_KEY:
        return _is_valid_email_domain_only(email)

    try:
        response = requests.get(
            "https://emailvalidation.abstractapi.com/v1/",
            params={"api_key": ABSTRACT_EMAIL_API_KEY, "email": email},
            timeout=5,
        )
        result = response.json()
        is_deliverable = result.get("deliverability", "") == "DELIVERABLE"
        is_catch_all = result.get("is_catchall_email", {}).get("value", False)
        return is_deliverable and not is_catch_all

    except Exception as e:
        print(f"Email verification API error: {e}")
        return _is_valid_email_domain_only(email)


def _is_valid_email_domain_only(email):
    """Fallback / major-provider path: just checks the domain has MX records."""
    domain = email.rsplit('@', 1)[-1]
    try:
        import dns.resolver
        dns.resolver.resolve(domain, 'MX')
        return True
    except Exception:
        return False


import phonenumbers

def is_valid_phone(phone):
    """
    Validates a phone number for ANY country. Expects the number to include
    a country code (e.g. +923001234567, +14155552671, +447911123456).
    """
    phone = (phone or "").strip()
    if not phone:
        return True  # empty is allowed; caller decides if it's required

    try:
        parsed = phonenumbers.parse(phone, None)  # None = must include country code
    except phonenumbers.NumberParseException:
        return False

    return phonenumbers.is_valid_number(parsed)


def normalize_phone(phone):
    """Converts any valid international number to clean E.164 format (+countrycodeXXXXXXXXXX)."""
    phone = (phone or "").strip()
    if not phone:
        return phone
    try:
        parsed = phonenumbers.parse(phone, None)
        return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    except phonenumbers.NumberParseException:
        return phone

def check_password(hashed_password, user_password):
    """Check a hashed password."""
    return bcrypt.checkpw(user_password.encode('utf-8'), hashed_password.encode('utf-8'))


def login_user(email, password):
    """Authenticate a user against the database."""
    user = users.find_one(email=email)
    if user and check_password(user['password'], password):
        return {
            "localId": user['uid'],
            "email": user['email'],
            "displayName": user.get('name') or email.split('@')[0]
        }
    return None
