"""Check UK legal-entity names against the current official Skilled Worker register."""
import csv
import io
import re
from urllib.request import urlopen

REGISTER = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
UK = re.compile(r"\b(united kingdom|uk|london|manchester|edinburgh|cardiff|bristol|england|scotland|wales)\b",re.I)

def normal(name):
    return re.sub(r"[^a-z0-9]", "", name.casefold().replace(" limited"," ltd"))

def licensed():
    with urlopen(REGISTER,timeout=25) as response:
        page=response.read().decode()
    link=re.search(r'href="(https://assets\.publishing\.service\.gov\.uk/[^"]+\.csv)"',page,re.I)
    if not link:
        raise ValueError("Current register missing")
    with urlopen(link[1],timeout=45) as response:
        rows=csv.DictReader(io.TextIOWrapper(response,encoding="utf-8-sig"))
        return {normal(row["Organisation Name"]):row["Organisation Name"] for row in rows
                if row["Route"].strip()=="Skilled Worker" and "A rating" in row["Type & Rating"]}

def match(company,names,aliases):
    base=aliases.get(company,company)
    candidates=[names[key] for key in {normal(base), normal(base+" Ltd")} if key in names]
    return candidates[0] if len(candidates)==1 else None
