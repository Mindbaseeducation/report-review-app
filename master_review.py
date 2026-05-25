"""
Complete, self-contained MPR Review System system in ONE file.

Inputs : Main file + Master Data file (.xlsx)
Output : One downloadable Excel  ->  "<Date> <N> cases.xlsx"

Pipeline (all in-process, no browser, no external apps):
  1. Master Academic Status lookup (Student ADEK Application ID; not found -> N/A)
  2. Standardize "Details of Pathway Alteration" (-, NA, n/a, null, blank -> N/A)
  3. Fill remaining blanks -> N/A
  4. Build "Student notes" = CONCAT of the 4 note columns
  5. Clean Data: list-like fields  ['a','b'] -> "a, b"   (from updated_excel.py)
  6. Batch split (50 for 100-200, 80 for >200) -- artifact/logging only
  7. Report Review: OpenAI gpt-5-mini, 10-rule prompt, parallel (from review.py)
  8. Merge -> reviewed dataset
  9. Grammar Check: Sensitive Word Flag + Incorrect Student Name Flag (from app.py)
 10. "Student Notes Character Count < 750" flag + final rename

SETUP
    pip install streamlit pandas openpyxl openai

"""

from __future__ import annotations

import ast
import concurrent.futures as cf
import datetime as dt
import io
import os
import re

import openai
import pandas as pd
import streamlit as st

# ===========================================================================
# CONSTANTS
# ===========================================================================
NOTE_COLUMNS = [
    "Personal Well-Being Note",
    "Academic Performance Note",
    "Personal Development Note",
    "Other topics discussed",
]
MASTER_STATUS_COL = "Master Academic Status"
STUDENT_NOTES_COL = "Student notes"
PATHWAY_COL = "Details of Pathway Alteration"
CHARCOUNT_FLAG_COL = "Student Notes Character Count < 750"
REVIEW_STATUS_COL = "Approved / Disapproved / Need Clarification"
REVIEW_REMARK_COL = "HQ Remark"

# Fixed values (no longer exposed in the sidebar).
OPENAI_MODEL = "gpt-5-mini"
REVIEW_WORKERS = 16

DEFAULT_CLEAN_COLUMNS = [
    "Academic Concerns",
    "Actions Taken on Academic Concerns",
    "Location of Transfer",
    "Pathway Alteration",
    "Student well-being concerns",
    "Actions taken on student well-being concerns",
    "Details of extracurricular activities",
    "Student recognition",
]
SENSITIVE_WORDS = ["sex", "drugs", "alcohol", "aggression", "aggressive"]

_NA_TOKENS = {"", "-", "na", "n/a", "null", "none", "nan"}


# ===========================================================================
# HELPERS
# ===========================================================================
def _norm(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def is_na_like(value) -> bool:
    return _norm(value).lower() in _NA_TOKENS


def _g(row, key):
    """Safe field access -> '' for missing/NaN."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return ""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return v


# ===========================================================================
# STAGE 1 -- master status, pathway clean, blanks, student notes
# ===========================================================================
def add_master_academic_status(main_df, master_df, key_column,
                               master_status_source_col=MASTER_STATUS_COL):
    if key_column not in main_df.columns:
        raise KeyError(f"Key column '{key_column}' not in main file.")
    if key_column not in master_df.columns:
        raise KeyError(f"Key column '{key_column}' not in master file.")
    if master_status_source_col not in master_df.columns:
        raise KeyError(f"'{master_status_source_col}' not in master file. "
                       f"Available: {list(master_df.columns)}")
    df = main_df.copy()
    master = master_df[[key_column, master_status_source_col]].copy()
    master["_k"] = master[key_column].map(_norm).str.lower()
    master = master.drop_duplicates(subset="_k", keep="first")
    lookup = dict(zip(master["_k"], master[master_status_source_col]))
    keys = df[key_column].map(_norm).str.lower()
    mapped = keys.map(lookup)
    matched = keys.isin(lookup.keys()) & keys.ne("")
    status = mapped.where(matched, other="N/A")
    status = status.map(lambda v: "N/A" if is_na_like(v) else v)
    df[MASTER_STATUS_COL] = status.values
    stats = {"total": len(df), "matched": int(matched.sum()),
             "not_found": int((~matched).sum())}
    return df, stats


def standardize_pathway_column(df, col=PATHWAY_COL):
    df = df.copy()
    if col in df.columns:
        df[col] = df[col].map(lambda v: "N/A" if is_na_like(v) else str(v).strip())
    return df


def build_student_notes(df, note_columns=NOTE_COLUMNS):
    df = df.copy()
    note_columns = list(note_columns)
    present = [c for c in note_columns if c in df.columns]
    missing = [c for c in note_columns if c not in df.columns]
    for c in present:
        df[c] = df[c].map(lambda v: "" if is_na_like(v) else str(v).strip())

    def _concat(row):
        return " ".join(str(row[c]) if c in present else "" for c in note_columns)

    df[STUDENT_NOTES_COL] = df.apply(_concat, axis=1)
    return df, missing


def fill_blanks_with_na(df, exclude=()):
    df = df.copy()
    exclude = set(exclude)
    for c in df.columns:
        if c in exclude:
            continue
        df[c] = df[c].map(lambda v: "N/A" if is_na_like(v) else v)
    return df


# ===========================================================================
# CLEAN DATA  (port of updated_excel.py)
# ===========================================================================
def _clean_cell(cell):
    try:
        parsed = ast.literal_eval(cell)
        if isinstance(parsed, list):
            return ", ".join(str(x) for x in parsed)
        return str(parsed)
    except Exception:
        return cell


def clean_data(df, columns=None):
    df = df.copy()
    cols = columns if columns is not None else DEFAULT_CLEAN_COLUMNS
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(str).apply(_clean_cell)
    return df


# ===========================================================================
# BATCH SPLIT
# ===========================================================================
def batch_size_for(n):
    if n > 200:
        return 80
    if 100 <= n <= 200:
        return 50
    return None


def split_into_batches(df):
    n = len(df)
    size = batch_size_for(n)
    if size is None:
        return [df.copy()]
    return [df.iloc[i:i + size].copy() for i in range(0, n, size)]


# ===========================================================================
# REPORT REVIEW  (port of review.py)
# ===========================================================================
def generate_prompt(row):
    return f"""
You are an expert academic reviewer.

Below is a student's monthly report:

--- STUDENT DATA ---

📘 Study Status:
- Khotwa Program Status: {_g(row,'Khotwa Program Status')}
- Reason for student not taking classes: {_g(row,'Reason for student not taking classes')}
- Reason why student may not return: {_g(row,'Reason why student may not return')}
- Master Academic Status: {_g(row,'Master Academic Status')}
- Current Academic Status: {_g(row,'Current Academic Status')}
- Flag for 1:1 mentoring session: {_g(row,'Did Mentoring Session take place?')}
- Date of meeting with student: {_g(row,'Date of meeting with student')}
- High Priority Flagging: {_g(row,'High Priority Flagging')}

📘 Academic:
- Academic Concerns: {_g(row,'Academic Concerns')}
- Actions Taken on Academic Concerns: {_g(row,'Actions Taken on Academic Concerns')}

🔁 Transfer:
- Type of Transfer: {_g(row,'Type of Transfer')}
- Stage of Transfer: {_g(row,'Stage of Transfer')}
- Applications Submitted: {_g(row,'No. of transfer applications submitted')}

💡 Well-being:
- Student well-being concerns: {_g(row,'Student well-being concerns')}
- Actions taken on student well-being concerns: {_g(row,'Actions taken on student well-being concerns')}

🎯 Address:
- Accommodation Type: {_g(row,'Accommodation Type')}
- Address Line 1: {_g(row,'Address Line 1')}
- Address Line 2: {_g(row,'Address Line 2')}
- Address City: {_g(row,'Address City')}
- Address State: {_g(row,'Address State')}

📘 Pathway Alteration:
- Pathway Alteration: {_g(row,'Pathway Alteration')}
- Details of Pathway Alteration: {_g(row,'Details of Pathway Alteration')}

📝 Notes on student:
{_g(row,'Student notes')}

---

🔍 PRIMARY REVIEW OBJECTIVE:

You must interpret the “Notes on student” to verify the logical correctness of:
- "Academic Concerns" and "Actions Taken on Academic Concerns"
- "Khotwa Program Status"
- "Student well-being concerns" and "Actions taken on student well-being concerns"
- "Type of Transfer"
- All other key fields

Each rule below is **mandatory**. If any one rule is violated, you must:
- Set **Status = Need Clarification**
- Mention the rule number and reason in **Remark**

---

📜 LOGICAL RULES TO VALIDATE (ALL ARE EQUALLY IMPORTANT):

🔹 Rule 1: Academic Concerns ↔ Actions Taken  
- If "Academic Concerns" = "No concerns", then "Actions Taken on Academic Concerns" must be "No action needed"  
Vice versa: If "Actions Taken" = "No action needed", then "Academic Concerns" must be "No concerns"
- If "Academic Concerns" includes "Failed course(s)", then "Actions Taken on Academic Concerns" must include at least one of the following: "AIP", "EIP", or "AAP". "Academic Concerns" and "Actions Taken on Academic Concerns" should be verified in "Notes on student"

🔹 Rule 2: Transfer Logic  
- If "Type of Transfer" = "Not Applicable" or "N/A", then "Stage of Transfer" **should** be "N/A" 
- If "Type of Transfer" ≠ "Not Applicable" or "N/A", then "Applications Submitted" **should** be greater than 0
Vice versa: If "Stage of Transfer" is "N/A" and "Applications Submitted" is "N/A" or 0, then "Type of Transfer" must be "Not Applicable" or "N/A"

🔹 Rule 3: Well-being Consistency  
- If "Student well-being concerns" = "None", then "Actions taken on student well-being concerns" = "None", and should be verified by "Notes on student" if the "Student well-being concerns" ≠ "None"
Vice versa: If actions = "None", concerns must also be "None"

🔹 Rule 4: Address Check  
- If "Khotwa Program Status" = "Scholarship Active - Currently taking classes", then "Accommodation Type" must not be "Student in Transition - Address will be updated soon" or "Student under withdrawal - Termination (Address not available)"
Vice Versa: If "Accommodation Type" is "Student in Transition - Address will be updated soon" or "Student under withdrawal - Termination (Address not available)", then "Khotwa Program Status" must not be "Scholarship Active - Currently taking classes"
- If "Accommodation Type" is not "Student in Transition - Address will be updated soon" or "Student under withdrawal - Termination (Address not available)", then the combined values of "Address Line 1", "Address Line 2", "Address City", and "Address State" should form a complete, meaningful, and logically valid address

🔹 Rule 5: Khotwa Status  
If "Khotwa Program Status" = "Scholarship Active - Not currently taking classes but planning to return" or "Scholarship Active - May not return" or "Scholarship Active - New Student not taking classes yet"
→ Then Notes must **justify** it
If "Khotwa Program Status" = "Scholarship Active - New Student not taking classes yet", then "Type of Transfer" **should** be "Not Applicable"

🔹 Rule 6: Pathway Alteration Check
- If "Pathway Alteration" ≠ "No", then "Details of Pathway Alteration" ≠ "N/A"
Vice Versa: If "Details of Pathway Alteration" ≠ "N/A", then "Pathway Alteration" ≠ "No"

🔹 Rule 7: Date of meeting with student Check
- If "Flag for 1:1 mentoring session" = "No" and "Reason for student not taking classes" = "N/A" and "Reason why student may not return" = "N/A", then "Academic Concerns" should contain "Missed mandatory mentor 1:1 session"
Vice Versa: If "Academic Concerns" contains "Missed mandatory mentor 1:1 session" and "Reason for student not taking classes" = "N/A" and "Reason why student may not return" = "N/A", then "Flag for 1:1 mentoring session" = "No"
- If "Flag for 1:1 mentoring session" = "Yes", then "Date of meeting with student" must not be a future date (i.e., should be less than or equal to the current date)
Vice Versa: If "Date of meeting with student" is a future date (greater than the current date), then "Flag for 1:1 mentoring session" must be "No"

🔹 Rule 8: Academic Hierarchy Check
Ensure that academic status progression follows this strict hierarchy: "English Program Courses Only" or "Foundation Courses" → "Hybrid / Bridge" → "Associate Degree Courses Only" or "Diploma" → "Bachelor Degree Courses Only"
The transition from "Master Academic Status" to "Current Academic Status" must always move forward or remain at the same level within this hierarchy

🔹 Rule 9: Reason for Absence Check
If the "Reason for student not taking classes" ≠ "N/A", then "Notes on student" must include sufficient details and context explaining that reason
If the "Reason why student may not return" ≠ "N/A", then "Notes on student" must include sufficient details, context explaining that reason, and Stop Salary Status

🔹 Rule 10: Additional Notes-Based Validations  
If "Academic Concerns" = "Behavioral issues impacting academics", the Notes must **justify** it
If "Actions taken on student well-being concerns" = "Informed ADEK Advisor of critical concerns", the Notes must **justify** it
If "High Priority Flagging" = "Yes", then Notes must **justify** it

---

If multiple rules are violated, list all.
Return the result **strictly** in the following format — do not add any explanation or extra commentary:

Status: [Approved / Need Clarification]  
Remark: List **all violated rules** together in the format:
        Rule A violated: explanation; Rule B violated: explanation; Rule C violated: explanation; Rule D violated: explanation

"""


def extract_field(lines, label):
    for line in lines:
        m = re.match(rf"^{label}\s*:\s*(.*)", line.strip(), re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return ""


def review_student(row):
    prompt = generate_prompt(row)
    try:
        response = openai.ChatCompletion.create(
            model="gpt-5-mini",
            messages=[
                {"role": "system", "content": "You are a strict academic reviewer applying all rules equally."},
                {"role": "user", "content": prompt}
            ]
        )
        content = response['choices'][0]['message']['content']
        lines = content.strip().split("\n")
        status = extract_field(lines, "Status")
        remark = extract_field(lines, "Remark") if status.lower() != "approved" else ""
        return status, remark
    except Exception as e:
        return "Error", str(e)


def review_dataframe(df, api_key=None, model="gpt-5-mini", max_workers=REVIEW_WORKERS, progress=None):
    df = df.copy()
    if api_key:
        openai.api_key = api_key
    n = len(df)
    rows = list(df.iterrows())
    results = {}

    def _worker(item):
        i, (_, row) = item
        return i, review_student(row)

    done = 0
    with cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, res in ex.map(_worker, enumerate(rows)):
            results[i] = res
            done += 1
            if progress:
                progress(done, n)
    df[REVIEW_STATUS_COL] = [results[i][0] for i in range(n)]
    df[REVIEW_REMARK_COL] = [results[i][1] for i in range(n)]
    return df


# ===========================================================================
# GRAMMAR CHECK  (port of app.py; contact check removed per request)
# ===========================================================================
def _detect_sensitive_words(note):
    note = "" if note is None else str(note)
    found = [w for w in SENSITIVE_WORDS
             if re.search(rf"\b{re.escape(w)}\b", note, re.IGNORECASE)]
    return f"Yes: {', '.join(found)}" if found else "No"


def _check_student_name(row):
    note = str(_g(row, "Student notes")).lower()
    name_val = _g(row, "Student Name")
    first = str(name_val).split()[0].lower() if str(name_val).strip() else ""
    if (first and first in note) or ("student" in note) or ("mentee" in note):
        return "No"
    return "Yes"


def grammar_check(df):
    df = df.copy()
    notes = df["Student notes"] if "Student notes" in df.columns else pd.Series([""] * len(df))
    df["Sensitive Word Flag"] = notes.apply(_detect_sensitive_words)
    df["Incorrect Student Name Flag"] = df.apply(_check_student_name, axis=1)
    return df


# ===========================================================================
# CHARACTER-COUNT FLAG + FILENAME
# ===========================================================================
def char_count(text):
    s = "" if text is None else str(text)
    s = re.sub(r"\s+", " ", s).strip()
    return len(s)


def add_charcount_flag(df, notes_col=STUDENT_NOTES_COL, threshold=750):
    df = df.copy()
    if notes_col not in df.columns:
        raise KeyError(f"'{notes_col}' column required for character-count flag.")
    df[CHARCOUNT_FLAG_COL] = df[notes_col].map(
        lambda v: "Yes" if char_count(v) < threshold else "No")
    return df


def _ordinal(day):
    if 11 <= day % 100 <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suf}"


def final_report_name(n_cases, when=None):
    when = when or dt.date.today()
    return f"{_ordinal(when.day)} {when.strftime('%B')} {n_cases} cases.xlsx"


def df_to_xlsx_bytes(df):
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return buf.getvalue()


# ===========================================================================
# PIPELINE
# ===========================================================================
def run_pipeline(main_df, master_df, *, match_key="Student ADEK Application ID", report_date=None,
                 api_key=None, model=OPENAI_MODEL, review_workers=REVIEW_WORKERS,
                 clean_columns=None, log=print):
    report_date = report_date or dt.date.today()

    log("Stage 1: master status + pathway clean + blanks->N/A + student notes")
    df, stats = add_master_academic_status(main_df, master_df, match_key)
    log(f"  matched={stats['matched']}  not_found(N/A)={stats['not_found']}  total={stats['total']}")
    df = standardize_pathway_column(df)
    df, missing = build_student_notes(df)
    if missing:
        log(f"  note columns missing (skipped): {missing}")
    df = fill_blanks_with_na(df, exclude=[STUDENT_NOTES_COL])

    log("Step: Clean Data (list-field cleaning)")
    df = clean_data(df, columns=clean_columns)

    batches = split_into_batches(df)
    size = batch_size_for(len(df))
    log(f"Step: batch split -> {len(batches)} batch(es) "
        f"(size {size if size else 'single (<100)'})")

    if not api_key:
        raise RuntimeError("Report Review needs an OpenAI API key. Enter it in the "
                           "sidebar or set OPENAI_API_KEY.")
    log(f"Step: AI Report Review via {model} ({review_workers} workers)")

    def _prog(done, total):
        if done == total or done % 25 == 0:
            log(f"  reviewed {done}/{total}")

    df = review_dataframe(df, api_key=api_key, model=model,
                          max_workers=review_workers, progress=_prog)

    log("Step: Grammar check (sensitive words + name)")
    df = grammar_check(df)

    notes_col = next((c for c in df.columns if "student notes" in c.lower()),
                     STUDENT_NOTES_COL)
    df = add_charcount_flag(df, notes_col=notes_col)
    flagged = int((df[CHARCOUNT_FLAG_COL] == "Yes").sum())
    fname = final_report_name(len(df), report_date)
    log(f"Step: char-count flag (flagged<750={flagged})  ->  {fname}")
    return df, fname, stats


# ===========================================================================
# STREAMLIT UI
# ===========================================================================
def main():
    st.set_page_config(page_title="MPR Review System", page_icon="📋", layout="wide")
    st.title("📋 MPR Review System")
    st.caption("Upload the Main file and the Master Data file. Everything runs "
               "in-process and produces one final Excel to download.")

    c1, c2 = st.columns(2)
    main_file = c1.file_uploader("Main file (.xlsx)", type=["xlsx", "xls"])
    master_file = c2.file_uploader("Master Data file (.xlsx)", type=["xlsx", "xls"])

    with st.sidebar:
        st.header("Settings")
        match_key = st.text_input("Match key column", value="Student ADEK Application ID")
        report_date = st.date_input("Report date", value=dt.date.today())

    # OpenAI key comes from Streamlit secrets:
    #   .streamlit/secrets.toml ->  [openai]
    #                                api_key = "sk-..."
    try:
        api_key = st.secrets["openai"]["api_key"]
        openai.api_key = api_key
    except Exception:
        api_key = os.environ.get("OPENAI_API_KEY")
        if api_key:
            openai.api_key = api_key
        else:
            st.sidebar.warning(
                "No OpenAI key found. Add it to .streamlit/secrets.toml under "
                "[openai] api_key = \"sk-...\" (or set OPENAI_API_KEY).")

    ready = bool(main_file and master_file)
    if st.button("🚀 Run", type="primary", disabled=not ready):
        try:
            main_df = pd.read_excel(main_file, dtype=str)
            master_df = pd.read_excel(master_file, dtype=str)
        except Exception as e:
            st.error(f"Could not read input files: {e}")
            return

        log_box = st.empty()
        logs = []
        def log(msg):
            logs.append(str(msg)); log_box.code("\n".join(logs))

        with st.status("Running pipeline…", expanded=True):
            try:
                result_df, fname, stats = run_pipeline(
                    main_df, master_df, match_key=match_key, report_date=report_date,
                    api_key=api_key or None, model=OPENAI_MODEL,
                    review_workers=REVIEW_WORKERS, log=log)
            except Exception as e:
                st.error(f"Pipeline failed: {e}")
                return

        st.success(f"Done — {len(result_df)} students processed → {fname}")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", stats["total"])
        m2.metric("Matched in master", stats["matched"])
        m3.metric("Set to N/A", stats["not_found"])
        st.dataframe(result_df.head(20), use_container_width=True)
        st.download_button(
            "⬇️ Download final Excel", data=df_to_xlsx_bytes(result_df),
            file_name=fname,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    main()
    
