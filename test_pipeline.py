"""Acceptance checks. Usage: python test_pipeline.py [dataset_key]"""
import io
import os
import sys
import tempfile

import pandas as pd

from impact import calc_impact
from loader import ScheduleError, VALID_CODE  # noqa: F401
from make_demo_data import build_mocks
from pipeline import available_datasets, run_dataset, run_pipeline, validate_against_planted

RESULTS = []


def check(n, name, fn):
    try:
        fn()
        RESULTS.append(True)
        print(f"PASS {n:>2}  {name}")
    except AssertionError as exc:
        RESULTS.append(False)
        print(f"FAIL {n:>2}  {name}  {exc}")
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print(f"FAIL {n:>2}  {name}  {type(exc).__name__}: {exc}")


def main():
    ds = available_datasets()
    if not ds:
        print("No datasets found. Run python make_demo_data.py first.")
        sys.exit(1)
    key = sys.argv[1] if len(sys.argv) > 1 else ("synthetic" if "synthetic" in ds else next(iter(ds)))
    print(f"Dataset: {key}")
    r = run_dataset(key, pct=100)
    ch, cs, imp, cfg = r["changes"], r["change_summary"], r["impact"], r["config"]

    def t1():
        codes = pd.concat([r["old_df"]["code"], r["new_df"]["code"]])
        share = codes.str.fullmatch(r"[A-Z0-9]{5,7}").mean()
        assert share > 0.99, f"only {share:.1%} look like codes"

    def t2():
        assert cs["in_both"] + cs["New code"] + cs["Deleted code"] == cs["total_rows"]

    def t3():
        assert ch["change_type"].notna().all() and (ch["change_type"] != "").all()

    def t4():
        nc = ch[(ch["change_type"] == "No change") & ch["old_rate"].notna()]
        assert ((nc["new_rate"] - nc["old_rate"]).abs() < 0.005).all()

    def t5():
        diff = abs(imp["total_increase"] + imp["total_decrease"] - imp["net_impact"])
        assert diff <= 0.01, f"off by {diff}"

    def t6():
        half = calc_impact(ch, pd.read_csv(ds[key]["claims"], dtype={"code": str, "modifier": str},
                                           keep_default_na=False), pct_of_fee_schedule=50)
        assert abs(half["net_impact"] * 2 - imp["net_impact"]) <= 0.02

    def t7():
        top = imp["impact_df"].sort_values("impact", ascending=False).iloc[0]
        expected = top["units"] * (top["new_rate"] - top["old_rate"])
        assert abs(expected - top["impact"]) <= 0.01, f"{expected} vs {top['impact']}"

    def t8():
        v = validate_against_planted(r)
        assert v is not None, "no planted errors file"
        assert not v["missed"], f"missed {v['missed']}"
        assert not v["unexpected"], f"unexpected {v['unexpected']}"

    def t9():
        for k in ("net_impact", "total_increase", "total_decrease"):
            assert pd.notna(imp[k]), k
        for k in ("total_mispaid", "total_overpayment", "total_underpayment"):
            assert pd.notna(cfg[k]), k

    def t10():
        def upload(path, name):
            b = io.BytesIO(open(path, "rb").read())
            b.name = name
            return b
        o = upload(ds[key]["old"], "older.xlsx")
        n = upload(ds[key]["new"], "newer.xlsx")
        from loader import load_fee_schedule
        od, _, _ = load_fee_schedule(o)
        nd, _, _ = load_fee_schedule(n)
        mocks = build_mocks(od, nd, tempfile.mkdtemp(prefix="mpic_test_"))
        res = run_pipeline(o, n, mocks["claims"], mocks["config"], mocks["meta"], pct=105)
        res["planted"] = mocks["planted"]
        v = validate_against_planted(res)
        assert v and not v["missed"] and not v["unexpected"]

    def t11():
        b = io.BytesIO(b"hello")
        b.name = "notes.txt"
        try:
            run_pipeline(b, b, pd.DataFrame(), pd.DataFrame(), {})
        except ScheduleError as exc:
            assert "not a fee schedule file" in str(exc), str(exc)
            return
        raise AssertionError("no ScheduleError raised")

    tests = [
        ("codes look like procedure codes", t1),
        ("in_both + new + deleted == total rows", t2),
        ("every line has a change type", t3),
        ("No change lines have identical rates", t4),
        ("increase + decrease == net impact", t5),
        ("50% of fee schedule halves net impact", t6),
        ("top driver impact == units x (new - old)", t7),
        ("config check finds exactly the planted errors", t8),
        ("headline totals are numbers, not NaN", t9),
        ("uploaded file-like objects run end to end", t10),
        (".txt upload raises a friendly ScheduleError", t11),
    ]
    for i, (name, fn) in enumerate(tests, start=1):
        check(i, name, fn)
    passed = sum(RESULTS)
    print(f"\n{passed} of {len(tests)} checks passed")
    sys.exit(0 if passed == len(tests) else 1)


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
