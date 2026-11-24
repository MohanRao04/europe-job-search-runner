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

if __name__=="__main__":
    unittest.main()
