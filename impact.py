"""Price the rate changes against claims volume and check the claims-system configuration."""
import numpy as np
import pandas as pd

FLAGS = ["Outdated rate in system", "Not configured", "Retired code still active"]
ACTIONS = ["Update rate", "Add code", "Retire code"]
KEY = ["code", "modifier"]


def normalize_keys(df):
    df = df.copy()
    if "modifier" not in df.columns:
        df["modifier"] = ""
    df["code"] = df["code"].fillna("").astype(str).str.strip().str.upper().astype(object)
    mod = df["modifier"].astype(object).where(df["modifier"].notna(), "")
    mod = mod.astype(str).str.strip().str.upper()
    mod = mod.where(~mod.isin(["NAN", "NONE"]), "")
    df["modifier"] = mod.astype(object)
    return df


def claim_units(claims):
    c = normalize_keys(claims)
    c["units"] = pd.to_numeric(c.get("units", 0), errors="coerce").fillna(0).astype(float)
    return c.groupby(KEY, as_index=False)["units"].sum()


def calc_impact(changes, claims, pct_of_fee_schedule=100, months_covered=12):
    pct = float(pct_of_fee_schedule)
    df = normalize_keys(changes)
    units = claim_units(claims)
    df = df.merge(units, on=KEY, how="left")
    df["units"] = df["units"].fillna(0.0).astype(float)
    annualized = bool(months_covered and months_covered < 12)
    if annualized:
        df["units"] = df["units"] * 12.0 / float(months_covered)
    df["impact"] = df["units"] * df["change_amt"].fillna(0.0) * pct / 100.0

    imp = df["impact"]
    total_increase = float(imp[imp > 0].sum())
    total_decrease = float(imp[imp < 0].sum())
    net = float(imp.sum())

    nz = df[df["impact"] != 0].copy()
    nz["_abs"] = nz["impact"].abs()
    top_10 = nz.sort_values("_abs", ascending=False, kind="mergesort").head(10).drop(columns="_abs")
    top_pos = float(top_10.loc[top_10["impact"] > 0, "impact"].sum())
    top_share = round(top_pos / total_increase * 100, 1) if total_increase > 0 else 0.0

    by_type = (df.groupby("change_type", as_index=False)
               .agg(codes=("code", "size"), units=("units", "sum"), impact=("impact", "sum")))
    by_type["_abs"] = by_type["impact"].abs()
    by_type = by_type.sort_values(["_abs", "codes"], ascending=False).drop(columns="_abs")
    by_type["impact"] = by_type["impact"].round(2)
    by_type = by_type.reset_index(drop=True)

    def vol(t):
        return float(df.loc[df["change_type"] == t, "units"].sum())

    impact_df = df.copy()
    impact_df["impact"] = impact_df["impact"].round(2)
    top_10 = top_10.copy()
    top_10["impact"] = top_10["impact"].round(2)

    return {
        "impact_df": impact_df,
        "net_impact": round(net, 2),
        "total_increase": round(total_increase, 2),
        "total_decrease": round(total_decrease, 2),
        "top_10": top_10.reset_index(drop=True),
        "top10_share_of_increase": top_share,
        "impact_by_type": by_type,
        "codes_with_volume": int((df["units"] > 0).sum()),
        "total_units": float(df["units"].sum()),
        "deleted_code_volume": vol("Deleted code"),
        "new_code_volume": vol("New code"),
        "not_priced_volume": vol("Became not priced"),
        "pct_of_fee_schedule": pct,
        "months_covered": months_covered,
        "annualized": annualized,
    }


def check_config(new_df, old_df, config_df, claims_df, pct=100):
    pct = float(pct)
    new = normalize_keys(new_df)[KEY + ["rate"]].rename(columns={"rate": "new_rate"})
    old_keys = normalize_keys(old_df)[KEY].drop_duplicates()
    cfg = normalize_keys(config_df)
    cfg["system_rate"] = pd.to_numeric(cfg["system_rate"].astype(object).replace("", np.nan),
                                       errors="coerce").astype(float)
    cfg = cfg.drop_duplicates(KEY, keep="first")[KEY + ["system_rate"]]

    m = new.merge(cfg, on=KEY, how="outer", indicator=True)
    old_keys["_in_old"] = True
    m = m.merge(old_keys, on=KEY, how="left")
    m["_in_old"] = m["_in_old"].astype(object).eq(True)
    side = m["_merge"].astype(str)

    outdated = (side.eq("both") & m["system_rate"].notna() & m["new_rate"].notna()
                & ((m["system_rate"] - m["new_rate"]).abs() >= 0.01))
    not_cfg = side.eq("left_only")
    retired = side.eq("right_only") & m["_in_old"]
    m["flag"] = np.select([outdated, not_cfg, retired], FLAGS, default="").astype(object)
    f = m[m["flag"] != ""].copy()

    f = f.merge(claim_units(claims_df), on=KEY, how="left")
    f["units"] = f["units"].fillna(0.0).astype(float)
    err = np.where(
        f["flag"].eq(FLAGS[0]), f["units"] * (f["system_rate"] - f["new_rate"]) * pct / 100,
        np.where(f["flag"].eq(FLAGS[2]), f["units"] * f["system_rate"] * pct / 100, np.nan))
    f["payment_error"] = pd.Series(err, index=f.index).astype(float)
    f["direction"] = np.select(
        [f["flag"].eq(FLAGS[1]), f["flag"].eq(FLAGS[2]), f["payment_error"] > 0],
        ["Claims will pend or deny", "Paid on invalid code", "Overpayment"],
        default="Underpayment").astype(object)
    f["_abs"] = f["payment_error"].abs()
    f = f.sort_values(["_abs", "units"], ascending=[False, False], na_position="last",
                      kind="mergesort")
    flags_df = f[["code", "modifier", "flag", "system_rate", "new_rate", "units",
                  "payment_error", "direction"]].reset_index(drop=True)

    action_map = dict(zip(FLAGS, ACTIONS))
    upd = pd.DataFrame({
        "code": flags_df["code"],
        "modifier": flags_df["modifier"],
        "current_system_rate": flags_df["system_rate"],
        "correct_rate": np.where(flags_df["flag"].eq(FLAGS[2]), np.nan, flags_df["new_rate"]),
        "action": flags_df["flag"].map(action_map),
        "claim_units_affected": flags_df["units"],
        "payment_error": flags_df["payment_error"].round(2),
    })
    order = {a: i for i, a in enumerate(ACTIONS)}
    upd["_o"] = upd["action"].map(order)
    upd = upd.sort_values("_o", kind="mergesort").drop(columns="_o").reset_index(drop=True)

    pe = flags_df["payment_error"]
    is_out = flags_df["flag"].eq(FLAGS[0])
    over = float(pe[is_out & (pe > 0)].sum())
    under = float(pe[is_out & (pe < 0)].sum())
    invalid = float(pe[flags_df["flag"].eq(FLAGS[2])].fillna(0).sum())
    counts = flags_df["flag"].value_counts()
    flags_df["payment_error"] = flags_df["payment_error"].round(2)
    return {
        "flags_df": flags_df,
        "config_update_df": upd,
        "count_per_flag": {k: int(counts.get(k, 0)) for k in FLAGS},
        "issues": int(len(flags_df)),
        "total_overpayment": round(over, 2),
        "total_underpayment": round(under, 2),
        "invalid_code_payments": round(invalid, 2),
        "total_mispaid": round(over + abs(under) + invalid, 2),
        "units_not_configured": float(flags_df.loc[flags_df["flag"].eq(FLAGS[1]), "units"].sum()),
    }
