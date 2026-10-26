"""Medicaid Payment Integrity Copilot - Streamlit app."""
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import ui
from compare import CHANGE_TYPES
from impact import FLAGS
from loader import ScheduleError, detect_layout, list_columns, sheet_names
from make_demo_data import build_mocks
from pipeline import (STEP_NAMES, available_datasets, read_keyed_csv, run_dataset, run_pipeline,
                      validate_against_planted)
from summary import build_summary, export_docx, export_excel, md_safe
from ui_format import fmt_int, fmt_money, fmt_pct, fmt_signed_money

st.set_page_config(page_title="Medicaid Payment Integrity Copilot", page_icon="🩺", layout="wide")
ui.inject_css()

R = st.session_state.get("results")
ui.header(R["meta"] if R else None)


# ----------------------------------------------------------------------------- helpers
def text_col(label):
    try:
        return st.column_config.TextColumn(label, alignment="right")
    except TypeError:  # older Streamlit without alignment
        return st.column_config.TextColumn(label)


def label_of(code, mod):
    return f"{code}-{mod}" if mod else str(code)


def azure_secrets():
    try:
        s = st.secrets
        keys = ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT")
        if all(k in s for k in keys):
            return {k: s[k] for k in keys}
    except Exception:  # noqa: BLE001
        pass
    return None


def execute(runner, pct):
    with st.status("Agent running", expanded=True) as status:
        def on_step(i, name, detail):
            st.write(md_safe(f"**Step {i + 1} of {len(STEP_NAMES)}, {name}.** {detail}"))
        try:
            res = runner(on_step)
        except ScheduleError as exc:
            status.update(label="Agent stopped: a file could not be read", state="error")
            st.error(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            status.update(label="Agent stopped", state="error")
            st.error("Something went wrong while running the agent. Check the files and the "
                     f"column mapping, then try again. ({type(exc).__name__}: {exc})")
            return
        text, source, err = build_summary(res["stats"], use_ai=False)
        res["summary_text"], res["summary_source"] = text, source
        res["validation"] = validate_against_planted(res)
        res["pct"] = pct
        status.update(label=f"Agent finished in {res['elapsed']:.1f} seconds", state="complete")
    st.session_state["results"] = res
    st.rerun()


# ----------------------------------------------------------------------------- sidebar
st.sidebar.markdown("### Agent workflow")
ui.sidebar_steps(R is not None)
if R:
    st.sidebar.markdown("### Data in this run")
    m = R["meta"]
    ui.sidebar_meta([m.get("name", ""), f"{m.get('old_label', '')} compared with "
                     f"{m.get('new_label', '')}", m.get("source", ""), m.get("claims_note", ""),
                     f"Plan pays {R['pct']:g}% of the fee schedule"])
st.sidebar.markdown("### About")
st.sidebar.caption("Uses public fee schedules and synthetic claims only. No client data, member "
                   "data or CPT descriptions. Every figure is computed by code; AI only rewrites "
                   "summary wording.")

tabs = st.tabs(["1  Load", "2  Changes", "3  Impact", "4  Config check", "5  Summary"])

# ============================================================================= 1 LOAD
with tabs[0]:
    left, right = st.columns([3, 2], gap="large")
    with right:
        with st.container(border=True):
            st.markdown("### Assumptions")
            pct = st.slider("Plan pays this % of the fee schedule", 50, 150, 100, 5,
                            help="Managed care plans often pay a percentage of the state "
                                 "schedule, such as 105%.")
            st.caption("Claims volume covers 12 months of synthetic claim lines.")
            if R and float(pct) != float(R["pct"]):
                st.warning(f"The last run used {R['pct']:g}%. Run the agent again to apply {pct}%.")
    with left:
        st.markdown("### Run the agent")
        st.write("The agent loads two fee schedule versions, detects every code-level change, "
                 "prices it against claims, checks the claims-system rates and recommends fixes.")
        datasets = available_datasets()
        if not datasets:
            st.error("No demo data found. Run `python make_demo_data.py` first.")
        else:
            keys = list(datasets)
            labels = {k: datasets[k]["meta"].get("name", k) for k in keys}
            if len(keys) == 1:
                choice = keys[0]
                st.caption(f"Dataset: {labels[choice]}")
            else:
                choice = st.radio("Dataset", keys, format_func=lambda k: labels[k])
            if st.button("Run agent", type="primary", icon=":material/play_arrow:"):
                execute(lambda cb: run_dataset(choice, pct=pct, on_step=cb, pause=0.45), pct)

        with st.expander("Analyze your own fee schedule files"):
            c1, c2 = st.columns(2)
            up_old = c1.file_uploader("Older fee schedule", type=["xls", "xlsx", "csv"],
                                      key="up_old")
            up_new = c2.file_uploader("Newer fee schedule", type=["xls", "xlsx", "csv"],
                                      key="up_new")
            c3, c4 = st.columns(2)
            up_claims = c3.file_uploader("Claims CSV (optional)", type=["csv"], key="up_claims")
            up_cfg = c4.file_uploader("Claims-system rates CSV (optional)", type=["csv"],
                                      key="up_cfg")
            layouts, ok = {}, True
            for tag, up in (("old", up_old), ("new", up_new)):
                if up is None:
                    continue
                st.markdown(f"**Column mapping: {up.name}**")
                try:
                    auto = detect_layout(up)
                    sheets = sheet_names(up)
                    k = f"{tag}_{up.name}"
                    a, b = st.columns(2)
                    sheet = a.selectbox("Sheet", sheets, index=sheets.index(auto["sheet"])
                                        if auto["sheet"] in sheets else 0, key=k + "_sheet")
                    hdr = b.number_input("Header row (Excel numbering)", 1, 10000,
                                         auto["header_row"] + 1, key=k + "_hdr")
                    cols = list_columns(up, sheet, int(hdr) - 1)
                    if not cols:
                        st.error("That header row is past the end of the sheet. Pick a smaller "
                                 "row number.")
                        ok = False
                        continue

                    def idx(name, cols=cols):
                        return cols.index(name) if name in cols else 0
                    d, e, f = st.columns(3)
                    code_col = d.selectbox("Code column", cols, index=idx(auto["code_col"]),
                                           key=k + "_code")
                    mods = e.multiselect("Modifier column(s)", cols,
                                         default=[c for c in auto["mod_cols"] if c in cols],
                                         key=k + "_mod")
                    rate_col = f.selectbox("Rate column", cols, index=idx(auto["rate_col"]),
                                           key=k + "_rate")
                    layouts[tag] = {"sheet": sheet, "header_row": int(hdr) - 1,
                                    "code_col": code_col, "mod_cols": mods, "rate_col": rate_col}
                except ScheduleError as exc:
                    st.error(str(exc))
                    ok = False
            if st.button("Run agent on these files", icon=":material/play_arrow:",
                         disabled=not (up_old and up_new and ok)):
                def run_uploads(cb):
                    meta = {"name": "Uploaded fee schedules", "old_label": up_old.name,
                            "new_label": up_new.name, "source": "User upload", "public": False}
                    planted = None
                    from loader import load_fee_schedule
                    if up_claims is None or up_cfg is None:
                        o, _, _ = load_fee_schedule(up_old, **layouts["old"])
                        n, _, _ = load_fee_schedule(up_new, **layouts["new"])
                        mocks = build_mocks(o, n, tempfile.mkdtemp(prefix="mpic_"), meta=meta)
                        claims = mocks["claims"] if up_claims is None else read_keyed_csv(up_claims)
                        cfg = mocks["config"] if up_cfg is None else read_keyed_csv(up_cfg)
                        meta = mocks["meta"]
                        if up_claims is None and up_cfg is None:
                            planted = mocks["planted"]
                    else:
                        claims, cfg = read_keyed_csv(up_claims), read_keyed_csv(up_cfg)
                        meta["claims_note"] = "uploaded claim lines"
                    res = run_pipeline(up_old, up_new, claims, cfg, meta, pct=pct,
                                       old_layout=layouts["old"], new_layout=layouts["new"],
                                       on_step=cb, pause=0.45)
                    res["planted"] = planted
                    return res
                execute(run_uploads, pct)

    if R:
        st.markdown("### What the agent did")
        ui.agent_log(R["log"], R["elapsed"])
        for side, ws in R["warnings"].items():
            for w in ws:
                st.caption(f"{'Older' if side == 'old' else 'Newer'} file: {w}")
        st.caption(f"Data: {R['meta'].get('source', '')}; {R['meta'].get('claims_note', '')}; "
                   f"plan pays {R['pct']:g}% of the fee schedule.")
        cs, imp, cfg = R["change_summary"], R["impact"], R["config"]
        n_changes = cs["total_rows"] - cs["No change"]
        ui.kpis([
            {"label": "Code lines compared", "value": fmt_int(cs["total_rows"]),
             "note": f"{fmt_int(cs['in_both'])} in both versions", "tone": "neutral"},
            {"label": "Rate changes", "value": fmt_int(n_changes),
             "note": "lines with a change", "tone": "accent"},
            {"label": "Net annual impact", "value": fmt_signed_money(imp["net_impact"]),
             "note": "higher cost" if imp["net_impact"] >= 0 else "savings",
             "tone": "cost" if imp["net_impact"] >= 0 else "saving"},
            {"label": "Paid incorrectly", "value": fmt_money(cfg["total_mispaid"]),
             "note": f"{cfg['issues']} configuration issues", "tone": "cost"},
        ])
        st.success("Results are ready. Open tabs 2 to 5 for changes, impact, the configuration "
                   "check and the client summary.")

# ============================================================================= 2 CHANGES
with tabs[1]:
    if not R:
        ui.empty_state()
    else:
        cs = R["change_summary"]
        ui.kpis([
            {"label": "Rate increases", "value": fmt_int(cs["Rate increase"]),
             "note": "higher cost", "tone": "cost"},
            {"label": "Rate decreases", "value": fmt_int(cs["Rate decrease"]),
             "note": "lower cost", "tone": "saving"},
            {"label": "New codes", "value": fmt_int(cs["New code"]),
             "note": "need configuration", "tone": "accent"},
            {"label": "Deleted codes", "value": fmt_int(cs["Deleted code"]),
             "note": "deny if still billed", "tone": "muted"},
            {"label": "Became not priced", "value": fmt_int(cs["Became not priced"]),
             "note": "need manual pricing", "tone": "caution"},
            {"label": "Became priced", "value": fmt_int(cs["Became priced"]),
             "note": "can now auto-price", "tone": "neutral"},
        ])
        changes = R["changes"]
        g1, g2 = st.columns(2, gap="large")
        with g1:
            types = [t for t in CHANGE_TYPES if t != "No change"]
            vals = sorted([(t, cs[t]) for t in types], key=lambda x: x[1])
            fig = go.Figure(go.Bar(
                x=[v for _, v in vals], y=[t for t, _ in vals], orientation="h",
                marker_color=[ui.CHANGE_COLORS[t] for t, _ in vals],
                text=[f"{v:,}" for _, v in vals], textposition="outside", cliponaxis=False,
                hovertemplate="%{y}: %{x:,}<extra></extra>"))
            fig.update_xaxes(range=[0, max(v for _, v in vals) * 1.18 + 1], showticklabels=False)
            st.plotly_chart(ui.style_fig(fig, 340, "What changed"), width="stretch")
            st.caption(f"{fmt_int(cs['No change'])} code lines kept the same rate.")
        with g2:
            fig = go.Figure()
            for t, col in (("Rate increase", ui.RED), ("Rate decrease", ui.GREEN)):
                p = changes.loc[changes["change_type"] == t, "change_pct"].dropna().clip(-30, 30)
                fig.add_trace(go.Histogram(x=p, name=t, marker_color=col, opacity=0.75,
                                           xbins=dict(start=-30, end=30.0001, size=1),
                                           hovertemplate="%{x}%: %{y} codes<extra></extra>"))
            fig.update_layout(barmode="overlay")
            fig.update_xaxes(ticksuffix="%", range=[-31, 31])
            st.plotly_chart(ui.style_fig(fig, 340, "Size of each rate change, % of old rate"),
                            width="stretch")
            st.caption("Red bars are increases and green bars are decreases, in 1% bins. "
                       "Changes beyond ±30% are grouped at the edges.")

        st.markdown("### Code-level changes")
        units = R["impact"]["impact_df"][["code", "modifier", "units"]]
        view = changes.merge(units, on=["code", "modifier"], how="left")
        f1, f2, f3 = st.columns([3, 2, 2])
        sel = f1.multiselect("Change type", CHANGE_TYPES,
                             default=[t for t in CHANGE_TYPES if t != "No change"])
        q = f2.text_input("Search code", placeholder="e.g. 99213")
        only = f3.toggle("Only codes with claims", value=False)
        v = view[view["change_type"].isin(sel)]
        if q.strip():
            v = v[v["code"].str.contains(q.strip().upper(), regex=False)]
        if only:
            v = v[v["units"] > 0]
        st.caption(f"Showing {len(v):,} of {len(view):,} code lines. Codes and rates only; "
                   "no CPT descriptions.")
        st.dataframe(
            v.rename(columns={"code": "Code", "modifier": "Modifier", "change_type": "Change",
                              "old_rate": "Old rate", "new_rate": "New rate",
                              "change_amt": "Change $", "change_pct": "Change %",
                              "units": "Claim units"}),
            hide_index=True, width="stretch", height=420,
            column_config={
                "Old rate": st.column_config.NumberColumn(format="dollar"),
                "New rate": st.column_config.NumberColumn(format="dollar"),
                "Change $": st.column_config.NumberColumn(format="dollar"),
                "Change %": st.column_config.NumberColumn(format="%.1f%%"),
                "Claim units": st.column_config.NumberColumn(format="localized"),
            })
        st.download_button("Download changes (CSV)", v.to_csv(index=False).encode(),
                           "fee_schedule_changes.csv", "text/csv", icon=":material/download:")

# ============================================================================= 3 IMPACT
with tabs[2]:
    if not R:
        ui.empty_state()
    else:
        imp = R["impact"]
        net = imp["net_impact"]
        ui.kpis([
            {"label": "Net annual impact", "value": fmt_signed_money(net),
             "note": "higher cost" if net >= 0 else "savings", "tone": "cost" if net >= 0 else "saving"},
            {"label": "Rate increases add", "value": fmt_money(imp["total_increase"]),
             "note": "higher cost", "tone": "cost"},
            {"label": "Rate decreases save", "value": fmt_money(abs(imp["total_decrease"])),
             "note": "lower cost", "tone": "saving"},
            {"label": "Claim units priced", "value": fmt_int(imp["total_units"]),
             "note": f"{fmt_int(imp['codes_with_volume'])} codes with volume", "tone": "neutral"},
        ])
        idf = imp["impact_df"]
        pos = idf[idf["impact"] > 0].sort_values("impact", ascending=False)
        if len(pos):
            t = pos.iloc[0]
            ui.insight(
                f"The top 10 codes drive <b>{fmt_pct(imp['top10_share_of_increase'])}</b> of the "
                f"cost increase. The largest cost driver is <b>{ui.esc(label_of(t.code, t.modifier))}"
                f"</b>: its rate rose from {fmt_money(t.old_rate, 2)} to {fmt_money(t.new_rate, 2)} "
                f"on {fmt_int(t.units)} units, adding {fmt_money(t.impact)} a year.")
        c1, c2 = st.columns([3, 2], gap="large")
        with c1:
            top = imp["top_10"].iloc[::-1]
            labels = [label_of(c, m) for c, m in zip(top["code"], top["modifier"])]
            span = float(top["impact"].abs().max()) if len(top) else 1.0
            lo = min(0.0, float(top["impact"].min()) if len(top) else 0.0)
            hi = max(0.0, float(top["impact"].max()) if len(top) else 0.0)
            fig = go.Figure(go.Bar(
                x=top["impact"], y=labels, orientation="h",
                marker_color=[ui.RED if x > 0 else ui.GREEN for x in top["impact"]],
                text=[fmt_signed_money(x) for x in top["impact"]], textposition="outside",
                cliponaxis=False,
                customdata=np.stack([top["old_rate"], top["new_rate"], top["units"]], axis=-1)
                if len(top) else None,
                hovertemplate="%{y}<br>Old rate $%{customdata[0]:,.2f}<br>New rate "
                              "$%{customdata[1]:,.2f}<br>Units %{customdata[2]:,.0f}<br>"
                              "Impact $%{x:,.0f}<extra></extra>"))
            fig.update_xaxes(range=[lo - span * 0.28 if lo < 0 else 0,
                                    hi + span * 0.28 if hi > 0 else 0],
                             tickprefix="$", tickformat=",.0f")
            fig.update_yaxes(type="category")
            st.plotly_chart(ui.style_fig(fig, 400, "Top 10 codes by annual dollar impact"),
                            width="stretch")
        with c2:
            fig = go.Figure(go.Waterfall(
                x=["Increases", "Decreases", "Net"], measure=["relative", "relative", "total"],
                y=[imp["total_increase"], imp["total_decrease"], 0],
                text=[fmt_signed_money(imp["total_increase"]),
                      fmt_signed_money(imp["total_decrease"]), fmt_signed_money(net)],
                textposition="outside", cliponaxis=False,
                increasing=dict(marker=dict(color=ui.RED)),
                decreasing=dict(marker=dict(color=ui.GREEN)),
                totals=dict(marker=dict(color=ui.INK)), connector=dict(line=dict(color=ui.LINE))))
            top_y = max(imp["total_increase"], 1.0) * 1.2
            low_y = min(0.0, net, imp["total_increase"] + imp["total_decrease"]) * 1.2
            fig.update_yaxes(tickprefix="$", tickformat=",.0f", range=[low_y, top_y])
            st.plotly_chart(ui.style_fig(fig, 400, "How the net impact builds up"),
                            width="stretch")
        st.markdown("### Impact by change type")
        bt = imp["impact_by_type"].rename(columns={"change_type": "Change", "codes": "Code lines",
                                                   "units": "Claim units", "impact": "Impact"})
        st.dataframe(bt, hide_index=True, width="stretch", column_config={
            "Code lines": st.column_config.NumberColumn(format="localized"),
            "Claim units": st.column_config.NumberColumn(format="localized"),
            "Impact": st.column_config.NumberColumn(format="dollar")})
        if imp["deleted_code_volume"] > 0:
            st.warning(f"{fmt_int(imp['deleted_code_volume'])} claim units were billed on deleted "
                       "codes. Will deny – configuration needed.")
        if imp["not_priced_volume"] > 0:
            st.info(f"{fmt_int(imp['not_priced_volume'])} claim units are on codes that became not "
                    "priced and will need manual pricing.")
        st.caption(md_safe(f"Impact = claim units × (new rate − old rate) × {R['pct']:g}% of the "
                           "fee schedule. Claim units cover 12 months."))

# ============================================================================= 4 CONFIG
with tabs[3]:
    if not R:
        ui.empty_state()
    else:
        cfg = R["config"]
        cpf = cfg["count_per_flag"]
        val = R.get("validation")
        verify = None
        if val:
            verify = (f"Validated: the agent found {val['found']} of {val['expected']} planted "
                      f"errors with {len(val['unexpected'])} false flags.")
        ui.alarm(fmt_money(cfg["total_mispaid"]),
                 f"paid incorrectly on {ui.esc(R['meta'].get('claims_note', 'the claims'))} "
                 f"because the claims system still holds old or invalid rates. "
                 f"<b>{cfg['issues']} codes</b> need a configuration change before the new "
                 f"schedule takes effect.", verify)
        ui.kpis([
            {"label": "Outdated rates", "value": fmt_int(cpf[FLAGS[0]]),
             "note": "system holds the old rate", "tone": "cost"},
            {"label": "Overpayment", "value": fmt_money(cfg["total_overpayment"]),
             "note": "paid above the new rate", "tone": "cost"},
            {"label": "Underpayment", "value": fmt_money(abs(cfg["total_underpayment"])),
             "note": "paid below the new rate", "tone": "caution"},
            {"label": "Retired codes still active", "value": fmt_int(cpf[FLAGS[2]]),
             "note": f"{fmt_money(cfg['invalid_code_payments'])} paid on invalid codes",
             "tone": "cost"},
            {"label": "New codes not configured", "value": fmt_int(cpf[FLAGS[1]]),
             "note": f"{fmt_int(cfg['units_not_configured'])} units will pend or deny",
             "tone": "caution"},
        ])
        st.markdown("### Codes the claims system would pay wrongly")
        fd = cfg["flags_df"]
        show = pd.DataFrame({
            "Code": fd["code"], "Modifier": fd["modifier"], "Flag": fd["flag"],
            "System rate": [fmt_money(x, 2) for x in fd["system_rate"]],
            "Correct rate": [fmt_money(x, 2) for x in fd["new_rate"]],
            "Claim units": [fmt_int(x) for x in fd["units"]],
            "Payment error": [fmt_signed_money(x) for x in fd["payment_error"]],
            "Direction": fd["direction"],
        })
        shade = {"Overpayment": "#FBEDEC", "Paid on invalid code": "#FBEDEC",
                 "Underpayment": "#FDF5E6", "Claims will pend or deny": "#F3F5F7"}

        def row_style(r):
            return [f"background-color: {shade.get(r['Direction'], '#FFFFFF')}"] * len(r)
        st.dataframe(show.style.apply(row_style, axis=1), hide_index=True, width="stretch",
                     height=min(460, 38 + 35 * len(show)),
                     column_config={c: text_col(c) for c in
                                    ("System rate", "Correct rate", "Claim units", "Payment error")})
        st.caption(md_safe("Payment error = claim units × (system rate − correct rate) × % of fee "
                           "schedule. Retired codes: units × system rate. Not configured codes "
                           "show units affected instead of dollars."))
        st.markdown("### Recommended configuration update")
        upd = cfg["config_update_df"]
        upd_show = pd.DataFrame({
            "Code": upd["code"], "Modifier": upd["modifier"], "Action": upd["action"],
            "Current system rate": [fmt_money(x, 2) for x in upd["current_system_rate"]],
            "Correct rate": [fmt_money(x, 2) for x in upd["correct_rate"]],
            "Claim units affected": [fmt_int(x) for x in upd["claim_units_affected"]],
            "Payment error": [fmt_signed_money(x) for x in upd["payment_error"]],
        })
        st.dataframe(upd_show, hide_index=True, width="stretch",
                     column_config={c: text_col(c) for c in
                                    ("Current system rate", "Correct rate", "Claim units affected",
                                     "Payment error")})
        st.download_button("Download config_update.csv", upd.to_csv(index=False).encode(),
                           "config_update.csv", "text/csv", type="primary",
                           icon=":material/download:")

# ============================================================================= 5 SUMMARY
with tabs[4]:
    if not R:
        ui.empty_state()
    else:
        l, r = st.columns([3, 2], gap="large")
        with l:
            st.markdown("### Client summary")
            with st.container(border=True):
                st.markdown(md_safe(R["summary_text"]))
            secrets = azure_secrets()
            if secrets and st.button("Rewrite with AI", icon=":material/auto_awesome:"):
                with st.spinner("Rewriting the wording; figures stay the same"):
                    text, source, err = build_summary(R["stats"], use_ai=True, secrets=secrets)
                R["summary_text"], R["summary_source"] = text, source
                if err:
                    st.toast("AI rewrite failed, so the template summary is shown.")
                st.rerun()
            st.caption(f"Written by: {R['summary_source']}. Every figure comes from the computed "
                       "results.")
        with r:
            st.markdown("### Action plan")
            ui.recs_panel(R["recommendations"])
            st.markdown("### Downloads")
            m = R["meta"]
            subtitle = (f"{m.get('name', '')}: {m.get('old_label', '')} compared with "
                        f"{m.get('new_label', '')}. {m.get('claims_note', '')}.")
            st.download_button(
                "Client summary (Word)",
                export_docx(R["summary_text"], "Medicaid fee schedule update: impact and "
                            "configuration review", subtitle),
                "fee_schedule_summary.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                icon=":material/description:", width="stretch")
            imp = R["impact"]
            top_x = imp["top_10"][["code", "modifier", "change_type", "old_rate", "new_rate",
                                   "change_amt", "units", "impact"]]
            st.download_button(
                "Full results (Excel)",
                export_excel({"Changes": R["changes"], "Top 10 impact": top_x,
                              "Config flags": R["config"]["flags_df"],
                              "Config update": R["config"]["config_update_df"]}),
                "fee_schedule_results.xlsx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/table_view:", width="stretch")
            st.download_button(
                "Configuration update (CSV)",
                R["config"]["config_update_df"].to_csv(index=False).encode(),
                "config_update.csv", "text/csv", icon=":material/download:", width="stretch")
