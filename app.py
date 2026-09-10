import os
from urllib.parse import quote_plus, urlencode

from authlib.integrations.flask_client import OAuth
from flask import Flask, redirect, render_template_string, session, url_for
import psycopg2

app = Flask(__name__)
app.secret_key = os.environ["APP_SECRET_KEY"]

DATABASE_URL = os.environ["DATABASE_URL"]
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


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # An earlier, pre-Auth0 version of this app created a "people"
            # table with a different schema (no auth0_sub/email columns).
            # If that old shape is still there, drop it so we can recreate
            # it with the columns this version needs.
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
                    first_login TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS logins (
                    id SERIAL PRIMARY KEY,
                    person_id INTEGER NOT NULL REFERENCES people(id),
                    logged_in_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()


init_db()


def upsert_person_and_log_login(sub, name, email):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO people (auth0_sub, name, email)
                VALUES (%s, %s, %s)
                ON CONFLICT (auth0_sub) DO UPDATE
                    SET name = EXCLUDED.name, email = EXCLUDED.email
                RETURNING id
                """,
                (sub, name, email),
            )
            person_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO logins (person_id) VALUES (%s)", (person_id,)
            )
        conn.commit()


LOGIN_PAGE = """
<!doctype html>
<title>Login</title>
<style>
  body { font-family: sans-serif; display: flex; justify-content: center;
         align-items: center; height: 100vh; margin: 0; background: #f4f4f7; }
  .card { background: white; padding: 2.5rem; border-radius: 8px;
          box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; }
  a.btn { display: inline-block; margin-top: 1rem; padding: 0.6rem 1.2rem;
          background: #4285F4; color: white; text-decoration: none;
          border-radius: 4px; font-size: 1rem; }
</style>
<div class="card">
  <h2>Welcome</h2>
  <a class="btn" href="/login">Sign in with Google</a>
</div>
"""

WELCOME_PAGE = """
<!doctype html>
<title>Welcome</title>
<style>
  body { font-family: sans-serif; display: flex; justify-content: center;
         align-items: center; height: 100vh; margin: 0; background: #f4f4f7; }
  h1 { font-size: 3rem; }
  a { display: block; margin-top: 1rem; text-align: center; }
</style>
<div style="text-align:center">
  <h1>{{ name }}</h1>
  <a href="/logout">Log out</a>
</div>
"""


@app.route("/")
def home():
    user = session.get("user")
    if user:
        name = user.get("name") or user.get("email") or "there"
        return render_template_string(WELCOME_PAGE, name=name)
    return render_template_string(LOGIN_PAGE)


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
    upsert_person_and_log_login(
        sub=userinfo["sub"],
        name=userinfo.get("name"),
        email=userinfo.get("email"),
    )
    return redirect("/")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(
        "https://"
        + AUTH0_DOMAIN
        + "/v2/logout?"
        + urlencode(
            {
                "returnTo": url_for("home", _external=True),
                "client_id": AUTH0_CLIENT_ID,
            },
            quote_via=quote_plus,
        )
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
