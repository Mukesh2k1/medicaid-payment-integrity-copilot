"""The five-step agent: load, detect, price, check, recommend."""
import json
import os
import time

import pandas as pd

from compare import compare_schedules
from impact import FLAGS, calc_impact, check_config
from loader import load_fee_schedule
from summary import build_stats, recommendations
from ui_format import fmt_int, fmt_money

STEP_NAMES = ["Load schedules", "Detect changes", "Price impact", "Check configuration",
              "Recommend actions"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")


def _find(folder, stem):
    for ext in (".xlsx", ".xls", ".xlsm", ".csv"):
        p = os.path.join(folder, stem + ext)
        if os.path.exists(p):
            return p
    return None


def available_datasets():
    """Every data/* folder with meta.json, both schedules, claims and config. Public first."""
    out = {}
    if not os.path.isdir(DATA_DIR):
        return out
    for key in sorted(os.listdir(DATA_DIR)):
        folder = os.path.join(DATA_DIR, key)
        meta_path = os.path.join(folder, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
        old = os.path.join(folder, meta.get("old_file", "")) if meta.get("old_file") else _find(folder, "fs_old")
        new = os.path.join(folder, meta.get("new_file", "")) if meta.get("new_file") else _find(folder, "fs_new")
        claims = os.path.join(folder, "claims_synthetic.csv")
        config = os.path.join(folder, "config_rates.csv")
        if not all(p and os.path.isfile(p) for p in (old, new, claims, config)):
            continue
        planted = os.path.join(folder, "planted_errors.csv")
        out[key] = {"key": key, "folder": folder, "meta": meta, "old": old, "new": new,
                    "claims": claims, "config": config,
                    "planted": planted if os.path.isfile(planted) else None}
    return dict(sorted(out.items(), key=lambda kv: (not kv[1]["meta"].get("public", False), kv[0])))


def read_keyed_csv(src):
    if hasattr(src, "getvalue"):
        import io
        src = io.BytesIO(src.getvalue())
    df = pd.read_csv(src, dtype={"code": str, "modifier": str, "CODE": str, "MODIFIER": str,
                                 "Code": str, "Modifier": str}, keep_default_na=False)
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "modifier" not in df.columns:
        df["modifier"] = ""
    df["code"] = df["code"].astype(str).astype(object)
    df["modifier"] = df["modifier"].astype(str).astype(object)
    return df


def _as_df(x):
    return x if isinstance(x, pd.DataFrame) else read_keyed_csv(x)


def _layout_kwargs(layout):
    if not layout:
        return {}
    return {k: layout.get(k) for k in ("sheet", "header_row", "code_col", "mod_cols", "rate_col")}


def run_pipeline(old_src, new_src, claims, config, meta, pct=100, old_layout=None,
                 new_layout=None, on_step=None, pause=0.0):
    t0 = time.perf_counter()
    log = []

    def step(i, detail):
        log.append({"step": i + 1, "name": STEP_NAMES[i], "detail": detail})
        if on_step:
            on_step(i, STEP_NAMES[i], detail)
        if pause:
            time.sleep(pause)

    # 1 Load
    old_df, w_old, lay_old = load_fee_schedule(old_src, **_layout_kwargs(old_layout))
    new_df, w_new, lay_new = load_fee_schedule(new_src, **_layout_kwargs(new_layout))
    claims_df = _as_df(claims)
    config_df = _as_df(config)
    old_label = meta.get("old_label", "the old version")
    new_label = meta.get("new_label", "the new version")
    rate_note = (f"rate from '{lay_new['rate_col']}'" if lay_old["rate_col"] == lay_new["rate_col"]
                 else f"rate from '{lay_old['rate_col']}' and '{lay_new['rate_col']}'")
    step(0, f"Read {fmt_int(len(old_df))} lines from {old_label} and {fmt_int(len(new_df))} "
            f"from {new_label}. Mapped columns automatically: {rate_note}.")

    # 2 Detect
    changes, csum = compare_schedules(old_df, new_df)
    step(1, f"{csum['Rate increase']:,} increases, {csum['Rate decrease']:,} decreases, "
            f"{csum['New code']:,} new and {csum['Deleted code']:,} deleted codes across "
            f"{csum['total_rows']:,} code lines.")

    # 3 Price
    impact = calc_impact(changes, claims_df, pct_of_fee_schedule=pct, months_covered=12)
    net = impact["net_impact"]
    word = "higher annual cost" if net >= 0 else "annual savings"
    step(2, f"Net {fmt_money(abs(net))} {word} on {fmt_int(impact['total_units'])} claim units. "
            f"Top 10 codes drive {impact['top10_share_of_increase']:.0f}% of the increase.")

    # 4 Check
    config_res = check_config(new_df, old_df, config_df, claims_df, pct=pct)
    step(3, f"{config_res['issues']:,} configuration issues found, "
            f"{fmt_money(config_res['total_mispaid'])} paid incorrectly.")

    # 5 Recommend
    stats = build_stats(meta, csum, impact, config_res)
    recs = recommendations(stats)
    n_actions = len(recs) - 1
    step(4, f"{n_actions} prioritized action{'s' if n_actions != 1 else ''} and a ready-to-load "
            f"configuration update file.")

    return {"meta": meta, "pct": pct, "old_df": old_df, "new_df": new_df, "changes": changes,
            "change_summary": csum, "impact": impact, "config": config_res, "stats": stats,
            "recommendations": recs, "log": log, "warnings": {"old": w_old, "new": w_new},
            "layouts": {"old": lay_old, "new": lay_new},
            "elapsed": time.perf_counter() - t0}


def run_dataset(key, pct=100, **kw):
    ds = available_datasets()[key]
    res = run_pipeline(ds["old"], ds["new"], ds["claims"], ds["config"], ds["meta"], pct=pct, **kw)
    res["planted"] = read_keyed_csv(ds["planted"]) if ds["planted"] else None
    res["dataset_key"] = key
    return res


def validate_against_planted(result):
    planted = result.get("planted")
    if planted is None or len(planted) == 0:
        return None

    def keyset(df, col):
        return {(str(c).strip().upper(), str(m).strip().upper(), str(f))
                for c, m, f in zip(df["code"], df["modifier"].fillna(""), df[col])}

    expected = keyset(planted, "error_type")
    found_all = keyset(result["config"]["flags_df"], "flag")
    found = expected & found_all
    return {"expected": len(expected), "found": len(found),
            "missed": sorted(expected - found_all), "unexpected": sorted(found_all - expected),
            "flags": FLAGS}
