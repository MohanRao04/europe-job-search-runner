"""Read only direct replies referencing mail the worker actually submitted."""
import email
import imaplib
import re
from email.policy import default
from email.utils import parseaddr

def classify(msg):
    part=msg.get_body(preferencelist=("plain",))
    if part is None:
        return None
    body=re.split(r"(?m)^>|^On .+wrote:|^From: ",part.get_content(),maxsplit=1)[0][:7000]
    patterns={
        "rejection":r"not (?:be )?(?:moving|proceeding) (?:forward |further )?with your application|unable to offer you (?:the |a )?position",
        "interview":r"invite you (?:to|for) (?:a|an|the|our) (?:first |technical |video |phone |initial )?interview|schedule (?:your|an) interview with",
        "offer":r"(?:pleased|delighted) to offer you (?:the |a )?(?:role|position|job)",
    }
    matches=[key for key,pattern in patterns.items() if re.search(pattern,body,re.I)]
    return matches[0] if len(matches)==1 else None

def collect(db,cfg,password,event):
    targets=db.execute("""SELECT m.id,m.message_id,m.domain FROM mail_threads m JOIN applications a
                       ON a.id=m.id WHERE a.status NOT IN ('offer','rejection')""").fetchall()
    if not targets or not cfg.get("imap_host"):
        return
    with imaplib.IMAP4_SSL(cfg["imap_host"],timeout=20) as box:
        box.login(cfg["smtp_user"],password)
        box.select("INBOX",readonly=True)
        for ref,mid,domain in targets:
            result,data=box.uid("search",None,"OR","HEADER","References",mid,"HEADER","In-Reply-To",mid)
            if result!="OK" or not data[0]:
                continue
            for uid in data[0].split():
                result,parts=box.uid("fetch",uid,"(BODY.PEEK[]<0.65536>)")
                if result!="OK":
                    continue
                raw=next((p[1] for p in parts if isinstance(p,tuple)),None)
                if not raw:
                    continue
                msg=email.message_from_bytes(raw,policy=default)
                sender=parseaddr(str(msg["From"]))[1].lower().rpartition("@")[2]
                if sender!=domain or mid not in (str(msg["References"])+" "+str(msg["In-Reply-To"])).split():
                    continue
                status=classify(msg)
                if status and db.execute("UPDATE applications SET status=? WHERE id=? AND status<>? AND status NOT IN ('offer','rejection')",(status,ref,status)).rowcount:
                    event(db,status,ref,"Employer email reply")
