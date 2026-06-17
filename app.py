from __future__ import annotations

import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, render_template, request, send_from_directory
from werkzeug.utils import secure_filename

from quotation_compare import compare_files


BASE_DIR = Path(__file__).resolve().parent
COMPARISONS_DIR = BASE_DIR / "comparisons"
ALLOWED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".pdf"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024


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


def build_downloads(comparison_id: str) -> dict[str, str]:
    return {
        "csv": f"/comparison/{comparison_id}/download/outputs/differences.csv",
        "json": f"/comparison/{comparison_id}/download/outputs/differences.json",
        "html": f"/comparison/{comparison_id}/download/outputs/report.html",
        "quote_1": f"/comparison/{comparison_id}/download/uploads/quote_1",
        "quote_2": f"/comparison/{comparison_id}/download/uploads/quote_2",
    }


@app.get("/")
def index():
    return render_template("index.html", result=None, error=None)


@app.post("/compare")
def compare_route():
    comparison_dir = create_comparison_folder()
    uploads_dir = comparison_dir / "uploads"
    outputs_dir = comparison_dir / "outputs"

    try:
        quote_1 = save_upload(request.files.get("quote_1"), uploads_dir, "quote_1")
        quote_2 = save_upload(request.files.get("quote_2"), uploads_dir, "quote_2")

        quote_1_alias = uploads_dir / f"quote_1{quote_1.suffix.lower()}"
        quote_2_alias = uploads_dir / f"quote_2{quote_2.suffix.lower()}"
        shutil.copy2(quote_1, quote_1_alias)
        shutil.copy2(quote_2, quote_2_alias)

        tolerance = float(request.form.get("tolerance") or 0.01)
        name_similarity = float(request.form.get("name_similarity") or 0.72)
        comparison = compare_files(quote_1, quote_2, outputs_dir, tolerance, name_similarity)

    except Exception as exc:
        shutil.rmtree(comparison_dir, ignore_errors=True)
        return render_template("index.html", result=None, error=str(exc)), 400

    comparison_id = comparison_dir.name
    result = {
        "comparison_id": comparison_id,
        "quote_1_name": quote_1.name,
        "quote_2_name": quote_2.name,
        "quote_1_count": comparison["quote_1_count"],
        "quote_2_count": comparison["quote_2_count"],
        "rows": comparison["rows"],
        "summary": comparison["summary"],
        "downloads": build_downloads(comparison_id),
        "folder": str(comparison_dir),
    }
    return render_template("index.html", result=result, error=None)


@app.get("/comparison/<comparison_id>/download/<path:relative_path>")
def download_file(comparison_id: str, relative_path: str):
    comparison_dir = (COMPARISONS_DIR / comparison_id).resolve()
    if not str(comparison_dir).startswith(str(COMPARISONS_DIR.resolve())):
        abort(404)

    target = (comparison_dir / relative_path).resolve()
    if not str(target).startswith(str(comparison_dir)):
        abort(404)

    if relative_path == "uploads/quote_1":
        matches = sorted((comparison_dir / "uploads").glob("quote_1.*"))
        if not matches:
            abort(404)
        target = matches[0]
    elif relative_path == "uploads/quote_2":
        matches = sorted((comparison_dir / "uploads").glob("quote_2.*"))
        if not matches:
            abort(404)
        target = matches[0]

    if not target.exists():
        abort(404)
    return send_from_directory(target.parent, target.name, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
