"""M5 check-in boundaries with fictional owners and frozen-clock changes."""
import base64
from copy import deepcopy
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import unittest

from connectthebots_server import agent_texts
from connectthebots_server.goal_checkin import GoalCheckinService, empty_block
from connectthebots_server.goal_findings import GoalFindingService
from connectthebots_server.goal_moderation import GoalModerationService
from connectthebots_server.recovery_authority import local_state
from connectthebots_server.sessions import storage_time
from tests_server.test_goal_findings import payload, source
from tests_server.test_goal_sessions import SessionFixture
from tests_server.test_goals import fictional_charter


class GoalCheckinTests(SessionFixture):
    def setUp(self):
        super().setUp()
        self.checkins = GoalCheckinService(self.db,lambda:self.now,self.goals,self.members,self.sessions)
        self.findings = GoalFindingService(self.db,lambda:self.now,self.goals,self.sessions)
        self.moderation = GoalModerationService(self.db,lambda:self.now,self.goals)

    def project(self,user='other',previous=None):
        with self.db.transaction() as connection:
            return self.checkins.project(connection,user,previous,self.now)

    def raw_finding(self,user='other',**changes):
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection,self.slug)
            member = self.members.member(connection,goal,user)
            return self.goals._append(connection,goal,'finding',member['handle'],
                {'summary':'Fictional own finding canary','written_by':'assistant',**changes},self.now,author_user_id=user)

    def answer(self,items,tier='share',topic='hobbies',skipped=False,user='other'):
        with self.db.transaction() as connection:
            connection.execute('INSERT INTO taste_answers(user_id,topic,items_json,tier,skipped,updated_at) VALUES(?,?,?,?,?,?) '
                'ON CONFLICT(user_id,topic) DO UPDATE SET items_json=excluded.items_json,tier=excluded.tier,skipped=excluded.skipped',
                (user,topic,json.dumps(items),tier,int(skipped),storage_time(self.now)))

    def work_state(self):
        with self.db.connect() as connection:
            return {table:[tuple(row) for row in connection.execute('SELECT * FROM '+table+' ORDER BY 1,2')]
                    for table in ('goal_allocations','goal_sessions','goal_tasks','goals','recovery_authority')}

    def test_disabled_feature_access_and_recovery_return_empty_block(self):
        self.raw_finding()
        self.setting('feature.goals','off')
        self.assertEqual(self.project(),empty_block())
        self.setting('feature.goals','early_access')
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM early_access_grants WHERE user_id='other'")
        self.assertEqual(self.project(),empty_block())
        self.setting('feature.goals','everyone')
        with self.db.transaction() as connection:
            connection.execute('UPDATE recovery_state SET recovery_hold=1')
        self.assertEqual(self.project(),empty_block())
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM goal_checkin_status').fetchone()[0],0)

    def test_due_is_current_permission_without_any_work_mutation(self):
        self.approve()
        before = self.work_state()
        result = self.project()
        self.assertEqual(len(result['due']),1)
        item = result['due'][0]
        self.assertEqual((item['goal'],item['sessions_left'],item['minutes_per_session']), (self.slug,3,20))
        self.assertEqual((item['reserved_minutes_7d'],item['reported_minutes_7d']),(0,0))
        self.assertEqual(before,self.work_state())

    def test_stopped_expired_wrong_scope_or_notice_are_not_due(self):
        cases = [('stopped_at',storage_time(self.now)),('permission_expires_at',storage_time(self.now)),
                 ('charter_revision',2),('notice_version','outdated')]
        for field,value in cases:
            with self.subTest(field=field):
                self.approve()
                with self.db.transaction() as connection:
                    connection.execute('UPDATE goal_allocations SET '+field+'=? WHERE user_id=?',(value,'other'))
                self.assertEqual(self.project()['due'],[])
                with self.db.transaction() as connection:
                    connection.execute("DELETE FROM goal_allocations WHERE user_id='other'")

    def test_lead_needs_separate_current_t3l(self):
        self.approve('lead')
        self.assertEqual(len(self.project('lead')['due']),1)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_allocations SET lead_notice_version=NULL,lead_approved_at=NULL WHERE user_id='lead'")
        self.assertEqual(self.project('lead')['due'],[])

    def test_membership_joining_site_limits_and_no_matching_task_gate_due(self):
        self.approve()
        self.setting('goal_session_minutes_max',10)
        self.assertEqual(self.project()['due'],[])
        self.setting('goal_session_minutes_max',60)
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_members SET capabilities_json='[\"writing\"]' WHERE user_id='other'")
        self.assertEqual(self.project()['due'],[])
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_members SET capabilities_json='[\"mathematics\"]',state='left' WHERE user_id='other'")
        self.assertEqual(self.project()['due'],[])

    def test_active_window_and_expired_grace_occupy_slot_without_mutating_expiry(self):
        self.approve()
        work = self.begin()
        self.assertEqual(self.project()['due'],[])
        self.now += timedelta(minutes=20)
        before = self.work_state()
        self.assertEqual(self.project()['due'],[])
        self.assertEqual(before,self.work_state())
        self.now += timedelta(minutes=5)
        self.assertEqual(self.project()['due'][0]['sessions_left'],2)
        with self.db.connect() as connection:
            self.assertIsNone(connection.execute('SELECT end_reason FROM goal_sessions WHERE id=?',(work['session_id'],)).fetchone()[0])

    def test_zero_report_never_refunds_reservation_or_rolling_session_count(self):
        self.approve(sessions_per_week=1)
        work = self.begin()
        self.finish(work,minutes=0)
        self.assertEqual(self.project()['due'],[])
        self.now += timedelta(days=7)
        self.assertEqual(self.project()['due'][0]['sessions_left'],1)

    def test_cross_goal_reserved_minutes_and_exact_cutoff(self):
        self.setting('goals_led_per_member',2)
        second = self.open_goal(charter=fictional_charter('Fictional prism sequence proof'))
        self.members.join('other',second['slug'],{'capabilities':['mathematics'],'approved_by_owner':True})
        self.approve()
        self.approve(slug=second['slug'])
        work = self.begin(slug=second['slug'])
        self.finish(work,minutes=0)
        self.setting('goal_weekly_minutes_total_max',30)
        self.assertEqual(self.project()['due'],[])
        self.now += timedelta(days=7)
        self.assertEqual(len(self.project()['due']),2)

    def test_effective_inactivity_hidden_scope_and_paused_goals_offer_no_work(self):
        self.approve()
        self.now += timedelta(days=21)
        before = self.work_state()
        self.assertEqual(self.project()['due'],[])
        self.assertEqual(before,self.work_state())
        self.now -= timedelta(days=21)
        with self.db.connect() as connection:
            charter = connection.execute("SELECT id FROM goal_entries WHERE kind='charter'").fetchone()[0]
        self.moderation.action(charter,'hide','The fictional charter needs operator review.')
        result = self.project()
        self.assertEqual(result['due'],[])
        self.assertEqual(result['lead'],[])

    def test_suggestions_are_share_only_exact_matches_and_at_most_one(self):
        self.setting('goals_led_per_member',2)
        self.open_goal(charter=fictional_charter('Fictional second mathematics construction'))
        for tier in ('ask','never'):
            self.answer(['mathematics'],tier=tier)
            self.assertEqual(self.project()['suggested'],[])
        self.answer(['mathematics'],skipped=True)
        self.assertEqual(self.project()['suggested'],[])
        self.answer(['mathematics'])
        result = self.project()['suggested']
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['goal'],min(self.slug,'fictional-second-mathematics-construction'))
        self.assertEqual(result[0]['why'],'Matches an interest you chose to share.')
        self.answer(['I enjoy mathematics sometimes'])
        self.assertEqual(self.project()['suggested'],[])

    def test_suggestions_never_use_politics_and_stopped_history_suppresses_them(self):
        self.answer(['mathematics'],topic='politics')
        self.assertEqual(self.project()['suggested'],[])
        self.answer(['mathematics'])
        self.approve()
        self.sessions.stop('other',self.slug)
        self.assertEqual(self.project()['suggested'],[])

    def test_explicit_joining_interest_matches_but_ineligible_owner_is_not_suggested(self):
        self.setting('goals_led_per_member',2)
        charter = fictional_charter('Fictional precise construction',category='physics')
        charter['joining'] = {'interests':[{'topic':'books','item':'fictional constellations'}]}
        goal = self.open_goal(charter=charter)
        self.answer(['Fictional Constellations'],topic='books')
        self.assertEqual(self.project()['suggested'][0]['goal'],goal['slug'])
        with self.db.transaction() as connection:
            connection.execute("UPDATE users SET created_at=? WHERE id='other'",(storage_time(self.now),))
        self.assertEqual(self.project()['suggested'],[])

    def test_frozen_clock_new_checked_and_dependency_visibility_each_delivered_once(self):
        self.approve()
        own = self.begin()
        entry = self.findings.finding('other',own['session_id'],payload(own['task']['id']))['entry']
        self.assertEqual(self.project()['your_findings'][0]['finding_status'],'new')
        self.assertEqual(self.project()['your_findings'],[])
        self.approve('third')
        reviewer = self.begin('third')
        self.findings.review('third',reviewer['session_id'],{'finding_id':entry['id'],'verdict':'supports','checked':['method'],
            'note':'The fictional exact method was independently repeated.'})
        checked = self.project()['your_findings'][0]
        self.assertEqual((checked['finding_status'],checked['public']),('checked',True))
        self.assertNotIn('summary',checked)
        with self.db.connect() as connection:
            task_source = connection.execute('SELECT task_entry_id FROM goal_findings WHERE entry_id=?',(entry['id'],)).fetchone()[0]
        self.moderation.action(task_source,'hide','The fictional task source needs operator review.')
        hidden = self.project()['your_findings'][0]
        self.assertEqual((hidden['status'],hidden['finding_status'],hidden['public']),('visible','checked',False))
        self.assertEqual(self.project()['your_findings'],[])
        self.moderation.action(task_source,'unhide','The operator restored the retained fictional source.')
        self.assertTrue(self.project()['your_findings'][0]['public'])

    def test_own_sensitive_hold_release_and_withdrawal_include_exact_notice_without_prose(self):
        self.setting('goals_led_per_member',2)
        self.setting('goals_sensitive_open_max',1)
        sensitive = self.open_goal(charter=fictional_charter('Fictional study comparison',category='animal_health'))
        self.members.join('other',sensitive['slug'],{'capabilities':['mathematics'],'approved_by_owner':True})
        self.approve(slug=sensitive['slug'])
        own = self.begin(slug=sensitive['slug'])
        held = self.findings.finding('other',own['session_id'],payload(own['task']['id'],
            summary='The fictional cited study reports 5 mg/kg.',sources=[source()]))['entry']
        result = self.project()
        self.assertEqual(result['banner'],agent_texts.GOAL_SENSITIVE_BANNER)
        self.assertEqual(result['your_findings'][0]['finding_status'],'held')
        self.assertNotIn('5 mg/kg',json.dumps(result))
        self.moderation.action(held['id'],'release','The operator reviewed this fictional study quotation.')
        self.assertEqual(self.project()['your_findings'][0]['finding_status'],'pending_peer_check')
        self.goals.withdrawal('other',held['id'])
        self.assertEqual(self.project()['your_findings'][0]['finding_status'],'withdrawn')

    def test_hidden_other_authors_charter_title_is_not_copied_to_owner_checkin(self):
        self.raw_finding()
        self.project()
        with self.db.connect() as connection:
            entry = connection.execute("SELECT id FROM goal_entries WHERE kind='charter'").fetchone()[0]
        self.moderation.action(entry,'hide','The operator hid the fictional scope for review.')
        # A generic fixture finding has no charter dependency; force its own
        # status change to exercise title projection without copying prose.
        with self.db.transaction() as connection:
            connection.execute("UPDATE goal_entries SET status='held' WHERE author_user_id='other' AND kind='finding'")
        update = self.project()['your_findings'][0]
        self.assertEqual(update['title'],'Goal content unavailable')

    def test_capped_changes_checkpoint_only_delivered_items_and_no_remote_authority(self):
        for number in range(55):
            self.raw_finding(summary='Fictional finding number '+str(number))
        before = self.work_state()
        first, second, third = self.project(),self.project(),self.project()
        self.assertEqual((len(first['your_findings']),len(second['your_findings']),third['your_findings']),(50,5,[]))
        self.assertTrue(first['finding_changes_more'])
        self.assertFalse(second['finding_changes_more'])
        self.assertEqual(before,self.work_state())
        self.assertEqual(len({item['entry_id'] for item in first['your_findings']+second['your_findings']}),55)

    def test_lead_summary_week_and_task_version_age_are_distinct_from_work_permission(self):
        self.now += timedelta(days=14)
        result = self.project('lead')['lead'][0]
        self.assertTrue(result['summary_due'])
        self.assertEqual(len(result['stale_tasks']),1)
        self.assertFalse(result['work_permission_current'])
        self.approve('lead')
        self.assertTrue(self.project('lead')['lead'][0]['work_permission_current'])
        # A recent version of the same task resets its stale reminder.
        with self.db.transaction() as connection:
            goal = self.goals._goal(connection,self.slug)
            task = connection.execute('SELECT * FROM goal_tasks WHERE goal_id=?',(goal['id'],)).fetchone()
            self.goals._append(connection,goal,'step',goal['lead_handle'],{'action':'task_changed','task_id':task['id'],
                'task':json.loads(task['task_json']),'reason':'A fictional version refreshed the problem.'},self.now,author_user_id='lead')
        self.assertEqual(self.project('lead')['lead'][0]['stale_tasks'],[])

    def test_owner_export_cache_is_metadata_only_and_deletion_cascades(self):
        self.raw_finding()
        self.project()
        exported = self.goals.export_owner('other')['finding_status_checkpoints']
        self.assertEqual(len(exported),1)
        self.assertNotIn('canary',json.dumps(exported))
        self.assertEqual(self.goals.export_owner('third')['finding_status_checkpoints'],[])
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM users WHERE id='other'")
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM goal_checkin_status').fetchone()[0],0)

    @unittest.skipUnless(os.name == 'posix' and importlib.util.find_spec('cryptography'), 'restore requires Ubuntu file permissions and cryptography')
    def test_real_current_snapshot_restore_clears_delivery_cache(self):
        from connectthebots_server.config import Settings
        from connectthebots_server.recovery import RecoveryService
        self.raw_finding()
        self.project()
        settings = Settings.from_env({'APP_ENV':'staging','PUBLIC_BASE_URL':'https://fictional.example.test',
            'DATABASE_PATH':str(self.db.path),'SESSION_SECRET':'fictional-only-'+'x'*40,
            'BACKUP_DIRECTORY':str(Path(self.temp.name)/'backups'),
            'BACKUP_ENCRYPTION_KEY':base64.urlsafe_b64encode(b'k'*32).decode()})
        service = RecoveryService(self.db,lambda:self.now,settings)
        saved = service.backup()
        service.restore(saved['path'])
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM goal_checkin_status').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT recovery_hold FROM recovery_state').fetchone()[0],1)


SERVER = all(importlib.util.find_spec(name) for name in ('starlette','httpx','jwt','jinja2'))
@unittest.skipUnless(SERVER,'optional server extra not installed')
class GoalCheckinApiTests(unittest.TestCase):
    def setUp(self):
        from tests_server.test_agent_growth import AgentApiTests
        self.case = AgentApiTests('runTest')
        self.addCleanup(self.case.doCleanups)
        self.case.setUp()
        self.app,self.db = self.case.app,self.case.db
        self.clock = lambda:self.case.now
        self.service = GoalCheckinService(self.db,self.clock,self.app.state.goals,self.app.state.goal_members,self.app.state.goal_work)

    def test_existing_checkin_payload_is_identical_except_new_goals_block(self):
        from connectthebots_server.agent_api import AgentApi
        from connectthebots_server.db import Database
        api = self.app.state.agents
        api.me('ama')  # Seed random own identifiers before cloning the snapshot.
        clone = Database(Path(self.case.temp.name)/'comparison.db',self.clock)
        clone.migrate()
        with self.db.connect() as source_connection,clone.connect() as destination:
            source_connection.backup(destination)
        legacy = AgentApi(clone,self.clock,self.app.state.settings,self.app.state.taste,self.app.state.referrals,
                          self.app.state.topic_boards,self.app.state.interactions)
        before = legacy.checkin('ama')
        api.goal_checkin = self.service
        after = api.checkin('ama')
        self.assertEqual({key:value for key,value in after.items() if key != 'goals'},
                         {key:value for key,value in before.items() if key != 'goals'})
        self.assertEqual(after['goals'],empty_block())

    def test_http_checkin_replay_is_generic_and_does_not_renew_work(self):
        self.app.state.agents.goal_checkin = self.service
        first = self.case.call('ama','POST','/api/v1/agent/checkin',{},**{'Idempotency-Key':'fictional-m5-checkin'})
        self.assertEqual(first.status_code,200,first.text)
        repeated = self.case.call('ama','POST','/api/v1/agent/checkin',{},**{'Idempotency-Key':'fictional-m5-checkin'})
        self.assertEqual(repeated.status_code,200,repeated.text)
        self.assertTrue(repeated.json()['already_applied'])
        self.assertNotIn('goals',repeated.json())
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT checkin_count FROM interview_state WHERE user_id='ama'").fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM goal_sessions').fetchone()[0],0)
            self.assertEqual(connection.execute('SELECT response_json FROM agent_receipts').fetchone()[0],'{}')
