-- SkySwap relational schema (PostgreSQL / Neon)
--
-- Every statement is idempotent, so running this against an existing database
-- is safe. Apply it with:  python init_db.py

-- ---------------------------------------------------------------- users ----
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL   PRIMARY KEY,
    uid         TEXT        NOT NULL UNIQUE,
    email       TEXT        NOT NULL UNIQUE,
    password    TEXT        NOT NULL,
    name        TEXT        NOT NULL DEFAULT '',
    phone       TEXT        NOT NULL DEFAULT '',
    role        TEXT        NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    is_blocked  BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS users_role_idx ON users (role);

-- Signups that have been given an OTP but haven't confirmed it yet. One row
-- per email: asking for a new code overwrites the old one.
CREATE TABLE IF NOT EXISTS pending_users (
    id         BIGSERIAL   PRIMARY KEY,
    temp_id    TEXT        NOT NULL,
    email      TEXT        NOT NULL UNIQUE,
    password   TEXT        NOT NULL,
    name       TEXT        NOT NULL DEFAULT '',
    otp        TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------ external inventory --
-- The GDS simulator's own store: what the "airline side" holds. Formerly
-- external_inventory_db.json.
CREATE TABLE IF NOT EXISTS inventory_tickets (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_id           TEXT      NOT NULL UNIQUE,
    flight_number       TEXT      NOT NULL DEFAULT '',
    airline_name        TEXT      NOT NULL DEFAULT '',
    departure_city      TEXT      NOT NULL DEFAULT '',
    destination_city    TEXT      NOT NULL DEFAULT '',
    departure_date      DATE,
    departure_time      TEXT      NOT NULL DEFAULT '',
    arrival_time        TEXT      NOT NULL DEFAULT '',
    seat_number         TEXT      NOT NULL DEFAULT '',
    travel_class        TEXT      NOT NULL DEFAULT 'Economy',
    ticket_status       TEXT      NOT NULL DEFAULT 'Available',
    original_price      INTEGER   NOT NULL DEFAULT 0 CHECK (original_price >= 0),
    resale_price        INTEGER   NOT NULL DEFAULT 0 CHECK (resale_price >= 0),
    availability_status TEXT      NOT NULL DEFAULT 'Available',
    seller_name         TEXT      NOT NULL DEFAULT '',
    seller_phone        TEXT      NOT NULL DEFAULT '',
    seller_email        TEXT      NOT NULL DEFAULT '',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- SkySwap's own copy of the inventory, pulled in by the admin sync. Kept
-- separate on purpose: local ticket_status (Published/Unpublished) is a
-- SkySwap decision and must survive a re-sync.
CREATE TABLE IF NOT EXISTS fetched_tickets (
    id                  BIGSERIAL PRIMARY KEY,
    ticket_id           TEXT      NOT NULL UNIQUE,
    flight_number       TEXT      NOT NULL DEFAULT '',
    airline_name        TEXT      NOT NULL DEFAULT '',
    departure_city      TEXT      NOT NULL DEFAULT '',
    destination_city    TEXT      NOT NULL DEFAULT '',
    departure_date      DATE,
    departure_time      TEXT      NOT NULL DEFAULT '',
    arrival_time        TEXT      NOT NULL DEFAULT '',
    seat_number         TEXT      NOT NULL DEFAULT '',
    travel_class        TEXT      NOT NULL DEFAULT 'Economy',
    ticket_status       TEXT      NOT NULL DEFAULT 'Unpublished',
    original_price      INTEGER   NOT NULL DEFAULT 0 CHECK (original_price >= 0),
    resale_price        INTEGER   NOT NULL DEFAULT 0 CHECK (resale_price >= 0),
    availability_status TEXT      NOT NULL DEFAULT 'Available',
    seller_name         TEXT      NOT NULL DEFAULT '',
    seller_phone        TEXT      NOT NULL DEFAULT '',
    seller_email        TEXT      NOT NULL DEFAULT '',
    fetched_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The dashboard search hits this constantly: published tickets on a route.
-- Matches the WHERE clause in the dashboard search exactly, TRIM included,
-- otherwise the planner cannot use it.
CREATE INDEX IF NOT EXISTS fetched_tickets_route_idx
    ON fetched_tickets (ticket_status, LOWER(TRIM(departure_city)), LOWER(TRIM(destination_city)));

-- ------------------------------------------------------------- bookings ----
CREATE TABLE IF NOT EXISTS bookings (
    id             BIGSERIAL   PRIMARY KEY,
    booking_id     TEXT        NOT NULL UNIQUE,
    user_uid       TEXT        NOT NULL REFERENCES users (uid) ON DELETE CASCADE,
    user_email     TEXT        NOT NULL DEFAULT '',
    flight_number  TEXT        NOT NULL DEFAULT '',
    airline        TEXT        NOT NULL DEFAULT '',
    origin         TEXT        NOT NULL DEFAULT '',
    destination    TEXT        NOT NULL DEFAULT '',
    departure      TEXT        NOT NULL DEFAULT '',
    arrival        TEXT        NOT NULL DEFAULT '',
    departure_date DATE,
    seat_number    TEXT        NOT NULL DEFAULT '',
    travel_class   TEXT        NOT NULL DEFAULT 'Economy',
    price          INTEGER     NOT NULL DEFAULT 0 CHECK (price >= 0),
    status         TEXT        NOT NULL DEFAULT 'Confirmed',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS bookings_user_uid_idx ON bookings (user_uid);

-- --------------------------------------------------------- sell requests ----
-- A seller listing a booking on SkySwap, and the buyer who later claims it.
CREATE TABLE IF NOT EXISTS sell_requests (
    id                BIGSERIAL   PRIMARY KEY,
    request_id        TEXT        NOT NULL UNIQUE,
    booking_id        TEXT        NOT NULL REFERENCES bookings (booking_id) ON DELETE CASCADE,
    seller_uid        TEXT        NOT NULL DEFAULT '',
    seller_email      TEXT        NOT NULL DEFAULT '',
    buyer_uid         TEXT,
    buyer_email       TEXT,
    flight_number     TEXT        NOT NULL DEFAULT '',
    origin            TEXT        NOT NULL DEFAULT '',
    destination       TEXT        NOT NULL DEFAULT '',
    asking_price      INTEGER     NOT NULL DEFAULT 0 CHECK (asking_price >= 0),
    resale_reason     TEXT,
    status            TEXT        NOT NULL DEFAULT 'Pending Verification',
    verified_by       TEXT,
    verification_date TEXT,
    approved_by       TEXT,
    approved_at       TIMESTAMPTZ,
    rejected_by       TEXT,
    rejected_at       TIMESTAMPTZ,
    requested_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS sell_requests_status_idx     ON sell_requests (status);
CREATE INDEX IF NOT EXISTS sell_requests_booking_id_idx ON sell_requests (booking_id);

-- ---------------------------------------------------------- transactions ----
CREATE TABLE IF NOT EXISTS transactions (
    id             BIGSERIAL   PRIMARY KEY,
    transaction_id TEXT        NOT NULL UNIQUE,
    type           TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL DEFAULT '',
    request_id     TEXT,
    booking_id     TEXT,
    buyer_uid      TEXT,
    buyer_email    TEXT,
    seller_email   TEXT,
    flight_number  TEXT        NOT NULL DEFAULT '',
    airline        TEXT        NOT NULL DEFAULT '',
    origin         TEXT        NOT NULL DEFAULT '',
    destination    TEXT        NOT NULL DEFAULT '',
    amount         INTEGER     NOT NULL DEFAULT 0,
    bank_name      TEXT        NOT NULL DEFAULT '',
    card_holder    TEXT        NOT NULL DEFAULT '',
    status         TEXT        NOT NULL DEFAULT 'Pending',
    flow_steps     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at   TIMESTAMPTZ,
    rejected_at    TIMESTAMPTZ
);

-- The transactions page looks a user up as either side of the trade.
CREATE INDEX IF NOT EXISTS transactions_buyer_email_idx  ON transactions (buyer_email);
CREATE INDEX IF NOT EXISTS transactions_seller_email_idx ON transactions (seller_email);
