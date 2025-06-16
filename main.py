# ===============================================================
# streamlit_app.py – Truth vs Extract workbook generator
# ===============================================================
# • Upload metadata.json (Truth) and an Export Data CSV (Extract)
# • Click one button, receive an Excel workbook with spacer columns
# • Chooses xlsxwriter if present; otherwise openpyxl automatically
# ---------------------------------------------------------------

import io
import json
import datetime as dt
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import streamlit as st


# ---------------------------------------------------------------------------
# 1️⃣  Field mapping: CSV header  →  metadata.json key-path
# ---------------------------------------------------------------------------
MAPPING: dict[str, tuple[str, ...]] = {
    "Content Type": ("contentType",),
    "Document Type": ("contentType",),
    "Name": ("metaData", "providerName"),
    "Issuing Entity": ("metaData", "issuingAuthority"),
    "Issued Date": ("metaData", "issueDate"),
    "Expiration Date": ("metaData", "expirationDate"),
    "State": ("metaData", "state"),
    "result_id": ("metaData", "resultsDate"),
    # All “sub-category” columns use the same JSON key
    "Education and Training Sub-Category": ("metaData", "subCategory"),
    "Life Support and Misc. Certifications Sub-Category": ("metaData", "subCategory"),
    "Board Certification Sub-Category": ("metaData", "subCategory"),
    "DEA Registration Sub-Category": ("metaData", "subCategory"),
    "Military Service Sub-Category": ("metaData", "subCategory"),
}


# ---------------------------------------------------------------------------
# 2️⃣  Helper functions
# ---------------------------------------------------------------------------
def _normalize(val) -> str:
    """Return a comparable string for numbers / NaNs / dates / None / etc."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return ""
    if isinstance(val, (int, float)):
        try:  # attempt epoch → YYYY-MM-DD
            if val > 1e12:                                   # milliseconds
                ts = dt.datetime.utcfromtimestamp(val / 1000)
            elif val > 1e9:                                  # seconds
                ts = dt.datetime.utcfromtimestamp(val)
            else:
                raise ValueError
            if 1900 <= ts.year <= 2100:
                return ts.strftime("%Y-%m-%d")
        except Exception:
            pass
        return str(int(val)) if isinstance(val, float) else str(val)
    return str(val).strip()


def _is_match(a, b):
    na, nb = _normalize(a), _normalize(b)
    if not (na or nb):
        return ""                                            # both blank → no verdict
    return na.lower() == nb.lower()


def _dig(data: dict, path: tuple[str, ...]):
    cur = data
    for key in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key, "")
    return cur


def _excel_engine() -> str:
    """Pick xlsxwriter if importable, else openpyxl."""
    if importlib.util.find_spec("xlsxwriter"):
        return "xlsxwriter"
    if importlib.util.find_spec("openpyxl"):
        return "openpyxl"
    raise ModuleNotFoundError(
        "Neither 'xlsxwriter' nor 'openpyxl' is installed. "
        "Install one of them in the Python environment running Streamlit."
    )


# ---------------------------------------------------------------------------
# 3️⃣  Core comparison logic
# ---------------------------------------------------------------------------
def build_comparison(extract_csv: pd.DataFrame, truth_json: dict) -> pd.DataFrame:
    truth_items = truth_json.get("testData", truth_json)     # handle either layout
    truth_lookup = {item["fileName"]: item for item in truth_items}

    rows = []
    for _, row in extract_csv.iterrows():
        file_name = row.get("Assets", "")
        truth = truth_lookup.get(file_name, {})

        rec: dict[str, str] = {"File Name": file_name}
        for header, path in MAPPING.items():
            truth_val   = _dig(truth, path)
            extract_val = row.get(header, "")

            rec[f"Truth: {header}"]   = _normalize(truth_val)
            rec[f"Extract: {header}"] = _normalize(extract_val)
            rec[f"{header} Match?"]   = _is_match(truth_val, extract_val)
            rec["  "] = ""            # spacer column (appears blank in Excel)

        rows.append(rec)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 4️⃣  Streamlit UI
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Truth vs Extract Comparer",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.title("📑 Truth vs Extract Comparer")
st.caption("Upload your **metadata.json** and an **Export Data CSV**, then click *Build*. "
           "You’ll get an Excel workbook with side-by-side comparisons.")

json_file = st.file_uploader("**Step 1 – Truth file**: metadata.json", type="json")
csv_file  = st.file_uploader("**Step 2 – Extract file**: Export Data CSV", type="csv")

build_btn = st.button(
    "⚙️ Build comparison workbook",
    disabled=not (json_file and csv_file),
)

st.markdown("---")

if build_btn:
    try:
        truth_json  = json.load(json_file)
        extract_df  = pd.read_csv(csv_file, dtype=str)
        comparison  = build_comparison(extract_df, truth_json)

        # Write Excel to memory
        output_bytes = io.BytesIO()
        engine = _excel_engine()
        with pd.ExcelWriter(output_bytes, engine=engine) as writer:
            comparison.to_excel(writer, index=False, sheet_name="Comparison")
        output_bytes.seek(0)

        st.success("✅ Workbook ready!")
        st.download_button(
            label="📥 Download comparison workbook",
            data=output_bytes,
            file_name="truth_extract_comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # Optional quick preview (first 15 rows)
        st.dataframe(comparison.head(15))

    except Exception as e:
        st.error(f"Something went wrong: {e}")
