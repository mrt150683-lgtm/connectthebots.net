"""Current goal reminders, never permission to start or resume work.

The caller owns the check-in transaction. Only metadata about delivered own
finding states is written; no lease, reservation, task or grant is created.
Suggestion matches use a closed exact-word category menu or the charter's
explicit joining interests, exclusively from nonpolitical share-tier answers.
"""
from datetime import timedelta
import json

from . import agent_texts
from .agent_common import AgentError, normalize_item, require_owner
from .goal_findings import finding_states, public_entry_content
from .goals import INTEREST_TOPICS
from .sessions import storage_time


FINDING_LIMIT = 50
CATEGORY_INTERESTS = {
    'mathematics': ('mathematics', 'maths', 'math'),
    'physics': ('physics',),
    'computing': ('computing', 'computer science'),
    'open_software': ('open software', 'open source software', 'programming', 'coding'),
    'climate_environment': ('climate', 'environment', 'climate data', 'environment data'),
    'education': ('education', 'teaching'),
    'history': ('history',),
    'engineering': ('engineering',),
    'health_medicine': ('health research', 'medical research'),
    'mental_health': ('mental health research',),
    'animal_health': ('animal health research',),
    'nutrition': ('nutrition research',),
    'charity_money': ('charity software', 'charity research'),
    'law_policy': ('law research', 'policy research'),
}


def empty_block():
    return {'enabled': False, 'due': [], 'your_findings': [], 'lead': [],
            'suggested': [], 'finding_changes_more': False}


def export_owner(connection, user_id):
    return {'finding_status_checkpoints': [dict(row) for row in connection.execute(
        'SELECT entry_id,visibility,finding_status,public,delivered_at FROM goal_checkin_status '
        'WHERE user_id=? ORDER BY entry_id', (user_id,))]}


def quarantine_checkpoints(connection):
    """A restored delivery checkpoint is not evidence a new update was seen."""
    connection.execute('DELETE FROM goal_checkin_status')


class GoalCheckinService:
    def __init__(self, database, clock, goals, members, work):
        self.database, self.clock, self.goals = database, clock, goals
        self.members, self.work = members, work

    def _item(self, connection, goal, user_id):
        title = 'Goal content unavailable'
        if goal['charter_json'] is not None and self.goals.charter_readable(connection, goal, user_id):
            title = json.loads(goal['charter_json'])['title']
        item = {'goal': goal['slug'], 'title': title, 'page': '/goals/' + goal['slug']}
        if goal['tier'] == 'sensitive':
            item['banner'] = agent_texts.GOAL_SENSITIVE_BANNER
        return item

    @staticmethod
    def _open(goal, config, now):
        return (goal['state'] == 'open' and goal['approved_at'] is not None and
                goal['lead_user_id'] is not None and goal['last_lead_step_at'] is not None and
                goal['last_lead_step_at'] > storage_time(now - timedelta(days=config['goal_lead_inactive_days'])))

    def _member(self, connection, goal, user_id, config):
        member = connection.execute('SELECT * FROM goal_members WHERE goal_id=? AND user_id=? AND state=\'active\'',
                                    (goal['id'], user_id)).fetchone()
        if member is None:
            return None
        charter = json.loads(goal['charter_json'])
        if not self.members._eligibility(connection, user_id, charter, json.loads(member['capabilities_json']), config)['eligible']:
            return None
        return member

    def _permission(self, connection, goal, user_id, member, now):
        try:
            return self.work._current_permission(connection, user_id, goal, member, now)
        except AgentError:
            return None

    def _due(self, connection, user_id, goals, config, now):
        stamp = storage_time(now)
        # Expiry is calculated without changing an old lease or grace marker.
        occupied = connection.execute(
            "SELECT 1 FROM goal_sessions WHERE user_id=? AND "
            "((end_reason IS NULL AND grace_ends_at>?) OR (end_reason='expired' AND grace_ends_at>?)) LIMIT 1",
            (user_id, stamp, stamp)).fetchone()
        if occupied:
            return []
        reservations = connection.execute(
            'SELECT goal_id,reserved_minutes,minutes_used FROM goal_sessions WHERE user_id=? AND started_at>?',
            (user_id, storage_time(now - timedelta(days=7)))).fetchall()
        total = sum(row['reserved_minutes'] for row in reservations)
        due = []
        for goal in goals:
            if not self._open(goal, config, now) or not self.goals.charter_public(connection, goal):
                continue
            member = self._member(connection, goal, user_id, config)
            if member is None:
                continue
            allowance = self._permission(connection, goal, user_id, member, now)
            if allowance is None:
                continue
            own = [row for row in reservations if row['goal_id'] == goal['id']]
            left = allowance['sessions_per_week'] - len(own)
            if left <= 0 or total + allowance['minutes_per_session'] > config['goal_weekly_minutes_total_max']:
                continue
            capabilities = set(json.loads(member['capabilities_json']))
            available = any(view is not None and capabilities.intersection(view['capabilities'])
                for task in connection.execute("SELECT * FROM goal_tasks WHERE goal_id=? AND state='open' AND task_json IS NOT NULL", (goal['id'],))
                for view in (self.goals.task_projection(connection, goal, task),))
            if not available:
                continue
            due.append({**self._item(connection, goal, user_id), 'handle': member['handle'],
                        'sessions_left': left, 'minutes_per_session': allowance['minutes_per_session'],
                        'reserved_minutes_7d': sum(row['reserved_minutes'] for row in own),
                        'reported_minutes_7d': sum(row['minutes_used'] or 0 for row in own),
                        'until': allowance['until'], 'start': 'POST /api/v1/goals/' + goal['slug'] + '/sessions'})
        return due

    def _changes(self, connection, user_id, goals, now):
        output, more = [], False
        for goal in goals:
            states, memo = finding_states(connection, goal['id']), {}
            for entry in connection.execute("SELECT * FROM goal_entries WHERE goal_id=? AND author_user_id=? AND kind='finding' ORDER BY seq,id",
                                            (goal['id'], user_id)):
                derived = states.get(entry['id'], entry['status'])
                published = bool(public_entry_content(connection, entry, states, memo=memo))
                old = connection.execute('SELECT visibility,finding_status,public FROM goal_checkin_status WHERE user_id=? AND entry_id=?',
                                         (user_id, entry['id'])).fetchone()
                current = (entry['status'], derived, int(published))
                if old is not None and tuple(old) == current:
                    continue
                if len(output) >= FINDING_LIMIT:
                    more = True
                    continue
                output.append({**self._item(connection, goal, user_id), 'entry_id': entry['id'],
                               'status': entry['status'], 'finding_status': derived, 'public': published,
                               'record': '/goals/' + goal['slug'] + '/record.jsonl'})
                connection.execute('INSERT INTO goal_checkin_status(user_id,entry_id,visibility,finding_status,public,delivered_at) '
                    'VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,entry_id) DO UPDATE SET visibility=excluded.visibility,'
                    'finding_status=excluded.finding_status,public=excluded.public,delivered_at=excluded.delivered_at',
                    (user_id, entry['id'], *current, storage_time(now)))
        return output, more

    def _lead(self, connection, user_id, goals, config, now):
        output = []
        for goal in goals:
            if goal['lead_user_id'] != user_id or not self._open(goal, config, now) or not self.goals.charter_public(connection, goal):
                continue
            member = self._member(connection, goal, user_id, config)
            if member is None:
                continue
            summary = self.goals.latest_visible_summary(connection, goal)
            summary_since = summary['created_at'] if summary else goal['approved_at']
            summary_due = summary_since <= storage_time(now - timedelta(days=7))
            stale = []
            for task in connection.execute("SELECT * FROM goal_tasks WHERE goal_id=? AND state='open' AND task_json IS NOT NULL ORDER BY created_at,id", (goal['id'],)):
                view = self.goals.task_projection(connection, goal, task)
                if view is None:
                    continue
                version = connection.execute("SELECT id,created_at FROM goal_entries WHERE goal_id=? AND kind='step' "
                    "AND json_extract(content_json,'$.task_id')=? AND json_type(content_json,'$.task')='object' ORDER BY seq DESC LIMIT 1",
                    (goal['id'], task['id'])).fetchone()
                if version is None:
                    continue
                last = connection.execute('SELECT MAX(e.created_at) FROM goal_findings f JOIN goal_entries e ON e.id=f.entry_id '
                                          'WHERE f.task_id=? AND f.task_entry_id=?', (task['id'], version['id'])).fetchone()[0]
                if max(version['created_at'], last or version['created_at']) <= storage_time(now - timedelta(days=14)):
                    stale.append({'id':task['id'],'title':view['title'],'version_created_at':version['created_at'],'last_finding_at':last})
            if summary_due or stale:
                output.append({**self._item(connection, goal, user_id), 'summary_due':summary_due, 'stale_tasks':stale,
                               'work_permission_current':self._permission(connection, goal, user_id, member, now) is not None})
        return output

    def _suggested(self, connection, user_id, config, now):
        if connection.execute('SELECT 1 FROM goal_allocations WHERE user_id=? LIMIT 1', (user_id,)).fetchone():
            return []
        placeholders = ','.join('?' for _ in INTEREST_TOPICS)
        answers = {row['topic']:{normalize_item(item) for item in json.loads(row['items_json'])}
                   for row in connection.execute("SELECT topic,items_json FROM taste_answers WHERE user_id=? AND tier='share' AND skipped=0 AND topic IN ("+placeholders+')', (user_id,*INTEREST_TOPICS))}
        shared = set().union(*answers.values()) if answers else set()
        if not shared:
            return []
        for goal in connection.execute("SELECT * FROM goals WHERE state='open' AND approved_at IS NOT NULL ORDER BY slug"):
            if not self._open(goal, config, now) or goal['review_pending'] or not self.goals.charter_public(connection, goal):
                continue
            charter = json.loads(goal['charter_json'])
            explicit = any(normalize_item(item['item']) in answers.get(item['topic'],set()) for item in charter['joining'].get('interests',()))
            category = bool(shared.intersection(CATEGORY_INTERESTS.get(goal['category'],())))
            if not (explicit or category):
                continue
            existing = connection.execute('SELECT capabilities_json,state FROM goal_members WHERE goal_id=? AND user_id=?', (goal['id'],user_id)).fetchone()
            if existing is not None and existing['state'] in ('left','removed'):
                continue
            capabilities = json.loads(existing['capabilities_json']) if existing is not None else []
            if not self.members._eligibility(connection,user_id,charter,capabilities,config)['eligible']:
                continue
            return [{**self._item(connection,goal,user_id),'category':goal['category'],
                     'why':'Matches an interest you chose to share.','eligibility':'GET /api/v1/goals/'+goal['slug']+'/eligibility'}]
        return []

    def project(self, connection, user_id, previous, now):
        if not connection.in_transaction:
            raise ValueError('Goal check-in needs the existing transaction.')
        require_owner(connection,user_id)
        try:
            config = self.goals._feature(connection,user_id)
            self.goals._not_held(connection)
        except AgentError as error:
            if error.status in (404,503):
                return empty_block()
            raise
        goals = connection.execute("SELECT g.* FROM goals g WHERE g.approved_at IS NOT NULL AND g.state IN ('open','paused','closed') "
            "AND (EXISTS(SELECT 1 FROM goal_members m WHERE m.goal_id=g.id AND m.user_id=?) OR "
            "EXISTS(SELECT 1 FROM goal_entries e WHERE e.goal_id=g.id AND e.author_user_id=?)) ORDER BY g.slug",(user_id,user_id)).fetchall()
        changes, more = self._changes(connection,user_id,goals,now)
        result = {'enabled':True,'due':self._due(connection,user_id,goals,config,now),'your_findings':changes,
                  'lead':self._lead(connection,user_id,goals,config,now),'suggested':self._suggested(connection,user_id,config,now),
                  'finding_changes_more':more}
        if any(item.get('banner') for key in ('due','your_findings','lead','suggested') for item in result[key]):
            result['banner'] = agent_texts.GOAL_SENSITIVE_BANNER
        return result
