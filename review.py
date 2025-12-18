import streamlit as st
import pandas as pd
import openai
from io import BytesIO
import re

# Set OpenAI API Key securely
openai.api_key = st.secrets["openai"]["api_key"]

st.set_page_config(page_title="Report Reviewer", layout="wide")
st.title("📘 Monthly Progress Report Review")

uploaded_file = st.file_uploader("Upload Excel file", type=["xlsx"])

if uploaded_file:
    df = pd.read_excel(uploaded_file, keep_default_na=False)

    def generate_prompt(row):
        return f"""
You are an expert academic reviewer.

Below is a student's monthly report:

--- STUDENT DATA ---

📘 Study Status:
- Khotwa Program Status: {row['Khotwa Program Status']}
- Next expected grade release date: {row['Next expected grade release date']}

📘 Academic:
- Academic Concerns: {row['Academic Concerns']}
- Actions Taken on Academic Concerns: {row['Actions Taken on Academic Concerns']}
- Is student on an Improvement Plan?: {row['Is student on an Improvement Plan?']}
- Improvememt Plan Progress: {row['Improvememt Plan Progress']}

📞 Mentor Contact with ADEK Advisor:
- Reason for contact with ADEK Advisor: {row['Reason for contact with ADEK Advisor']}
- Date of meeting with ADEK Advisor: {row['Date of meeting with ADEK Advisor']}

🔁 Transfer:
- Type of Transfer: {row['Type of Transfer']}
- Stage of Transfer: {row['Stage of Transfer']}
- Applications Submitted: {row['No. of transfer applications submitted']}

💡 Well-being:
- Student well-being concerns: {row['Student well-being concerns']}
- Actions taken on student well-being concerns: {row['Actions taken on student well-being concerns']}

🎯 Extracurricular:
- Student participation in any extracurricular activity?: {row['Student participation in any extracurricular activity?']}
- Details of extracurricular activities: {row['Details of extracurricular activities']}

📝 Notes on student:
{row['Student notes']}

---

🔍 PRIMARY REVIEW OBJECTIVE:

You must interpret the “Notes on student” to verify the logical correctness of:
- "Academic Concerns" and "Actions Taken on Academic Concerns"
- "Khotwa Program Status"
- Well-being and transfer actions
- All other key fields

Each rule below is **mandatory**. If any one rule is violated, you must:
- Set **Status = Need Clarification**
- Mention the rule number and reason in **Remark**

---

📜 LOGICAL RULES TO VALIDATE (ALL ARE EQUALLY IMPORTANT):

🔹 Rule 1: Academic Concerns ↔ Actions Taken  
If "Academic Concerns" = "No concerns", then "Actions Taken on Academic Concerns" must be "No action needed"  
Vice versa: If "Actions Taken" = "No action needed", then "Academic Concerns" must be "No concerns"

🔹 Rule 2: Academic Improvement Plan Required  
If "Actions Taken on Academic Concerns" = "Academic Improvement Plan (AIP) in place":
- Then "Is student on an Improvement Plan?" = "Yes"
- And "Improvement Plan Progress" ≠ "Not applicable to student"  
Vice versa: If "Is student on an Improvement Plan?" = "Yes" or progress is listed, then "Actions Taken on Academic Concerns" = "Academic Improvement Plan (AIP) in place"

🔹 Rule 3: Transfer Logic  
- If "Type of Transfer" = "Not Applicable" or "N/A", then "Stage of Transfer" **should** be "N/A" 
- If "Type of Transfer" <> "Not Applicable" or "N/A", then "Applications Submitted" **should** be greater than 0.
Vice versa: If "Stage of Transfer" is "N/A" and "Applications Submitted" is "N/A" or 0, then "Type of Transfer" must be "Not Applicable" or "N/A"

🔹 Rule 4: Well-being Consistency  
If "Student well-being concerns" = "None", then "Actions taken on student well-being concerns" = "None"  
Vice versa: If actions = "None", concerns must also be "None"

🔹 Rule 5: Extracurricular Activity  
If participation ≠ "No":
- Then "Details of extracurricular activities" must not be "N/A" or "Not Applicable"  
Vice versa: If "Details of extracurricular activities" is filled with values neither "N/A" nor "Not Applicable", participation cannot be "No"

🔹 Rule 6: Khotwa Status  
If "Khotwa Program Status" = "Withdrawal / Termination Requested - Pending ADEK approval" or "Withdrawal / Termination approved by ADEK",  
→ Then Notes must **justify** it  
Vice versa: If Notes mention student's withdrawal, then "Khotwa Program Status" should be "Withdrawal / Termination Requested - Pending ADEK approval" or "Withdrawal / Termination approved by ADEK"

🔹 Rule 7: Grade Release Date Check  
If "Khotwa Program Status" = "Active-Enrolled", then Year of "Next expected grade release date" (if not "N/A") should not be "1900"  
Vice versa: If Year of "Next expected grade release date" is "1900", then "Khotwa Program Status" must not be "Active-Enrolled"

🔹 Rule 8: Additional Notes-Based Validations  
If "Academic Concerns" = "Behavioral issues impacting academics", the Notes must **justify** it  
If "Actions taken on student well-being concerns" = "Informed ADEK Advisor of critical concerns", the Notes must **justify** it

---

If multiple rules are violated, list all.
Return the result **strictly** in the following format — do not add any explanation or extra commentary:

Status: [Approved / Need Clarification]  
Remark: List **all violated rules** together in the format:
        Rule A violated: explanation; Rule B violated: explanation; Rule C violated: explanation; Rule D violated: explanation

"""

    def extract_field(lines, label):
        for line in lines:
            match = re.match(rf"^{label}\s*:\s*(.*)", line.strip(), re.IGNORECASE)
            if match:
                return match.group(1).strip()
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

    if st.button("🔍 Perform Review"):
        with st.spinner("Reviewing all records..."):
            statuses, remarks = [], []
            for _, row in df.iterrows():
                status, remark = review_student(row)
                statuses.append(status)
                remarks.append(remark)

            df["Approved / Disapproved / Need Clarification"] = statuses
            df["HQ Remark"] = remarks

            output = BytesIO()
            df.to_excel(output, index=False, engine='openpyxl')
            output.seek(0)

            st.success("✅ Review Complete!")
            st.download_button(
                label="📥 Download Reviewed File",
                data=output,
                file_name="Reviewed_Students.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )





