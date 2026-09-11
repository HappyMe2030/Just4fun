import os
from functools import wraps
from urllib.parse import quote_plus, urlencode

from authlib.integrations.flask_client import OAuth
from flask import (
    Flask, flash, g, redirect, render_template, request, session, url_for,
)

import db
from constants import MATERIALS, NOZZLE_DIAMETERS_MM, TECHNOLOGIES
from matching import find_nearby_printers
from tracing import init_tracing

app = Flask(__name__)
app.secret_key = os.environ["APP_SECRET_KEY"]

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
init_tracing(app, db)


# ---------- auth helpers ----------

def get_or_create_person(sub, name, email):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO people (auth0_sub, name, email)
                VALUES (%s, %s, %s)
                ON CONFLICT (auth0_sub) DO UPDATE
                    SET name = EXCLUDED.name, email = EXCLUDED.email
                RETURNING *
                """,
                (sub, name, email),
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
    return {"user": g.get("user"), "person": g.get("person")}


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
        sub=userinfo["sub"], name=userinfo.get("name"), email=userinfo.get("email")
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
    return render_template("home.html", has_location=has_location)


@app.route("/profile")
@login_required
def profile():
    return render_template("profile.html")


@app.route("/profile/location", methods=["POST"])
@login_required
def update_location():
    lat = float(request.form["latitude"])
    lng = float(request.form["longitude"])
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE people SET latitude = %s, longitude = %s WHERE id = %s",
                (lat, lng, g.person["id"]),
            )
        conn.commit()
    flash("Location updated.")
    return redirect(url_for("profile"))


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
    return render_template("printers_list.html", my_printers=my_printers)


@app.route("/printers/add", methods=["GET", "POST"])
@login_required
def add_printer():
    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("materials")
        nozzle = f.get("nozzle_diameter_mm") or None
        max_temp = f.get("max_nozzle_temp_c") or None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO printers (
                        owner_id, name, description, latitude, longitude,
                        technology, materials, nozzle_diameter_mm,
                        build_volume_x_mm, build_volume_y_mm, build_volume_z_mm,
                        heated_bed, enclosed, max_nozzle_temp_c
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        g.person["id"], f["name"], f.get("description"),
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
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE printers SET
                        name=%s, description=%s, latitude=%s, longitude=%s,
                        technology=%s, materials=%s, nozzle_diameter_mm=%s,
                        build_volume_x_mm=%s, build_volume_y_mm=%s, build_volume_z_mm=%s,
                        heated_bed=%s, enclosed=%s, max_nozzle_temp_c=%s
                    WHERE id = %s
                    """,
                    (
                        f["name"], f.get("description"),
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


# ---------- projects ----------

@app.route("/projects")
@login_required
def list_projects():
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM projects ORDER BY created_at DESC")
            projects = cur.fetchall()
    return render_template("projects_list.html", projects=projects)


@app.route("/projects/add", methods=["GET", "POST"])
@login_required
def add_project():
    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("required_materials")
        max_nozzle = f.get("required_nozzle_diameter_max_mm") or None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO projects (
                        creator_id, title, description, required_technology,
                        required_materials, required_build_volume_x_mm,
                        required_build_volume_y_mm, required_build_volume_z_mm,
                        required_nozzle_diameter_max_mm, required_heated_bed,
                        required_enclosed
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        g.person["id"], f["title"], f.get("description"),
                        f["required_technology"], materials,
                        int(f["required_build_volume_x_mm"]),
                        int(f["required_build_volume_y_mm"]),
                        int(f["required_build_volume_z_mm"]),
                        max_nozzle,
                        "required_heated_bed" in f, "required_enclosed" in f,
                    ),
                )
            conn.commit()
        flash("Project posted.")
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
        flash("Project not found.")
        return redirect(url_for("list_projects"))
    if project["creator_id"] != g.person["id"]:
        flash("You can only edit your own projects.")
        return redirect(url_for("list_projects"))

    if request.method == "POST":
        f = request.form
        materials = request.form.getlist("required_materials")
        max_nozzle = f.get("required_nozzle_diameter_max_mm") or None
        with db.get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE projects SET
                        title=%s, description=%s, required_technology=%s,
                        required_materials=%s, required_build_volume_x_mm=%s,
                        required_build_volume_y_mm=%s, required_build_volume_z_mm=%s,
                        required_nozzle_diameter_max_mm=%s, required_heated_bed=%s,
                        required_enclosed=%s
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
                        project_id,
                    ),
                )
            conn.commit()
        flash("Project updated.")
        return redirect(url_for("project_detail", project_id=project_id))

    return render_template(
        "project_form.html",
        technologies=TECHNOLOGIES, materials=MATERIALS,
        nozzle_diameters=NOZZLE_DIAMETERS_MM, project=project,
    )


@app.route("/projects/<int:project_id>")
@login_required
def project_detail(project_id):
    with db.get_conn() as conn:
        with db.dict_cursor(conn) as cur:
            cur.execute("SELECT * FROM projects WHERE id = %s", (project_id,))
            project = cur.fetchone()
    if not project:
        flash("Project not found.")
        return redirect(url_for("list_projects"))

    has_location = g.person["latitude"] is not None
    printers = []
    if has_location:
        printers = find_nearby_printers(
            project, g.person["latitude"], g.person["longitude"]
        )
    is_owner = project["creator_id"] == g.person["id"]
    return render_template(
        "project_detail.html", project=project, printers=printers,
        has_location=has_location, is_owner=is_owner,
    )


# ---------- orders ----------

@app.route("/orders/create", methods=["POST"])
@login_required
def create_order():
    project_id = int(request.form["project_id"])
    printer_id = int(request.form["printer_id"])
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO orders (project_id, printer_id, requester_id)
                VALUES (%s, %s, %s)
                """,
                (project_id, printer_id, g.person["id"]),
            )
        conn.commit()
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

            role = "owner" if order["printer_owner_id"] == g.person["id"] else (
                "requester" if order["requester_id"] == g.person["id"] else None
            )
            allowed_role = VALID_TRANSITIONS.get((order["status"], new_status))
            if role is None or allowed_role != role:
                flash("You can't perform that action on this order.")
                return redirect(url_for("list_orders"))

            cur.execute(
                "UPDATE orders SET status = %s, updated_at = now() WHERE id = %s",
                (new_status, order_id),
            )
        conn.commit()
    return redirect(url_for("list_orders"))


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
    return render_template("orders.html", my_requests=my_requests, incoming=incoming)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
