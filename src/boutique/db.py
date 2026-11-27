from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.pool import ThreadedConnectionPool

from boutique import config
from boutique.security import hash_password

_pool = None


def start_database():
    global _pool
    if not config.SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET doit être défini avant le démarrage de la boutique")
    if not config.ADMIN_EMAIL or not config.ADMIN_PASSWORD:
        raise RuntimeError("ADMIN_EMAIL et ADMIN_PASSWORD doivent être définis")
    if len(config.ADMIN_PASSWORD) < 12:
        raise RuntimeError("ADMIN_PASSWORD doit contenir au moins 12 caractères")

    _pool = ThreadedConnectionPool(1, 10, dsn=config.DATABASE_URL)
    with connection() as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute((Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8"))
                cur.execute(
                    """
                    INSERT INTO boutique.accounts (email, password_hash, role, full_name)
                    VALUES (%s, %s, 'admin', 'Administrateur')
                    ON CONFLICT (email) DO NOTHING
                    """,
                    (config.ADMIN_EMAIL, hash_password(config.ADMIN_PASSWORD)),
                )
                cur.execute(
                    "SELECT role FROM boutique.accounts WHERE email = %s",
                    (config.ADMIN_EMAIL,),
                )
                row = cur.fetchone()
                if row is None or row[0] != "admin":
                    raise RuntimeError("ADMIN_EMAIL est déjà utilisé par un compte non administrateur")


def stop_database():
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def connection():
    if _pool is None:
        raise RuntimeError("Le pool PostgreSQL de la boutique n'est pas démarré")
    conn = _pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
