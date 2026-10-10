import csv
import io
import sqlite3
from datetime import date, timedelta

import pytest

from app import attendance_percentage, create_app

TOKEN = "test-csrf-token"


@pytest.fixture()
def app(tmp_path):
    return create_app(
        {"TESTING": True, "DATABASE": str(tmp_path / "test.db"), "SECRET_KEY": "test-secret"}
    )


@pytest.fixture()
def empty_app(tmp_path):
    return create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "empty.db"),
            "SECRET_KEY": "test-secret",
            "SEED_SAMPLE_DATA": False,
        }
    )


@pytest.fixture()
def client(app):
    return app.test_client()


def post(client, url, data=None, **kwargs):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = TOKEN
    payload = dict(data or {})
    payload["csrf_token"] = TOKEN
    return client.post(url, data=payload, **kwargs)


def login(client, username="admin", password="Admin@123"):
    return post(client, "/login", {"username": username, "password": password})


@pytest.fixture()
def auth(client):
    response = login(client)
    assert response.status_code == 302
    return client


def query(app, sql, params=()):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def scalar(app, sql, params=()):
    return query(app, sql, params)[0][0]


def make_class_subject_students(app, client, count=2, prefix="T"):
    """Create a fresh class, subject and students through the real routes."""
    post(client, "/classes/add", {"name": f"{prefix}-Class"})
    post(client, "/subjects/add", {"code": f"{prefix}SUB", "name": f"{prefix} Subject"})
    class_id = scalar(app, "SELECT id FROM classes WHERE name = ?", (f"{prefix}-Class",))
    subject_id = scalar(app, "SELECT id FROM subjects WHERE code = ?", (f"{prefix}SUB",))
    student_ids = []
    for i in range(count):
        post(
            client,
            "/students/add",
            {"roll_no": f"{prefix}{i:03d}", "name": f"{prefix} Student {i}", "email": "", "class_id": class_id},
        )
        student_ids.append(scalar(app, "SELECT id FROM students WHERE roll_no = ?", (f"{prefix}{i:03d}",)))
    return class_id, subject_id, student_ids


def mark(client, day, class_id, subject_id, statuses):
    data = {"date": day, "class_id": class_id, "subject_id": subject_id}
    for student_id, status in statuses.items():
        data[f"status_{student_id}"] = status
    return post(client, "/attendance", data)


# ---------------------------------------------------------------- startup
def test_startup_creates_and_seeds_database(app, client):
    assert client.get("/login").status_code == 200
    assert scalar(app, "SELECT COUNT(*) FROM admin_users") == 1
    assert scalar(app, "SELECT COUNT(*) FROM students") == 12
    assert scalar(app, "SELECT COUNT(*) FROM subjects") == 5
    assert scalar(app, "SELECT COUNT(*) FROM classes") == 2
    assert scalar(app, "SELECT COUNT(*) FROM attendance") > 0


def test_password_is_hashed(app):
    stored = scalar(app, "SELECT password_hash FROM admin_users WHERE username = 'admin'")
    assert stored != "Admin@123"
    assert "Admin@123" not in stored


def test_restart_keeps_existing_data(app, client, tmp_path):
    login(client)
    post(client, "/classes/add", {"name": "Keep-Me"})
    before = scalar(app, "SELECT COUNT(*) FROM students")
    class_id = scalar(app, "SELECT id FROM classes WHERE name = 'Keep-Me'")
    post(client, "/students/add", {"roll_no": "KEEP1", "name": "Keeper", "class_id": class_id})
    # Starting the app again on the same database must not reseed or delete anything.
    create_app({"TESTING": True, "DATABASE": app.config["DATABASE"]})
    assert scalar(app, "SELECT COUNT(*) FROM students") == before + 1
    assert scalar(app, "SELECT COUNT(*) FROM admin_users") == 1
    assert scalar(app, "SELECT COUNT(*) FROM students WHERE roll_no = 'KEEP1'") == 1


def test_foreign_keys_enforced(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("PRAGMA foreign_keys = ON")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO attendance (student_id, subject_id, attendance_date, status) "
            "VALUES (9999, 9999, '2026-01-01', 'Present')"
        )
    conn.close()


# ------------------------------------------------------------------ login
def test_login_success_and_logout(client):
    response = login(client)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")
    assert client.get("/dashboard").status_code == 200
    response = post(client, "/logout")
    assert response.status_code == 302
    assert client.get("/dashboard").status_code == 302


@pytest.mark.parametrize(
    "username,password",
    [("admin", "wrong"), ("nobody", "Admin@123"), ("", ""), ("admin", "admin@123")],
)
def test_login_failure(client, username, password):
    response = login(client, username, password)
    assert response.status_code == 200
    assert b"Invalid username or password" in response.data
    assert client.get("/dashboard").status_code == 302


def test_post_without_csrf_token_is_rejected(client):
    response = client.post("/login", data={"username": "admin", "password": "Admin@123"})
    assert response.status_code == 400


PROTECTED = [
    "/dashboard",
    "/students",
    "/subjects",
    "/attendance",
    "/attendance/history",
    "/reports",
    "/reports/export.csv",
]


@pytest.mark.parametrize("url", PROTECTED)
def test_protected_pages_redirect_to_login(client, url):
    response = client.get(url)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_protected_post_requires_login(client):
    response = post(client, "/students/add", {"roll_no": "X1", "name": "X", "class_id": 1})
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]
    assert scalar(client.application, "SELECT COUNT(*) FROM students WHERE roll_no = 'X1'") == 0


@pytest.mark.parametrize("url", PROTECTED)
def test_pages_render_with_seed_data(auth, url):
    assert auth.get(url).status_code == 200


@pytest.mark.parametrize("url", PROTECTED)
def test_pages_render_on_empty_database(empty_app, url):
    client = empty_app.test_client()
    login(client)
    assert client.get(url).status_code == 200


def test_unknown_page_returns_404(auth):
    assert auth.get("/does-not-exist").status_code == 404


# --------------------------------------------------------------- students
def test_add_edit_search_delete_student(app, auth):
    class_id = scalar(app, "SELECT id FROM classes ORDER BY id LIMIT 1")
    response = post(
        auth,
        "/students/add",
        {"roll_no": "Z100", "name": "Zed Tester", "email": "zed@example.com", "class_id": class_id},
        follow_redirects=True,
    )
    assert b"was added" in response.data
    student_id = scalar(app, "SELECT id FROM students WHERE roll_no = 'Z100'")

    # Search
    assert b"Zed Tester" in auth.get("/students?q=zed").data
    assert b"Zed Tester" not in auth.get("/students?q=no-such-person").data
    assert b"No students found" in auth.get("/students?q=%25%25no-match").data

    # Edit
    response = post(
        auth,
        f"/students/{student_id}/edit",
        {"roll_no": "Z100", "name": "Zed Renamed", "email": "", "class_id": class_id},
        follow_redirects=True,
    )
    assert b"was updated" in response.data
    assert scalar(app, "SELECT name FROM students WHERE id = ?", (student_id,)) == "Zed Renamed"
    assert scalar(app, "SELECT email FROM students WHERE id = ?", (student_id,)) is None

    # Delete
    response = post(auth, f"/students/{student_id}/delete", follow_redirects=True)
    assert b"were deleted" in response.data
    assert scalar(app, "SELECT COUNT(*) FROM students WHERE id = ?", (student_id,)) == 0


def test_duplicate_roll_number_rejected(app, auth):
    class_id = scalar(app, "SELECT id FROM classes ORDER BY id LIMIT 1")
    response = post(
        auth,
        "/students/add",
        {"roll_no": "s001", "name": "Dup", "class_id": class_id},
        follow_redirects=True,
    )
    assert b"already exists" in response.data
    assert scalar(app, "SELECT COUNT(*) FROM students WHERE roll_no = 'S001'") == 1


@pytest.mark.parametrize(
    "data",
    [
        {"roll_no": "", "name": "No Roll", "class_id": 1},
        {"roll_no": "E1", "name": "", "class_id": 1},
        {"roll_no": "E2", "name": "Bad Email", "email": "not-an-email", "class_id": 1},
        {"roll_no": "E3", "name": "Bad Class", "class_id": 99999},
        {"roll_no": "E4", "name": "No Class"},
    ],
)
def test_invalid_student_rejected(app, auth, data):
    before = scalar(app, "SELECT COUNT(*) FROM students")
    response = post(auth, "/students/add", data, follow_redirects=True)
    assert b"alert-danger" in response.data
    assert scalar(app, "SELECT COUNT(*) FROM students") == before


def test_deleting_student_removes_attendance(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=1, prefix="D")
    mark(auth, date.today().isoformat(), class_id, subject_id, {ids[0]: "Present"})
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (ids[0],)) == 1
    post(auth, f"/students/{ids[0]}/delete")
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (ids[0],)) == 0


def test_class_with_students_cannot_be_deleted(app, auth):
    class_id, _subject_id, _ids = make_class_subject_students(app, auth, count=1, prefix="C")
    response = post(auth, f"/classes/{class_id}/delete", follow_redirects=True)
    assert b"still has students" in response.data
    assert scalar(app, "SELECT COUNT(*) FROM classes WHERE id = ?", (class_id,)) == 1


def test_subject_and_class_management(app, auth):
    post(auth, "/subjects/add", {"code": "GEO", "name": "Geography"})
    subject_id = scalar(app, "SELECT id FROM subjects WHERE code = 'GEO'")
    response = post(auth, "/subjects/add", {"code": "geo", "name": "Dup"}, follow_redirects=True)
    assert b"already exists" in response.data
    post(auth, f"/subjects/{subject_id}/edit", {"code": "GEO", "name": "World Geography"})
    assert scalar(app, "SELECT name FROM subjects WHERE id = ?", (subject_id,)) == "World Geography"
    post(auth, f"/subjects/{subject_id}/delete")
    assert scalar(app, "SELECT COUNT(*) FROM subjects WHERE id = ?", (subject_id,)) == 0

    post(auth, "/classes/add", {"name": "Temp Class"})
    class_id = scalar(app, "SELECT id FROM classes WHERE name = 'Temp Class'")
    post(auth, f"/classes/{class_id}/edit", {"name": "Renamed Class"})
    assert scalar(app, "SELECT name FROM classes WHERE id = ?", (class_id,)) == "Renamed Class"
    post(auth, f"/classes/{class_id}/delete")
    assert scalar(app, "SELECT COUNT(*) FROM classes WHERE id = ?", (class_id,)) == 0


# ------------------------------------------------------------- attendance
def test_mark_attendance_and_update_without_duplicates(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=2, prefix="M")
    today = date.today().isoformat()

    response = mark(auth, today, class_id, subject_id, {ids[0]: "Present", ids[1]: "Absent"})
    assert response.status_code == 302
    rows = query(
        app,
        "SELECT student_id, status FROM attendance WHERE subject_id = ? AND attendance_date = ?",
        (subject_id, today),
    )
    assert {r["student_id"]: r["status"] for r in rows} == {ids[0]: "Present", ids[1]: "Absent"}

    # Saving the same date/subject again must update, never duplicate.
    mark(auth, today, class_id, subject_id, {ids[0]: "Late", ids[1]: "Absent"})
    rows = query(
        app,
        "SELECT student_id, status FROM attendance WHERE subject_id = ? AND attendance_date = ?",
        (subject_id, today),
    )
    assert len(rows) == 2
    assert {r["student_id"]: r["status"] for r in rows}[ids[0]] == "Late"

    page = auth.get(f"/attendance?date={today}&class_id={class_id}&subject_id={subject_id}")
    assert page.status_code == 200
    assert b"M Student 0" in page.data


def test_database_rejects_duplicate_attendance(app):
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("PRAGMA foreign_keys = ON")
    sid = conn.execute("SELECT id FROM students LIMIT 1").fetchone()[0]
    sub = conn.execute("SELECT id FROM subjects LIMIT 1").fetchone()[0]
    sql = (
        "INSERT INTO attendance (student_id, subject_id, attendance_date, status) "
        "VALUES (?, ?, '2000-01-01', 'Present')"
    )
    conn.execute(sql, (sid, sub))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (sid, sub))
    conn.close()


def test_invalid_status_and_future_date_rejected(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=1, prefix="V")
    today = date.today().isoformat()
    response = mark(auth, today, class_id, subject_id, {ids[0]: "Sleeping"})
    assert response.status_code == 302
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE subject_id = ?", (subject_id,)) == 0

    future = (date.today() + timedelta(days=3)).isoformat()
    mark(auth, future, class_id, subject_id, {ids[0]: "Present"})
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE subject_id = ?", (subject_id,)) == 0

    mark(auth, "not-a-date", class_id, subject_id, {ids[0]: "Present"})
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE subject_id = ?", (subject_id,)) == 0


def test_attendance_history_filters_and_delete(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=1, prefix="H")
    mark(auth, date.today().isoformat(), class_id, subject_id, {ids[0]: "Present"})
    page = auth.get(f"/attendance/history?student_id={ids[0]}")
    assert page.status_code == 200
    assert b"H Student 0" in page.data
    record_id = scalar(app, "SELECT id FROM attendance WHERE student_id = ?", (ids[0],))
    post(auth, f"/attendance/{record_id}/delete")
    assert scalar(app, "SELECT COUNT(*) FROM attendance WHERE student_id = ?", (ids[0],)) == 0
    assert auth.get("/attendance/history?date_from=bad-date").status_code == 200


# ------------------------------------------------------ percentage / reports
def test_attendance_percentage_function():
    assert attendance_percentage(0, 0) == 0.0
    assert attendance_percentage(2, 4) == 50.0
    assert attendance_percentage(1, 3) == 33.3
    assert attendance_percentage(5, 5) == 100.0


def test_percentage_in_reports_and_student_list(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=1, prefix="P")
    today = date.today()
    # Present, Late, Absent, Absent -> (1 + 1) / 4 = 50.0 %
    for offset, status in enumerate(["Present", "Late", "Absent", "Absent"]):
        day = (today - timedelta(days=offset)).isoformat()
        mark(auth, day, class_id, subject_id, {ids[0]: status})

    report = auth.get(f"/reports?student_id={ids[0]}")
    assert report.status_code == 200
    assert b"50.0%" in report.data
    assert b"P Student 0" in report.data

    students_page = auth.get("/students?q=P000")
    assert b"50.0%" in students_page.data

    # Date filter narrows to the two most recent days: Present + Late = 100 %.
    date_from = (today - timedelta(days=1)).isoformat()
    narrowed = auth.get(f"/reports?student_id={ids[0]}&date_from={date_from}&date_to={today.isoformat()}")
    assert b"100.0%" in narrowed.data

    by_subject = auth.get(f"/reports?subject_id={subject_id}")
    assert b"P Subject" in by_subject.data


def test_report_with_invalid_dates_still_renders(auth):
    response = auth.get("/reports?date_from=2026-02-30&date_to=oops")
    assert response.status_code == 200
    assert b"YYYY-MM-DD" in response.data
    response = auth.get("/reports?date_from=2026-05-02&date_to=2026-05-01")
    assert b"cannot be after" in response.data


def test_csv_export_records_and_summary(app, auth):
    class_id, subject_id, ids = make_class_subject_students(app, auth, count=1, prefix="X")
    mark(auth, date.today().isoformat(), class_id, subject_id, {ids[0]: "Late"})

    response = auth.get(f"/reports/export.csv?kind=records&student_id={ids[0]}")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "attachment" in response.headers["Content-Disposition"]
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert rows[0] == ["Date", "Roll No", "Student", "Class", "Subject", "Status"]
    assert len(rows) == 2
    assert rows[1][2:] == ["X Student 0", "X-Class", "X Subject", "Late"]

    response = auth.get(f"/reports/export.csv?kind=summary&student_id={ids[0]}")
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    assert rows[0][-1] == "Attendance %"
    assert rows[1][3:] == ["1", "0", "1", "0", "100.0"]


def test_csv_export_neutralises_formulas(app, auth):
    class_id = scalar(app, "SELECT id FROM classes ORDER BY id LIMIT 1")
    post(auth, "/students/add", {"roll_no": "F1", "name": "=HYPERLINK(1)", "class_id": class_id})
    student_id = scalar(app, "SELECT id FROM students WHERE roll_no = 'F1'")
    subject_id = scalar(app, "SELECT id FROM subjects ORDER BY id LIMIT 1")
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute(
        "INSERT INTO attendance (student_id, subject_id, attendance_date, status) "
        "VALUES (?, ?, '2020-01-01', 'Present')",
        (student_id, subject_id),
    )
    conn.commit()
    conn.close()
    text = auth.get(f"/reports/export.csv?student_id={student_id}").data.decode("utf-8-sig")
    assert "'=HYPERLINK(1)" in text
