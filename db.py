import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # --- people (created by the earlier login version of this app) ---
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'people' AND column_name = 'auth0_sub'
                """
            )
            has_new_schema = cur.fetchone() is not None
            if not has_new_schema:
                cur.execute("DROP TABLE IF EXISTS logins")
                cur.execute("DROP TABLE IF EXISTS people")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS people (
                    id SERIAL PRIMARY KEY,
                    auth0_sub TEXT UNIQUE NOT NULL,
                    name TEXT,
                    email TEXT,
                    latitude DOUBLE PRECISION,
                    longitude DOUBLE PRECISION,
                    first_login TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # additive, safe even if the table already existed
            cur.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION")
            cur.execute("ALTER TABLE people ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS logins (
                    id SERIAL PRIMARY KEY,
                    person_id INTEGER NOT NULL REFERENCES people(id),
                    logged_in_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            # --- printers ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS printers (
                    id SERIAL PRIMARY KEY,
                    owner_id INTEGER NOT NULL REFERENCES people(id),
                    name TEXT NOT NULL,
                    description TEXT,
                    latitude DOUBLE PRECISION NOT NULL,
                    longitude DOUBLE PRECISION NOT NULL,
                    technology TEXT NOT NULL,
                    materials TEXT[] NOT NULL DEFAULT '{}',
                    nozzle_diameter_mm NUMERIC,
                    build_volume_x_mm INTEGER NOT NULL,
                    build_volume_y_mm INTEGER NOT NULL,
                    build_volume_z_mm INTEGER NOT NULL,
                    heated_bed BOOLEAN NOT NULL DEFAULT false,
                    enclosed BOOLEAN NOT NULL DEFAULT false,
                    max_nozzle_temp_c INTEGER,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            # --- projects ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id SERIAL PRIMARY KEY,
                    creator_id INTEGER NOT NULL REFERENCES people(id),
                    title TEXT NOT NULL,
                    description TEXT,
                    required_technology TEXT NOT NULL,
                    required_materials TEXT[] NOT NULL DEFAULT '{}',
                    required_build_volume_x_mm INTEGER NOT NULL,
                    required_build_volume_y_mm INTEGER NOT NULL,
                    required_build_volume_z_mm INTEGER NOT NULL,
                    required_nozzle_diameter_max_mm NUMERIC,
                    required_heated_bed BOOLEAN NOT NULL DEFAULT false,
                    required_enclosed BOOLEAN NOT NULL DEFAULT false,
                    image_data BYTEA,
                    image_mime TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # additive, safe even if the table already existed
            cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS image_data BYTEA")
            cur.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS image_mime TEXT")

            # --- orders ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    project_id INTEGER NOT NULL REFERENCES projects(id),
                    printer_id INTEGER NOT NULL REFERENCES printers(id),
                    requester_id INTEGER NOT NULL REFERENCES people(id),
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            # --- messages (per-order chat between requester and printer owner) ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id),
                    sender_id INTEGER NOT NULL REFERENCES people(id),
                    body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

            # tracks, per person per order, when they last read the chat
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_reads (
                    order_id INTEGER NOT NULL REFERENCES orders(id),
                    person_id INTEGER NOT NULL REFERENCES people(id),
                    last_read_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (order_id, person_id)
                )
                """
            )

            # web push subscriptions, one row per browser/device a person
            # has granted notification permission on
            cur.execute("DROP TABLE IF EXISTS push_subscriptions")

            # --- reviews: requester rates the printer after order completion ---
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),
                    printer_id INTEGER NOT NULL REFERENCES printers(id),
                    requester_id INTEGER NOT NULL REFERENCES people(id),
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
