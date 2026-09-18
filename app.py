import os
from functools import wraps
from urllib.parse import quote_plus, urlencode

import psycopg2
import requests
from authlib.integrations.flask_client import OAuth
from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request,
    session, url_for,
)
import db
import notifications
from constants import MATERIALS, NOZZLE_DIAMETERS_MM, TECHNOLOGIES
from images import InvalidImage, process_image
from matching import find_nearby_printers, browse_printers, get_target_rating
from tracing import init_tracing

app = Flask(__name__)
app.secret_key = os.environ["APP_SECRET_KEY"]
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB upload limit

AUTH0_DOMAIN = os.environ["AUTH0_DOMAIN"]
AUTH0_CLIENT_ID = os.environ["AUTH0_CLIENT_ID"]
AUTH0_CLIENT_SECRET = os.environ["AUTH0_CLIENT_SECRET"]

oauth = OAuth(app)
oauth.register(
    "auth0",
    client_id=AUTH0_CLIENT_ID,
    client_secret=AUTH0_CLIENT_SECRET,
    client_kwargs={"scope": "openid profile email"},
    server_metadata_url=f"https://{AUTH0_DOMAIN}/.well-known/openid-configuration",
)

db.init_db()
db.seed_demo_data()
init_tracing(app, db)


def avatar_url(name, picture=None, size=64):
    """A real profile photo (from Auth0) when we have one, otherwise a
    generated initials avatar so every user/printer/project always shows
    something, including seeded demo accounts with no Auth0 login."""
    if picture:
        return picture
    return (
        "https://ui-avatars.com/api/?"
        + urlencode({
            "name": name or "?", "background": "1C2321", "color": "EDEFE9",
            "size": size, "rounded": "true", "bold": "true",
        })
    )


app.jinja_env.globals["avatar_url"] = avatar_url


# ---------- OCI APM: Real User Monitoring (browser) ----------
# RUM is configured entirely via env vars so it can be turned on without code
# changes, and skips cleanly (no snippet rendered) if unset.
#   APM_RUM_ENDPOINT            e.g. https://aaaa....apm-agt.eu-frankfurt-1.oci.oraclecloud.com
#                                (this is OCI's "Data Upload Endpoint" for the APM domain)
#   APM_RUM_PUBLIC_DATA_KEY     the domain's public Data Key (RUM/browser use only —
#                                never use the private key here, it's exposed client-side)
#   APM_RUM_SERVICE_NAME        optional, defaults below
#   APM_RUM_WEB_APP_NAME        optional, defaults below
APM_RUM_ENDPOINT = os.environ.get("APM_RUM_ENDPOINT")
APM_RUM_PUBLIC_DATA_KEY = os.environ.get("APM_RUM_PUBLIC_DATA_KEY")
APM_RUM_SERVICE_NAME = os.environ.get("APM_RUM_SERVICE_NAME", "3D Print Marketplace")
APM_RUM_WEB_APP_NAME = os.environ.get("APM_RUM_WEB_APP_NAME", "3d-print-marketplace")


# ---------- auth helpers ----------

def get_or_create_person(sub, name, email, picture=None):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO people (auth0_sub, name, email, picture)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (auth0_sub) DO UPDATE
                    SET name = EXCLUDED.name, email = EXCLUDED.email,
                        picture = EXCLUDED.picture
                RETURNING *
                """,
                (sub, name, email, picture),
            )
            person = cur.fetchone()
            cur.execute(
                "INSERT INTO logins (person_id) VALUES (%s)", (person["id"],)
            )
        conn.commit()
    return person


@app.before_request
def load_user():
    g.user = session.get("user")
    g.person = None
    if g.user:
        with db.get_conn() as conn:
            with db.dict_cursor(conn) as cur:
                cur.execute(
                    "SELECT * FROM people WHERE auth0_sub = %s", (g.user["sub"],)
                )
                g.person = cur.fetchone()


@app.context_processor
def inject_user():
    return {
        "user": g.get("user"), "person": g.get("person"),
        "apm_rum_endpoint": APM_RUM_ENDPOINT,
        "apm_rum_public_data_key": APM_RUM_PUBLIC_DATA_KEY,
        "apm_rum_service_name": APM_RUM_SERVICE_NAME,
        "apm_rum_web_app_name": APM_RUM_WEB_APP_NAME,
    }


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.user:
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------- auth routes ----------

@app.route("/login")
def login():
    return oauth.auth0.authorize_redirect(
        redirect_uri=url_for("callback", _external=True)
    )


@app.route("/callback")
def callback():
    token = oauth.auth0.authorize_access_token()
    userinfo = token["userinfo"]
    session["user"] = userinfo
    get_or_create_person(
        sub=userinfo["sub"], name=userinfo.get("name"), email=userinfo.get("email"),
        picture=userinfo.get("picture"),
    )
    return redirect(url_for("home"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(
        f"https://{AUTH0_DOMAIN}/v2/logout?"
        + urlencode(
            {"returnTo": url_for("home", _external=True), "client_id": AUTH0_CLIENT_ID},
            quote_via=quote_plus,
        )
    )


# ---------- home / profile ----------

@app.route("/")
def home():
    has_location = bool(g.person and g.person["latitude"] is not None)
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM printers")
            printer_count = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM projects")
            project_count = cur.fetchone()["cnt"]

            cur.execute(
                """
                SELECT p.*, pe.name AS creator_name, pe.picture AS creator_picture,
                       AVG(r.rating)::numeric(3,2) AS avg_rating, COUNT(r.id) AS review_count
                FROM projects p
                JOIN people pe ON pe.id = p.creator_id
                JOIN reviews r ON r.target_type = 'project' AND r.target_id = p.id
                GROUP BY p.id, pe.name, pe.picture
                ORDER BY avg_rating DESC, review_count DESC
                LIMIT 6
                """
            )
            top_ideas = cur.fetchall()
    return render_template(
        "home.html", has_location=has_location,
        printer_count=printer_count, project_count=project_count,
        top_ideas=top_ideas,
    )


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


@app.route("/profile/location", methods=["POST"])
@login_required
def update_location():
    lat = float(request.form["latitude"])
    lng = float(request.form["longitude"])
    address = request.form.get("address", "").strip() or None
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE people SET latitude = %s, longitude = %s, address = %s WHERE id = %s",
                (lat, lng, address, g.person["id"]),
            )
        conn.commit()
    flash("Location updated.")
    return redirect(url_for("profile"))


NOMINATIM_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT", "3DPrintMarketplace/1.0 (contact not configured)"
)


@app.route("/geocode")
@login_required
def geocode():
    address = request.args.get("address", "").strip()
    if not address:
        return {"error": "Enter an address first."}, 400
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address, "format": "json", "limit": 1},
            headers={"User-Agent": NOMINATIM_USER_AGENT},
            timeout=8,
        )
        resp.raise_for_status()
        results = resp.json()
    except Exception as e:
        app.logger.warning("geocode failed: %s", e)
        return {"error": "Couldn't reach the geocoding service. Try again."}, 502
    if not results:
        return {"error": "No location found for that address."}, 404
    top = results[0]
    return {
        "lat": float(top["lat"]), "lng": float(top["lon"]),
        "display_name": top["display_name"],
    }


# ---------- printers ----------

@app.route("/printers")
@login_required
def list_printers():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM printers WHERE owner_id = %s ORDER BY created_at DESC",
                (g.person["id"],),
            )
            my_printers = cur.fetchall()
    for p in my_printers:
        p["rating"] = get_target_rating("printer", p["id"], max_comments=0)

    has_location = g.person["latitude"] is not None
    per_page = 10
    page = max(1, request.args.get("page", 1, type=int))
    filter_technology = request.args.get("technology") or None
    filter_material = request.args.get("material") or None
    nearby_printers, total = [], 0
    if has_location:
        nearby_printers, total = browse_printers(
            g.person["latitude"], g.person["longitude"], page=page, per_page=per_page,
            technology=filter_technology, material=filter_material,
        )
    total_pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "printers_list.html", my_printers=my_printers, has_location=has_location,
        nearby_printers=nearby_printers, page=page, total_pages=total_pages,
        technologies=TECHNOLOGIES, materials=MATERIALS,
        filter_technology=filter_technology, filter_material=filter_material,
    )


@app.route("/printers/<int:printer_id>")
@login_required
def printer_detail(printer_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT pr.*, pe.name AS owner_name, pe.picture AS owner_picture
                FROM printers pr
                JOIN people pe ON pe.id = pr.owner_id
                WHERE pr.id = %s
                """,
                (printer_id,),
            )
            printer = cur.fetchone()
    if not printer:
        flash("Printer not found.")
        return redirect(url_for("list_printers"))

    is_owner = printer["owner_id"] == g.person["id"]
    rating = get_target_rating("printer", printer_id, max_comments=50)
    return render_template(
        "printer_detail.html", printer=printer, is_owner=is_owner, rating=rating,
    )


@app.route("/printers/add", methods=["GET", "POST"])
@login_required
def add_printer():
    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("materials")
        nozzle = f.get("nozzle_diameter_mm") or None
        max_temp = f.get("max_nozzle_temp_c") or None
        address = f.get("address", "").strip() or None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO printers (
                        owner_id, name, description, address, latitude, longitude,
                        technology, materials, nozzle_diameter_mm,
                        build_volume_x_mm, build_volume_y_mm, build_volume_z_mm,
                        heated_bed, enclosed, max_nozzle_temp_c
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        g.person["id"], f["name"], f.get("description"), address,
                        float(f["latitude"]), float(f["longitude"]),
                        f["technology"], materials, nozzle,
                        int(f["build_volume_x_mm"]), int(f["build_volume_y_mm"]),
                        int(f["build_volume_z_mm"]),
                        "heated_bed" in f, "enclosed" in f, max_temp,
                    ),
                )
            conn.commit()
        flash("Printer added.")
        return redirect(url_for("list_printers"))

    return render_template(
        "printer_form.html",
        technologies=TECHNOLOGIES, materials=MATERIALS,
        nozzle_diameters=NOZZLE_DIAMETERS_MM,
    )


@app.route("/printers/<int:printer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_printer(printer_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM printers WHERE id = %s", (printer_id,))
            printer = cur.fetchone()
    if not printer:
        flash("Printer not found.")
        return redirect(url_for("list_printers"))
    if printer["owner_id"] != g.person["id"]:
        flash("You can only edit your own printers.")
        return redirect(url_for("list_printers"))

    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("materials")
        nozzle = f.get("nozzle_diameter_mm") or None
        max_temp = f.get("max_nozzle_temp_c") or None
        address = f.get("address", "").strip() or None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE printers SET
                        name=%s, description=%s, address=%s, latitude=%s, longitude=%s,
                        technology=%s, materials=%s, nozzle_diameter_mm=%s,
                        build_volume_x_mm=%s, build_volume_y_mm=%s, build_volume_z_mm=%s,
                        heated_bed=%s, enclosed=%s, max_nozzle_temp_c=%s
                    WHERE id = %s
                    """,
                    (
                        f["name"], f.get("description"), address,
                        float(f["latitude"]), float(f["longitude"]),
                        f["technology"], materials, nozzle,
                        int(f["build_volume_x_mm"]), int(f["build_volume_y_mm"]),
                        int(f["build_volume_z_mm"]),
                        "heated_bed" in f, "enclosed" in f, max_temp,
                        printer_id,
                    ),
                )
            conn.commit()
        flash("Printer updated.")
        return redirect(url_for("list_printers"))

    return render_template(
        "printer_form.html",
        technologies=TECHNOLOGIES, materials=MATERIALS,
        nozzle_diameters=NOZZLE_DIAMETERS_MM, printer=printer,
    )


@app.route("/printers/<int:printer_id>/delete", methods=["POST"])
@login_required
def delete_printer(printer_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM printers WHERE id = %s", (printer_id,))
            printer = cur.fetchone()
    if not printer:
        flash("Printer not found.")
        return redirect(url_for("list_printers"))
    if printer["owner_id"] != g.person["id"]:
        flash("You can only delete your own printers.")
        return redirect(url_for("list_printers"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reviews WHERE order_id IN (SELECT id FROM orders WHERE printer_id = %s)",
                (printer_id,),
            )
            cur.execute(
                "DELETE FROM messages WHERE order_id IN (SELECT id FROM orders WHERE printer_id = %s)",
                (printer_id,),
            )
            cur.execute(
                "DELETE FROM chat_reads WHERE order_id IN (SELECT id FROM orders WHERE printer_id = %s)",
                (printer_id,),
            )
            cur.execute("DELETE FROM orders WHERE printer_id = %s", (printer_id,))
            cur.execute("DELETE FROM printers WHERE id = %s", (printer_id,))
        conn.commit()
    flash("Printer deleted.")
    return redirect(url_for("list_printers"))


# ---------- projects ----------

@app.route("/projects")
@login_required
def list_projects():
    filter_technology = request.args.get("technology") or None
    filter_material = request.args.get("material") or None

    where_clauses = []
    params = {}
    if filter_technology:
        where_clauses.append("p.required_technology = %(technology)s")
        params["technology"] = filter_technology
    if filter_material:
        where_clauses.append("%(material)s = ANY(p.required_materials)")
        params["material"] = filter_material
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT p.*, pe.name AS creator_name, pe.picture AS creator_picture
                FROM projects p
                JOIN people pe ON pe.id = p.creator_id
                {where_sql}
                ORDER BY p.created_at DESC
                """,
                params,
            )
            projects = cur.fetchall()
    for p in projects:
        p["rating"] = get_target_rating("project", p["id"], max_comments=0)
    return render_template(
        "projects_list.html", projects=projects,
        technologies=TECHNOLOGIES, materials=MATERIALS,
        filter_technology=filter_technology, filter_material=filter_material,
    )


@app.route("/projects/add", methods=["GET", "POST"])
@login_required
def add_project():
    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("required_materials")
        max_nozzle = f.get("required_nozzle_diameter_max_mm") or None

        image_data, image_mime = None, None
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            try:
                resized_bytes, image_mime = process_image(image_file)
            except InvalidImage as e:
                flash(str(e))
                return render_template(
                    "project_form.html",
                    technologies=TECHNOLOGIES, materials=MATERIALS,
                    nozzle_diameters=NOZZLE_DIAMETERS_MM,
                )
            image_data = psycopg2.Binary(resized_bytes)

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO projects (
                        creator_id, title, description, required_technology,
                        required_materials, required_build_volume_x_mm,
                        required_build_volume_y_mm, required_build_volume_z_mm,
                        required_nozzle_diameter_max_mm, required_heated_bed,
                        required_enclosed, image_data, image_mime
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        g.person["id"], f["title"], f.get("description"),
                        f["required_technology"], materials,
                        int(f["required_build_volume_x_mm"]),
                        int(f["required_build_volume_y_mm"]),
                        int(f["required_build_volume_z_mm"]),
                        max_nozzle,
                        "required_heated_bed" in f, "required_enclosed" in f,
                        image_data, image_mime,
                    ),
                )
            conn.commit()
        flash("Idea posted.")
        return redirect(url_for("list_projects"))

    return render_template(
        "project_form.html",
        technologies=TECHNOLOGIES, materials=MATERIALS,
        nozzle_diameters=NOZZLE_DIAMETERS_MM,
    )


@app.route("/projects/<int:project_id>/edit", methods=["GET", "POST"])
@login_required
def edit_project(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            project = cur.fetchone()
    if not project:
        flash("Idea not found.")
        return redirect(url_for("list_projects"))
    if project["creator_id"] != g.person["id"]:
        flash("You can only edit your own ideas.")
        return redirect(url_for("list_projects"))

    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("required_materials")
        max_nozzle = f.get("required_nozzle_diameter_max_mm") or None

        image_data, image_mime = project["image_data"], project["image_mime"]
        image_file = request.files.get("image")
        if image_file and image_file.filename:
            try:
                resized_bytes, image_mime = process_image(image_file)
            except InvalidImage as e:
                flash(str(e))
                return render_template(
                    "project_form.html",
                    technologies=TECHNOLOGIES, materials=MATERIALS,
                    nozzle_diameters=NOZZLE_DIAMETERS_MM, project=project,
                )
            image_data = psycopg2.Binary(resized_bytes)
        elif "remove_image" in f:
            image_data, image_mime = None, None

        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects SET
                        title=%s, description=%s, required_technology=%s,
                        required_materials=%s, required_build_volume_x_mm=%s,
                        required_build_volume_y_mm=%s, required_build_volume_z_mm=%s,
                        required_nozzle_diameter_max_mm=%s, required_heated_bed=%s,
                        required_enclosed=%s, image_data=%s, image_mime=%s
                    WHERE id = %s
                    """,
                    (
                        f["title"], f.get("description"), f["required_technology"],
                        materials,
                        int(f["required_build_volume_x_mm"]),
                        int(f["required_build_volume_y_mm"]),
                        int(f["required_build_volume_z_mm"]),
                        max_nozzle,
                        "required_heated_bed" in f, "required_enclosed" in f,
                        image_data, image_mime,
                        project_id,
                    ),
                )
            conn.commit()
        flash("Idea updated.")
        return redirect(url_for("project_detail", project_id=project_id))

    return render_template(
        "project_form.html",
        technologies=TECHNOLOGIES, materials=MATERIALS,
        nozzle_diameters=NOZZLE_DIAMETERS_MM, project=project,
    )


@app.route("/projects/<int:project_id>/delete", methods=["POST"])
@login_required
def delete_project(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            project = cur.fetchone()
    if not project:
        flash("Idea not found.")
        return redirect(url_for("list_projects"))
    if project["creator_id"] != g.person["id"]:
        flash("You can only delete your own ideas.")
        return redirect(url_for("list_projects"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM reviews WHERE order_id IN (SELECT id FROM orders WHERE project_id = %s)",
                (project_id,),
            )
            cur.execute(
                "DELETE FROM messages WHERE order_id IN (SELECT id FROM orders WHERE project_id = %s)",
                (project_id,),
            )
            cur.execute(
                "DELETE FROM chat_reads WHERE order_id IN (SELECT id FROM orders WHERE project_id = %s)",
                (project_id,),
            )
            cur.execute("DELETE FROM orders WHERE project_id = %s", (project_id,))
            cur.execute("DELETE FROM projects WHERE id = %s", (project_id,))
        conn.commit()
    flash("Idea deleted.")
    return redirect(url_for("list_projects"))


@app.route("/projects/<int:project_id>/image")
def project_image(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                "SELECT image_data, image_mime FROM projects WHERE id = %s",
                (project_id,),
            )
            row = cur.fetchone()
    if not row or not row["image_data"]:
        abort(404)
    return Response(bytes(row["image_data"]), mimetype=row["image_mime"] or "application/octet-stream")


@app.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT p.*, pe.name AS creator_name, pe.picture AS creator_picture
                FROM projects p
                JOIN people pe ON pe.id = p.creator_id
                WHERE p.id = %s
                """,
                (project_id,),
            )
            project = cur.fetchone()
    if not project:
        flash("Idea not found.")
        return redirect(url_for("list_projects"))

    has_location = g.person["latitude"] is not None
    printers = []
    if has_location:
        printers = find_nearby_printers(
            project, g.person["latitude"], g.person["longitude"]
        )
    is_owner = project["creator_id"] == g.person["id"]
    rating = get_target_rating("project", project["id"])
    return render_template(
        "project_detail.html", project=project, printers=printers,
        has_location=has_location, is_owner=is_owner, rating=rating,
    )


# ---------- orders ----------

@app.route("/orders/create", methods=["POST"])
@login_required
def create_order():
    project_id = int(request.form["project_id"])
    printer_id = int(request.form["printer_id"])
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO orders (project_id, printer_id, requester_id)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (project_id, printer_id, g.person["id"]),
            )
            order_id = cur.fetchone()["id"]

            cur.execute(
                """
                SELECT pr.owner_id AS printer_owner_id, pe1.email AS printer_owner_email,
                       pe1.name AS printer_owner_name, p.creator_id AS project_creator_id,
                       pe2.email AS project_creator_email, pe2.name AS project_creator_name,
                       pr.name AS printer_name, p.title AS project_title
                FROM printers pr
                JOIN people pe1 ON pe1.id = pr.owner_id
                JOIN projects p ON p.id = %s
                JOIN people pe2 ON pe2.id = p.creator_id
                WHERE pr.id = %s
                """,
                (project_id, printer_id),
            )
            info = cur.fetchone()
        conn.commit()

    requester_name = g.person["name"] or g.person["email"] or "Someone"
    order_url = url_for("list_orders", _external=True)
    notified_emails = set()
    if info["printer_owner_id"] != g.person["id"] and info["printer_owner_email"]:
        notifications.send_email(
            app, info["printer_owner_email"], f"New print request: {info['project_title']}",
            f"{requester_name} requested a print of \"{info['project_title']}\" on your "
            f"printer \"{info['printer_name']}\".\n\nView it here: {order_url}",
        )
        notified_emails.add(info["printer_owner_email"])
    if (
        info["project_creator_id"] != g.person["id"]
        and info["project_creator_email"]
        and info["project_creator_email"] not in notified_emails
    ):
        notifications.send_email(
            app, info["project_creator_email"], f"Your idea is being printed: {info['project_title']}",
            f"{requester_name} ordered a print of your idea \"{info['project_title']}\" "
            f"on the printer \"{info['printer_name']}\".\n\nView it here: {order_url}",
        )

    flash("Request sent to the printer owner.")
    return redirect(url_for("list_orders"))


VALID_TRANSITIONS = {
    ("pending", "accepted"): "owner",
    ("pending", "rejected"): "owner",
    ("pending", "cancelled"): "requester",
    ("accepted", "printing"): "owner",
    ("printing", "ready_for_pickup"): "owner",
    ("ready_for_pickup", "completed"): "requester",
}


@app.route("/orders/<int:order_id>/<new_status>", methods=["POST"])
@login_required
def order_transition(order_id, new_status):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT o.*, pr.owner_id AS printer_owner_id
                FROM orders o
                JOIN printers pr ON pr.id = o.printer_id
                WHERE o.id = %s
                """,
                (order_id,),
            )
            order = cur.fetchone()
            if not order:
                flash("Order not found.")
                return redirect(url_for("list_orders"))

            is_requester = order["requester_id"] == g.person["id"]
            is_owner = order["printer_owner_id"] == g.person["id"]
            allowed_role = VALID_TRANSITIONS.get((order["status"], new_status))
            has_permission = (
                (allowed_role == "owner" and is_owner)
                or (allowed_role == "requester" and is_requester)
            )
            if not has_permission:
                flash("You can't perform that action on this order.")
                return redirect(url_for("list_orders"))

            cur.execute(
                "UPDATE orders SET status = %s, updated_at = now() WHERE id = %s",
                (new_status, order_id),
            )
        conn.commit()
    return redirect(url_for("list_orders"))


def get_order_for_participant(order_id):
    """Loads an order, returning (order, roles) where roles is the subset of
    {"requester", "owner"} the current person actually holds on this order —
    both, if they ordered their own project from their own printer. Returns
    (None, set()) if they're not a participant at all."""
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT o.*, p.title AS project_title, pr.name AS printer_name,
                       pr.owner_id AS printer_owner_id
                FROM orders o
                JOIN projects p ON p.id = o.project_id
                JOIN printers pr ON pr.id = o.printer_id
                WHERE o.id = %s
                """,
                (order_id,),
            )
            order = cur.fetchone()
    if not order:
        return None, set()
    roles = set()
    if order["requester_id"] == g.person["id"]:
        roles.add("requester")
    if order["printer_owner_id"] == g.person["id"]:
        roles.add("owner")
    if not roles:
        return None, set()
    return order, roles


@app.route("/orders/<int:order_id>/delete", methods=["POST"])
@login_required
def delete_order(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        flash("Order not found.")
        return redirect(url_for("list_orders"))
    if "requester" not in roles:
        flash("Only the person who requested the print can delete this order.")
        return redirect(url_for("list_orders"))

    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM reviews WHERE order_id = %s", (order_id,))
            cur.execute("DELETE FROM messages WHERE order_id = %s", (order_id,))
            cur.execute("DELETE FROM chat_reads WHERE order_id = %s", (order_id,))
            cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        conn.commit()
    flash("Order deleted.")
    return redirect(url_for("list_orders"))


@app.route("/orders/<int:order_id>/chat")
@login_required
def order_chat(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        flash("You don't have access to that order's chat.")
        return redirect(url_for("list_orders"))
    return render_template("chat.html", order=order, roles=roles)


@app.route("/orders/<int:order_id>/messages.json")
@login_required
def order_messages(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        abort(403)
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT m.id, m.body, m.created_at, m.sender_id, pe.name AS sender_name
                FROM messages m
                JOIN people pe ON pe.id = m.sender_id
                WHERE m.order_id = %s
                ORDER BY m.created_at ASC
                """,
                (order_id,),
            )
            rows = cur.fetchall()
    return {
        "messages": [
            {
                "id": r["id"],
                "body": r["body"],
                "created_at": r["created_at"].isoformat(),
                "is_mine": r["sender_id"] == g.person["id"],
                "sender_name": r["sender_name"],
            }
            for r in rows
        ]
    }


@app.route("/orders/<int:order_id>/messages", methods=["POST"])
@login_required
def send_message(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        abort(403)
    if request.is_json:
        body = (request.get_json(silent=True) or {}).get("body") or ""
    else:
        body = request.form.get("body") or ""
    body = body.strip()
    if not body:
        return {"error": "empty"}, 400

    other_person_id = (
        order["printer_owner_id"] if g.person["id"] == order["requester_id"]
        else order["requester_id"]
    )

    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM messages m
                LEFT JOIN chat_reads cr
                    ON cr.order_id = m.order_id AND cr.person_id = %(pid)s
                WHERE m.order_id = %(oid)s AND m.sender_id != %(pid)s
                  AND m.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                """,
                {"pid": other_person_id, "oid": order_id},
            )
            prior_unread = cur.fetchone()["cnt"]

            cur.execute(
                "INSERT INTO messages (order_id, sender_id, body) VALUES (%s, %s, %s)",
                (order_id, g.person["id"], body[:2000]),
            )
        conn.commit()

    if other_person_id != g.person["id"] and prior_unread == 0:
        with db.get_conn() as conn:
            with db.dict_cursor(conn) as cur:
                cur.execute("SELECT email FROM people WHERE id = %s", (other_person_id,))
                recipient = cur.fetchone()
        if recipient and recipient["email"]:
            sender_name = g.person["name"] or g.person["email"] or "Someone"
            chat_url = url_for("order_chat", order_id=order_id, _external=True)
            notifications.send_email(
                app, recipient["email"], f"New message about {order['project_title']}",
                f"{sender_name} sent you a message:\n\n{body}\n\nReply here: {chat_url}",
            )

    return {"ok": True}


@app.route("/orders/<int:order_id>/mark_read", methods=["POST"])
@login_required
def mark_read(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        abort(403)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO chat_reads (order_id, person_id, last_read_at)
                VALUES (%s, %s, now())
                ON CONFLICT (order_id, person_id) DO UPDATE SET last_read_at = now()
                """,
                (order_id, g.person["id"]),
            )
        conn.commit()
    return {"ok": True}


@app.route("/api/unread_count")
@login_required
def api_unread_count():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM messages m
                JOIN orders o ON o.id = m.order_id
                JOIN printers pr ON pr.id = o.printer_id
                LEFT JOIN chat_reads cr
                    ON cr.order_id = o.id AND cr.person_id = %(pid)s
                WHERE (o.requester_id = %(pid)s OR pr.owner_id = %(pid)s)
                  AND m.sender_id != %(pid)s
                  AND m.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                """,
                {"pid": g.person["id"]},
            )
            count = cur.fetchone()["cnt"]
    return {"count": count}


REVIEW_TARGETS_BY_ROLE = {
    "requester": ["printer", "project"],
    "owner": ["customer", "project"],
}


def _review_target_id(order, target_type):
    return {
        "printer": order["printer_id"],
        "project": order["project_id"],
        "customer": order["requester_id"],
    }[target_type]


@app.route("/orders/<int:order_id>/review")
@login_required
def review_order(order_id):
    order, roles = get_order_for_participant(order_id)
    if not order:
        flash("You don't have access to that order.")
        return redirect(url_for("list_orders"))
    if order["status"] != "completed":
        flash("You can only review an order once it's completed.")
        return redirect(url_for("list_orders"))

    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                "SELECT * FROM reviews WHERE order_id = %s AND rater_id = %s",
                (order_id, g.person["id"]),
            )
            existing_by_type = {r["target_type"]: r for r in cur.fetchall()}

    target_types = []
    for role in ("requester", "owner"):
        if role in roles:
            for t in REVIEW_TARGETS_BY_ROLE[role]:
                if t not in target_types:
                    target_types.append(t)

    targets = []
    labels = {
        "printer": f"Rate the printer ({order['printer_name']})",
        "project": f"Rate the idea ({order['project_title']})",
        "customer": "Rate the customer",
    }
    for target_type in target_types:
        targets.append({
            "type": target_type,
            "label": labels[target_type],
            "existing": existing_by_type.get(target_type),
        })

    return render_template("review_form.html", order=order, targets=targets)


@app.route("/orders/<int:order_id>/review/<target_type>", methods=["POST"])
@login_required
def submit_review(order_id, target_type):
    order, roles = get_order_for_participant(order_id)
    if not order:
        abort(403)
    if order["status"] != "completed":
        flash("You can only review an order once it's completed.")
        return redirect(url_for("list_orders"))
    allowed_types = set()
    for role in roles:
        allowed_types.update(REVIEW_TARGETS_BY_ROLE[role])
    if target_type not in allowed_types:
        abort(403)

    rating = int(request.form["rating"])
    comment = request.form.get("comment", "").strip() or None
    if rating < 1 or rating > 5:
        flash("Rating must be between 1 and 5.")
        return redirect(url_for("review_order", order_id=order_id))

    target_id = _review_target_id(order, target_type)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reviews (order_id, rater_id, target_type, target_id, rating, comment)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_id, rater_id, target_type) DO UPDATE
                    SET rating = EXCLUDED.rating, comment = EXCLUDED.comment
                """,
                (order_id, g.person["id"], target_type, target_id, rating, comment),
            )
        conn.commit()
    flash("Thanks for the feedback!")
    return redirect(url_for("review_order", order_id=order_id))


# ---------- direct conversations (contact a printer/idea owner) ----------

def _get_or_create_conversation(context_type, context_id, other_person_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT * FROM conversations
                WHERE context_type = %(ctype)s AND context_id = %(cid)s
                  AND (
                        (person_a_id = %(me)s AND person_b_id = %(other)s)
                        OR (person_a_id = %(other)s AND person_b_id = %(me)s)
                      )
                """,
                {
                    "ctype": context_type, "cid": context_id,
                    "me": g.person["id"], "other": other_person_id,
                },
            )
            convo = cur.fetchone()
            if convo:
                return convo["id"]

            cur.execute(
                """
                INSERT INTO conversations (person_a_id, person_b_id, context_type, context_id)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (g.person["id"], other_person_id, context_type, context_id),
            )
            new_id = cur.fetchone()["id"]
        conn.commit()
    return new_id


@app.route("/printers/<int:printer_id>/contact", methods=["POST"])
@login_required
def contact_printer_owner(printer_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM printers WHERE id = %s", (printer_id,))
            printer = cur.fetchone()
    if not printer:
        flash("Printer not found.")
        return redirect(url_for("list_printers"))
    if printer["owner_id"] == g.person["id"]:
        flash("That's your own printer.")
        return redirect(url_for("printer_detail", printer_id=printer_id))

    convo_id = _get_or_create_conversation("printer", printer_id, printer["owner_id"])
    return redirect(url_for("conversation_view", conversation_id=convo_id))


@app.route("/projects/<int:project_id>/contact", methods=["POST"])
@login_required
def contact_project_owner(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            project = cur.fetchone()
    if not project:
        flash("Idea not found.")
        return redirect(url_for("list_projects"))
    if project["creator_id"] == g.person["id"]:
        flash("That's your own idea.")
        return redirect(url_for("project_detail", project_id=project_id))

    convo_id = _get_or_create_conversation("project", project_id, project["creator_id"])
    return redirect(url_for("conversation_view", conversation_id=convo_id))


def _get_conversation_for_participant(conversation_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM conversations WHERE id = %s", (conversation_id,))
            convo = cur.fetchone()
    if not convo:
        return None, None
    if convo["person_a_id"] == g.person["id"]:
        return convo, convo["person_b_id"]
    if convo["person_b_id"] == g.person["id"]:
        return convo, convo["person_a_id"]
    return None, None


@app.route("/conversations")
@login_required
def list_conversations():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT c.*, pe.name AS other_name, pe.picture AS other_picture,
                       CASE WHEN c.context_type = 'printer' THEN pr.name ELSE pj.title END AS context_title,
                       COUNT(cm.id) FILTER (
                           WHERE cm.sender_id != %(me)s
                             AND cm.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                       ) AS unread_count
                FROM conversations c
                JOIN people pe ON pe.id = (
                    CASE WHEN c.person_a_id = %(me)s THEN c.person_b_id ELSE c.person_a_id END
                )
                LEFT JOIN printers pr ON c.context_type = 'printer' AND pr.id = c.context_id
                LEFT JOIN projects pj ON c.context_type = 'project' AND pj.id = c.context_id
                LEFT JOIN conversation_messages cm ON cm.conversation_id = c.id
                LEFT JOIN conversation_reads cr ON cr.conversation_id = c.id AND cr.person_id = %(me)s
                WHERE c.person_a_id = %(me)s OR c.person_b_id = %(me)s
                GROUP BY c.id, pe.name, pe.picture, pr.name, pj.title
                ORDER BY c.created_at DESC
                """,
                {"me": g.person["id"]},
            )
            conversations = cur.fetchall()
    return render_template("conversations_list.html", conversations=conversations)


@app.route("/conversations/<int:conversation_id>")
@login_required
def conversation_view(conversation_id):
    convo, other_id = _get_conversation_for_participant(conversation_id)
    if not convo:
        flash("You don't have access to that conversation.")
        return redirect(url_for("list_conversations"))
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM people WHERE id = %s", (other_id,))
            other_person = cur.fetchone()
            if convo["context_type"] == "printer":
                cur.execute("SELECT name FROM printers WHERE id = %s", (convo["context_id"],))
            else:
                cur.execute("SELECT title AS name FROM projects WHERE id = %s", (convo["context_id"],))
            context_row = cur.fetchone()
    return render_template(
        "conversation.html", conversation=convo, other_person=other_person,
        context_name=context_row["name"] if context_row else "",
    )


@app.route("/conversations/<int:conversation_id>/messages.json")
@login_required
def conversation_messages(conversation_id):
    convo, other_id = _get_conversation_for_participant(conversation_id)
    if not convo:
        abort(403)
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT cm.id, cm.body, cm.created_at, cm.sender_id, pe.name AS sender_name
                FROM conversation_messages cm
                JOIN people pe ON pe.id = cm.sender_id
                WHERE cm.conversation_id = %s
                ORDER BY cm.created_at ASC
                """,
                (conversation_id,),
            )
            rows = cur.fetchall()
    return {
        "messages": [
            {
                "id": r["id"], "body": r["body"],
                "created_at": r["created_at"].isoformat(),
                "is_mine": r["sender_id"] == g.person["id"],
                "sender_name": r["sender_name"],
            }
            for r in rows
        ]
    }


@app.route("/conversations/<int:conversation_id>/messages", methods=["POST"])
@login_required
def send_conversation_message(conversation_id):
    convo, other_id = _get_conversation_for_participant(conversation_id)
    if not convo:
        abort(403)
    if request.is_json:
        body = (request.get_json(silent=True) or {}).get("body") or ""
    else:
        body = request.form.get("body") or ""
    body = body.strip()
    if not body:
        return {"error": "empty"}, 400

    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM conversation_messages cm
                LEFT JOIN conversation_reads cr
                    ON cr.conversation_id = cm.conversation_id AND cr.person_id = %(pid)s
                WHERE cm.conversation_id = %(cid)s AND cm.sender_id != %(pid)s
                  AND cm.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                """,
                {"pid": other_id, "cid": conversation_id},
            )
            prior_unread = cur.fetchone()["cnt"]

            cur.execute(
                "INSERT INTO conversation_messages (conversation_id, sender_id, body) VALUES (%s, %s, %s)",
                (conversation_id, g.person["id"], body[:2000]),
            )
        conn.commit()

    if prior_unread == 0:
        with db.get_conn() as conn:
            with db.dict_cursor(conn) as cur:
                cur.execute("SELECT email, name FROM people WHERE id = %s", (other_id,))
                recipient = cur.fetchone()
        if recipient and recipient["email"]:
            sender_name = g.person["name"] or g.person["email"] or "Someone"
            convo_url = url_for("conversation_view", conversation_id=conversation_id, _external=True)
            notifications.send_email(
                app, recipient["email"], f"New message from {sender_name}",
                f"{sender_name} sent you a message:\n\n{body}\n\nReply here: {convo_url}",
            )

    return {"ok": True}


@app.route("/conversations/<int:conversation_id>/mark_read", methods=["POST"])
@login_required
def mark_conversation_read(conversation_id):
    convo, other_id = _get_conversation_for_participant(conversation_id)
    if not convo:
        abort(403)
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO conversation_reads (conversation_id, person_id, last_read_at)
                VALUES (%s, %s, now())
                ON CONFLICT (conversation_id, person_id) DO UPDATE SET last_read_at = now()
                """,
                (conversation_id, g.person["id"]),
            )
        conn.commit()
    return {"ok": True}


@app.route("/api/conversations/unread_count")
@login_required
def api_conversations_unread_count():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt
                FROM conversation_messages cm
                JOIN conversations c ON c.id = cm.conversation_id
                LEFT JOIN conversation_reads cr
                    ON cr.conversation_id = c.id AND cr.person_id = %(pid)s
                WHERE (c.person_a_id = %(pid)s OR c.person_b_id = %(pid)s)
                  AND cm.sender_id != %(pid)s
                  AND cm.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                """,
                {"pid": g.person["id"]},
            )
            count = cur.fetchone()["cnt"]
    return {"count": count}


@app.route("/orders")
@login_required
def list_orders():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                SELECT o.*, p.title AS project_title, pr.name AS printer_name,
                       pe.name AS owner_name
                FROM orders o
                JOIN projects p ON p.id = o.project_id
                JOIN printers pr ON pr.id = o.printer_id
                JOIN people pe ON pe.id = pr.owner_id
                WHERE o.requester_id = %s
                ORDER BY o.created_at DESC
                """,
                (g.person["id"],),
            )
            my_requests = cur.fetchall()

            cur.execute(
                """
                SELECT o.*, p.title AS project_title, pr.name AS printer_name,
                       pe.name AS requester_name
                FROM orders o
                JOIN projects p ON p.id = o.project_id
                JOIN printers pr ON pr.id = o.printer_id
                JOIN people pe ON pe.id = o.requester_id
                WHERE pr.owner_id = %s
                ORDER BY o.created_at DESC
                """,
                (g.person["id"],),
            )
            incoming = cur.fetchall()

            cur.execute(
                """
                SELECT o.id AS order_id, COUNT(*) AS cnt
                FROM messages m
                JOIN orders o ON o.id = m.order_id
                JOIN printers pr ON pr.id = o.printer_id
                LEFT JOIN chat_reads cr
                    ON cr.order_id = o.id AND cr.person_id = %(pid)s
                WHERE (o.requester_id = %(pid)s OR pr.owner_id = %(pid)s)
                  AND m.sender_id != %(pid)s
                  AND m.created_at > COALESCE(cr.last_read_at, '-infinity'::timestamptz)
                GROUP BY o.id
                """,
                {"pid": g.person["id"]},
            )
            unread_by_order = {r["order_id"]: r["cnt"] for r in cur.fetchall()}

            cur.execute(
                "SELECT 1 FROM printers WHERE owner_id = %s LIMIT 1", (g.person["id"],)
            )
            has_printers = cur.fetchone() is not None

    for o in my_requests:
        o["unread_count"] = unread_by_order.get(o["id"], 0)
    for o in incoming:
        o["unread_count"] = unread_by_order.get(o["id"], 0)
        if o["status"] == "pending":
            o["requester_rating"] = get_target_rating("customer", o["requester_id"])

    return render_template(
        "orders.html", my_requests=my_requests, incoming=incoming,
        has_printers=has_printers,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
