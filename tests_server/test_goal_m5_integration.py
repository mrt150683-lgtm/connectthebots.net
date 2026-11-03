"""Approved notices and the independently runnable public verifier, fictional data."""
import html
from html.parser import HTMLParser
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from connectthebots_server import agent_texts
from tests_server import test_goals as cases
from tests_server.test_goal_verify import encoded, fixture

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT/'tools/verify_goal_record.py'
PACKAGED = ROOT/'src/connectthebots_server/goal_verifier.py'
SNAPSHOT = ROOT/'tests_server/fixtures/goal_approved_notices.md'


class NoticeParagraph(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active = False
        self.values = []

    def handle_starttag(self,tag,attrs):
        if tag == 'p' and dict(attrs).get('id') == 'common-goals-notice':
            self.active = True

    def handle_endtag(self,tag):
        if tag == 'p':
            self.active = False

    def handle_data(self,data):
        if self.active:
            self.values.append(data)


class StandaloneVerifierTests(unittest.TestCase):
    def test_packaged_verifier_has_no_source_drift(self):
        self.assertEqual(TOOL.read_bytes(),PACKAGED.read_bytes())

    def test_detached_script_runs_without_repository_or_site_packages_and_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            script, record = directory/'verify_goal_record.py', directory/'fictional.jsonl'
            script.write_bytes(PACKAGED.read_bytes())
            environment = {name:value for name,value in os.environ.items() if name not in ('PYTHONPATH','PYTHONHOME')}
            for tamper, code in ((False,0),(True,1)):
                entries = fixture()
                if tamper:
                    entries[1]['content'] = {'fictional changed wording':True}
                record.write_bytes(encoded(entries))
                process = subprocess.run([sys.executable,'-I','-S',str(script),str(record)],cwd=directory,
                    env=environment,capture_output=True,text=True,timeout=30)
                self.assertEqual(process.returncode,code,process.stderr+process.stdout)
                self.assertIn('BROKEN at entry 2' if tamper else 'Verified 3 entries',process.stdout)

    def test_exact_approved_constants_and_existing_permissions_are_unchanged(self):
        quotes = re.findall(r'^> (.+)$',SNAPSHOT.read_text(encoding='utf-8'),re.M)
        self.assertEqual(len(quotes),5)
        values = (agent_texts.GOAL_PRIVACY_NOTICE,agent_texts.GOAL_TERMS_NOTICE,
                  agent_texts.GOAL_CONTRIBUTION_STATEMENT,agent_texts.GOAL_LEAD_STATEMENT,
                  agent_texts.GOAL_SENSITIVE_BANNER)
        for actual,expected in zip(values,quotes):
            self.assertEqual(actual,expected.replace('**',''))
        self.assertEqual(agent_texts.GOAL_SAFETY_RULES,agent_texts.GOAL_SAFETY_GUIDE)
        self.assertEqual(agent_texts.GOAL_CONTRIBUTION_CONSENT_STATEMENT,agent_texts.GOAL_CONTRIBUTION_STATEMENT)


@unittest.skipUnless(cases.SERVER,'optional server extra not installed')
class GoalM5HttpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = cases.GoalHttpTests('runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.app,self.client,self.db = self.fixture.app,self.fixture.client,self.fixture.db

    def test_rendered_notices_are_exact_approved_t1_t2_even_while_feature_off(self):
        for path,expected in (('/privacy',agent_texts.GOAL_PRIVACY_NOTICE),('/terms',agent_texts.GOAL_TERMS_NOTICE)):
            response = self.client.get(path)
            self.assertEqual(response.status_code,200)
            parser = NoticeParagraph()
            parser.feed(response.text)
            self.assertEqual(''.join(parser.values),expected)
            self.assertEqual(response.text.count('id="common-goals-notice"'),1)

    def test_download_is_feature_gated_before_query_validation_and_is_exact_standalone_source(self):
        self.assertEqual(self.client.get('/goals/verify_goal_record.py?unexpected=1').status_code,404)
        self.fixture.enable()
        response = self.client.get('/goals/verify_goal_record.py')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.content,TOOL.read_bytes())
        self.assertIn('attachment; filename="verify_goal_record.py"',response.headers['content-disposition'])
        self.assertIn('text/x-python',response.headers['content-type'])
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertIn('no-store',response.headers['cache-control'])
        self.assertEqual(self.client.get('/goals/verify_goal_record.py?unexpected=1').status_code,422)
        self.assertNotIn('banner',self.client.get('/goals/verify_goal_record.py?unexpected=1').json())

    def test_checkin_service_is_injected_and_dashboard_public_navigation_is_gated(self):
        self.assertIs(self.app.state.agents.goal_checkin,self.app.state.goal_checkin)
        self.assertFalse(self.app.state.agents.checkin('lead')['goals']['enabled'])
        self.assertIsNone(self.app.state.agents.dashboard('lead')['goals_public_index'])
        self.fixture.enable()
        self.assertTrue(self.app.state.agents.checkin('lead')['goals']['enabled'])
        self.assertEqual(self.app.state.agents.dashboard('lead')['goals_public_index'],'/goals')
