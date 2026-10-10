# Student Attendance Management System

A Flask + SQLite web app for managing students, subjects, classes and daily attendance, with reports and CSV export.
Bootstrap is bundled in `static/vendor/`, so the app works without an internet connection.

## Windows setup (VS Code, PowerShell)

Open the project folder in VS Code. Open the PowerShell terminal and execute:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

If Python 3.11 is unavailable, use any installed compatible version (3.11 or 3.12). Check with `py -0p`, then replace the first command:

```powershell
py -3.12 -m venv .venv
```

or, if `py` is not installed but `python` is on your PATH (check `python --version`):

```powershell
python -m venv .venv
```

The remaining three commands stay exactly the same. If PowerShell blocks script execution, you do not need to activate the
virtual environment: every command above calls `.\.venv\Scripts\python.exe` directly.

Open this URL in the browser:

http://127.0.0.1:5000

Demo login:

| Username | Password   |
|----------|------------|
| admin    | Admin@123  |

Press `Ctrl+C` in the terminal to stop the server.

## Running the tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests use a temporary SQLite database in a pytest temp folder and never touch `instance/attendance.db`.

## Features

- Admin login / logout (Werkzeug password hashing, Flask sessions, CSRF protection on all forms)
- Dashboard: total students, present students, absent students, overall attendance %, subject-wise bars, recent days, students below 75%
- Students: add, edit, delete, search (name, roll number, email) and filter by class
- Subjects and classes: add, edit, delete
- Mark attendance (Present / Absent / Late) for a chosen date, class and subject; saving again for the same date updates it
- Attendance history with filters and pagination
- Reports by student, subject, class and date range; CSV export of detailed records or the per-student summary
- Database and tables are created automatically on first start; sample data is inserted only into empty tables

## How attendance percentage is calculated

`percentage = (Present + Late) / total records x 100`, rounded to one decimal. Late counts as attended.
On the dashboard, a student counts as "present" for the latest attendance date if they are Present or Late in at least one
session that day, and "absent" if they are Absent in every session that day.

## Configuration

| Setting      | How                                  | Default                       |
|--------------|--------------------------------------|-------------------------------|
| Secret key   | `SECRET_KEY` environment variable    | a development-only value      |
| Debug mode   | `FLASK_DEBUG=1` environment variable | off                           |
| Database     | `instance/attendance.db`             | created automatically         |

Set a real secret key before using this anywhere other than your own machine, for example in PowerShell:

```powershell
$env:SECRET_KEY = "a-long-random-string"
.\.venv\Scripts\python.exe app.py
```

To reset to a fresh sample database, stop the app and delete `instance/attendance.db`. The next start recreates it.

## Project structure

```
student_attendance_system/
  app.py
  requirements.txt
  pytest.ini
  README.md
  .gitignore
  templates/   base, login, dashboard, students, subjects, attendance, reports, error
  static/
    css/style.css
    js/app.js
    vendor/bootstrap/   (Bootstrap 5.3.3 CSS and JS, MIT licence)
  tests/test_app.py
  instance/    (SQLite database is created here)
```

## Notes

- This is a local demo application. The demo admin password is public; change it for any real use.
- Deleting a student deletes their attendance records. Deleting a subject deletes its attendance records.
  A class that still has students cannot be deleted.
- Attendance cannot be marked for future dates.
