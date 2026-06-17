from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import pandas as pd


ITEM_ALIASES = {
    "item",
    "itemname",
    "name",
    "description",
    "desc",
    "product",
    "productname",
    "material",
    "service",
    "part",
    "particulars",
}

QTY_ALIASES = {"qty", "quantity", "qnty", "units", "unit", "no", "nos", "pcs", "pieces"}

UNIT_PRICE_ALIASES = {
    "unitprice",
    "unitrate",
    "rate",
    "price",
    "unitcost",
    "cost",
    "uprice",
    "priceeach",
}

TOTAL_ALIASES = {
    "total",
    "totalprice",
    "linetotal",
    "amount",
    "value",
    "extendedprice",
    "netamount",
    "subtotal",
}


@dataclass(frozen=True)
class ColumnMap:
    item: str
    qty: str
    unit_price: str
    total: str


@dataclass(frozen=True)
class LoadedQuotation:
    rows: list[dict[str, object]]
    columns: ColumnMap
    raw_columns: list[str]


def compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def normalize_item(value: object) -> str:
    text = str(value).strip().lower()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^a-z0-9 ]+", "", text).strip()


def display(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def parse_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9.\-]", "", text)
    if cleaned in {"", "-", ".", "-."}:
        return None
    try:
        number = float(cleaned)
        return -number if negative else number
    except ValueError:
        return None


def money(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.2f}"


def header_score(row: list[object]) -> int:
    compacted = [compact(cell) for cell in row]
    score = 0
    if any(cell in ITEM_ALIASES or any(alias in cell for alias in ITEM_ALIASES) for cell in compacted):
        score += 1
    if any(cell in QTY_ALIASES or any(alias in cell for alias in QTY_ALIASES) for cell in compacted):
        score += 1
    if any(
        cell in UNIT_PRICE_ALIASES or any(alias in cell for alias in UNIT_PRICE_ALIASES)
        for cell in compacted
    ):
        score += 1
    if any(cell in TOTAL_ALIASES or any(alias in cell for alias in TOTAL_ALIASES) for cell in compacted):
        score += 1
    return score


def clean_pdf_cell(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\n", " ")).strip()


def make_location(
    source_type: str,
    source_name: str,
    row_number: int | None = None,
    page_number: int | None = None,
    table_number: int | None = None,
    pdf_bbox: tuple[float, float, float, float] | None = None,
) -> dict[str, object]:
    if source_type == "pdf":
        label_parts = [f"page {page_number}"]
        if table_number is not None:
            label_parts.append(f"table {table_number}")
        if row_number is not None:
            label_parts.append(f"row {row_number}")
        label = ", ".join(label_parts)
    else:
        label = f"row {row_number}" if row_number is not None else "row unknown"

    return {
        "source_file": source_name,
        "source_type": source_type,
        "label": label,
        "row_number": row_number,
        "page_number": page_number,
        "table_number": table_number,
        "pdf_bbox": list(pdf_bbox) if pdf_bbox else None,
    }


def dataframe_from_pdf_table(
    table_data: list[list[object]],
    source_name: str,
    page_number: int,
    table_number: int,
    table_bbox: tuple[float, float, float, float] | None,
) -> pd.DataFrame | None:
    cleaned = [
        [clean_pdf_cell(cell) for cell in row]
        for row in table_data
        if any(clean_pdf_cell(cell) for cell in row)
    ]
    if not cleaned:
        return None

    header_index = max(range(len(cleaned)), key=lambda index: header_score(cleaned[index]))
    if header_score(cleaned[header_index]) < 3:
        return None

    headers = cleaned[header_index]
    data_rows = cleaned[header_index + 1 :]
    if not data_rows:
        return None

    width = len(headers)
    normalized_rows = []
    locations = []
    for offset, row in enumerate(data_rows, start=1):
        if len(row) < width:
            row = row + [""] * (width - len(row))
        normalized_rows.append(row[:width])
        locations.append(
            make_location(
                "pdf",
                source_name,
                row_number=offset,
                page_number=page_number,
                table_number=table_number,
                pdf_bbox=table_bbox,
            )
        )

    frame = pd.DataFrame(normalized_rows, columns=headers)
    frame["_source_location"] = locations
    return frame


def parse_pdf_text_lines(text: str, source_name: str) -> pd.DataFrame:
    rows = []
    number = r"\(?-?(?:[A-Z]{2,4}\s*)?\$?\d[\d,]*(?:\.\d+)?\)?"
    line_pattern = re.compile(
        rf"^(?P<item>.+?)\s+(?P<qty>{number})\s+(?P<unit>{number})\s+(?P<total>{number})$"
    )
    for page_index, page_text in enumerate(text.split("\f"), start=1):
        extracted_row = 0
        for line in page_text.splitlines():
            normalized = re.sub(r"\s+", " ", line).strip()
            match = line_pattern.match(normalized)
            if not match:
                continue
            extracted_row += 1
            rows.append(
                {
                    "Item": match.group("item"),
                    "Qty": match.group("qty"),
                    "Unit Price": match.group("unit"),
                    "Total": match.group("total"),
                    "_source_location": make_location(
                        "pdf",
                        source_name,
                        row_number=extracted_row,
                        page_number=page_index,
                    ),
                }
            )

    if not rows:
        raise ValueError("Could not extract quotation rows from PDF text.")
    return pd.DataFrame(rows)


def read_pdf_table(path: Path) -> pd.DataFrame:
    import pdfplumber

    frames: list[pd.DataFrame] = []
    extracted_text_parts: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            extracted_text_parts.append(page.extract_text() or "")
            try:
                tables = page.find_tables()
            except Exception:
                tables = []

            for table_index, table in enumerate(tables, start=1):
                frame = dataframe_from_pdf_table(
                    table.extract(),
                    path.name,
                    page_index,
                    table_index,
                    table.bbox,
                )
                if frame is not None:
                    frames.append(frame)

            if not tables:
                for table_index, table_data in enumerate(page.extract_tables() or [], start=1):
                    frame = dataframe_from_pdf_table(
                        table_data,
                        path.name,
                        page_index,
                        table_index,
                        None,
                    )
                    if frame is not None:
                        frames.append(frame)

    if frames:
        return pd.concat(frames, ignore_index=True)

    return parse_pdf_text_lines("\f".join(extracted_text_parts), path.name)


def read_table(path: Path, sheet: str | int | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, sep=None, engine="python")
        df["_source_location"] = [
            make_location("csv", path.name, row_number=index + 2) for index in range(len(df))
        ]
        return df
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=0 if sheet is None else sheet)
        df["_source_location"] = [
            make_location("spreadsheet", path.name, row_number=index + 2) for index in range(len(df))
        ]
        return df
    if suffix == ".pdf":
        return read_pdf_table(path)
    raise ValueError(f"Unsupported file type for {path}. Use .csv, .xlsx, .xls, or .pdf.")


def find_column(columns: Iterable[str], aliases: set[str], label: str) -> str:
    public_columns = [column for column in columns if not str(column).startswith("_")]
    by_compact = {compact(col): col for col in public_columns}

    for alias in aliases:
        if alias in by_compact:
            return by_compact[alias]

    for key, original in by_compact.items():
        if any(alias in key for alias in aliases):
            return original

    available = ", ".join(str(col) for col in public_columns)
    raise ValueError(f"Could not find a {label} column. Available columns: {available}")


def detect_columns(df: pd.DataFrame) -> ColumnMap:
    columns = [str(col) for col in df.columns]
    return ColumnMap(
        item=find_column(columns, ITEM_ALIASES, "item/name"),
        qty=find_column(columns, QTY_ALIASES, "quantity"),
        unit_price=find_column(columns, UNIT_PRICE_ALIASES, "unit price"),
        total=find_column(columns, TOTAL_ALIASES, "total price"),
    )


def clean_quotation(df: pd.DataFrame, columns: ColumnMap) -> list[dict[str, object]]:
    cleaned: list[dict[str, object]] = []
    for row_number, row in df.iterrows():
        item_name = display(row[columns.item])
        item_key = normalize_item(item_name)
        if not item_key:
            continue

        location = row.get("_source_location")
        if not isinstance(location, dict):
            location = make_location("table", "unknown", row_number=int(row_number) + 2)

        cleaned.append(
            {
                "row_number": int(row_number) + 2,
                "item": item_name,
                "key": item_key,
                "qty": parse_number(row[columns.qty]),
                "unit_price": parse_number(row[columns.unit_price]),
                "total": parse_number(row[columns.total]),
                "location": location,
            }
        )
    return cleaned


def load_quotation(path: Path, sheet: str | int | None = None) -> LoadedQuotation:
    df = read_table(path, sheet)
    columns = detect_columns(df)
    return LoadedQuotation(
        rows=clean_quotation(df, columns),
        columns=columns,
        raw_columns=[str(column) for column in df.columns if not str(column).startswith("_")],
    )


def nearly_equal(left: object, right: object, tolerance: float) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) <= tolerance
    return left == right


def difference_row(
    difference_type: str,
    quote_1: dict[str, object] | None,
    quote_2: dict[str, object] | None,
    tolerance: float,
) -> dict[str, object]:
    qty_1 = quote_1["qty"] if quote_1 else None
    qty_2 = quote_2["qty"] if quote_2 else None
    unit_1 = quote_1["unit_price"] if quote_1 else None
    unit_2 = quote_2["unit_price"] if quote_2 else None
    total_1 = quote_1["total"] if quote_1 else None
    total_2 = quote_2["total"] if quote_2 else None

    unit_diff = unit_2 - unit_1 if isinstance(unit_1, float) and isinstance(unit_2, float) else None
    total_diff = total_2 - total_1 if isinstance(total_1, float) and isinstance(total_2, float) else None

    return {
        "difference_type": difference_type,
        "item_quote_1": quote_1["item"] if quote_1 else "",
        "item_quote_2": quote_2["item"] if quote_2 else "",
        "qty_quote_1": money(qty_1 if isinstance(qty_1, float) else None),
        "qty_quote_2": money(qty_2 if isinstance(qty_2, float) else None),
        "unit_price_quote_1": money(unit_1 if isinstance(unit_1, float) else None),
        "unit_price_quote_2": money(unit_2 if isinstance(unit_2, float) else None),
        "unit_price_difference": money(unit_diff),
        "total_quote_1": money(total_1 if isinstance(total_1, float) else None),
        "total_quote_2": money(total_2 if isinstance(total_2, float) else None),
        "total_difference": money(total_diff),
        "qty_differs": "Yes" if not nearly_equal(qty_1, qty_2, tolerance) else "No",
        "unit_price_differs": "Yes" if not nearly_equal(unit_1, unit_2, tolerance) else "No",
        "total_differs": "Yes" if not nearly_equal(total_1, total_2, tolerance) else "No",
        "quote_1_location": quote_1.get("location") if quote_1 else None,
        "quote_2_location": quote_2.get("location") if quote_2 else None,
    }


def best_unmatched_pairs(
    quote_1: list[dict[str, object]],
    quote_2: list[dict[str, object]],
    threshold: float,
) -> list[tuple[dict[str, object], dict[str, object], float]]:
    candidates: list[tuple[float, int, int]] = []
    for i, left in enumerate(quote_1):
        for j, right in enumerate(quote_2):
            score = SequenceMatcher(None, str(left["key"]), str(right["key"])).ratio()
            if score >= threshold:
                candidates.append((score, i, j))

    pairs: list[tuple[dict[str, object], dict[str, object], float]] = []
    used_left: set[int] = set()
    used_right: set[int] = set()
    for score, i, j in sorted(candidates, reverse=True):
        if i in used_left or j in used_right:
            continue
        used_left.add(i)
        used_right.add(j)
        pairs.append((quote_1[i], quote_2[j], score))
    return pairs


def compare(
    quote_1: list[dict[str, object]],
    quote_2: list[dict[str, object]],
    tolerance: float = 0.01,
    name_similarity: float = 0.72,
) -> list[dict[str, object]]:
    by_key_1 = {str(row["key"]): row for row in quote_1}
    by_key_2 = {str(row["key"]): row for row in quote_2}
    exact_keys = sorted(set(by_key_1) & set(by_key_2))

    results: list[dict[str, object]] = []
    matched_1 = set()
    matched_2 = set()

    for key in exact_keys:
        left = by_key_1[key]
        right = by_key_2[key]
        matched_1.add(key)
        matched_2.add(key)
        qty_diff = not nearly_equal(left["qty"], right["qty"], tolerance)
        unit_diff = not nearly_equal(left["unit_price"], right["unit_price"], tolerance)
        total_diff = not nearly_equal(left["total"], right["total"], tolerance)
        if qty_diff or unit_diff or total_diff:
            types = []
            if unit_diff:
                types.append("unit_price_differs")
            if qty_diff:
                types.append("qty_differs")
            if total_diff:
                types.append("total_differs")
            results.append(difference_row("; ".join(types), left, right, tolerance))

    unmatched_1 = [row for row in quote_1 if str(row["key"]) not in matched_1]
    unmatched_2 = [row for row in quote_2 if str(row["key"]) not in matched_2]

    paired_left_keys: set[str] = set()
    paired_right_keys: set[str] = set()
    for left, right, score in best_unmatched_pairs(unmatched_1, unmatched_2, name_similarity):
        paired_left_keys.add(str(left["key"]))
        paired_right_keys.add(str(right["key"]))
        row = difference_row("item_name_differs", left, right, tolerance)
        row["name_similarity"] = f"{score:.2f}"
        results.append(row)

    for left in unmatched_1:
        if str(left["key"]) not in paired_left_keys:
            results.append(difference_row("item_only_in_quote_1", left, None, tolerance))

    for right in unmatched_2:
        if str(right["key"]) not in paired_right_keys:
            results.append(difference_row("item_only_in_quote_2", None, right, tolerance))

    return results


def location_label(location: object) -> str:
    if not isinstance(location, dict):
        return ""
    return str(location.get("label") or "")


def format_row_value(row: dict[str, object] | None, key: str) -> str:
    if row is None:
        return ""
    value = row.get(key)
    return money(value if isinstance(value, float) else None)


def similar_to_cluster(row: dict[str, object], cluster: list[dict[str, object]], threshold: float) -> bool:
    row_key = str(row["key"])
    return any(SequenceMatcher(None, row_key, str(existing["key"])).ratio() >= threshold for existing in cluster)


def cluster_multi_rows(
    quote_rows: list[tuple[int, str, dict[str, object]]],
    name_similarity: float,
) -> list[list[tuple[int, str, dict[str, object]]]]:
    clusters: list[list[tuple[int, str, dict[str, object]]]] = []
    for quote_index, quote_name, row in sorted(quote_rows, key=lambda item: str(item[2]["key"])):
        placed = False
        for cluster in clusters:
            if similar_to_cluster(row, [entry[2] for entry in cluster], name_similarity):
                cluster.append((quote_index, quote_name, row))
                placed = True
                break
        if not placed:
            clusters.append([(quote_index, quote_name, row)])
    return clusters


def values_differ(values: list[float | None], tolerance: float) -> bool:
    present_values = [value for value in values if value is not None]
    if len(present_values) <= 1:
        return False
    first = present_values[0]
    return any(abs(value - first) > tolerance for value in present_values[1:])


def compare_many(
    quotations: list[tuple[str, LoadedQuotation]],
    tolerance: float = 0.01,
    name_similarity: float = 0.72,
) -> list[dict[str, object]]:
    all_rows: list[tuple[int, str, dict[str, object]]] = []
    for quote_index, (quote_name, quotation) in enumerate(quotations, start=1):
        for row in quotation.rows:
            all_rows.append((quote_index, quote_name, row))

    results: list[dict[str, object]] = []
    for cluster in cluster_multi_rows(all_rows, name_similarity):
        by_quote: dict[int, tuple[str, dict[str, object]]] = {}
        for quote_index, quote_name, row in cluster:
            by_quote.setdefault(quote_index, (quote_name, row))

        entries = []
        item_names = set()
        qty_values: list[float | None] = []
        unit_values: list[float | None] = []
        total_values: list[float | None] = []

        for quote_index, (quote_name, _quotation) in enumerate(quotations, start=1):
            matched = by_quote.get(quote_index)
            row = matched[1] if matched else None
            if row:
                item_names.add(str(row["item"]).strip().lower())
                qty_values.append(row.get("qty") if isinstance(row.get("qty"), float) else None)
                unit_values.append(row.get("unit_price") if isinstance(row.get("unit_price"), float) else None)
                total_values.append(row.get("total") if isinstance(row.get("total"), float) else None)
            entries.append(
                {
                    "quote_index": quote_index,
                    "quote_name": quote_name,
                    "present": row is not None,
                    "item": row.get("item") if row else "",
                    "qty": format_row_value(row, "qty"),
                    "unit_price": format_row_value(row, "unit_price"),
                    "total": format_row_value(row, "total"),
                    "location": row.get("location") if row else None,
                }
            )

        difference_types = []
        if len(by_quote) < len(quotations):
            difference_types.append("item_missing")
        if len(item_names) > 1:
            difference_types.append("item_name_differs")
        if values_differ(qty_values, tolerance):
            difference_types.append("qty_differs")
        if values_differ(unit_values, tolerance):
            difference_types.append("unit_price_differs")
        if values_differ(total_values, tolerance):
            difference_types.append("total_differs")

        if difference_types:
            preferred_name = next((str(entry["item"]) for entry in entries if entry["item"]), "")
            results.append(
                {
                    "item_group": preferred_name,
                    "difference_type": "; ".join(difference_types),
                    "entries": entries,
                }
            )

    return results


def write_csv_report(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "difference_type",
        "item_quote_1",
        "item_quote_2",
        "qty_quote_1",
        "qty_quote_2",
        "unit_price_quote_1",
        "unit_price_quote_2",
        "unit_price_difference",
        "total_quote_1",
        "total_quote_2",
        "total_difference",
        "qty_differs",
        "unit_price_differs",
        "total_differs",
        "name_similarity",
        "quote_1_source_file",
        "quote_1_location",
        "quote_2_source_file",
        "quote_2_location",
    ]
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            loc_1 = row.get("quote_1_location")
            loc_2 = row.get("quote_2_location")
            flattened = dict(row)
            flattened["quote_1_source_file"] = loc_1.get("source_file") if isinstance(loc_1, dict) else ""
            flattened["quote_1_location"] = location_label(loc_1)
            flattened["quote_2_source_file"] = loc_2.get("source_file") if isinstance(loc_2, dict) else ""
            flattened["quote_2_location"] = location_label(loc_2)
            writer.writerow({field: flattened.get(field, "") for field in fieldnames})


def write_json_report(rows: list[dict[str, object]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def write_multi_csv_report(rows: list[dict[str, object]], output_path: Path, quote_names: list[str]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["item_group", "difference_type"]
    for index, quote_name in enumerate(quote_names, start=1):
        prefix = f"quote_{index}"
        fieldnames.extend(
            [
                f"{prefix}_name",
                f"{prefix}_item",
                f"{prefix}_qty",
                f"{prefix}_unit_price",
                f"{prefix}_total",
                f"{prefix}_location",
            ]
        )

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            flattened = {
                "item_group": row.get("item_group", ""),
                "difference_type": row.get("difference_type", ""),
            }
            for entry in row.get("entries", []):
                prefix = f"quote_{entry['quote_index']}"
                flattened[f"{prefix}_name"] = entry.get("quote_name", "")
                flattened[f"{prefix}_item"] = entry.get("item", "")
                flattened[f"{prefix}_qty"] = entry.get("qty", "")
                flattened[f"{prefix}_unit_price"] = entry.get("unit_price", "")
                flattened[f"{prefix}_total"] = entry.get("total", "")
                flattened[f"{prefix}_location"] = location_label(entry.get("location"))
            writer.writerow({field: flattened.get(field, "") for field in fieldnames})


def write_multi_html_report(
    rows: list[dict[str, object]],
    output_path: Path,
    quote_names: list[str],
    title: str = "Quotation Differences",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header_cells = "".join(
        f"<th>{html.escape(name)}<br>Item</th><th>{html.escape(name)}<br>Qty</th>"
        f"<th>{html.escape(name)}<br>Unit</th><th>{html.escape(name)}<br>Total</th>"
        f"<th>{html.escape(name)}<br>Location</th>"
        for name in quote_names
    )
    rendered_rows = []
    for row in rows:
        entry_cells = []
        for entry in row.get("entries", []):
            location = entry.get("location")
            location_text = ""
            if isinstance(location, dict):
                location_text = (
                    f"{html.escape(str(location.get('source_file', '')))}<br>"
                    f"<strong>{html.escape(str(location.get('label', '')))}</strong>"
                )
            entry_cells.extend(
                [
                    f"<td>{html.escape(str(entry.get('item', '')))}</td>",
                    f"<td>{html.escape(str(entry.get('qty', '')))}</td>",
                    f"<td>{html.escape(str(entry.get('unit_price', '')))}</td>",
                    f"<td>{html.escape(str(entry.get('total', '')))}</td>",
                    f"<td>{location_text}</td>",
                ]
            )
        rendered_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('difference_type', '')))}</td>"
            f"<td>{html.escape(str(row.get('item_group', '')))}</td>"
            f"{''.join(entry_cells)}"
            "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 28px; color: #18212f; }}
    h1 {{ margin: 0 0 16px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
    th, td {{ border: 1px solid #d9e0e8; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #eef4f8; }}
    strong {{ color: #111827; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>Total item groups with differences: {len(rows)}</p>
  <table>
    <thead>
      <tr><th>Difference</th><th>Item group</th>{header_cells}</tr>
    </thead>
    <tbody>{''.join(rendered_rows)}</tbody>
  </table>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")


def write_html_report(rows: list[dict[str, object]], output_path: Path, title: str = "Quotation Differences") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    rendered_rows = []
    for row in rows:
        loc_1 = row.get("quote_1_location")
        loc_2 = row.get("quote_2_location")
        loc_1_text = ""
        loc_2_text = ""
        if isinstance(loc_1, dict):
            loc_1_file = html.escape(str(loc_1.get("source_file", "")))
            loc_1_label = html.escape(str(loc_1.get("label", "")))
            loc_1_text = f"{loc_1_file}<br><strong>{loc_1_label}</strong>"
        if isinstance(loc_2, dict):
            loc_2_file = html.escape(str(loc_2.get("source_file", "")))
            loc_2_label = html.escape(str(loc_2.get("label", "")))
            loc_2_text = f"{loc_2_file}<br><strong>{loc_2_label}</strong>"

        rendered_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('difference_type', '')))}</td>"
            f"<td>{html.escape(str(row.get('item_quote_1', '')))}</td>"
            f"<td>{loc_1_text}</td>"
            f"<td>{html.escape(str(row.get('item_quote_2', '')))}</td>"
            f"<td>{loc_2_text}</td>"
            f"<td>{html.escape(str(row.get('qty_quote_1', '')))} / {html.escape(str(row.get('qty_quote_2', '')))}</td>"
            f"<td>{html.escape(str(row.get('unit_price_quote_1', '')))} / {html.escape(str(row.get('unit_price_quote_2', '')))}</td>"
            f"<td>{html.escape(str(row.get('total_quote_1', '')))} / {html.escape(str(row.get('total_quote_2', '')))}</td>"
            "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 28px; color: #18212f; }}
    h1 {{ margin: 0 0 16px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d9e0e8; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #eef4f8; }}
    strong {{ color: #111827; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p>Total differences: {len(rows)}</p>
  <table>
    <thead>
      <tr>
        <th>Difference</th>
        <th>Document 1 item</th>
        <th>Document 1 location</th>
        <th>Document 2 item</th>
        <th>Document 2 location</th>
        <th>Qty</th>
        <th>Unit price</th>
        <th>Total</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rendered_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    output_path.write_text(document, encoding="utf-8")


def summarize(rows: list[dict[str, object]]) -> dict[str, int]:
    summary = {
        "total_differences": len(rows),
        "unit_price_differs": 0,
        "qty_differs": 0,
        "total_differs": 0,
        "item_name_differs": 0,
        "item_only_in_quote_1": 0,
        "item_only_in_quote_2": 0,
        "item_missing": 0,
    }
    for row in rows:
        difference_type = str(row.get("difference_type", ""))
        for key in list(summary.keys())[1:]:
            if key in difference_type:
                summary[key] += 1
    return summary


def compare_multiple_files(
    quote_paths: list[Path],
    output_dir: Path,
    tolerance: float = 0.01,
    name_similarity: float = 0.72,
) -> dict[str, object]:
    if len(quote_paths) < 2:
        raise ValueError("Upload at least two quotation documents.")

    loaded = [(path.name, load_quotation(path)) for path in quote_paths]
    rows = compare_many(loaded, tolerance, name_similarity)
    quote_names = [name for name, _quotation in loaded]

    csv_path = output_dir / "differences.csv"
    json_path = output_dir / "differences.json"
    html_path = output_dir / "report.html"
    write_multi_csv_report(rows, csv_path, quote_names)
    write_json_report(rows, json_path)
    write_multi_html_report(rows, html_path, quote_names)

    return {
        "mode": "multi",
        "quote_names": quote_names,
        "quote_counts": [len(quotation.rows) for _name, quotation in loaded],
        "rows": rows,
        "summary": summarize(rows),
        "csv_path": csv_path,
        "json_path": json_path,
        "html_path": html_path,
    }


def compare_files(
    quote_1_path: Path,
    quote_2_path: Path,
    output_dir: Path,
    tolerance: float = 0.01,
    name_similarity: float = 0.72,
) -> dict[str, object]:
    quote_1 = load_quotation(quote_1_path)
    quote_2 = load_quotation(quote_2_path)
    rows = compare(quote_1.rows, quote_2.rows, tolerance, name_similarity)

    csv_path = output_dir / "differences.csv"
    json_path = output_dir / "differences.json"
    html_path = output_dir / "report.html"
    write_csv_report(rows, csv_path)
    write_json_report(rows, json_path)
    write_html_report(rows, html_path)

    return {
        "quote_1_count": len(quote_1.rows),
        "quote_2_count": len(quote_2.rows),
        "rows": rows,
        "summary": summarize(rows),
        "csv_path": csv_path,
        "json_path": json_path,
        "html_path": html_path,
    }
