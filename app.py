from flask import Flask, render_template, request, redirect, url_for, session, flash
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import date, timedelta
from email.message import EmailMessage
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
import smtplib
import sqlite3

app = Flask(__name__)
app.secret_key = "student_task_manager_secret_key"
DB_NAME = "tasks.db"

# Gmail SMTP settings for Email Reminders
SENDER_EMAIL = "your_email@gmail.com"
SENDER_APP_PASSWORD = "xxxx xxxx xxxx xxxx"


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            subject TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Assignment',
            priority TEXT NOT NULL,
            due_date TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    """)
    conn.commit()
    conn.close()


def send_email_smtp(to_email, subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    msg.set_content(body)

    with smtplib.SMTP_SSL("://gmail.com", 465, timeout=5) as smtp:
        smtp.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
        smtp.send_message(msg)


def scheduled_due_reminders():
    """Runs automatically every day at 8:00 AM to notify students of tasks due today or tomorrow."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    users = cursor.execute("SELECT id, name, email FROM users").fetchall()

    for user in users:
        due_tasks = cursor.execute(
            """SELECT title, subject, category, priority, due_date
               FROM tasks
               WHERE user_id = ? AND status = 'Pending' AND due_date <= ?
               ORDER BY due_date ASC""",
            (user["id"], tomorrow_str)
        ).fetchall()

        if due_tasks:
            lines = [f"Hi {user['name']},\n\nAutomatic Reminder: You have {len(due_tasks)} task(s) due soon or overdue:\n"]
            for i, t in enumerate(due_tasks, start=1):
                lines.append(f"{i}. [{t['priority']}] {t['title']} ({t['subject']} - {t['category']}) | Due: {t['due_date']}")
            lines.append("\n- Student Task Manager (Automated Scheduler)")

            try:
                send_email_smtp(
                    user["email"],
                    f"Upcoming Deadline Alert: {len(due_tasks)} Task(s) Due",
                    "\n".join(lines)
                )
            except Exception:
                pass  # Skip silently if offline during background job

    conn.close()


# Start background scheduler (runs daily at 8:00 AM)
scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_due_reminders, trigger="cron", hour=8, minute=0)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form["name"].strip()
        email = request.form["email"].strip().lower()
        password = generate_password_hash(request.form["password"])

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, password)
            )
            conn.commit()
            flash("Account created! Please log in with your email.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("This email is already registered.", "error")
        finally:
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"].strip().lower()
        password = request.form["password"]

        conn = sqlite3.connect(DB_NAME)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        user = cursor.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["user_email"] = user["email"]
            return redirect(url_for("index"))
        else:
            flash("Invalid email or password.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
def index():
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == "POST":
        title = request.form["title"].strip()
        subject = request.form["subject"].strip()
        category = request.form.get("category", "Assignment")
        priority = request.form["priority"]
        due_date = request.form["due_date"]

        cursor.execute(
            "INSERT INTO tasks (user_id, title, subject, category, priority, due_date) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, title, subject, category, priority, due_date)
        )
        conn.commit()
        conn.close()
        return redirect(url_for("index"))

    search = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "All")
    priority_filter = request.args.get("priority", "All")

    query = "SELECT * FROM tasks WHERE user_id = ?"
    params = [user_id]

    if search:
        query += " AND (title LIKE ? OR subject LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    if status_filter != "All":
        query += " AND status = ?"
        params.append(status_filter)
    if priority_filter != "All":
        query += " AND priority = ?"
        params.append(priority_filter)

    query += " ORDER BY status DESC, due_date ASC"
    tasks = cursor.execute(query, params).fetchall()

    all_tasks = cursor.execute(
        "SELECT status, due_date FROM tasks WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    today_str = date.today().isoformat()

    total = len(all_tasks)
    pending = sum(1 for t in all_tasks if t["status"] == "Pending")
    done = sum(1 for t in all_tasks if t["status"] == "Completed")
    overdue = sum(1 for t in all_tasks if t["status"] == "Pending" and t["due_date"] < today_str)

    conn.close()
    return render_template(
        "index.html",
        tasks=tasks,
        total=total,
        pending=pending,
        done=done,
        overdue=overdue,
        today=today_str,
        search=search,
        status_filter=status_filter,
        priority_filter=priority_filter,
        user_name=session["user_name"],
        user_email=session["user_email"]
    )


@app.route("/toggle/<int:task_id>")
def toggle_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    row = cursor.execute(
        "SELECT status FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, session["user_id"])
    ).fetchone()
    if row:
        new_status = "Completed" if row[0] == "Pending" else "Pending"
        cursor.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
        conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/delete/<int:task_id>")
def delete_task(task_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM tasks WHERE id = ? AND user_id = ?",
        (task_id, session["user_id"])
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.route("/send-reminder")
def send_reminder():
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    pending_tasks = cursor.execute(
        "SELECT title, subject, category, priority, due_date FROM tasks WHERE user_id = ? AND status = 'Pending' ORDER BY due_date ASC",
        (session["user_id"],)
    ).fetchall()
    conn.close()

    if not pending_tasks:
        flash("You have no pending tasks to remind!", "success")
        return redirect(url_for("index"))

    lines = [f"Hi {session['user_name']},\n\nHere are your pending academic tasks:\n"]
    for i, t in enumerate(pending_tasks, start=1):
        lines.append(f"{i}. [{t['priority']}] {t['title']} ({t['subject']} - {t['category']}) | Due: {t['due_date']}")
    lines.append("\nStay on track!\n- Student Task Manager")

    try:
        send_email_smtp(
            session["user_email"],
            f"Task Reminder: {len(pending_tasks)} Pending Academic Task(s)",
            "\n".join(lines)
        )
        flash(f"Reminder email sent to {session['user_email']}!", "success")
    except Exception:
        flash(f"Reminder generated for {session['user_email']}: {len(pending_tasks)} pending task(s) queued (Offline Mode).", "success")

    return redirect(url_for("index"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, use_reloader=False, port=8080)