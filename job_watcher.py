import os
import re
import sqlite3
import smtplib
import requests
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from collections import Counter

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------
DB_PATH = "/tmp/jobs_seen.db"
MIN_SALARY = 20000
MAX_RESULTS = 15  # top jobs in summary email

# ------------------------------------------------------------
# STEP 1 — Read and analyze resume.pdf
# ------------------------------------------------------------
def extract_resume_text(pdf_path="resume.pdf"):
    text = ""
    if not os.path.exists(pdf_path):
        print("⚠️ resume.pdf not found, using default keywords.")
        return "Frontend Developer HTML CSS JavaScript React UI UX Design Mathematics"
    with fitz.open(pdf_path) as doc:
        for page in doc:
            text += page.get_text()
    return text

def extract_keywords(text):
    words = re.findall(r"[A-Za-z]{3,}", text)
    common = Counter(w.lower() for w in words)
    tech_terms = [
        w for w in common
        if w.lower() in (
            "html css javascript react angular vue bootstrap python java ui ux design frontend "
            "developer web figma wordpress ai chatbot mathematics data entry"
        )
    ]
    if not tech_terms:
        tech_terms = ["frontend", "developer", "web", "design"]
    print("Extracted keywords:", ", ".join(tech_terms))
    return tech_terms

RESUME_TEXT = extract_resume_text()
KEYWORDS = extract_keywords(RESUME_TEXT)

# ------------------------------------------------------------
# STEP 2 — SQLite database to avoid duplicates
# ------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY)")
    conn.close()

def is_new_job(job_id):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM jobs WHERE id=?", (job_id,))
    exists = cur.fetchone()
    if not exists:
        cur.execute("INSERT INTO jobs (id) VALUES (?)", (job_id,))
    conn.commit()
    conn.close()
    return not exists

# ------------------------------------------------------------
# STEP 3 — Fetch jobs from sources
# ------------------------------------------------------------
def fetch_internshala_jobs():
    query = "-".join(KEYWORDS[:2])
    url = f"https://internshala.com/internships/work-from-home-{query}-internship"
    html = requests.get(url, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for c in soup.select("div.individual_internship"):
        title = c.find("h3", class_="heading_4_5").text.strip()
        company = c.find("p", class_="company_name").text.strip()
        link = "https://internshala.com" + c.find("a")["href"]
        stipend_tag = c.find("span", string=re.compile("₹"))
        stipend_text = stipend_tag.text.strip() if stipend_tag else "N/A"
        amt = re.findall(r"\d+", stipend_text.replace(",", ""))
        salary = int(amt[0]) if amt else 0
        if salary >= MIN_SALARY:
            jobs.append({"id": link, "title": title, "company": company,
                         "salary": salary, "url": link})
    return jobs

def fetch_indeed_jobs():
    query = "+".join(KEYWORDS[:3])
    url = f"https://www.indeed.com/jobs?q={query}+fresher&l=remote"
    html = requests.get(url, timeout=20).text
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for c in soup.select("a.tapItem")[:20]:
        title = c.find("h2").text.strip()
        company = c.find("span", class_="companyName")
        comp = company.text.strip() if company else "N/A"
        link = "https://www.indeed.com" + c["href"]
        jobs.append({"id": link, "title": title, "company": comp,
                     "salary": 0, "url": link})
    return jobs

# ------------------------------------------------------------
# STEP 4 — Rank by resume similarity
# ------------------------------------------------------------
def rank_jobs_by_resume(jobs, resume_text):
    if not jobs:
        return []
    corpus = [resume_text] + [j["title"] + " " + j["company"] for j in jobs]
    vectorizer = TfidfVectorizer(stop_words="english")
    tfidf = vectorizer.fit_transform(corpus)
    scores = cosine_similarity(tfidf[0:1], tfidf[1:]).flatten()
    for j, s in zip(jobs, scores):
        j["score"] = round(float(s), 3)
    jobs.sort(key=lambda x: x["score"], reverse=True)
    return jobs

# ------------------------------------------------------------
# STEP 5 — Email sending
# ------------------------------------------------------------
def send_email(subject, jobs):
    user = os.getenv("EMAIL_USER")
    password = os.getenv("EMAIL_PASS")
    recipient = os.getenv("RECIPIENT_EMAIL")

    if not user or not password or not recipient:
        print("❌ Email credentials missing. Set secrets in GitHub.")
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = user
    msg["To"] = recipient
    msg["Subject"] = subject

    if not jobs:
        html = "<p>No matching jobs found today.</p>"
    else:
        html = "<h3>Top Matches Based on Your Resume</h3>"
        for i, j in enumerate(jobs[:MAX_RESULTS], 1):
            html += f"<p><b>{i}. {j['title']}</b> — {j['company']} (Score {j['score']})<br>"
            html += f"<a href='{j['url']}'>Apply here</a></p>"

    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, recipient, msg.as_string())
    print("✅ Email sent with", len(jobs), "jobs.")

# ------------------------------------------------------------
# STEP 6 — Main logic
# ------------------------------------------------------------
def run():
    init_db()
    print("🔍 Starting daily job search at", datetime.now())
    all_jobs = []

    for fetcher in [fetch_internshala_jobs, fetch_indeed_jobs]:
        try:
            jobs = fetcher()
            for j in jobs:
                if is_new_job(j["id"]):
                    all_jobs.append(j)
        except Exception as e:
            print("Error fetching jobs:", e)

    ranked = rank_jobs_by_resume(all_jobs, RESUME_TEXT)
    send_email(f"Daily Job Summary — {datetime.utcnow():%Y-%m-%d}", ranked)

if __name__ == "__main__":
    run()
