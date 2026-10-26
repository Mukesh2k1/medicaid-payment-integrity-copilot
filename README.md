# Medicaid Payment Integrity Copilot

State Medicaid agencies publish new versions of their fee schedules several times a year. Health plans must find what changed, price the cost impact and update their claims system. A missed update means claims are paid at the wrong rate.

This app is an AI agent that compares two versions of a fee schedule, prices the change against claims volume and finds codes the claims system would pay wrongly.

## The five steps

| Step | What the agent does |
|---|---|
| 1. Load schedules | Reads two versions (xls, xlsx, csv) and auto-detects the header row, code, modifier and rate columns |
| 2. Detect changes | Labels every code + modifier: rate increase, rate decrease, new, deleted, became priced, became not priced, no change |
| 3. Price impact | Claim units × rate change × % of fee schedule the plan pays |
| 4. Check configuration | Compares a mock claims-system rate table with the new schedule: outdated rates, codes not configured, retired codes still active |
| 5. Recommend actions | Prioritized action plan, client summary and Word, Excel and CSV exports |

Every number is computed by pandas code. The optional AI (Azure OpenAI) only rewrites the wording of the summary.

## Run it on Windows (PowerShell)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python make_demo_data.py
python test_pipeline.py
python -m streamlit run app.py
```

`python test_pipeline.py` should print **11 of 11 checks passed**. In the app, open the Load tab and click **Run agent**.

## Use the real Florida files

1. Download the Florida Medicaid Practitioner Fee Schedule (Oct 1, 2025 and Jan 1, 2026) from the AHCA Rule 59G-4.002 page.
2. Save them as `data/florida/fs_old.xlsx` (or `.xls`) and `data/florida/fs_new.xlsx`.
3. Run `python make_demo_data.py --florida`. This builds synthetic claims and a mock claims-system table for the Florida codes.
4. Restart the app. The Florida dataset is listed first. To test it, run `python test_pipeline.py florida`.

You can also upload any two schedules under **Analyze your own fee schedule files** on the Load tab and adjust the column mapping there.

## Data guardrails

- Public fee schedules and synthetic claims only. No client data, member data or PHI.
- Member IDs, claim IDs and dates are invented, using a fixed random seed of 42.
- Codes and rates only. No CPT descriptions, which are licensed by the AMA.
- Never commit API keys. `.streamlit/secrets.toml` is in `.gitignore`.

## Optional: Rewrite with AI (Azure OpenAI)

Run `pip install openai`, then create `.streamlit/secrets.toml`:

```toml
AZURE_OPENAI_ENDPOINT = "https://<your-resource>.openai.azure.com/"
AZURE_OPENAI_KEY = "<key>"
AZURE_OPENAI_DEPLOYMENT = "<deployment name>"
```

The **Rewrite with AI** button appears only when these secrets exist. The AI receives only aggregated, pre-formatted figures. If the call fails, the template summary is used instead.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit interface with 5 tabs |
| `ui.py`, `ui_format.py` | Visual components, CSS and number formatting |
| `loader.py` | Reads and cleans fee schedules, detects the layout |
| `compare.py` | Change detection |
| `impact.py` | Dollar impact and configuration check |
| `summary.py` | Stats, recommendations, template and AI summary, Word and Excel export |
| `pipeline.py` | The five-step agent |
| `make_demo_data.py` | Synthetic schedules, claims, config table and planted errors |
| `test_pipeline.py` | 11 acceptance checks |

## How it was built

First version and debugging with Microsoft Copilot; specification and rebuild with Claude (Anthropic); this replica generated with Microsoft Copilot.
