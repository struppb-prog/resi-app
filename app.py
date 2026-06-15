"""
app.py — Backend Flask para la app de limpieza de La Playa.

Rutas:
  /               → redirige según sesión
  /login          → login con nombre + PIN
  /logout         → cierra sesión
  /residente      → dashboard del residente
  /admin          → dashboard del administrador
  /admin/pins     → gestión de PINs (POST)
  /api/whatsapp   → mensajes pre-armados para WhatsApp (JSON)
  /api/refresh    → fuerza recarga del caché de Sheets
"""

import json
import os
from datetime import date

from dotenv import load_dotenv
from flask import (Flask, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from sheets_parser import ResidenciaParser

load_dotenv()

# ------------------------------------------------------------------ Config
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cambiar-en-produccion-123")

SHEET_ID    = os.environ.get("SHEET_ID", "")
CREDS_PATH  = os.environ.get("GOOGLE_CREDS_PATH", "credentials.json")
ADMIN_PIN   = os.environ.get("ADMIN_PIN", "0000")
# En Railway con volumen montado en /data, usar esa ruta. Si no, usar local.
_DATA_DIR  = os.environ.get("DATA_DIR", os.path.dirname(__file__))
USERS_FILE = os.path.join(_DATA_DIR, "users.json")

ALIAS_PAGO  = "RESI.PAGOS.UALA"

parser = ResidenciaParser(SHEET_ID, CREDS_PATH)

# ------------------------------------------------------------------ Helpers
def load_users() -> dict:
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(users: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2, ensure_ascii=False)

def logged_in() -> bool:
    return "name" in session

def is_admin() -> bool:
    return session.get("role") == "admin"

def require_login():
    if not logged_in():
        return redirect(url_for("login"))

def require_admin():
    if not is_admin():
        return redirect(url_for("login"))

# ------------------------------------------------------------------ Auth
@app.route("/")
def index():
    if is_admin():
        return redirect(url_for("admin"))
    if logged_in():
        return redirect(url_for("residente"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        pin  = request.form.get("pin",  "").strip()

        # Admin
        if name.lower() == "admin" and pin == ADMIN_PIN:
            session.clear()
            session["name"] = "Admin"
            session["role"] = "admin"
            return redirect(url_for("admin"))

        # Residente
        users = load_users()
        if name in users and users[name] == pin:
            session.clear()
            session["name"] = name
            session["role"] = "resident"
            return redirect(url_for("residente"))

        return render_template("login.html", error="Nombre o PIN incorrecto. Revisá con el tesorero.", names=sorted(users.keys()))

    users = load_users()
    return render_template("login.html", names=sorted(users.keys()))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ------------------------------------------------------------------ Residente
@app.route("/residente")
def residente():
    redir = require_login()
    if redir:
        return redir

    name = session["name"]
    error = None
    my_tasks, debt, week_date = [], None, ""

    try:
        week_tasks = parser.get_current_week_tasks()
        my_tasks   = [t for t in week_tasks if t["resident"].lower() == name.lower()]
        debt       = parser.get_resident_debt(name)
        week_date  = parser.get_current_week_date()
    except Exception as e:
        error = f"No se pudo cargar la información: {e}"

    return render_template(
        "resident.html",
        name=name,
        my_tasks=my_tasks,
        debt=debt,
        week_date=week_date,
        error=error,
    )

# ------------------------------------------------------------------ Admin
@app.route("/admin")
def admin():
    redir = require_admin()
    if redir:
        return redir

    ctx = {"error": None, "week_tasks": [], "all_debts": [],
           "supplies": [], "week_date": "", "users": {},
           "cash_balance": 0, "cash_movements": []}

    try:
        ctx["week_tasks"]      = parser.get_current_week_tasks()
        ctx["all_debts"]       = parser.get_all_debts()
        ctx["supplies"]        = parser.get_supplies()
        ctx["week_date"]       = parser.get_current_week_date()
        ctx["users"]           = load_users()
        ctx["cash_movements"], ctx["cash_balance"] = parser.get_cash_movements(30)
        ctx["all_names"]       = parser.get_all_resident_names()
    except Exception as e:
        ctx["error"] = str(e)

    return render_template("admin.html", **ctx)


# ------------------------------------------------------------------ API
@app.route("/api/whatsapp")
def api_whatsapp():
    redir = require_admin()
    if redir:
        return jsonify({"error": "No autorizado"}), 401

    try:
        week_tasks = parser.get_current_week_tasks()
        all_debts  = parser.get_all_debts()
        week_date  = parser.get_current_week_date()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    debt_map = {d["name"].lower(): d for d in all_debts}
    messages = []
    residents_with_tasks = set()

    for t in week_tasks:
        res = t["resident"]
        residents_with_tasks.add(res.lower())
        debt = debt_map.get(res.lower())
        debt_block = ""
        if debt and debt["total"] > 0:
            debt_block = (
                f"\n\n💰 También recordá que tenés deuda pendiente: *{debt['debt_str']}*"
                f"\nPagá al alias: *{ALIAS_PAGO}*"
                f"\nNo te olvides de mandar el comprobante ✅"
            )

        msg = (
            f"🧹 *LIMPIEZA — Semana del {week_date}*\n\n"
            f"Hola {res}! 👋\n\n"
            f"Esta semana te toca:\n"
            f"📌 *{t['task']}*\n"
            f"📅 {t['freq']}"
            f"{debt_block}"
        )
        messages.append({"resident": res, "type": "tarea", "message": msg})

    # Residentes con deuda pero sin tarea esta semana
    for debt in all_debts:
        if debt["name"].lower() not in residents_with_tasks and debt["total"] > 0:
            msg = (
                f"💰 *AVISO DE DEUDA — Resi La Playa*\n\n"
                f"Hola {debt['name']}! 👋\n\n"
                f"Tenés una deuda pendiente de: *{debt['debt_str']}*\n\n"
                f"Pagá al alias: *{ALIAS_PAGO}*\n"
                f"No te olvides de mandar el comprobante ✅"
            )
            messages.append({"resident": debt["name"], "type": "deuda", "message": msg})

    return jsonify(messages)

@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    redir = require_admin()
    if redir:
        return jsonify({"error": "No autorizado"}), 401
    parser.clear_cache()
    return jsonify({"status": "ok", "message": "Caché limpiado. Los datos se recargarán del Sheet."})

@app.route("/api/status")
def api_status():
    """Endpoint de health-check."""
    return jsonify({
        "status": "ok",
        "date": date.today().isoformat(),
        "sheet_id": SHEET_ID[:8] + "..." if SHEET_ID else "no configurado",
    })

# ------------------------------------------------------------------ Run
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
