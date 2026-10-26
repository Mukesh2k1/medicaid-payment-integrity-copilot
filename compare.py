"""Detect code-level changes between two fee schedule versions. Pure pandas, no AI."""
import numpy as np
import pandas as pd

CHANGE_TYPES = ["Rate increase", "Rate decrease", "New code", "Deleted code",
                "Became not priced", "Became priced", "No change"]
TOLERANCE = 0.005


def _keys(df):
    df = df.copy()
    df["code"] = df["code"].astype(str).str.strip().str.upper()
    df["modifier"] = df["modifier"].fillna("").astype(str).str.strip().str.upper()
    df["code"] = df["code"].astype(object)
    df["modifier"] = df["modifier"].astype(object)
    return df


def compare_schedules(old_df, new_df):
    """Return (changes DataFrame, summary dict)."""
    old = _keys(old_df)[["code", "modifier", "rate", "rate_status"]]
    new = _keys(new_df)[["code", "modifier", "rate", "rate_status"]]
    m = old.merge(new, on=["code", "modifier"], how="outer", suffixes=("_old", "_new"),
                  indicator=True)
    m = m.rename(columns={"rate_old": "old_rate", "rate_new": "new_rate",
                          "rate_status_old": "old_status", "rate_status_new": "new_status"})
    m["old_rate"] = pd.to_numeric(m["old_rate"], errors="coerce").astype(float)
    m["new_rate"] = pd.to_numeric(m["new_rate"], errors="coerce").astype(float)
    side = m["_merge"].astype(str)
    old_p = m["old_status"].astype(object).eq("Priced")
    new_p = m["new_status"].astype(object).eq("Priced")
    old_np = m["old_status"].astype(object).eq("Not priced")
    new_np = m["new_status"].astype(object).eq("Not priced")
    both = side.eq("both")
    diff = (m["new_rate"] - m["old_rate"]).astype(float)

    conditions = [
        side.eq("right_only"),
        side.eq("left_only"),
        both & old_p & new_np,
        both & old_np & new_p,
        both & old_np & new_np,
        both & old_p & new_p & (diff >= TOLERANCE),
        both & old_p & new_p & (-diff >= TOLERANCE),
    ]
    choices = ["New code", "Deleted code", "Became not priced", "Became priced", "No change",
               "Rate increase", "Rate decrease"]
    m["change_type"] = np.select(conditions, choices, default="No change").astype(object)

    moved = m["change_type"].isin(["Rate increase", "Rate decrease"])
    still = m["change_type"].eq("No change") & both & old_p & new_p
    m["change_amt"] = np.where(moved, diff, np.where(still, 0.0, np.nan)).astype(float)
    pct_ok = moved & (m["old_rate"] > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        pct = m["change_amt"] / m["old_rate"] * 100
    m["change_pct"] = np.where(pct_ok, pct, np.nan).astype(float)

    m["_abs"] = m["change_amt"].abs()
    m = m.sort_values(["_abs", "code", "modifier"], ascending=[False, True, True],
                      na_position="last", kind="mergesort")
    changes = m[["code", "modifier", "change_type", "old_rate", "new_rate", "change_amt",
                 "change_pct"]].reset_index(drop=True)

    counts = changes["change_type"].value_counts()
    summary = {t: int(counts.get(t, 0)) for t in CHANGE_TYPES}
    summary.update({"old_rows": int(len(old)), "new_rows": int(len(new)),
                    "total_rows": int(len(changes)), "in_both": int(both.sum())})
    return changes, summary
