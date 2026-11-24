import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import agent
from sponsors import match, normal

class SearchTests(unittest.TestCase):
    def setUp(self):
        self.job=dict(id="gh/example/1",company="Example",title="Data Engineer",location="London",
                      description="SQL Python. We offer visa sponsorship.",url="https://example.com/job",refresh="")

    def test_conflicting_evidence(self):
        self.job["description"]+=" However, we cannot sponsor overseas applicants."
        self.assertEqual(agent.assess(self.job,"SQL Python")[0],"work permission restriction")

    def test_repeated_and_failed_scan(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(agent,"ROOT",Path(temp)), patch.object(agent,"BOARDS",{"example":"Example"}), patch.object(agent,"licensed",return_value={}), patch.object(agent,"portal",return_value=[]), patch.object(agent,"ashby",return_value=[]), patch.object(agent,"greenhouse",side_effect=[[self.job],[self.job],TimeoutError(),[]]):
            db=agent.database()
            try:
                for _ in range(3):
                    agent.scan(db,["SQL Python"],{})
                row=db.execute("SELECT count(*),active,reason FROM jobs").fetchone()
                self.assertEqual(tuple(row),(1,1,"UK sponsor entity unconfirmed"))
                agent.scan(db,["SQL Python"],{})
                self.assertEqual(db.execute("SELECT active FROM jobs").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT count(*) FROM applications").fetchone()[0],0)
            finally:
                db.close()

    def test_draft_never_dispatched(self):
        with patch.object(agent,"fetch",side_effect=AssertionError("Unexpected dispatch")):
            agent.apply_if_supported(None,self.job,["Example [phone]"],{"application_ready":True})

    def test_name_matching(self):
        names={normal("Example Limited"):"Example Limited"}
        self.assertEqual(match("Example",names,{}),"Example Limited")
        self.assertIsNone(match("Example Co",names,{}))

    def test_mail_receipt_and_uncertain_delivery_are_never_replayed(self):
        for failure in (None,TimeoutError()):
            with self.subTest(failure=bool(failure)),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                pdf=root/"resume.pdf"
                pdf.write_bytes(b"test attachment")
                job=dict(self.job,id="gh/adyen/8",company="Adyen",location="Amsterdam")
                desc=job["description"]+" Applicants from India are welcome. Send your CV to jobs@adyen.com."
                listing=dict(content=desc,title=job["title"],location={"name":job["location"]})
                cfg=dict(application_ready=True,name="Example",applicant_email="candidate@example.org")
                with patch.object(agent,"ROOT",root),patch.object(agent,"fetch",return_value=listing),patch.object(agent,"package",return_value=(pdf,"Example letter")),patch.dict(agent.os.environ,{"SMTP_PASSWORD":"fixture","PERSIST_GIT":"0"}),patch.object(agent,"deliver",side_effect=failure) as send:
                    db=agent.database()
                    try:
                        for _ in range(2):
                            agent.apply_if_supported(db,job,["SQL Python"],cfg)
                        self.assertEqual(send.call_count,1)
                        row=db.execute("SELECT status FROM applications").fetchone()
                        self.assertEqual(row[0],"delivery uncertain" if failure else "submitted")
                    finally:
                        db.close()

if __name__=="__main__":
    unittest.main()
