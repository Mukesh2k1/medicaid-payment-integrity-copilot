"""Number formatting helpers. No Streamlit import so they can be tested anywhere."""
import math


def _is_missing(x):
    if x is None:
        return True
    try:
        return math.isnan(float(x))
    except (TypeError, ValueError):
        return True


def fmt_money(x, decimals=0):
    """$1,234 / -$1,234; '–' for None/NaN; never '-$0'."""
    if _is_missing(x):
        return "–"
    v = round(float(x), decimals)
    if v == 0:
        v = 0.0
    text = f"{abs(v):,.{decimals}f}"
    if v < 0 and float(text.replace(",", "")) != 0:
        return "-$" + text
    return "$" + text


def fmt_signed_money(x, decimals=0):
    if _is_missing(x):
        return "–"
    text = fmt_money(x, decimals)
    if round(float(x), decimals) > 0:
        return "+" + text
    return text


def fmt_int(x):
    if _is_missing(x):
        return "–"
    return f"{int(round(float(x))):,}"


def fmt_pct(x):
    if _is_missing(x):
        return "–"
    return f"{float(x):.1f}%"
