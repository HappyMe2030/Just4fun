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

            # --- reviews: bidirectional feedback tied to a completed order.
            # target_type is 'printer' | 'project' | 'customer'; target_id
            # points at printers.id / projects.id / people.id respectively.
            cur.execute(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'reviews' AND column_name = 'target_type'
                """
            )
            reviews_has_new_schema = cur.fetchone() is not None
            if not reviews_has_new_schema:
                cur.execute("DROP TABLE IF EXISTS reviews")

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reviews (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER NOT NULL REFERENCES orders(id),
                    rater_id INTEGER NOT NULL REFERENCES people(id),
                    target_type TEXT NOT NULL CHECK (target_type IN ('printer', 'project', 'customer')),
                    target_id INTEGER NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (order_id, rater_id, target_type)
                )
                """
            )
        conn.commit()


def dict_cursor(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def seed_demo_data():
    """Inserts a small set of DEMO_NOT_USE printers/projects (and one
    completed demo order with reviews) so the app isn't empty on first
    load. No-ops if demo data already exists."""
    with get_conn() as conn:
        with dict_cursor(conn) as cur:
            cur.execute("SELECT 1 FROM printers WHERE name LIKE 'DEMO_NOT_USE%' LIMIT 1")
            if cur.fetchone():
                return

            def demo_person(sub, name, email):
                cur.execute(
                    """
                    INSERT INTO people (auth0_sub, name, email)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (auth0_sub) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                    """,
                    (sub, name, email),
                )
                return cur.fetchone()["id"]

            owner_id = demo_person(
                "demo|printer_owner", "DEMO_NOT_USE Printer Owner", "demo-owner@example.invalid"
            )
            creator_id = demo_person(
                "demo|project_creator", "DEMO_NOT_USE Project Creator", "demo-creator@example.invalid"
            )
            customer_id = demo_person(
                "demo|customer", "DEMO_NOT_USE Customer", "demo-customer@example.invalid"
            )

            printers = [
                (
                    "DEMO_NOT_USE Amsterdam FDM Pro",
                    "Reliable everyday FDM printer, centrally located.",
                    52.3702, 4.8952, "FDM", ["PLA", "PETG", "ABS"], 0.4,
                    250, 210, 220, True, False, 260,
                ),
                (
                    "DEMO_NOT_USE Haarlem Mini",
                    "Compact FDM printer, great for small parts.",
                    52.3874, 4.6462, "FDM", ["PLA", "TPU"], 0.4,
                    180, 180, 180, False, False, 240,
                ),
                (
                    "DEMO_NOT_USE Utrecht Resin Master",
                    "High-detail SLA resin printing for miniatures.",
                    52.0907, 5.1214, "SLA", ["RESIN_STANDARD", "RESIN_TOUGH"], None,
                    145, 145, 175, False, True, None,
                ),
            ]
            printer_ids = []
            for (name, desc, lat, lng, tech, materials, nozzle,
                 bx, by, bz, bed, enc, temp) in printers:
                cur.execute(
                    """
                    INSERT INTO printers (
                        owner_id, name, description, latitude, longitude,
                        technology, materials, nozzle_diameter_mm,
                        build_volume_x_mm, build_volume_y_mm, build_volume_z_mm,
                        heated_bed, enclosed, max_nozzle_temp_c
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (owner_id, name, desc, lat, lng, tech, materials, nozzle,
                     bx, by, bz, bed, enc, temp),
                )
                printer_ids.append(cur.fetchone()["id"])

            projects = [
                (
                    "DEMO_NOT_USE Articulated Dragon Toy",
                    "A fun flexible-jointed dragon print, no supports needed.",
                    "FDM", ["PLA", "PETG"], 150, 80, 60, 0.4, False, False,
                ),
                (
                    "DEMO_NOT_USE Phone Stand",
                    "Simple adjustable phone stand.",
                    "FDM", ["PLA"], 100, 100, 80, None, False, False,
                ),
                (
                    "DEMO_NOT_USE Miniature Figurine",
                    "High-detail 32mm tabletop miniature.",
                    "SLA", ["RESIN_STANDARD"], 60, 60, 100, None, False, True,
                ),
            ]
            project_ids = []
            for (title, desc, tech, materials, bx, by, bz,
                 nozzle, bed, enc) in projects:
                cur.execute(
                    """
                    INSERT INTO projects (
                        creator_id, title, description, required_technology,
                        required_materials, required_build_volume_x_mm,
                        required_build_volume_y_mm, required_build_volume_z_mm,
                        required_nozzle_diameter_max_mm, required_heated_bed,
                        required_enclosed
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    RETURNING id
                    """,
                    (creator_id, title, desc, tech, materials, bx, by, bz,
                     nozzle, bed, enc),
                )
                project_ids.append(cur.fetchone()["id"])

            # one completed order with reviews in both directions, so the
            # star ratings actually have something to show
            cur.execute(
                """
                INSERT INTO orders (project_id, printer_id, requester_id, status)
                VALUES (%s, %s, %s, 'completed')
                RETURNING id
                """,
                (project_ids[0], printer_ids[0], customer_id),
            )
            order_id = cur.fetchone()["id"]

            demo_reviews = [
                (customer_id, "printer", printer_ids[0], 5, "Great print quality, fast turnaround!"),
                (customer_id, "project", project_ids[0], 4, "Fun design, printed cleanly."),
                (owner_id, "customer", customer_id, 5, "Great communication, picked up on time."),
                (owner_id, "project", project_ids[0], 5, "Well designed model, no issues printing it."),
            ]
            for rater_id, target_type, target_id, rating, comment in demo_reviews:
                cur.execute(
                    """
                    INSERT INTO reviews (order_id, rater_id, target_type, target_id, rating, comment)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """,
                    (order_id, rater_id, target_type, target_id, rating, comment),
                )
        conn.commit()
