import os
from flask import Flask, request, render_template_string
import psycopg2

app = Flask(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]


def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS people (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    logged_in_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        conn.commit()


init_db()

LOGIN_PAGE = """
<!doctype html>
<title>Login</title>
<style>
  body { font-family: sans-serif; display: flex; justify-content: center;
         align-items: center; height: 100vh; margin: 0; background: #f4f4f7; }
  form { background: white; padding: 2rem; border-radius: 8px;
         box-shadow: 0 2px 8px rgba(0,0,0,0.1); text-align: center; }
  input { padding: 0.5rem; font-size: 1rem; margin-right: 0.5rem; }
  button { padding: 0.5rem 1rem; font-size: 1rem; }
</style>
<form method="post">
  <h2>Login</h2>
  <input name="name" placeholder="Your name" required autofocus>
  <button type="submit">Login</button>
</form>
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
  <a href="/">Log in as someone else</a>
</div>
"""


@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO people (name) VALUES (%s)", (name,))
                conn.commit()
            return render_template_string(WELCOME_PAGE, name=name)
    return render_template_string(LOGIN_PAGE)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
