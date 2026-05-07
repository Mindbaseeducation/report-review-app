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
- Reason for student not taking classes: {row['Reason for student not taking classes']}
- Reason why student may not return: {row['Reason why student may not return']}
- Master Academic Status: {row['Master Academic Status']}
- Current Academic Status: {row['Current Academic Status']}
- Date of meeting with student: {row['Date of meeting with student']}

📘 Academic:
- Academic Concerns: {row['Academic Concerns']}
- Actions Taken on Academic Concerns: {row['Actions Taken on Academic Concerns']}

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

📘 Pathway Alteration:
- Pathway Alteration: {row['Pathway Alteration']}
- Details of Pathway Alteration: {row['Details of Pathway Alteration']}

📝 Notes on student:
{row['Student notes']}

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

🔹 Rule 2: Transfer Logic  
- If "Type of Transfer" = "Not Applicable" or "N/A", then "Stage of Transfer" **should** be "N/A" 
- If "Type of Transfer" ≠ "Not Applicable" or "N/A", then "Applications Submitted" **should** be greater than 0
Vice versa: If "Stage of Transfer" is "N/A" and "Applications Submitted" is "N/A" or 0, then "Type of Transfer" must be "Not Applicable" or "N/A"

🔹 Rule 3: Well-being Consistency  
- If "Student well-being concerns" = "None", then "Actions taken on student well-being concerns" = "None", and should be verified by "Notes on student" if the "Student well-being concerns" ≠ "None"
Vice versa: If actions = "None", concerns must also be "None"

🔹 Rule 4: Extracurricular Activity  
If participation ≠ "No":
→ Then "Details of extracurricular activities" must not be "N/A" or "Not Applicable"  
Vice versa: If "Details of extracurricular activities" is filled with values neither "N/A" nor "Not Applicable", participation cannot be "No"

🔹 Rule 5: Khotwa Status  
If "Khotwa Program Status" = "Scholarship Active - Not currently taking classes but planning to return" or "Scholarship Active - May not return" or "Scholarship Active - New Student not taking classes yet"
→ Then Notes must **justify** it  

🔹 Rule 6: Pathway Alteration Check
- If "Pathway Alteration" ≠ "No", then "Details of Pathway Alteration" ≠ "N/A"
Vice Versa: If "Details of Pathway Alteration" ≠ "N/A", then "Pathway Alteration" ≠ "No"

🔹 Rule 7: Date of meeting with student Check
- If "Date of meeting with student" is "1900" and "Reason for student not taking classes" = "N/A", then "Academic Concerns" should contain "Missed mandatory mentor 1:1 session"
Vice Versa: If "Academic Concerns" contains "Missed mandatory mentor 1:1 session" and "Reason for student not taking classes" = "N/A", then "Date of meeting with student" should be "1900"
- If "Date of meeting with student" is "1900" and "Reason why student may not return" = "N/A", then "Academic Concerns" should contain "Missed mandatory mentor 1:1 session"
Vice Versa: If "Academic Concerns" contains "Missed mandatory mentor 1:1 session" and "Reason why student may not return" = "N/A", then "Date of meeting with student" should be "1900"

🔹 Rule 8: Academic Hierarchy Check
Ensure that academic status progression follows this strict hierarchy: "English Program Courses Only" or "Foundation Courses" → "Hybrid / Bridge" → "Associate Degree Courses Only" or "Diploma" → "Bachelor Degree Courses Only"
The transition from "Master Academic Status" to "Current Academic Status" must always move forward or remain at the same level within this hierarchy

🔹 Rule 9: Reason for Absence Check
If the "Reason for student not taking classes" ≠ "N/A", then "Notes on student" must include sufficient details and context explaining that reason
If the "Reason why student may not return" ≠ "N/A", then "Notes on student" must include sufficient details, context explaining that reason, and Stop Salary Status

🔹 Rule 10: Additional Notes-Based Validations  
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
            
