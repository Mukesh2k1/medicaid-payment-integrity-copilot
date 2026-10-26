"""Generate the demo data.

python make_demo_data.py            synthetic fee schedules + mocks in data/synthetic/
python make_demo_data.py --florida  mocks for the public Florida files in data/florida/
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

from compare import compare_schedules
from impact import FLAGS
from loader import load_fee_schedule

SEED = 42
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SYN_DIR = os.path.join(BASE_DIR, "data", "synthetic")
FL_DIR = os.path.join(BASE_DIR, "data", "florida")

# (start, end, count, median, sigma, prof/tech split, is E&M)
NUMERIC_GROUPS = [
    (100, 1999, 60, 180, 0.5, False, False),
    (10021, 69990, 520, 420, 0.9, False, False),
    (70010, 79999, 140, 110, 0.7, True, False),
    (80047, 89398, 160, 22, 0.7, False, False),
    (90281, 99199, 120, 85, 0.7, False, False),
    (99202, 99499, 40, 95, 0.4, False, True),
]
HCPCS_GROUPS = [("A", 30, 45), ("E", 25, 140), ("G", 25, 60), ("J", 40, 30)]
HCPCS_SIGMA = 0.8


# ----------------------------------------------------------------------------- synthetic schedules
def _old_schedule(rng):
    rows = []
    for start, end, count, median, sigma, split, em in NUMERIC_GROUPS:
        codes = np.sort(rng.choice(np.arange(start, end + 1), size=count, replace=False))
        rates = median * rng.lognormal(0, sigma, size=count)
        for c, r in zip(codes, rates):
            code = f"{c:05d}"
            if split:
                rows.append((code, "", r, em))
                rows.append((code, "26", r * 0.35, em))
                rows.append((code, "TC", r * 0.65, em))
            else:
                rows.append((code, "", r, em))
    for letter, count, median in HCPCS_GROUPS:
        nums = np.sort(rng.choice(np.arange(1, 10000), size=count, replace=False))
        rates = median * rng.lognormal(0, HCPCS_SIGMA, size=count)
        for n, r in zip(nums, rates):
            rows.append((f"{letter}{n:04d}", "", r, False))
    df = pd.DataFrame(rows, columns=["code", "modifier", "rate", "em"])
    df["rate"] = df["rate"].round(2)
    by_report = rng.random(len(df)) < 0.03
    df.loc[by_report, "rate"] = np.nan
    return df


def _new_schedule(old, rng):
    new = old.copy()
    n = len(new)
    priced = new["rate"].notna().to_numpy()
    em = new["em"].to_numpy()
    u = rng.random(n)
    inc = priced & ~em & (u < 0.20)
    dec = priced & ~em & (u >= 0.20) & (u < 0.34)
    big = rng.random(n) < 0.15
    inc_pct = np.where(big, rng.uniform(0.10, 0.25, n), rng.uniform(0.02, 0.08, n))
    dec_pct = rng.uniform(0.02, 0.15, n)
    rate = new["rate"].to_numpy(dtype=float).copy()
    rate = np.where(inc, rate * (1 + inc_pct), rate)
    rate = np.where(dec, rate * (1 - dec_pct), rate)
    rate = np.where(priced & em, rate * 1.062, rate)
    unchanged = priced & ~em & ~inc & ~dec
    to_br = unchanged & (rng.random(n) < 0.015)
    rate = np.where(to_br, np.nan, rate)
    was_br = ~priced
    now_priced = was_br & (rng.random(n) < 0.40)
    rate = np.where(now_priced, rng.uniform(40, 300, n), rate)
    new["rate"] = np.round(rate, 2)
    keep = rng.random(n) >= 0.03
    new = new[keep].copy()

    existing = set(old["code"])
    adds = []
    while len(adds) < 45:
        c = f"{int(rng.integers(10021, 99500)):05d}"
        if c not in existing:
            existing.add(c)
            adds.append((c, "", round(150 * float(rng.lognormal(0, 0.8)), 2), False))
    new = pd.concat([new, pd.DataFrame(adds, columns=new.columns)], ignore_index=True)
    return new.sort_values(["code", "modifier"]).reset_index(drop=True)


def _write_state_style(df, path, title, headers):
    code_h, mod_h, fee_h, fac_h, pa_h = headers
    body = pd.DataFrame({
        code_h: df["code"],
        mod_h: df["modifier"],
        fee_h: [("BR" if pd.isna(r) else float(r)) for r in df["rate"]],
        fac_h: [("BR" if pd.isna(r) else round(float(r) * 0.82, 2)) for r in df["rate"]],
        pa_h: ["Y" if c[0] in ("J", "E") else "N" for c in df["code"]],
    })
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([[title]]).to_excel(xw, sheet_name="Fee Schedule", header=False,
                                         index=False, startrow=0)
        body.to_excel(xw, sheet_name="Fee Schedule", index=False, startrow=2)
        ws = xw.sheets["Fee Schedule"]
        for row in ws.iter_rows(min_row=4, max_col=2):
            for cell in row:
                cell.number_format = "@"
                cell.value = "" if cell.value is None else str(cell.value)


# ----------------------------------------------------------------------------- mocks
def build_mocks(old_df, new_df, out_dir, n_claims=20000, seed=SEED, meta=None):
    """Synthetic claims, mock claims-system rate table with planted errors. Also used for uploads."""
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)
    changes, _ = compare_schedules(old_df, new_df)
    key = lambda d: list(zip(d["code"], d["modifier"]))  # noqa: E731

    inc = changes[changes["change_type"] == "Rate increase"]
    dec = changes[changes["change_type"] == "Rate decrease"]
    same = changes[(changes["change_type"] == "No change") & changes["new_rate"].notna()]
    new_only = changes[(changes["change_type"] == "New code") & changes["new_rate"].notna()]
    old_only = changes[(changes["change_type"] == "Deleted code") & changes["old_rate"].notna()]

    def sample(d, k):
        pairs = key(d)
        if not pairs:
            return []
        idx = rng.choice(len(pairs), size=min(k, len(pairs)), replace=False)
        return [pairs[i] for i in sorted(idx)]

    inc_pairs = sample(inc, 110)
    pool = inc_pairs + sample(dec, 45) + sample(new_only, 15) + sample(same, 130)
    pool = list(dict.fromkeys(pool))
    if not pool:
        pool = key(changes[changes["new_rate"].notna()])[:300]
    weights = 1.0 / np.arange(1, len(pool) + 1) ** 0.8
    rng.shuffle(weights)
    inc_set = set(inc_pairs)
    weights = np.array([w * 2.5 if p in inc_set else w for p, w in zip(pool, weights)])
    weights = weights / weights.sum()

    deleted_pairs = sample(old_only, 12)
    n_deleted = int(round(n_claims * 0.015)) if deleted_pairs else 0
    picks = [pool[i] for i in rng.choice(len(pool), size=n_claims - n_deleted, p=weights)]
    if n_deleted:
        picks += [deleted_pairs[i] for i in rng.integers(0, len(deleted_pairs), n_deleted)]
    order = rng.permutation(len(picks))
    picks = [picks[i] for i in order]

    start = np.datetime64("2026-01-01")
    claims = pd.DataFrame({
        "claim_id": [f"C{i:06d}" for i in range(1, n_claims + 1)],
        "member_id": [f"M{m:05d}" for m in rng.integers(1, 2501, n_claims)],
        "code": [p[0] for p in picks],
        "modifier": [p[1] for p in picks],
        "units": rng.choice([1, 2, 3], size=n_claims, p=[0.72, 0.20, 0.08]),
        "service_date": (start + rng.integers(0, 365, n_claims)).astype(str),
    })
    units = claims.groupby(["code", "modifier"])["units"].sum().to_dict()

    # config: every new pair at its new rate
    newk = new_df[["code", "modifier", "rate"]].copy()
    config = pd.DataFrame({"code": newk["code"], "modifier": newk["modifier"],
                           "system_rate": newk["rate"]})
    planted = []

    def eligible(d, k):
        d = d[[units.get(p, 0) >= 5 for p in key(d)]]
        d = d[(d["new_rate"] - d["old_rate"]).abs() >= 0.01]
        return sample(d, k)

    old_rate = {p: r for p, r in zip(key(changes), changes["old_rate"])}
    for p in eligible(inc, 12) + eligible(dec, 12):
        mask = (config["code"] == p[0]) & (config["modifier"] == p[1])
        config.loc[mask, "system_rate"] = old_rate[p]
        planted.append((p[0], p[1], FLAGS[0]))
    new_with_claims = [p for p in key(new_only) if units.get(p, 0) > 0]
    for p in new_with_claims[:6]:
        config = config[~((config["code"] == p[0]) & (config["modifier"] == p[1]))]
        planted.append((p[0], p[1], FLAGS[1]))
    old_with_claims = [p for p in deleted_pairs if units.get(p, 0) > 0]
    adds = [(p[0], p[1], old_rate[p]) for p in old_with_claims[:6]]
    planted += [(p[0], p[1], FLAGS[2]) for p in old_with_claims[:6]]
    if adds:
        config = pd.concat([config, pd.DataFrame(adds, columns=config.columns)], ignore_index=True)
    config["system_rate"] = config["system_rate"].round(2)

    claims.to_csv(os.path.join(out_dir, "claims_synthetic.csv"), index=False)
    config.to_csv(os.path.join(out_dir, "config_rates.csv"), index=False)
    planted_df = pd.DataFrame(planted, columns=["code", "modifier", "error_type"])
    planted_df.to_csv(os.path.join(out_dir, "planted_errors.csv"), index=False)
    meta = dict(meta or {})
    meta["claims_note"] = f"{n_claims:,} synthetic claim lines"
    with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return {"claims": claims, "config": config, "planted": planted_df, "meta": meta}


# ----------------------------------------------------------------------------- CLI
def make_synthetic():
    rng = np.random.default_rng(SEED)
    os.makedirs(SYN_DIR, exist_ok=True)
    old = _old_schedule(rng)
    new = _new_schedule(old, rng)
    old_path = os.path.join(SYN_DIR, "fs_old.xlsx")
    new_path = os.path.join(SYN_DIR, "fs_new.xlsx")
    _write_state_style(old, old_path, "Synthetic practitioner fee schedule, version A (demo data)",
                       ["Procedure Code", "Modifier", "Non-Facility Fee", "Facility Fee",
                        "Prior Authorization"])
    _write_state_style(new, new_path, "Synthetic practitioner fee schedule, version B (demo data)",
                       ["Proc Code", "Mod", "Max Fee Non-Facility", "Max Fee Facility",
                        "PA Required"])
    old_df, _, _ = load_fee_schedule(old_path)
    new_df, _, _ = load_fee_schedule(new_path)
    meta = {"name": "Synthetic practitioner fee schedule", "old_label": "Version A",
            "new_label": "Version B", "source": "Synthetic demo data", "public": False,
            "old_file": "fs_old.xlsx", "new_file": "fs_new.xlsx"}
    res = build_mocks(old_df, new_df, SYN_DIR, seed=SEED, meta=meta)
    _report(SYN_DIR, old_df, new_df, res)


def make_florida():
    files = {}
    for stem in ("fs_old", "fs_new"):
        for ext in (".xlsx", ".xls", ".xlsm", ".csv"):
            p = os.path.join(FL_DIR, stem + ext)
            if os.path.exists(p):
                files[stem] = p
                break
    if len(files) < 2:
        print("Put fs_old.xls(x) and fs_new.xls(x) in data/florida/ first. "
              "See data/florida/PUT_FLORIDA_FILES_HERE.txt.")
        sys.exit(1)
    old_df, w1, _ = load_fee_schedule(files["fs_old"])
    new_df, w2, _ = load_fee_schedule(files["fs_new"])
    for w in w1 + w2:
        print("Note:", w)
    meta = {"name": "Florida Medicaid Practitioner Fee Schedule", "old_label": "Oct 1, 2025",
            "new_label": "Jan 1, 2026", "source": "Florida AHCA, Rule 59G-4.002 (public)",
            "public": True, "old_file": os.path.basename(files["fs_old"]),
            "new_file": os.path.basename(files["fs_new"])}
    res = build_mocks(old_df, new_df, FL_DIR, seed=SEED, meta=meta)
    _report(FL_DIR, old_df, new_df, res)


def _report(folder, old_df, new_df, res):
    print(f"Wrote data to {folder}")
    print(f"  old schedule lines: {len(old_df):,}   new schedule lines: {len(new_df):,}")
    print(f"  claims: {len(res['claims']):,}   config rows: {len(res['config']):,}")
    print("  planted errors:")
    for t, n in res["planted"]["error_type"].value_counts().items():
        print(f"    {t}: {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--florida", action="store_true", help="build mocks for data/florida files")
    args = ap.parse_args()
    make_florida() if args.florida else make_synthetic()
