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
