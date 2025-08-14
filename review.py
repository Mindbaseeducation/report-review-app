import streamlit as st
import pandas as pd
import openai
from io import BytesIO
import re

# Set OpenAI API Key securely
openai.api_key = st.secrets["openai"]["api_key"]

st.set_page_config(page_title="Report Reviewer", layout="wide")
st.title("📘 Monthly Tier Report Review")

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
- Current Academic Status: {row['Current Academic Status']}
- Next Month Academic Status: {row['Next Month Academic Status']}

📘 Academic:
- Academic Concerns: {row['Academic Concerns']}
- Actions Taken on Academic Concerns: {row['Actions Taken on Academic Concerns']}
- Proactive Actions Taken: {row['Proactive Actions Taken']}
- Is student on an Improvement Plan?: {row['Is student on an Improvement Plan?']}
- Improvememt Plan Progress: {row['Improvememt Plan Progress']}

📞 Mentor Contact with ADEK Advisor:
- Reason for contact with ADEK Advisor: {row['Reason for contact with ADEK Advisor']}
- Date of meeting with ADEK Advisor: {row['Date of meeting with ADEK Advisor']}

🏫 Mentor Contact with Institution:
- Mentor's Contact Point with Institutions: {row["Mentor's Contact Point with Institutions"]}
- Date of meeting with institution: {row['Date of meeting with institution']}

🔁 Transfer:
- Type of Transfer: {row['Type of Transfer']}
- Stage of Transfer: {row['Stage of Transfer']}
- Institution student transferred to: {row['Institution student transferred to']}
- Mentor support provided in transfer: {row['Mentor support provided in transfer']}

💡 Well-being:
- Student well-being concerns: {row['Student well-being concerns']}
- Actions taken on student well-being concerns: {row['Actions taken on student well-being concerns']}

🎯 Extracurricular:
- Student participation in any extracurricular activity?: {row['Student participation in any extracurricular activity?']}
- Details of extracurricular activities: {row['Details of extracurricular activities']}
- Activities' impact on academics: {row["Activities' impact on academics"]}

📝 Notes on student:
{row['Student notes']}

---

🔍 PRIMARY REVIEW OBJECTIVE:

You must interpret the “Notes on student” to verify the logical correctness of:
- "Academic Concerns" and "Actions Taken on Academic Concerns"
- Transitions in "Academic Status" (Current ↔ Next)
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
If "Actions Taken on Academic Concerns" = "Created or revised Academic Improvement Plan":
- Then "Is student on an Improvement Plan?" = "Yes"
- And "Improvement Plan Progress" ≠ "Not applicable to student"  
Vice versa: If "Is student on an Improvement Plan?" = "Yes" or progress is listed, then "Actions Taken on Academic Concerns" = "Created or revised Academic Improvement Plan"

🔹 Rule 3: Transfer Logic  
- If "Type of Transfer" = "Not Applicable",
  → Institution student transferred to **should** be "N/A" or "Unknown"
- If "Stage of Transfer" = "Not Applicable" or "Transfer rejected",
  → Institution student transferred to **should** be "N/A" or "Unknown"
  → else Institution student transferred to **can** be any value except "N/A"
Vice versa: If institution is N/A, stage must be "Not Applicable"

🔹 Rule 4: Well-being Consistency  
If "Student well-being concerns" = "None", then "Actions taken on student well-being concerns" = "None"  
Vice versa: If actions = "None", concerns must also be "None"

🔹 Rule 5: Extracurricular Activity  
If participation ≠ "No":
- Then both "Details of extracurricular activities" and "Activities' impact on academics" must be filled  
Vice versa: If those fields are filled, participation cannot be "No"

🔹 Rule 6: Academic Status Progression (CRITICAL)  
- If Current Academic Status = "Bachelor Degree Courses Only" or "Associate Degree Courses Only",  
  → Next Month Academic Status **cannot** be "English Program Courses Only"
- If Current Academic Status = "English Program Courses Only",  
  → Next Month must also be "English Program Courses Only"
- If either value = "Associate & Bachelor Degree Courses",  
  → Must be justified in Notes on student  
Vice versa: Any status change between English and Degree programs must be justified. If not, mark "Need Clarification"

🔹 Rule 7: Current Academic Status ↔ Khotwa Status  
If "Current Academic Status" = "None",  
→ Khotwa Status must be "Termination - In Progress" or "On Hold - Not Enrolled"  
Vice versa: If Khotwa Status = one of those, Current Academic Status should be "None"

🔹 Rule 8: Grade Release Date Check  
If "Khotwa Program Status" contains "Active", then expected grade release date (if present) must not include "1900"  
Vice versa: If grade date = "1900", then Khotwa status must not say "Active"

🔹 Rule 9: Additional Notes-Based Validations  
If “Academic Concerns” = “Behavioral issues impacting academics”, the Notes must **justify** it  
If “Actions taken on student well-being concerns” = “Informed ADEK Advisor of Critical concerns”, the Notes must **justify** it

---

If multiple rules are violated, list all.
Return the result **strictly** in the following format — do not add any explanation or extra commentary:

Status: [Approved / Need Clarification]  
Remark: [Rule X violated: explanation
         Rule Y violated: explanation]
(Include all violated rules)]

"""

    # Regex-safe field extractor
    def extract_field(lines, label):
        for line in lines:
            match = re.match(rf".*{label}\s*:\s*(.*)", line.strip(), re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return "Error"

    def review_student(row):
        prompt = generate_prompt(row)
        try:
            response = openai.ChatCompletion.create(
                model="gpt-5",
                messages=[
                    {"role": "system", "content": "You are a logical and insightful academic reviewer."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0
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

