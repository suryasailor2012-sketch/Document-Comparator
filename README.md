# Quotation Comparator Web App

A Railway-ready Flask application for comparing two quotation documents. It accepts PDF, Excel, and CSV files, stores each comparison in its own folder, and produces CSV and JSON reports with source locations for every difference.

## Features

- Upload two quotation documents from the browser.
- Compare item names, quantities, unit prices, and total prices.
- Store each comparison under `comparisons/<comparison-id>/`.
- Preserve uploaded parent documents under `uploads/`.
- Save reports under `outputs/`.
- Create `outputs/report.html`, `outputs/differences.csv`, and `outputs/differences.json`.
- Show document locations for differences:
  - PDF: page, table, row, and bounding box metadata in JSON.
  - Excel/CSV: row number.
- Login required for all comparison and download pages.
- Admin page for adding and updating users.
- User password-change page.

## Default Admin

On first startup the app creates one admin account.

- Username: `admin`
- Password: `admin123`

For production, set these Railway environment variables before the first deploy:

- `ADMIN_USERNAME`
- `ADMIN_PASSWORD`
- `SECRET_KEY`
- `DATA_DIR`

Use a Railway volume for `DATA_DIR` if you want users and comparison folders to persist across redeploys.

## Local Run

```powershell
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

## Railway

Railway can deploy this repository directly. The app uses:

- `Procfile`: `web: gunicorn app:app`
- `requirements.txt`
- `runtime.txt`

Railway will provide the `PORT` environment variable automatically.
