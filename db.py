"""PostgreSQL (Neon) access layer for SkySwap.

Everything the app persists lives in Postgres now. This module owns the
connection pool and exposes two ways to talk to it:

  * ``query`` / ``query_one`` / ``execute`` for hand-written SQL.
  * ``Table``, a thin helper for the CRUD that would otherwise be the same
    INSERT/SELECT/UPDATE typed out seventy times.

Both build statements with ``psycopg.sql`` and bind every value as a
parameter, so nothing here ever splices user input into SQL text.
"""

import datetime
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from dotenv import load_dotenv
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool

load_dotenv()

# Neon gives you two strings: a direct one and a "-pooler" one. Use the pooler
# for serverless (Vercel) so short-lived functions don't exhaust connections.
DATABASE_URL = (
    os.getenv("DATABASE_URL")
    or os.getenv("POSTGRES_URL")          # what the Vercel/Neon integration sets
    or ""
).strip()

_pool = None
_pool_lock = threading.Lock()

# Holds the connection belonging to the innermost `transaction()` block, so
# Table calls made inside one join that transaction instead of checking out a
# second connection from the pool.
_current_conn = ContextVar("skyswap_connection", default=None)


def _conninfo():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set.\n"
            "  Local:  put your Neon connection string in .env as DATABASE_URL=...\n"
            "  Vercel: add it under Project Settings -> Environment Variables."
        )
    return DATABASE_URL


def get_pool():
    """Lazily build the pool so importing this module never needs a network."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    conninfo=_conninfo(),
                    min_size=0,
                    max_size=int(os.getenv("DB_POOL_MAX", "5")),
                    max_idle=float(os.getenv("DB_POOL_MAX_IDLE", "60")),
                    timeout=float(os.getenv("DB_POOL_TIMEOUT", "15")),
                    # Neon suspends idle computes; verify a connection is alive
                    # before handing it out rather than failing the request.
                    check=ConnectionPool.check_connection,
                    kwargs={"row_factory": dict_row, "autocommit": True},
                    open=True,
                )
    return _pool


@contextmanager
def connection():
    """The transaction's connection if we're inside one, else a pooled one."""
    existing = _current_conn.get()
    if existing is not None:
        yield existing
        return
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def transaction():
    """Run every statement inside the block as one atomic unit.

    Nesting is safe: an inner `transaction()` joins the outer one rather than
    opening a second connection.
    """
    existing = _current_conn.get()
    if existing is not None:
        yield existing
        return
    with get_pool().connection() as conn:
        token = _current_conn.set(conn)
        try:
            with conn.transaction():
                yield conn
        finally:
            _current_conn.reset(token)


def query(statement, params=None):
    """Run a SELECT and return every row as a dict.

    `params` stays None when there is nothing to bind: psycopg only treats `%`
    as special when parameters are passed, so a parameterless query is free to
    contain a literal one.
    """
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.fetchall()


def query_one(statement, params=None):
    """Run a SELECT and return the first row, or None."""
    rows = query(statement, params)
    return rows[0] if rows else None


def execute(statement, params=None):
    """Run an INSERT/UPDATE/DELETE and return the number of affected rows."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            return cur.rowcount


def execute_script(script):
    """Run a multi-statement SQL script (used for schema creation)."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(script)


class Table:
    """CRUD over one table, with the column list known up front.

    Every column referenced by a caller is checked against ``columns`` before
    it reaches a statement, so a typo raises here instead of producing SQL that
    silently matches nothing.
    """

    def __init__(self, name, columns, date_columns=(), json_columns=()):
        self.name = name
        self.columns = tuple(columns)
        # DATE columns come back as `datetime.date`; the templates and the
        # JSON blobs posted between pages expect "YYYY-MM-DD" strings.
        self.date_columns = frozenset(date_columns)
        self.json_columns = frozenset(json_columns)

    # -- statement building ------------------------------------------------

    def _table(self):
        return sql.Identifier(self.name)

    def _column(self, name):
        if name not in self.columns:
            raise KeyError(f"table {self.name!r} has no column {name!r}")
        return sql.Identifier(name)

    def _where(self, criteria):
        if not criteria:
            return sql.SQL(""), []
        parts, params = [], []
        for name, value in criteria.items():
            column = self._column(name)
            if value is None:
                parts.append(sql.SQL("{} IS NULL").format(column))
            elif isinstance(value, (list, tuple, set)):
                parts.append(sql.SQL("{} = ANY(%s)").format(column))
                params.append(list(value))
            else:
                parts.append(sql.SQL("{} = %s").format(column))
                params.append(value)
        return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params

    def _order(self, order_by):
        """Turn "price DESC, created_at" into a checked ORDER BY clause."""
        if not order_by:
            return sql.SQL("")
        clauses = []
        for item in order_by.split(","):
            bits = item.strip().split()
            direction = sql.SQL("DESC") if len(bits) > 1 and bits[1].upper() == "DESC" else sql.SQL("ASC")
            clauses.append(sql.SQL("{} {}").format(self._column(bits[0]), direction))
        return sql.SQL(" ORDER BY ") + sql.SQL(", ").join(clauses)

    # -- value conversion --------------------------------------------------

    def _adapt(self, data):
        """Python values -> what psycopg should bind."""
        adapted = {}
        for name, value in data.items():
            self._column(name)
            if name in self.json_columns:
                value = Json(value)
            elif name in self.date_columns and value == "":
                value = None
            adapted[name] = value
        return adapted

    def _clean(self, row):
        """Row from Postgres -> what the rest of the app expects."""
        if row is None:
            return None
        for name in self.date_columns:
            value = row.get(name)
            if isinstance(value, (datetime.date, datetime.datetime)):
                row[name] = value.isoformat()[:10]
            elif value is None:
                row[name] = ""
        return row

    # -- reads -------------------------------------------------------------

    def find_one(self, **criteria):
        where, params = self._where(criteria)
        statement = sql.SQL("SELECT * FROM {}{} LIMIT 1").format(self._table(), where)
        return self._clean(query_one(statement, params))

    def find_all(self, order_by=None, limit=None, **criteria):
        where, params = self._where(criteria)
        statement = sql.SQL("SELECT * FROM {}{}{}").format(
            self._table(), where, self._order(order_by)
        )
        if limit is not None:
            statement = statement + sql.SQL(" LIMIT %s")
            params = params + [int(limit)]
        return [self._clean(row) for row in query(statement, params)]

    def count(self, **criteria):
        where, params = self._where(criteria)
        statement = sql.SQL("SELECT COUNT(*) AS total FROM {}{}").format(self._table(), where)
        return query_one(statement, params)["total"]

    def exists(self, **criteria):
        return self.count(**criteria) > 0

    # -- writes ------------------------------------------------------------

    def insert(self, data):
        values = self._adapt(data)
        names = list(values)
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            self._table(),
            sql.SQL(", ").join(sql.Identifier(name) for name in names),
            sql.SQL(", ").join(sql.Placeholder() for _ in names),
        )
        return self._clean(query_one(statement, [values[name] for name in names]))

    def upsert(self, key, data):
        """INSERT, or UPDATE the row whose unique ``key`` column already matches."""
        values = self._adapt(data)
        if key not in values:
            raise KeyError(f"upsert on {self.name!r} needs {key!r} in the data")
        names = list(values)
        updatable = [name for name in names if name != key]
        if updatable:
            conflict = sql.SQL("DO UPDATE SET ") + sql.SQL(", ").join(
                sql.SQL("{0} = EXCLUDED.{0}").format(sql.Identifier(name)) for name in updatable
            )
        else:
            conflict = sql.SQL("DO NOTHING")
        statement = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) {} RETURNING *"
        ).format(
            self._table(),
            sql.SQL(", ").join(sql.Identifier(name) for name in names),
            sql.SQL(", ").join(sql.Placeholder() for _ in names),
            sql.Identifier(key),
            conflict,
        )
        return self._clean(query_one(statement, [values[name] for name in names]))

    def update(self, criteria, values):
        """Returns the number of rows changed, so callers can detect a no-op."""
        values = self._adapt(values)
        if not values:
            return 0
        names = list(values)
        where, where_params = self._where(criteria)
        statement = sql.SQL("UPDATE {} SET {}{}").format(
            self._table(),
            sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(name)) for name in names),
            where,
        )
        return execute(statement, [values[name] for name in names] + where_params)

    def delete(self, **criteria):
        where, params = self._where(criteria)
        return execute(sql.SQL("DELETE FROM {}{}").format(self._table(), where), params)
