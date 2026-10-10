"""Student Attendance Management System (Flask + SQLite)."""

import csv
import hmac
import io
import math
import os
import random
import secrets
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Blueprint,
    Flask,
    Response,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent
STATUSES = ("Present", "Absent", "Late")
PAGE_SIZE = 25
REPORT_RECORD_LIMIT = 200
LOW_ATTENDANCE_THRESHOLD = 75.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS admin_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS students (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT NOT NULL COLLATE NOCASE UNIQUE,
    name TEXT NOT NULL,
    email TEXT,
    class_id INTEGER NOT NULL REFERENCES classes (id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER NOT NULL REFERENCES students (id) ON DELETE CASCADE,
    subject_id INTEGER NOT NULL REFERENCES subjects (id) ON DELETE CASCADE,
    attendance_date TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Present', 'Absent', 'Late')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, subject_id, attendance_date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance (attendance_date);
CREATE INDEX IF NOT EXISTS idx_attendance_subject ON attendance (subject_id);
CREATE INDEX IF NOT EXISTS idx_students_class ON students (class_id);
"""

RECORD_FROM = (
    "FROM attendance a "
    "JOIN students s ON s.id = a.student_id "
    "JOIN classes c ON c.id = s.class_id "
    "JOIN subjects sub ON sub.id = a.subject_id"
)

SAMPLE_CLASSES = ["Class 10-A", "Class 10-B"]
SAMPLE_SUBJECTS = [
    ("MATH", "Mathematics"),
    ("PHY", "Physics"),
    ("CHEM", "Chemistry"),
    ("ENG", "English"),
    ("CS", "Computer Science"),
]
SAMPLE_STUDENTS = [
    ("S001", "Aarav Sharma", "aarav.sharma@example.com", 0),
    ("S002", "Diya Patel", "diya.patel@example.com", 0),
    ("S003", "Kabir Singh", "kabir.singh@example.com", 0),
    ("S004", "Meera Nair", "meera.nair@example.com", 0),
    ("S005", "Rohit Verma", "rohit.verma@example.com", 0),
    ("S006", "Sana Khan", "sana.khan@example.com", 0),
    ("S007", "Ishaan Gupta", "ishaan.gupta@example.com", 1),
    ("S008", "Ananya Iyer", "ananya.iyer@example.com", 1),
    ("S009", "Vihaan Joshi", "vihaan.joshi@example.com", 1),
    ("S010", "Priya Desai", "priya.desai@example.com", 1),
    ("S011", "Arjun Reddy", "arjun.reddy@example.com", 1),
    ("S012", "Neha Kulkarni", "neha.kulkarni@example.com", 1),
]


# --------------------------------------------------------------------------
# Database helpers
# --------------------------------------------------------------------------
def connect(db_path):
    """Open a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(db_path, seed_sample_data=True):
    """Create tables if missing and seed data into empty tables only."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        seed_database(conn, seed_sample_data)
        conn.commit()
    finally:
        conn.close()


def _count(conn, table):
    # Table names here are fixed constants, never user input.
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def seed_database(conn, seed_sample_data=True):
    if _count(conn, "admin_users") == 0:
        conn.execute(
            "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("Admin@123")),
        )
    if not seed_sample_data:
        return

    if _count(conn, "classes") == 0:
        conn.executemany(
            "INSERT INTO classes (name) VALUES (?)", [(n,) for n in SAMPLE_CLASSES]
        )
    subjects_seeded = _count(conn, "subjects") == 0
    if subjects_seeded:
        conn.executemany(
            "INSERT INTO subjects (code, name) VALUES (?, ?)", SAMPLE_SUBJECTS
        )

    students_seeded = _count(conn, "students") == 0
    if students_seeded:
        class_ids = {
            row["name"]: row["id"]
            for row in conn.execute("SELECT id, name FROM classes")
        }
        rows = []
        for roll_no, name, email, class_index in SAMPLE_STUDENTS:
            class_id = class_ids.get(SAMPLE_CLASSES[class_index])
            if class_id is not None:
                rows.append((roll_no, name, email, class_id))
        conn.executemany(
            "INSERT INTO students (roll_no, name, email, class_id) VALUES (?, ?, ?, ?)",
            rows,
        )

    if students_seeded and subjects_seeded and _count(conn, "attendance") == 0:
        rng = random.Random(7)
        student_ids = [r["id"] for r in conn.execute("SELECT id FROM students ORDER BY id")]
        subject_ids = [r["id"] for r in conn.execute("SELECT id FROM subjects ORDER BY id")]
        today = date.today()
        records = []
        for index, student_id in enumerate(student_ids):
            weights = (55, 35, 10) if index % 5 == 0 else (78, 12, 10)
            for offset in range(10):
                day = (today - timedelta(days=offset)).isoformat()
                for subject_id in subject_ids:
                    status = rng.choices(STATUSES, weights=weights)[0]
                    records.append((student_id, subject_id, day, status))
        conn.executemany(
            "INSERT INTO attendance (student_id, subject_id, attendance_date, status) "
            "VALUES (?, ?, ?, ?)",
            records,
        )


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def attendance_percentage(attended, total):
    """Attended = Present + Late. Returns a float percentage rounded to 1 dp."""
    if not total:
        return 0.0
    return round(attended * 100.0 / total, 1)


def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def escape_like(text):
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def clean(form, key, max_len):
    return (form.get(key) or "").strip()[:max_len]


def with_percentage(rows):
    result = []
    for row in rows:
        item = dict(row)
        item["percentage"] = attendance_percentage(
            (item.get("present") or 0) + (item.get("late") or 0), item.get("total") or 0
        )
        result.append(item)
    return result


def csv_safe(value):
    """Neutralise spreadsheet formula injection in exported text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def parse_filters(args):
    filters = {
        "student_id": args.get("student_id", type=int),
        "class_id": args.get("class_id", type=int),
        "subject_id": args.get("subject_id", type=int),
        "date_from": "",
        "date_to": "",
    }
    error = None
    for key in ("date_from", "date_to"):
        raw = (args.get(key) or "").strip()
        if raw:
            parsed = parse_date(raw)
            if parsed is None:
                error = "Dates must use the format YYYY-MM-DD."
            else:
                filters[key] = parsed.isoformat()
    if not error and filters["date_from"] and filters["date_to"]:
        if filters["date_from"] > filters["date_to"]:
            error = "The 'from' date cannot be after the 'to' date."
    if error:
        filters["date_from"] = ""
        filters["date_to"] = ""
    return filters, error


def build_where(filters):
    """Build a WHERE clause from fixed fragments; values go in params."""
    clauses, params = [], []
    if filters["student_id"]:
        clauses.append("a.student_id = ?")
        params.append(filters["student_id"])
    if filters["class_id"]:
        clauses.append("s.class_id = ?")
        params.append(filters["class_id"])
    if filters["subject_id"]:
        clauses.append("a.subject_id = ?")
        params.append(filters["subject_id"])
    if filters["date_from"]:
        clauses.append("a.attendance_date >= ?")
        params.append(filters["date_from"])
    if filters["date_to"]:
        clauses.append("a.attendance_date <= ?")
        params.append(filters["date_to"])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def active_args(filters):
    return {key: value for key, value in filters.items() if value}


def get_csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(16)
        session["_csrf_token"] = token
    return token


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_id"):
            flash("Please log in to continue.", "warning")
            return redirect(url_for("main.login"))
        return view(*args, **kwargs)

    return wrapped


bp = Blueprint("main", __name__)


@bp.before_app_request
def csrf_protect():
    if request.method == "POST":
        sent = (request.form.get("csrf_token") or "").encode("utf-8")
        expected = (session.get("_csrf_token") or "").encode("utf-8")
        if not expected or not hmac.compare_digest(sent, expected):
            abort(400)


@bp.app_context_processor
def inject_globals():
    return {
        "csrf_token": get_csrf_token,
        "statuses": STATUSES,
        "today_iso": date.today().isoformat(),
        "admin_username": session.get("admin_username"),
    }


@bp.app_template_filter("pct_class")
def pct_class(value):
    if value >= 85:
        return "success"
    if value >= LOW_ATTENDANCE_THRESHOLD:
        return "warning"
    return "danger"


@bp.app_errorhandler(400)
def bad_request(_error):
    return (
        render_template(
            "error.html",
            code=400,
            message="Your session expired or the form was invalid. Please go back and try again.",
        ),
        400,
    )


@bp.app_errorhandler(404)
def not_found(_error):
    return render_template("error.html", code=404, message="Page not found."), 404


@bp.app_errorhandler(500)
def server_error(_error):
    return (
        render_template("error.html", code=500, message="Something went wrong on the server."),
        500,
    )


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------
@bp.route("/")
def index():
    return redirect(url_for("main.dashboard"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin_id"):
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = clean(request.form, "username", 50)
        password = request.form.get("password") or ""
        user = get_db().execute(
            "SELECT id, username, password_hash FROM admin_users WHERE username = ?",
            (username,),
        ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["admin_id"] = user["id"]
            session["admin_username"] = user["username"]
            flash(f"Welcome back, {user['username']}!", "success")
            return redirect(url_for("main.dashboard"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.login"))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@bp.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    total_students = _count(db, "students")
    total_subjects = _count(db, "subjects")
    total_classes = _count(db, "classes")

    totals = db.execute(
        "SELECT COUNT(*) AS total, "
        "COALESCE(SUM(status = 'Present'), 0) AS present, "
        "COALESCE(SUM(status = 'Absent'), 0) AS absent, "
        "COALESCE(SUM(status = 'Late'), 0) AS late FROM attendance"
    ).fetchone()
    overall_pct = attendance_percentage(totals["present"] + totals["late"], totals["total"])

    today = date.today().isoformat()
    stat_date = db.execute(
        "SELECT MAX(attendance_date) AS d FROM attendance WHERE attendance_date <= ?",
        (today,),
    ).fetchone()["d"]
    present_students = absent_students = 0
    if stat_date:
        per_student = db.execute(
            "SELECT COALESCE(SUM(status IN ('Present', 'Late')), 0) AS attended "
            "FROM attendance WHERE attendance_date = ? GROUP BY student_id",
            (stat_date,),
        ).fetchall()
        present_students = sum(1 for r in per_student if r["attended"] > 0)
        absent_students = sum(1 for r in per_student if r["attended"] == 0)

    subject_stats = with_percentage(
        db.execute(
            "SELECT sub.name AS name, COUNT(a.id) AS total, "
            "COALESCE(SUM(a.status = 'Present'), 0) AS present, "
            "COALESCE(SUM(a.status = 'Late'), 0) AS late "
            "FROM subjects sub LEFT JOIN attendance a ON a.subject_id = sub.id "
            "GROUP BY sub.id ORDER BY sub.name COLLATE NOCASE"
        ).fetchall()
    )

    student_rows = with_percentage(
        db.execute(
            "SELECT s.name AS name, s.roll_no AS roll_no, COUNT(a.id) AS total, "
            "COALESCE(SUM(a.status = 'Present'), 0) AS present, "
            "COALESCE(SUM(a.status = 'Late'), 0) AS late "
            "FROM students s JOIN attendance a ON a.student_id = s.id GROUP BY s.id"
        ).fetchall()
    )
    low_students = sorted(
        (r for r in student_rows if r["percentage"] < LOW_ATTENDANCE_THRESHOLD),
        key=lambda r: r["percentage"],
    )[:5]

    trend = with_percentage(
        db.execute(
            "SELECT attendance_date AS day, COUNT(*) AS total, "
            "COALESCE(SUM(status = 'Present'), 0) AS present, "
            "COALESCE(SUM(status = 'Late'), 0) AS late "
            "FROM attendance GROUP BY attendance_date "
            "ORDER BY attendance_date DESC LIMIT 7"
        ).fetchall()
    )
    trend.reverse()

    return render_template(
        "dashboard.html",
        total_students=total_students,
        total_subjects=total_subjects,
        total_classes=total_classes,
        totals=totals,
        overall_pct=overall_pct,
        stat_date=stat_date,
        is_today=stat_date == today,
        present_students=present_students,
        absent_students=absent_students,
        subject_stats=subject_stats,
        low_students=low_students,
        low_threshold=LOW_ATTENDANCE_THRESHOLD,
        trend=trend,
    )


# --------------------------------------------------------------------------
# Students
# --------------------------------------------------------------------------
def read_student_form(form, db):
    roll_no = clean(form, "roll_no", 30)
    name = clean(form, "name", 100)
    email = clean(form, "email", 120)
    class_id = form.get("class_id", type=int)
    if not roll_no:
        return None, "Roll number is required."
    if not name:
        return None, "Student name is required."
    if email and (" " in email or "@" not in email or "." not in email.rsplit("@", 1)[-1]):
        return None, "Please enter a valid email address or leave it blank."
    if not class_id or db.execute(
        "SELECT 1 FROM classes WHERE id = ?", (class_id,)
    ).fetchone() is None:
        return None, "Please select a valid class."
    return {"roll_no": roll_no, "name": name, "email": email or None, "class_id": class_id}, None


@bp.route("/students")
@login_required
def students():
    db = get_db()
    q = (request.args.get("q") or "").strip()[:100]
    class_id = request.args.get("class_id", type=int)
    clauses, params = [], []
    if q:
        pattern = f"%{escape_like(q)}%"
        clauses.append(
            "(s.name LIKE ? ESCAPE '\\' OR s.roll_no LIKE ? ESCAPE '\\' "
            "OR COALESCE(s.email, '') LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])
    if class_id:
        clauses.append("s.class_id = ?")
        params.append(class_id)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = db.execute(
        "SELECT s.id, s.roll_no, s.name, s.email, s.class_id, c.name AS class_name, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.student_id = s.id) AS total, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.student_id = s.id "
        " AND a.status = 'Present') AS present, "
        "(SELECT COUNT(*) FROM attendance a WHERE a.student_id = s.id "
        " AND a.status = 'Late') AS late "
        "FROM students s JOIN classes c ON c.id = s.class_id"
        + where
        + " ORDER BY s.name COLLATE NOCASE",
        params,
    ).fetchall()
    classes = db.execute("SELECT id, name FROM classes ORDER BY name COLLATE NOCASE").fetchall()
    return render_template(
        "students.html", students=with_percentage(rows), classes=classes, q=q, class_id=class_id
    )


@bp.post("/students/add")
@login_required
def add_student():
    db = get_db()
    data, error = read_student_form(request.form, db)
    if error:
        flash(error, "danger")
        return redirect(url_for("main.students"))
    try:
        db.execute(
            "INSERT INTO students (roll_no, name, email, class_id) VALUES (?, ?, ?, ?)",
            (data["roll_no"], data["name"], data["email"], data["class_id"]),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A student with roll number '{data['roll_no']}' already exists.", "danger")
        return redirect(url_for("main.students"))
    flash(f"Student '{data['name']}' was added.", "success")
    return redirect(url_for("main.students"))


@bp.post("/students/<int:student_id>/edit")
@login_required
def edit_student(student_id):
    db = get_db()
    if db.execute("SELECT 1 FROM students WHERE id = ?", (student_id,)).fetchone() is None:
        flash("Student not found.", "danger")
        return redirect(url_for("main.students"))
    data, error = read_student_form(request.form, db)
    if error:
        flash(error, "danger")
        return redirect(url_for("main.students"))
    try:
        db.execute(
            "UPDATE students SET roll_no = ?, name = ?, email = ?, class_id = ? WHERE id = ?",
            (data["roll_no"], data["name"], data["email"], data["class_id"], student_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A student with roll number '{data['roll_no']}' already exists.", "danger")
        return redirect(url_for("main.students"))
    flash(f"Student '{data['name']}' was updated.", "success")
    return redirect(url_for("main.students"))


@bp.post("/students/<int:student_id>/delete")
@login_required
def delete_student(student_id):
    db = get_db()
    row = db.execute("SELECT name FROM students WHERE id = ?", (student_id,)).fetchone()
    if row is None:
        flash("Student not found.", "danger")
    else:
        db.execute("DELETE FROM students WHERE id = ?", (student_id,))
        db.commit()
        flash(f"Student '{row['name']}' and their attendance records were deleted.", "success")
    return redirect(url_for("main.students"))


# --------------------------------------------------------------------------
# Subjects and classes
# --------------------------------------------------------------------------
@bp.route("/subjects")
@login_required
def subjects():
    db = get_db()
    subject_rows = db.execute(
        "SELECT sub.id, sub.code, sub.name, COUNT(a.id) AS records "
        "FROM subjects sub LEFT JOIN attendance a ON a.subject_id = sub.id "
        "GROUP BY sub.id ORDER BY sub.name COLLATE NOCASE"
    ).fetchall()
    class_rows = db.execute(
        "SELECT c.id, c.name, COUNT(s.id) AS student_count "
        "FROM classes c LEFT JOIN students s ON s.class_id = c.id "
        "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
    ).fetchall()
    return render_template("subjects.html", subjects=subject_rows, classes=class_rows)


def read_subject_form(form):
    code = clean(form, "code", 20)
    name = clean(form, "name", 100)
    if not code or not name:
        return None, "Both subject code and subject name are required."
    return (code, name), None


@bp.post("/subjects/add")
@login_required
def add_subject():
    db = get_db()
    data, error = read_subject_form(request.form)
    if error:
        flash(error, "danger")
        return redirect(url_for("main.subjects"))
    try:
        db.execute("INSERT INTO subjects (code, name) VALUES (?, ?)", data)
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A subject with code '{data[0]}' already exists.", "danger")
        return redirect(url_for("main.subjects"))
    flash(f"Subject '{data[1]}' was added.", "success")
    return redirect(url_for("main.subjects"))


@bp.post("/subjects/<int:subject_id>/edit")
@login_required
def edit_subject(subject_id):
    db = get_db()
    if db.execute("SELECT 1 FROM subjects WHERE id = ?", (subject_id,)).fetchone() is None:
        flash("Subject not found.", "danger")
        return redirect(url_for("main.subjects"))
    data, error = read_subject_form(request.form)
    if error:
        flash(error, "danger")
        return redirect(url_for("main.subjects"))
    try:
        db.execute(
            "UPDATE subjects SET code = ?, name = ? WHERE id = ?", (data[0], data[1], subject_id)
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A subject with code '{data[0]}' already exists.", "danger")
        return redirect(url_for("main.subjects"))
    flash(f"Subject '{data[1]}' was updated.", "success")
    return redirect(url_for("main.subjects"))


@bp.post("/subjects/<int:subject_id>/delete")
@login_required
def delete_subject(subject_id):
    db = get_db()
    row = db.execute("SELECT name FROM subjects WHERE id = ?", (subject_id,)).fetchone()
    if row is None:
        flash("Subject not found.", "danger")
    else:
        db.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
        db.commit()
        flash(f"Subject '{row['name']}' and its attendance records were deleted.", "success")
    return redirect(url_for("main.subjects"))


@bp.post("/classes/add")
@login_required
def add_class():
    db = get_db()
    name = clean(request.form, "name", 60)
    if not name:
        flash("Class name is required.", "danger")
        return redirect(url_for("main.subjects"))
    try:
        db.execute("INSERT INTO classes (name) VALUES (?)", (name,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A class named '{name}' already exists.", "danger")
        return redirect(url_for("main.subjects"))
    flash(f"Class '{name}' was added.", "success")
    return redirect(url_for("main.subjects"))


@bp.post("/classes/<int:class_id>/edit")
@login_required
def edit_class(class_id):
    db = get_db()
    name = clean(request.form, "name", 60)
    if db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone() is None:
        flash("Class not found.", "danger")
        return redirect(url_for("main.subjects"))
    if not name:
        flash("Class name is required.", "danger")
        return redirect(url_for("main.subjects"))
    try:
        db.execute("UPDATE classes SET name = ? WHERE id = ?", (name, class_id))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(f"A class named '{name}' already exists.", "danger")
        return redirect(url_for("main.subjects"))
    flash(f"Class '{name}' was updated.", "success")
    return redirect(url_for("main.subjects"))


@bp.post("/classes/<int:class_id>/delete")
@login_required
def delete_class(class_id):
    db = get_db()
    row = db.execute("SELECT name FROM classes WHERE id = ?", (class_id,)).fetchone()
    if row is None:
        flash("Class not found.", "danger")
        return redirect(url_for("main.subjects"))
    try:
        db.execute("DELETE FROM classes WHERE id = ?", (class_id,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        flash(
            f"Class '{row['name']}' still has students. Move or delete them first.", "danger"
        )
        return redirect(url_for("main.subjects"))
    flash(f"Class '{row['name']}' was deleted.", "success")
    return redirect(url_for("main.subjects"))


# --------------------------------------------------------------------------
# Attendance
# --------------------------------------------------------------------------
UPSERT_ATTENDANCE = (
    "INSERT INTO attendance (student_id, subject_id, attendance_date, status) "
    "VALUES (?, ?, ?, ?) "
    "ON CONFLICT (student_id, subject_id, attendance_date) "
    "DO UPDATE SET status = excluded.status, updated_at = CURRENT_TIMESTAMP"
)


@bp.get("/attendance")
@login_required
def attendance():
    db = get_db()
    classes = db.execute("SELECT id, name FROM classes ORDER BY name COLLATE NOCASE").fetchall()
    subject_rows = db.execute(
        "SELECT id, code, name FROM subjects ORDER BY name COLLATE NOCASE"
    ).fetchall()
    selected_date = parse_date(request.args.get("date")) or date.today()
    class_ids = {c["id"] for c in classes}
    subject_ids = {s["id"] for s in subject_rows}
    class_id = request.args.get("class_id", type=int)
    subject_id = request.args.get("subject_id", type=int)
    if class_id not in class_ids:
        class_id = classes[0]["id"] if classes else None
    if subject_id not in subject_ids:
        subject_id = subject_rows[0]["id"] if subject_rows else None

    student_rows, existing = [], {}
    if class_id and subject_id:
        student_rows = db.execute(
            "SELECT id, roll_no, name FROM students WHERE class_id = ? "
            "ORDER BY name COLLATE NOCASE",
            (class_id,),
        ).fetchall()
        for row in db.execute(
            "SELECT student_id, status FROM attendance "
            "WHERE subject_id = ? AND attendance_date = ? "
            "AND student_id IN (SELECT id FROM students WHERE class_id = ?)",
            (subject_id, selected_date.isoformat(), class_id),
        ):
            existing[row["student_id"]] = row["status"]

    return render_template(
        "attendance.html",
        mode="mark",
        classes=classes,
        subjects=subject_rows,
        students=student_rows,
        existing=existing,
        selected_date=selected_date.isoformat(),
        class_id=class_id,
        subject_id=subject_id,
    )


@bp.post("/attendance")
@login_required
def save_attendance():
    db = get_db()
    selected_date = parse_date(request.form.get("date"))
    class_id = request.form.get("class_id", type=int)
    subject_id = request.form.get("subject_id", type=int)
    back = url_for("main.attendance")

    if selected_date is None:
        flash("Please choose a valid attendance date.", "danger")
        return redirect(back)
    back = url_for(
        "main.attendance",
        date=selected_date.isoformat(),
        class_id=class_id,
        subject_id=subject_id,
    )
    if selected_date > date.today():
        flash("Attendance cannot be marked for a future date.", "danger")
        return redirect(back)
    if not class_id or db.execute("SELECT 1 FROM classes WHERE id = ?", (class_id,)).fetchone() is None:
        flash("Please select a valid class.", "danger")
        return redirect(back)
    if not subject_id or db.execute(
        "SELECT 1 FROM subjects WHERE id = ?", (subject_id,)
    ).fetchone() is None:
        flash("Please select a valid subject.", "danger")
        return redirect(back)

    student_ids = [
        r["id"] for r in db.execute("SELECT id FROM students WHERE class_id = ?", (class_id,))
    ]
    if not student_ids:
        flash("This class has no students to mark.", "warning")
        return redirect(back)

    records = []
    for student_id in student_ids:
        status = request.form.get(f"status_{student_id}")
        if status not in STATUSES:
            flash("Every student needs a status of Present, Absent, or Late.", "danger")
            return redirect(back)
        records.append((student_id, subject_id, selected_date.isoformat(), status))

    db.executemany(UPSERT_ATTENDANCE, records)
    db.commit()
    flash(
        f"Attendance saved for {len(records)} student(s) on {selected_date.isoformat()}.",
        "success",
    )
    return redirect(back)


@bp.get("/attendance/history")
@login_required
def attendance_history():
    db = get_db()
    filters, error = parse_filters(request.args)
    if error:
        flash(error, "danger")
    where, params = build_where(filters)
    total = db.execute(f"SELECT COUNT(*) {RECORD_FROM}{where}", params).fetchone()[0]
    pages = max(1, math.ceil(total / PAGE_SIZE))
    page = min(max(1, request.args.get("page", 1, type=int)), pages)
    records = db.execute(
        "SELECT a.id, a.attendance_date, a.status, s.roll_no, s.name AS student_name, "
        f"c.name AS class_name, sub.name AS subject_name {RECORD_FROM}{where} "
        "ORDER BY a.attendance_date DESC, s.name COLLATE NOCASE, sub.name COLLATE NOCASE "
        "LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    return render_template(
        "attendance.html",
        mode="history",
        records=records,
        total=total,
        page=page,
        pages=pages,
        filters=filters,
        query_args=active_args(filters),
        **filter_choices(db),
    )


@bp.post("/attendance/<int:record_id>/delete")
@login_required
def delete_attendance(record_id):
    db = get_db()
    cursor = db.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
    db.commit()
    if cursor.rowcount:
        flash("Attendance record deleted.", "success")
    else:
        flash("Attendance record not found.", "danger")
    return redirect(url_for("main.attendance_history"))


def filter_choices(db):
    return {
        "classes": db.execute("SELECT id, name FROM classes ORDER BY name COLLATE NOCASE").fetchall(),
        "subjects": db.execute(
            "SELECT id, code, name FROM subjects ORDER BY name COLLATE NOCASE"
        ).fetchall(),
        "all_students": db.execute(
            "SELECT id, roll_no, name FROM students ORDER BY name COLLATE NOCASE"
        ).fetchall(),
    }


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------
SUMMARY_COLUMNS = (
    "COUNT(*) AS total, "
    "COALESCE(SUM(a.status = 'Present'), 0) AS present, "
    "COALESCE(SUM(a.status = 'Absent'), 0) AS absent, "
    "COALESCE(SUM(a.status = 'Late'), 0) AS late"
)


def student_summary(db, where, params):
    return with_percentage(
        db.execute(
            "SELECT s.id AS id, s.roll_no AS roll_no, s.name AS name, c.name AS class_name, "
            f"{SUMMARY_COLUMNS} {RECORD_FROM}{where} GROUP BY s.id "
            "ORDER BY s.name COLLATE NOCASE",
            params,
        ).fetchall()
    )


@bp.get("/reports")
@login_required
def reports():
    db = get_db()
    filters, error = parse_filters(request.args)
    if error:
        flash(error, "danger")
    where, params = build_where(filters)

    totals = with_percentage(
        db.execute(f"SELECT {SUMMARY_COLUMNS} {RECORD_FROM}{where}", params).fetchall()
    )[0]
    by_student = student_summary(db, where, params)
    by_subject = with_percentage(
        db.execute(
            "SELECT sub.id AS id, sub.code AS code, sub.name AS name, "
            f"{SUMMARY_COLUMNS} {RECORD_FROM}{where} GROUP BY sub.id "
            "ORDER BY sub.name COLLATE NOCASE",
            params,
        ).fetchall()
    )
    records = db.execute(
        "SELECT a.attendance_date, a.status, s.roll_no, s.name AS student_name, "
        f"c.name AS class_name, sub.name AS subject_name {RECORD_FROM}{where} "
        "ORDER BY a.attendance_date DESC, s.name COLLATE NOCASE, sub.name COLLATE NOCASE "
        "LIMIT ?",
        params + [REPORT_RECORD_LIMIT],
    ).fetchall()
    return render_template(
        "reports.html",
        filters=filters,
        query_args=active_args(filters),
        totals=totals,
        by_student=by_student,
        by_subject=by_subject,
        records=records,
        record_limit=REPORT_RECORD_LIMIT,
        **filter_choices(db),
    )


@bp.get("/reports/export.csv")
@login_required
def export_csv():
    db = get_db()
    kind = request.args.get("kind", "records")
    if kind not in ("records", "summary"):
        kind = "records"
    filters, error = parse_filters(request.args)
    if error:
        flash(error, "danger")
        return redirect(url_for("main.reports"))
    where, params = build_where(filters)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if kind == "summary":
        writer.writerow(
            ["Roll No", "Student", "Class", "Total", "Present", "Late", "Absent", "Attendance %"]
        )
        for row in student_summary(db, where, params):
            writer.writerow(
                [
                    csv_safe(row["roll_no"]),
                    csv_safe(row["name"]),
                    csv_safe(row["class_name"]),
                    row["total"],
                    row["present"],
                    row["late"],
                    row["absent"],
                    row["percentage"],
                ]
            )
    else:
        writer.writerow(["Date", "Roll No", "Student", "Class", "Subject", "Status"])
        rows = db.execute(
            "SELECT a.attendance_date, s.roll_no, s.name AS student_name, "
            f"c.name AS class_name, sub.name AS subject_name, a.status {RECORD_FROM}{where} "
            "ORDER BY a.attendance_date DESC, s.name COLLATE NOCASE, sub.name COLLATE NOCASE",
            params,
        )
        for row in rows:
            writer.writerow(
                [
                    row["attendance_date"],
                    csv_safe(row["roll_no"]),
                    csv_safe(row["student_name"]),
                    csv_safe(row["class_name"]),
                    csv_safe(row["subject_name"]),
                    row["status"],
                ]
            )

    filename = f"attendance_{kind}_{date.today().isoformat()}.csv"
    return Response(
        "\ufeff" + buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------
# Application factory
# --------------------------------------------------------------------------
def create_app(test_config=None):
    app = Flask(__name__, instance_path=str(BASE_DIR / "instance"))
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-secret-change-me"),
        DATABASE=str(BASE_DIR / "instance" / "attendance.db"),
        SEED_SAMPLE_DATA=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    init_db(app.config["DATABASE"], app.config["SEED_SAMPLE_DATA"])

    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)
    return app



# Create the Flask application instance for Vercel
app = create_app()

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )

