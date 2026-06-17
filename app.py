from __future__ import annotations

import os
import uuid
from functools import wraps
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_from_directory, session, url_for
from werkzeug.utils import secure_filename

from auth_store import (
    DATA_DIR,
    authenticate_user,
    change_password,
    create_user,
    get_user_by_id,
    init_db,
    list_users,
    update_user,
)
from quotation_compare import compare_multiple_files


BASE_DIR = Path(__file__).resolve().parent
COMPARISONS_DIR = DATA_DIR / "comparisons"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
init_db()


def current_user():
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_user_by_id(int(user_id))


@app.context_processor
def inject_user():
    return {"current_user": current_user()}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if user is None:
            return redirect(url_for("login", next=request.path))
        if not user["is_admin"]:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def allowed_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def create_comparison_folder() -> Path:
    run_id = f"{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    comparison_dir = COMPARISONS_DIR / run_id
    (comparison_dir / "uploads").mkdir(parents=True, exist_ok=True)
    (comparison_dir / "outputs").mkdir(parents=True, exist_ok=True)
    return comparison_dir


def save_upload(file_storage, destination_dir: Path, fallback_name: str) -> Path:
    if not file_storage or not file_storage.filename:
        raise ValueError(f"{fallback_name} is required.")

    filename = secure_filename(file_storage.filename)
    if not filename:
        filename = fallback_name
    if not allowed_file(filename):
        raise ValueError(f"{filename} is not supported. Upload CSV, Excel, or PDF files.")

    destination = destination_dir / filename
    file_storage.save(destination)
    return destination


def save_uploads(file_storages, destination_dir: Path) -> list[Path]:
    saved_paths = []
    for index, file_storage in enumerate(file_storages, start=1):
        if not file_storage or not file_storage.filename:
            continue
        saved_paths.append(save_upload(file_storage, destination_dir, f"quote_{index}"))
    if len(saved_paths) < 2:
        raise ValueError("Upload at least two quotation documents.")
    return saved_paths


def build_downloads(comparison_id: str) -> dict[str, str]:
    return {
        "csv": f"/comparison/{comparison_id}/download/outputs/differences.csv",
        "json": f"/comparison/{comparison_id}/download/outputs/differences.json",
        "html": f"/comparison/{comparison_id}/download/outputs/report.html",
    }


@app.get("/")
@login_required
def index():
    return render_template("index.html", result=None, error=None)


@app.post("/compare")
@login_required
def compare_route():
    comparison_dir = create_comparison_folder()
    uploads_dir = comparison_dir / "uploads"
    outputs_dir = comparison_dir / "outputs"

    try:
        quote_paths = save_uploads(request.files.getlist("quotes"), uploads_dir)
        tolerance = float(request.form.get("tolerance") or 0.01)
        name_similarity = float(request.form.get("name_similarity") or 0.72)
        comparison = compare_multiple_files(quote_paths, outputs_dir, tolerance, name_similarity)

    except Exception as exc:
        shutil.rmtree(comparison_dir, ignore_errors=True)
        return render_template("index.html", result=None, error=str(exc)), 400

    comparison_id = comparison_dir.name
    result = {
        "comparison_id": comparison_id,
        "mode": comparison["mode"],
        "quote_names": comparison["quote_names"],
        "quote_counts": comparison["quote_counts"],
        "rows": comparison["rows"],
        "summary": comparison["summary"],
        "downloads": build_downloads(comparison_id),
        "folder": str(comparison_dir),
    }
    return render_template("index.html", result=result, error=None)


@app.get("/comparison/<comparison_id>/download/<path:relative_path>")
@login_required
def download_file(comparison_id: str, relative_path: str):
    comparison_dir = (COMPARISONS_DIR / comparison_id).resolve()
    if not str(comparison_dir).startswith(str(COMPARISONS_DIR.resolve())):
        abort(404)

    target = (comparison_dir / relative_path).resolve()
    if not str(target).startswith(str(comparison_dir)):
        abort(404)

    if not target.exists():
        abort(404)
    return send_from_directory(target.parent, target.name, as_attachment=True)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user() is not None:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        user = authenticate_user(username, password)
        if user is None:
            error = "Invalid username or password."
        else:
            session.clear()
            session["user_id"] = user["id"]
            next_url = request.args.get("next") or url_for("index")
            return redirect(next_url)

    return render_template("login.html", error=error)


@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    error = None
    message = None
    if request.method == "POST":
        try:
            create_user(
                username=request.form.get("username", ""),
                password=request.form.get("password", ""),
                is_admin=request.form.get("is_admin") == "on",
                is_active=request.form.get("is_active") == "on",
            )
            message = "User created."
        except Exception as exc:
            error = str(exc)

    return render_template("admin_users.html", users=list_users(), error=error, message=message)


@app.post("/admin/users/<int:user_id>")
@admin_required
def admin_update_user(user_id: int):
    try:
        if user_id == int(session["user_id"]) and request.form.get("is_active") != "on":
            raise ValueError("You cannot deactivate your own account.")
        if user_id == int(session["user_id"]) and request.form.get("is_admin") != "on":
            raise ValueError("You cannot remove your own admin access.")
        update_user(
            user_id=user_id,
            username=request.form.get("username", ""),
            is_admin=request.form.get("is_admin") == "on",
            is_active=request.form.get("is_active") == "on",
            new_password=request.form.get("new_password", ""),
        )
        flash("User updated.", "message")
    except Exception as exc:
        flash(str(exc), "error")
    return redirect(url_for("admin_users"))


@app.route("/change-password", methods=["GET", "POST"])
@login_required
def change_password_route():
    error = None
    message = None
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if new_password != confirm_password:
            error = "New password and confirmation do not match."
        else:
            try:
                change_password(
                    user_id=int(session["user_id"]),
                    current_password=request.form.get("current_password", ""),
                    new_password=new_password,
                )
                message = "Password updated."
            except Exception as exc:
                error = str(exc)

    return render_template("change_password.html", error=error, message=message)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
