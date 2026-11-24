"""Scheduled applicant-side job search. All applicant data lives in STATE_DIR."""
import argparse
import hashlib
import html
import json
import os
import re
import smtplib
import sqlite3
import ssl
import subprocess
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
from sponsors import UK, REGISTER, licensed, match
from ranking import rank

ROOT = Path(os.environ.get("STATE_DIR", "../private-state")).resolve()
BOARDS = {"monzo": "Monzo", "adyen": "Adyen", "deliveroo": "Deliveroo",
          "canonical": "Canonical", "elastic": "Elastic", "datadog": "Datadog",
          "cloudflare": "Cloudflare", "mongodb": "MongoDB"}
ROLE = re.compile(r"\b(data engineer(?:ing)?|analytics engineer(?:ing)?|(?:bi|business intelligence|sql|etl|power bi|data warehouse) (?:developer|engineer|analyst)|data platform engineer|analytical engineer)\b", re.I)
EUROPE = re.compile(r"\b(united kingdom|uk|london|manchester|cardiff|edinburgh|bristol|dublin|ireland|germany|berlin|munich|hamburg|netherlands|amsterdam|rotterdam|france|paris|spain|madrid|barcelona|portugal|lisbon|poland|warsaw|krak[oó]w|sweden|stockholm|denmark|copenhagen|belgium|brussels|finland|helsinki|austria|vienna|italy|milan|czechia|prague|switzerland|zurich|norway|oslo|estonia|tallinn|luxembourg|romania|greece|hungary|slovakia|slovenia|croatia|lithuania|latvia|bulgaria|europe)\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|head|manager|director|architect)\b", re.I)
SKILLS = "SQL Server|SQL|T-SQL|Python|SSIS|ETL|ELT|DAX|Snowflake|ClickHouse|AWS S3|Power BI|Microsoft Fabric|pandas|dimensional modelling|dbt|Airflow|Databricks|Spark|Scala|Kafka|BigQuery|Looker|Tableau|PostgreSQL|Terraform|Docker|Azure|AWS|Git".split("|")
NEGATIVE = re.compile(r"(?:no|not|cannot|unable|unavailable|without|won.t|ineligible).{0,70}(?:sponsor|work permit)|(?:sponsorship|work permit).{0,50}(?:not available|unavailable|not offered)|(?:already|existing|unrestricted|must have).{0,45}(?:right to work|work authori[sz]ation)|(?:EU|EEA|UK) citizens only", re.I)
POSITIVE = re.compile(r"(?:we (?:can|will|do) (?:provide |offer )?|we (?:offer|provide) )(?:visa sponsorship|sponsor (?:a |your )?(?:visa|work permit))|visa sponsorship (?:is )?(?:available|provided|offered)", re.I)

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def fetch(url):
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": "ApplicantSearch/1.0", "Accept": "application/json"}), timeout=20) as res:
                return json.load(res)
        except Exception as exc:
            if attempt == 2 or getattr(exc, "code", 0) in (401, 403, 404):
                raise
            time.sleep(2 ** attempt)

def plain(value):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(value or "")))).strip()

def skills(text):
    return {s for s in SKILLS if re.search(r"(?<!\w)" + re.escape(s) + r"(?!\w)", text, re.I)}

def assess(job, cv):
    desc = job["description"]
    wanted = skills(desc)
    found = wanted & skills(cv)
    negative = NEGATIVE.search(desc)
    positive = POSITIVE.search(desc)
    evidence = negative or positive
    quote = desc[max(0, evidence.start()-40):evidence.end()+80] if evidence else ""
    if SENIOR.search(job["title"]):
        result = "senior role"
    elif negative:
        result = "work permission restriction"
    elif not positive:
        result = "sponsorship unconfirmed"
    elif len(found) < 2 or len(found) / max(1,len(wanted)) < 0.5:
        result = "limited skill match"
    else:
        result = "sponsorship evidence found"
    return result, sorted(found), sorted(wanted-found), quote

def greenhouse(board):
    endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
    rows = fetch(endpoint + "?content=true")["jobs"]
    return [dict(id=f"gh/{board}/{r['id']}", title=r["title"], company=BOARDS[board],
                 location=r["location"]["name"], description=plain(r.get("content")),
                 url=r["absolute_url"], refresh=endpoint + "/" + str(r["id"])) for r in rows]

def portal():
    rows = fetch("https://www.arbeitnow.com/api/job-board-api")["data"]
    return [dict(id="arbeitnow/"+r["slug"],title=r["title"],company=r["company_name"],
                 location=r["location"],description=plain(r["description"]),url=r["url"],refresh="") for r in rows]

def ashby(board,company):
    rows=fetch("https://api.ashbyhq.com/posting-api/job-board/"+board)["jobs"]
    return [dict(id="ashby/"+board+"/"+r["jobUrl"].rstrip("/").split("/")[-1],
                 title=r["title"],company=company,location=r.get("location") or "",
                 description=plain(r.get("descriptionPlain")),url=r["jobUrl"],refresh="")
            for r in rows if r.get("isListed",True)]

def database():
    db = sqlite3.connect(ROOT / "history.sqlite")
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, company TEXT, title TEXT, location TEXT,
      url TEXT, reason TEXT, matched TEXT, gaps TEXT, evidence TEXT, first_seen TEXT, seen TEXT, active INTEGER);
    CREATE TABLE IF NOT EXISTS events(ts TEXT, kind TEXT, ref TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS applications(id TEXT PRIMARY KEY, identity TEXT UNIQUE, status TEXT, ts TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS reports(day TEXT, span INTEGER, sent INTEGER DEFAULT 0, PRIMARY KEY(day,span));
    CREATE TABLE IF NOT EXISTS sponsor_checks(id TEXT PRIMARY KEY, checked TEXT, entity TEXT, source TEXT);
    """)
    return db

def event(db, kind, ref="", detail=""):
    db.execute("INSERT INTO events VALUES(?,?,?,?)", (now(), kind, ref, detail))

def checkpoint(db):
    db.commit()
    if os.environ.get("PERSIST_GIT") == "1":
        def git(*args):
            return subprocess.run(["git",*args],cwd=ROOT,check=True,capture_output=True)
        git("add","--all")
        if git("status","--porcelain").stdout:
            git("commit","-qm","Update private search history")
        git("push","origin","HEAD:main")

def read_cv():
    from docx import Document
    doc = Document(ROOT / "cv.docx")
    lines = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        lines.extend(cell.text for row in table.rows for cell in row.cells if cell.text)
    return lines

def package(job, lines, cfg):
    from xml.sax.saxutils import escape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    wanted = skills(job["description"])
    evidence = [line for line in lines if len(line)>65 and skills(line)&wanted and "[" not in line]
    evidence=rank(job["title"]+" "+job["description"],evidence)
    path = ROOT / "applications" / hashlib.sha256(job["id"].encode()).hexdigest()[:20]
    path.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    flow = []
    for line in [lines[0],"Relevant experience",*evidence[:2],"Curriculum vitae",*lines[1:]]:
        flow.extend([Paragraph(escape(line),styles["Heading2" if line in ("Relevant experience","Curriculum vitae") else "BodyText"]),Spacer(1,4)])
    pdf = path/"Resume.pdf"
    SimpleDocTemplate(str(pdf),pagesize=A4).build(flow)
    letter = (f"Dear {job['company']} hiring team,\n\nI am applying for the {job['title']} position. "
              + " ".join(evidence[:2]) + "\n\nI am based in India and would need employer sponsorship to relocate. "
              + "I would welcome the opportunity to discuss this work with your team.\n\n"+cfg["name"])
    (path/"cover-letter.txt").write_text(letter,encoding="utf-8")
    return pdf,letter

def scan(db, lines, cfg):
    cv = "\n".join(lines)
    try:
        names=licensed()
    except Exception as exc:
        names={}
        event(db,"source failure","UK sponsor register",type(exc).__name__)
    sources=[(f"gh/{b}",lambda b=b: greenhouse(b)) for b in BOARDS]
    sources.extend((f"ashby/{b}",lambda b=b,c=c: ashby(b,c)) for b,c in
                   [("deepl","DeepL"),("pleo","Pleo"),("n8n","n8n"),("personio","Personio")])
    for source, loader in sources+[("arbeitnow",portal)]:
        try:
            all_jobs = loader()
        except Exception as exc:
            event(db,"source failure",source,type(exc).__name__)
            db.commit()
            continue
        stamp = now()
        if source.startswith(("gh/","ashby/")):
            db.execute("UPDATE jobs SET active=0 WHERE id LIKE ?",(source+"/%",))
        for j in all_jobs:
            if not ROLE.search(j["title"]) or not EUROPE.search(j["location"]):
                continue
            reason, matched, gaps, quote = assess(j,cv)
            if UK.search(j["location"]):
                entity=match(j["company"],names,cfg.get("legal_names",{}))
                db.execute("INSERT OR REPLACE INTO sponsor_checks VALUES(?,?,?,?)",(j["id"],stamp,entity,REGISTER))
                if reason=="sponsorship evidence found" and not entity:
                    reason="UK sponsor entity unconfirmed"
            values = (j["id"],j["company"],j["title"],j["location"],j["url"],reason,
                      json.dumps(matched),json.dumps(gaps),quote,stamp,stamp,1)
            db.execute("""INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,location=excluded.location,url=excluded.url,reason=excluded.reason,
              matched=excluded.matched,gaps=excluded.gaps,evidence=excluded.evidence,seen=excluded.seen,active=1""",values)
            event(db,"analyzed",j["id"])
            if reason == "sponsorship evidence found" and j["refresh"]:
                apply_if_supported(db,j,lines,cfg)
        event(db,"source scanned",source,str(len(all_jobs)))
        db.commit()
    db.execute("UPDATE jobs SET active=0 WHERE id LIKE 'arbeitnow/%' AND seen < ?",((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))
    checkpoint(db)

def apply_if_supported(db, job, lines, cfg):
    if not cfg.get("application_ready") or any(re.search(r"\[[^\]]+\]|\bTBD\b",line,re.I) for line in lines):
        return
    route = cfg.get("routes",{}).get(job["id"].rsplit("/",1)[0])
    if not route or not route.get("employer_automation_policy"):
        return
    # Applicant-facing email route only when the official posting explicitly invites this method.
    if not cfg.get("name") or not cfg.get("applicant_email"):
        return
    if db.execute("SELECT 1 FROM applications WHERE id=?",(job["id"],)).fetchone():
        return
    try:
        listing = fetch(job["refresh"])
    except Exception as exc:
        event(db,"recheck failed",job["id"],type(exc).__name__)
        return
    desc = plain(listing.get("content"))
    fresh = dict(job,description=desc,title=listing["title"],location=listing["location"]["name"])
    if fresh["title"] != job["title"] or fresh["location"] != job["location"] or assess(fresh,"\n".join(lines))[0] != "sponsorship evidence found":
        return
    # Require an unambiguous invitation for overseas applicants and the site's plain CV-by-email instruction.
    invite = re.search(r"(?:applicants from india|candidates from (?:india|any country)|applicants worldwide) (?:are )?(?:welcome|eligible|accepted)",desc,re.I)
    address = re.search(r"(?:apply by emailing|send (?:your |a )?(?:cv|resume)(?: and (?:a |your )?cover letter)? to)\s+([\w.+-]+@[\w.-]+\.[a-z]{2,})",desc,re.I)
    if not invite:
        return
    if not route.get("sponsor_evidence_url") or route.get("valid_until","") < now()[:10]:
        return
    if not role_requirements_met(desc,lines,cfg):
        return
    email_route = address and address[1].lower().split("@")[-1] == route.get("mail_domain") and os.getenv("SMTP_PASSWORD")
    if not email_route and not route.get("form_origin"):
        return
    identity = "|".join(" ".join(fresh[k].lower().split()) for k in ("company","title","location"))
    pdf,letter = package(fresh,lines,cfg)
    msg = EmailMessage()
    msg["From"] = cfg["applicant_email"]
    msg["Subject"] = "Application for "+job["title"]
    msg.set_content(letter)
    msg.add_attachment(pdf.read_bytes(),maintype="application",subtype="pdf",filename="Resume.pdf")
    browser = None
    if email_route:
        msg["To"] = address[1]
    else:
        from forms import prepare
        try:
            browser = prepare(fresh["url"],route["form_origin"],cfg,pdf,letter)
        except Exception as exc:
            event(db,"form needs attention",job["id"],type(exc).__name__)
            return
    try:
        db.execute("INSERT INTO applications VALUES(?,?,?,?,?)",(job["id"],identity,"delivery uncertain",now(),"Reservation"))
    except sqlite3.IntegrityError:
        if browser:
            browser.close()
        return
    event(db,"attempted",job["id"])
    checkpoint(db)  # Must be durable before a non-idempotent send.
    try:
        if browser:
            browser.submit()
        else:
            deliver(msg,cfg)
    except Exception as exc:
        event(db,"delivery uncertain",job["id"],type(exc).__name__)
    else:
        receipt = "Hosted form displayed confirmation" if browser else "Accepted by outgoing mail server"
        db.execute("UPDATE applications SET status=?,detail=? WHERE id=?",("submitted",receipt,job["id"]))
        event(db,"submitted",job["id"])
    finally:
        if browser:
            browser.close()
    checkpoint(db)

def role_requirements_met(desc,lines,cfg):
    if skills(desc)-skills(" ".join(lines)):
        return False
    years = [int(n) for n in re.findall(r"\b(\d+)\+?\s*(?:years|yrs)",desc,re.I)]
    if years and max(years)>cfg.get("experience_years",0):
        return False
    if re.search(r"security clearance|citizenship required|native speaker|fluent (?:in )?(?!english\b)\w+|application fee|pay.{0,20}fee|must reside|currently based|must be based",desc,re.I):
        return False
    return True

def deliver(msg,cfg):
    with smtplib.SMTP_SSL(cfg["smtp_host"],465,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(cfg["smtp_user"],os.environ["SMTP_PASSWORD"])
        if smtp.send_message(msg):
            raise RuntimeError("Recipient refused")

def report(db,day,span,cfg):
    zone = ZoneInfo("Asia/Kolkata")
    end = datetime.fromisoformat(day).replace(tzinfo=zone)+timedelta(days=1)
    start = (end-timedelta(days=span)).astimezone(timezone.utc).isoformat()
    stop = end.astimezone(timezone.utc).isoformat()
    period = (start,stop)
    counts = dict(db.execute("SELECT kind,count(*) FROM events WHERE ts>=? AND ts<? GROUP BY kind",period))
    new = db.execute("SELECT count(*) FROM jobs WHERE first_seen>=? AND first_seen<?",period).fetchone()[0]
    rows = db.execute("SELECT * FROM jobs WHERE active=1 AND seen>=? AND seen<? ORDER BY seen DESC",period).fetchall()
    title = f"{'Weekly' if span==7 else 'Daily'} job search | {day} IST"
    body = [title,"",f"New relevant vacancies: {new}",f"Distinct jobs analyzed: {db.execute('SELECT count(DISTINCT ref) FROM events WHERE kind=\'analyzed\' AND ts>=? AND ts<?',period).fetchone()[0]}",
            *[f"{key.capitalize()}: {counts.get(key,0)}" for key in ("attempted","submitted","delivery uncertain","interview","rejection","offer","source failure")],
            "", "Current decisions in this period:",*[f"{k}: {v}" for k,v in Counter(r["reason"] for r in rows).most_common()]]
    if not cfg.get("application_ready"):
        body.append("Applications paused: complete the identity and contact fields in the CV.")
    for r in sorted(rows,key=lambda r:(r["reason"]!="sponsorship evidence found",-len(json.loads(r["matched"]))))[:15]:
        body.extend(["",f"{r['title']} | {r['company']} | {r['location']}",r["reason"]+" | "+", ".join(json.loads(r["matched"])),r["url"]])
        if r["evidence"]:
            body.append(r["evidence"])
        reg=db.execute("SELECT * FROM sponsor_checks WHERE id=?",(r["id"],)).fetchone()
        if reg:
            body.append("UK register: "+(reg["entity"] or "Legal entity unconfirmed")+" | "+reg["source"])
    if span==7:
        gaps=Counter(s for r in rows if not SENIOR.search(r["title"]) for s in json.loads(r["gaps"]))
        body.extend(["","Skills mentioned in target listings but absent from the CV (mentions can include optional skills):",*[f"{s}: {n}" for s,n in gaps.most_common(6)]])
        if gaps:
            body.append("Consider a demonstrable project using "+gaps.most_common(1)[0][0]+" if the roles mentioning it appeal to you.")
    folder=ROOT/"reports"
    folder.mkdir(exist_ok=True)
    (folder/f"{day}-{span}d.txt").write_text("\n".join(body),encoding="utf-8")
    db.execute("INSERT OR IGNORE INTO reports(day,span) VALUES(?,?)",(day,span))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--outcome",nargs=2,metavar=("JOB_ID","STATUS"))
    args=parser.parse_args()
    cfg=json.loads((ROOT/"settings.json").read_text(encoding="utf-8-sig"))
    db=database()
    if args.outcome:
        ref,status=args.outcome
        if status not in ("interview","rejection","offer") or not db.execute("SELECT 1 FROM applications WHERE id=?",(ref,)).fetchone():
            parser.error("Supply an existing application and interview, rejection or offer.")
        if db.execute("UPDATE applications SET status=? WHERE id=? AND status<>?",(status,ref,status)).rowcount:
            event(db,status,ref)
        checkpoint(db)
        return
    scan(db,read_cv(),cfg)
    day=datetime.now(ZoneInfo("Asia/Kolkata")).date()
    report(db,day.isoformat(),1,cfg)
    if day.weekday()==6 or not db.execute("SELECT 1 FROM reports WHERE span=7").fetchone():
        report(db,day.isoformat(),7,cfg)
    checkpoint(db)
    if cfg.get("report_to") and cfg.get("smtp_user") and os.getenv("SMTP_PASSWORD"):
        for r in db.execute("SELECT * FROM reports WHERE sent=0 ORDER BY day,span").fetchall():
            msg=EmailMessage()
            msg["From"],msg["To"]=cfg["smtp_user"],cfg["report_to"]
            content=(ROOT/"reports"/f"{r['day']}-{r['span']}d.txt").read_text(encoding="utf-8")
            msg["Subject"]=content.splitlines()[0]
            msg.set_content(content)
            deliver(msg,cfg)
            db.execute("UPDATE reports SET sent=1 WHERE day=? AND span=?",(r["day"],r["span"]))
            checkpoint(db)

if __name__=="__main__":
    main()
