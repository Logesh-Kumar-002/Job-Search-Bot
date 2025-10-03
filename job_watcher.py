#!/usr/bin/env python3
"""
job_watcher.py

Searches jobs (Naukri, Internshala, Indeed, AngelList) every run and emails
new matches: Frontend / Design, Remote, Fresher, >= 20k INR.
Designed to run on GitHub Actions every 2 hours.

Dependencies:
  pip install -r requirements.txt
"""

import os
import re
import sqlite3
import smtplib
import requests
from datetime import datetime
from typing import List, Dict, Optional
from email.message import EmailMessage
from bs4 import BeautifulSoup

# -------------------
# CONFIG
# -------------------
EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", EMAIL_USER)
MIN_SALARY_INR = int(os.getenv("MIN_SALARY_INR", "20000"))

RESUME_KEYWORDS = [
    "html", "css", "javascript", "front end", "frontend", "web developer",
    "web design", "ui", "ux", "react", "vue", "angular", "responsive", "design"
]

DB_PATH = "jobs_seen.db"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36"
}

# -------------------
# DB Functions
# -------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS seen_jobs (
        id TEXT PRIMARY KEY,
        site TEXT,
        title TEXT,
        company TEXT,
        url TEXT,
        first_seen TIMESTAMP
    )""")
    conn.commit()
    conn.close()

def job_seen(job_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM seen_jobs WHERE id = ?", (job_id,))
    res = c.fetchone()
    conn.close()
    return res is not None

def mark_job_seen(job_id: str, site: str, title: str, company: str, url: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO seen_jobs VALUES (?, ?, ?, ?, ?, ?)",
              (job_id, site, title, company, url, datetime.utcnow()))
    conn.commit()
    conn.close()

# -------------------
# Helpers
# -------------------
def text_contains_keywords(text: str, keywords=RESUME_KEYWORDS) -> bool:
    t = text.lower()
    return any(k in t for k in keywords)

def extract_salary_in_inr(s: Optional[str]) -> Optional[int]:
    if not s:
        return None
    s = s.replace(",", "").lower()

    # ₹25000
    m = re.search(r'₹\s*([0-9]+)', s)
    if m: return int(m.group(1))

    # 25k
    m = re.search(r'([0-9]+)\s*k', s)
    if m: return int(m.group(1)) * 1000

    # 2.5 LPA → monthly
    m = re.search(r'([0-9]+(?:\.[0-9]+)?)\s*lpa', s)
    if m:
        lakhs = float(m.group(1))
        return int((lakhs * 100000) / 12)

    return None

# -------------------
# Site Scrapers
# -------------------
def search_naukri(query="front end developer", location="remote") -> List[Dict]:
    results = []
    q = query.replace(" ", "+")
    url = f"https://www.naukri.com/{q}-jobs-in-{location}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print("Naukri error:", e)
        return results

    for card in soup.find_all("article"):
        jobid = card.get("data-job-id") or None
        title_tag = card.find("a")
        title = title_tag.get_text(strip=True) if title_tag else "No Title"
        company_tag = card.find("a", {"class": "subTitle"})
        company = company_tag.get_text(strip=True) if company_tag else "Unknown"
        link = title_tag["href"] if title_tag else None
        desc = card.get_text(" ", strip=True)
        sal_el = card.find("span", {"class": "salary"})
        salary_text = sal_el.get_text(strip=True) if sal_el else None
        salary_val = extract_salary_in_inr(salary_text)

        if not text_contains_keywords(title + desc): continue
        if "remote" not in desc.lower() and "work from home" not in desc.lower(): continue
        if salary_val is not None and salary_val < MIN_SALARY_INR: continue
        if salary_val is None: continue

        results.append({
            "id": f"naukri::{jobid or title}",
            "site": "Naukri",
            "title": title,
            "company": company,
            "url": link,
            "salary_text": salary_text,
            "snippet": desc[:300]
        })
    return results

def search_internshala(query="front end developer") -> List[Dict]:
    results = []
    q = query.replace(" ", "+")
    url = f"https://internshala.com/internships/{q}-internship"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print("Internshala error:", e)
        return results

    for card in soup.select("div.container-fluid div.internship_meta"):
        title = card.get_text(strip=True)[:60]
        link_tag = card.find_previous("a", href=True)
        link = "https://internshala.com" + link_tag["href"] if link_tag else None
        company = "Internshala Employer"
        snippet = card.get_text(" ", strip=True)
        stipend_tag = card.find("span", text=re.compile("₹"))
        salary_text = stipend_tag.get_text(strip=True) if stipend_tag else None
        salary_val = extract_salary_in_inr(salary_text)

        if not text_contains_keywords(title + snippet): continue
        if "work from home" not in snippet.lower(): continue
        if salary_val is not None and salary_val < MIN_SALARY_INR: continue
        if salary_val is None: continue

        results.append({
            "id": f"internshala::{title}",
            "site": "Internshala",
            "title": title,
            "company": company,
            "url": link,
            "salary_text": salary_text,
            "snippet": snippet[:300]
        })
    return results

def search_indeed(query="front end developer", location="remote") -> List[Dict]:
    results = []
    q = query.replace(" ", "+")
    url = f"https://in.indeed.com/jobs?q={q}&l={location}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print("Indeed error:", e)
        return results

    for card in soup.select("td.resultContent"):
        title = card.get_text(strip=True)[:60]
        link_tag = card.find("a", href=True)
        link = "https://in.indeed.com" + link_tag["href"] if link_tag else None
        company_tag = card.find("span", {"data-testid": "company-name"})
        company = company_tag.get_text(strip=True) if company_tag else "Unknown"
        snippet = card.get_text(" ", strip=True)
        salary_tag = card.find("div", {"class": "metadata salary-snippet-container"})
        salary_text = salary_tag.get_text(strip=True) if salary_tag else None
        salary_val = extract_salary_in_inr(salary_text)

        if not text_contains_keywords(title + snippet): continue
        if "remote" not in snippet.lower() and "work from home" not in snippet.lower(): continue
        if salary_val is not None and salary_val < MIN_SALARY_INR: continue
        if salary_val is None: continue

        results.append({
            "id": f"indeed::{title}",
            "site": "Indeed",
            "title": title,
            "company": company,
            "url": link,
            "salary_text": salary_text,
            "snippet": snippet[:300]
        })
    return results

def search_angellist(query="front end developer") -> List[Dict]:
    results = []
    url = f"https://wellfound.com/role/{query.replace(' ', '%20')}?remote=true"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print("AngelList error:", e)
        return results

    for card in soup.select("a[href*='/jobs/']"):
        title = card.get_text(strip=True)
        link = "https://wellfound.com" + card["href"]
        if not text_contains_keywords(title): continue
        results.append({
            "id": f"angellist::{link}",
            "site": "AngelList",
            "title": title,
            "company": "Startup",
            "url": link,
            "salary_text": None,
            "snippet": title
        })
    return results

SITE_HANDLERS = [search_naukri, search_internshala, search_indeed, search_angellist]

# -------------------
# Email Sending
# -------------------
def send_email(subject: str, jobs: List[Dict]):
    if not EMAIL_USER or not EMAIL_PASS or not RECIPIENT_EMAIL:
        print("Email credentials missing")
        return
    msg = EmailMessage()
    msg["From"] = EMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject

    lines = []
    for j in jobs:
        lines.append(f"{j['title']} — {j['company']}\n{j['salary_text'] or 'N/A'}\n{j['url']}\n")

    msg.set_content("\n".join(lines))

    html = "".join([f"<p><b>{j['title']}</b> — {j['company']}<br>"
                    f"Salary: {j['salary_text'] or 'N/A'}<br>"
                    f"<a href='{j['url']}'>Apply</a></p>"
                    for j in jobs])
    msg.add_alternative(f"<html><body>{html}</body></html>", subtype="html")

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(EMAIL_USER, EMAIL_PASS)
        server.send_message(msg)
    print("Email sent with", len(jobs), "jobs")

# -------------------
# Main Logic
# -------------------
def run():
    init_db()
    all_jobs = []
    for fn in SITE_HANDLERS:
        try:
            jobs = fn()
            for j in jobs:
                if not job_seen(j["id"]):
                    all_jobs.append(j)
                    mark_job_seen(j["id"], j["site"], j["title"], j["company"], j["url"])
        except Exception as e:
            print("Error in", fn.__name__, e)

    if all_jobs:
        send_email(f"Job Digest — {len(all_jobs)} new jobs — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}", all_jobs)
    else:
        print("No new jobs at", datetime.utcnow())

if __name__ == "__main__":
    run()