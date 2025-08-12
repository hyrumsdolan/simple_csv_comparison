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
    "Issuing Entity": ("metaData", "issuingAuthority|issuingEntity|issuer|issuingAgency|issuingOrganization"),
    "Issued Date": ("metaData", "issueDate|issuedDate|dateIssued|issuedOn"),
    "Expiration Date": ("metaData", "expirationDate|expireDate|expires|expDate"),
    "State": ("metaData", "state|stateCode|issuingState|jurisdiction|stateAbbreviation"),
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
        # Support alternative keys separated by '|'
        if "|" in key:
            found = ""
            for option in key.split("|"):
                if option in cur:
                    found = cur.get(option, "")
                    break
            cur = found
        else:
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
    """
    Build a side-by-side comparison DataFrame from an extract CSV and a truth JSON
    object. The truth JSON is expected to be a list of records, where each record
    contains a "METADATA" key that is a stringified JSON object.
    """
    if isinstance(truth_json, dict):
        truth_items = truth_json.get("testData", [])
    elif isinstance(truth_json, list):
        truth_items = truth_json
    else:
        truth_items = []

    def extract_base_filename(filename):
        """Extract base filename by removing UUID suffix if present."""
        if not filename:
            return ""
        import re
        uuid_pattern = r'-[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}(\.[^.]+)?$'
        base_name = re.sub(uuid_pattern, r'\1', filename)
        return base_name

    def normalize_key(val: str) -> str:
        return str(val).strip().lower() if val is not None else ""

    truth_lookup: dict[str, dict] = {}
    truth_by_name: dict[str, dict] = {}

    for item in truth_items:
        metadata_raw = item.get("METADATA")
        if isinstance(metadata_raw, str):
            # Case 1: METADATA is a stringified JSON object (original behavior)
            try:
                metadata_obj = json.loads(metadata_raw)
            except json.JSONDecodeError:
                continue
        elif isinstance(metadata_raw, dict):
            # Case 2: METADATA is already a dictionary object (your case)
            metadata_obj = metadata_raw
        else:
            # Case 3: METADATA is neither string nor dict, skip
            continue

        original_file_name = metadata_obj.get("fileName") or metadata_obj.get("filename")
        new_file_name = item.get("NEW_FILE_NAME") or metadata_obj.get("newFileName")
        provider_name = metadata_obj.get("providerName")

        if not original_file_name and not new_file_name and not provider_name:
            continue

        truth_record = {
            "fileName": original_file_name or new_file_name or "",
            "contentType": item.get("NAME"),
            "metaData": metadata_obj,
        }

        # Primary match on NEW_FILE_NAME (CSV Assets uses this)
        if new_file_name:
            truth_lookup[new_file_name] = truth_record
            truth_lookup[extract_base_filename(new_file_name)] = truth_record

        # Fallback matches on original names
        if original_file_name:
            truth_lookup[original_file_name] = truth_record
            truth_lookup[extract_base_filename(original_file_name)] = truth_record

        # Provider-name fallback lookup
        if provider_name:
            truth_by_name[normalize_key(provider_name)] = truth_record

    rows = []
    for _, row in extract_csv.iterrows():
        file_name = row.get("Assets", "")
        base_file_name = extract_base_filename(file_name)

        # Try filename-based strategies
        truth = (
            truth_lookup.get(file_name)
            or truth_lookup.get(base_file_name)
        )

        # Fallback: match by provider name when filenames don't line up
        if truth is None:
            csv_name = row.get("Name", "")
            truth = truth_by_name.get(normalize_key(csv_name))

        rec: dict[str, str] = {"File Name": file_name}
        for header, path in MAPPING.items():
            truth_val = _dig(truth, path) if truth else ""
            extract_val = row.get(header, "")

            rec[f"Truth: {header}"] = _normalize(truth_val)
            rec[f"Extract: {header}"] = _normalize(extract_val)
            rec[f"{header} Match?"] = _is_match(truth_val, extract_val)
            rec["  "] = ""

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

        # Diagnostics: show match coverage
        truth_cols = [c for c in comparison.columns if c.startswith("Truth: ")]
        matched_mask = (comparison[truth_cols] != "").any(axis=1) if truth_cols else pd.Series([], dtype=bool)
        num_rows = len(comparison)
        num_matched = int(matched_mask.sum()) if num_rows else 0
        st.write(f"Matched rows: {num_matched} / {num_rows}")

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
