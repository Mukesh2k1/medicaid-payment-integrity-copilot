"""Read a state fee schedule (xls, xlsx, xlsm, csv) into one clean table.

Output columns: code, modifier, rate, rate_status.
"""
from __future__ import annotations

import io
import os
import re

import numpy as np
import pandas as pd

CODE_NAME = re.compile(r"(procedure|proc\b|proc\.|hcpcs|cpt|\bcode\b|^code)", re.I)
CODE_EXCLUDE = re.compile(r"(desc|modifier|\bmod\b|type|pos\b|place|category|section|revenue)", re.I)
MOD_NAME = re.compile(r"(^mod|modifier|\bmod\b|\bmod\s*\d)", re.I)
RATE_NAME = re.compile(r"(fee|rate|amount|price|reimb|allow|max)", re.I)
RATE_EXCLUDE = re.compile(r"(effective|date|percent|%|unit|type|indicator|flag|required)", re.I)
VALID_CODE = re.compile(r"^[A-Z0-9]{4,7}$")

HEADER_SCAN_ROWS = 40
EXCEL_EXT = {".xls": "xlrd", ".xlsx": "openpyxl", ".xlsm": "openpyxl"}


class ScheduleError(ValueError):
    """A fee schedule could not be read. The message tells the user how to fix it."""


# ----------------------------------------------------------------------------- source helpers
def _name_and_bytes(src):
    """Return (display name, bytes or None, path or None) for a path or uploaded file."""
    if isinstance(src, (str, os.PathLike)):
        path = os.fspath(src)
        return os.path.basename(path), None, path
    name = getattr(src, "name", None) or "uploaded file"
    if hasattr(src, "getvalue"):
        data = src.getvalue()
    else:
        try:
            src.seek(0)
        except Exception:
            pass
        data = src.read()
    return os.path.basename(str(name)), data, None


def _ext(name):
    return os.path.splitext(name)[1].lower()


def _check_ext(name):
    ext = _ext(name)
    if ext not in EXCEL_EXT and ext != ".csv":
        raise ScheduleError(
            f"'{name}' is not a fee schedule file. Upload an Excel (.xls, .xlsx) or .csv file."
        )
    return ext


def _fresh(data, path):
    return io.BytesIO(data) if data is not None else path


def _read_raw(src) -> dict:
    """Read every sheet with header=None, dtype=str. Returns {sheet: DataFrame}."""
    name, data, path = _name_and_bytes(src)
    ext = _check_ext(name)
    try:
        if ext == ".csv":
            raw = pd.read_csv(_fresh(data, path), header=None, dtype=str,
                              keep_default_na=False, encoding_errors="replace")
            return {"(csv)": raw}
        return pd.read_excel(_fresh(data, path), sheet_name=None, header=None, dtype=str,
                             engine=EXCEL_EXT[ext])
    except ScheduleError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ScheduleError(
            f"Could not open '{name}'. Check it is a valid {ext} file that is not password "
            f"protected. Details: {exc}"
        ) from exc


def sheet_names(src) -> list:
    name, _, _ = _name_and_bytes(src)
    if _check_ext(name) == ".csv":
        return ["(csv)"]
    return list(_read_raw(src).keys())


# ----------------------------------------------------------------------------- header + columns
def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and np.isnan(v):
        return ""
    s = str(v)
    if s.lower() == "nan":
        return ""
    return re.sub(r"\s+", " ", s).strip()


def _is_code_name(name: str) -> bool:
    return bool(CODE_NAME.search(name)) and not CODE_EXCLUDE.search(name)


def find_header_row(raw: pd.DataFrame) -> int:
    for i in range(min(HEADER_SCAN_ROWS, len(raw))):
        cells = [_cell(v) for v in raw.iloc[i].tolist()]
        filled = [c for c in cells if c]
        if len(filled) >= 3 and any(_is_code_name(c) for c in filled):
            return i
    return 0


def _column_names(row_values) -> list:
    names, seen = [], {}
    for i, v in enumerate(row_values):
        n = _cell(v) or f"Column {i + 1}"
        if n in seen:
            seen[n] += 1
            n = f"{n} ({seen[n]})"
        else:
            seen[n] = 1
        names.append(n)
    return names


def _frame(raw: pd.DataFrame, header_row: int) -> pd.DataFrame:
    df = raw.iloc[header_row + 1:].copy()
    df.columns = _column_names(raw.iloc[header_row].tolist())
    return df.reset_index(drop=True)


def _clean_code_series(s: pd.Series) -> pd.Series:
    s = s.fillna("").astype(str).str.strip().str.upper()
    return s.str.replace(r"\.0$", "", regex=True)


def _numeric_share(s: pd.Series) -> float:
    vals = s.fillna("").astype(str).str.replace(r"[\$,\s]", "", regex=True)
    vals = vals[vals != ""]
    if len(vals) == 0:
        return 0.0
    return float(pd.to_numeric(vals, errors="coerce").notna().mean())


def _pick_columns(df: pd.DataFrame):
    cols = list(df.columns)
    # code column
    code_col, best = None, -1.0
    for c in cols:
        if not _is_code_name(c):
            continue
        vals = _clean_code_series(df[c])
        vals = vals[vals != ""]
        share = float(vals.str.match(VALID_CODE).mean()) if len(vals) else 0.0
        if share > best:
            code_col, best = c, share
    # modifier columns
    mod_cols = [c for c in cols if c != code_col and MOD_NAME.search(c) and "desc" not in c.lower()]
    # rate column
    rate_col, best = None, -99.0
    for c in cols:
        if c == code_col or c in mod_cols:
            continue
        if not RATE_NAME.search(c) or RATE_EXCLUDE.search(c):
            continue
        low = c.lower()
        score = _numeric_share(df[c])
        if "non" in low and "facil" in low:
            score += 1.0
        elif "facil" in low:
            score -= 0.5
        if score > best:
            rate_col, best = c, score
    if rate_col is None:
        best = 0.5
        for c in cols:
            if c == code_col or c in mod_cols:
                continue
            share = _numeric_share(df[c])
            if share > best:
                rate_col, best = c, share
    return code_col, mod_cols, rate_col


def detect_layout(src) -> dict:
    sheets = _read_raw(src)
    best, best_score = None, -1.0
    for sheet, raw in sheets.items():
        if raw is None or raw.empty:
            continue
        hdr = find_header_row(raw)
        df = _frame(raw, hdr)
        code_col, mod_cols, rate_col = _pick_columns(df)
        score = len(df) * (1 if code_col else 0.01) * (1 if rate_col else 0.01)
        if score > best_score:
            best_score = score
            best = {"sheet": sheet, "header_row": hdr, "columns": list(df.columns),
                    "code_col": code_col, "mod_cols": mod_cols, "rate_col": rate_col}
    if best is None:
        name, _, _ = _name_and_bytes(src)
        raise ScheduleError(f"'{name}' has no data. Check you uploaded the right file.")
    return best


def list_columns(src, sheet, header_row) -> list:
    sheets = _read_raw(src)
    raw = sheets.get(sheet)
    if raw is None:
        raw = next(iter(sheets.values()))
    if header_row >= len(raw):
        return []
    return _column_names(raw.iloc[header_row].tolist())


# ----------------------------------------------------------------------------- main loader
def load_fee_schedule(src, sheet=None, header_row=None, code_col=None, mod_cols=None,
                      rate_col=None):
    """Return (df, warnings, layout). Anything left as None is auto-detected."""
    name, _, _ = _name_and_bytes(src)
    sheets = _read_raw(src)
    warnings = []

    if sheet is None or sheet not in sheets:
        auto = detect_layout(src)
        sheet = auto["sheet"]
        if header_row is None:
            header_row = auto["header_row"]
    raw = sheets[sheet]
    if raw is None or raw.empty:
        raise ScheduleError(f"Sheet '{sheet}' in '{name}' is empty. Pick the sheet that holds the rates.")
    if header_row is None:
        header_row = find_header_row(raw)
    header_row = int(header_row)
    if header_row < 0 or header_row >= len(raw):
        raise ScheduleError(
            f"Header row {header_row + 1} is past the end of sheet '{sheet}', which has "
            f"{len(raw)} rows. Pick a smaller header row under 'Column mapping'."
        )
    df = _frame(raw, header_row)
    auto_code, auto_mods, auto_rate = _pick_columns(df)
    code_col = code_col or auto_code
    if mod_cols is None:
        mod_cols = auto_mods
    rate_col = rate_col or auto_rate

    found = ", ".join(list(df.columns)[:15])
    if not code_col or code_col not in df.columns:
        raise ScheduleError(
            f"Could not find the procedure code column in '{name}' (sheet '{sheet}', header row "
            f"{header_row + 1}). Columns found: {found}. Pick them under 'Column mapping'."
        )
    if not rate_col or rate_col not in df.columns:
        raise ScheduleError(
            f"Could not find the rate column in '{name}' (sheet '{sheet}', header row "
            f"{header_row + 1}). Columns found: {found}. Pick them under 'Column mapping'."
        )
    mod_cols = [m for m in (mod_cols or []) if m in df.columns and m not in (code_col, rate_col)]

    out = pd.DataFrame()
    out["code"] = _clean_code_series(df[code_col])
    short = out["code"].str.fullmatch(r"\d{1,4}")
    if short.any():
        out.loc[short, "code"] = out.loc[short, "code"].str.zfill(5)
        warnings.append(f"Restored leading zeros on {int(short.sum()):,} numeric codes.")

    if mod_cols:
        parts = []
        for m in mod_cols:
            s = df[m].fillna("").astype(str).str.strip().str.upper()
            s = s.str.replace(r"\.0$", "", regex=True)
            s = s.where(~s.isin(["", "NAN", "NONE"]), "")
            parts.append(s)
        joined = parts[0].to_frame("m0")
        for i, p in enumerate(parts[1:], start=1):
            joined[f"m{i}"] = p
        out["modifier"] = joined.apply(lambda r: "-".join([v for v in r if v]), axis=1)
        out["modifier"] = out["modifier"].str.replace(r"\.0$", "", regex=True)
    else:
        out["modifier"] = ""

    rate_text = df[rate_col].fillna("").astype(str).str.replace(r"[\$,\s]", "", regex=True)
    out["rate"] = pd.to_numeric(rate_text, errors="coerce").astype(float)

    valid = out["code"].str.match(VALID_CODE)
    skipped = int((~valid).sum())
    blank = int((out["code"] == "").sum())
    out = out[valid].copy()
    non_blank_skipped = skipped - blank
    if non_blank_skipped > 0:
        warnings.append(
            f"Skipped {non_blank_skipped:,} rows that are titles, notes or section headers, not codes."
        )
    if out.empty:
        raise ScheduleError(
            f"No valid procedure codes found in column '{code_col}' of '{name}'. Check the header "
            f"row and the code column under 'Column mapping'."
        )
    if out["rate"].notna().sum() == 0:
        raise ScheduleError(
            f"The rate column '{rate_col}' in '{name}' has no numeric values. Pick the column "
            f"that holds dollar rates under 'Column mapping'."
        )

    dup = out.duplicated(["code", "modifier"], keep="first")
    if dup.any():
        warnings.append(f"Kept the first of {int(dup.sum()):,} repeated code and modifier rows.")
        out = out[~dup]

    out["rate_status"] = np.where(out["rate"].notna(), "Priced", "Not priced")
    out["code"] = out["code"].astype(object)
    out["modifier"] = out["modifier"].astype(object)
    out["rate_status"] = out["rate_status"].astype(object)
    out = out[["code", "modifier", "rate", "rate_status"]].reset_index(drop=True)

    layout = {"sheet": sheet, "header_row": header_row, "columns": list(df.columns),
              "code_col": code_col, "mod_cols": mod_cols, "rate_col": rate_col}
    return out, warnings, layout
