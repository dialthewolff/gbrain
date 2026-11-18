import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

FILE = Path(__file__).resolve().parents[1] / 'scripts' / 'memory_quality_gate.py'
spec = importlib.util.spec_from_file_location('memory_quality_gate', FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NightlyTests(unittest.TestCase):
    def test_failed_nightly(self):
        r = m.parse_nightly('2026-11-15T02:16:19-0700 FAILED failed_steps=legal_tripwire:1 gbrain_scope_check:1 legacy_skills_check:1 lint_errors=35 blocked=0 index_health=WARN')
        self.assertEqual(r['status'], 'fail')
        self.assertEqual(r['lint_errors'], 35)
        self.assertEqual(r['failed_step_count'], 3)

    def test_unknown_not_green(self):
        self.assertEqual(m.parse_nightly('Something unexpected')['status'], 'unknown')

    def test_success(self):
        self.assertEqual(m.parse_nightly('2026-11-01 SUCCESS failed_steps= lint_errors=0 index_health=OK')['status'], 'pass')


class GBrainTests(unittest.TestCase):
    TABLE = '[gbrain] status\n SOURCE LAG EMBED BACKFILL FAILS QUEUE PAGES LAST SYNC\n secondbrain 0s 73% idle 0 0 6,208 2026-11-15 09:00\n'

    def test_partial_coverage_not_green(self):
        r = m.parse_source_status(self.TABLE, source='secondbrain')
        self.assertEqual((r['status'], r['embed_percent'], r['pages']), ('attention', 73.0, 6208))

    def test_full_coverage(self):
        text = self.TABLE.replace('73%', '100%')
        self.assertEqual(m.parse_source_status(text, source='secondbrain')['status'], 'pass')

    def test_missing_source_not_green(self):
        self.assertEqual(m.parse_source_status(self.TABLE, source='wrong')['status'], 'unknown')

    def test_bad_numbers_fail_closed(self):
        text = self.TABLE.replace('6,208', 'not-a-number')
        self.assertEqual(m.parse_source_status(text, source='secondbrain')['status'], 'unknown')

    def test_failed_items_attention(self):
        text = self.TABLE.replace('73%', '100%').replace('idle 0 0', 'idle 2 0')
        self.assertEqual(m.parse_source_status(text, source='secondbrain')['status'], 'attention')


class GitTests(unittest.TestCase):
    def test_untracked_and_rename_with_nuls(self):
        p = ' M secret-client.txt\0?? note with newline\ninside.txt\0R  new.txt\0old.txt\0'
        r = m.parse_git_porcelain(p)
        self.assertEqual((r['modified_or_staged'], r['untracked'], r['renames']), (2, 1, 1))
        self.assertNotIn('secret-client', json.dumps(r))

    def test_empty(self):
        self.assertEqual(m.parse_git_porcelain('')['status'], 'pass')

    def test_malformed_rename(self):
        self.assertEqual(m.parse_git_porcelain('R  new.txt\0')['status'], 'unknown')


class ObservationTests(unittest.TestCase):
    def row(self, **kw):
        obj = dict(id='evt-1', scope_id='dev', project_id='sample', agent_id='test-agent',
                   event_type='decision', decision_key='renderer',
                   occurred_at='2026-11-17T21:00:00-07:00', rationale='Test decision only',
                   state='verified', evidence_refs=['repo://commit/a1'])
        obj.update(kw)
        return obj

    def test_valid(self):
        self.assertEqual(m.validate_observations([self.row()])['status'], 'pass')

    def test_missing_evidence_and_timezone(self):
        r = m.validate_observations([self.row(evidence_refs=[], occurred_at='2026-11-17')])
        self.assertIn('missing_evidence_refs', r['issue_counts'])
        self.assertIn('timestamp_without_timezone', r['issue_counts'])

    def test_duplicate_active_decision(self):
        r = m.validate_observations([self.row(), self.row(id='evt-2')])
        self.assertIn('multiple_verified_decisions_for_key', r['issue_counts'])

    def test_cross_scope_supersession_rejected(self):
        old = self.row(state='superseded')
        new = self.row(id='evt-2', scope_id='legal', supersedes='evt-1')
        self.assertIn('cross_scope_supersession', m.validate_observations([old, new])['issue_counts'])

    def test_verified_supersession_requires_old_retirement(self):
        old = self.row()
        new = self.row(id='evt-2', supersedes='evt-1')
        self.assertIn('superseded_record_still_verified', m.validate_observations([old, new])['issue_counts'])

    def test_cycle_rejected(self):
        a = self.row(id='evt-1', state='superseded', supersedes='evt-2')
        b = self.row(id='evt-2', supersedes='evt-1')
        self.assertIn('supersession_cycle', m.validate_observations([a, b])['issue_counts'])


class EvaluationTests(unittest.TestCase):
    def case(self):
        return {'id':'q-1', 'expected_note_ids':['note-1'], 'allowed_scopes':['dev']}

    def result(self):
        return {'id':'q-1', 'answer_state':'current', 'answer_evidence_refs':['repo://commit/a1'],
                'used_note_ids':['note-1'], 'hits':[{'note_id':'note-1','scope_id':'dev','state':'verified'}]}

    def test_pass(self):
        self.assertEqual(m.evaluate_retrieval([self.case()], [self.result()])['status'], 'pass')

    def test_missing_results_never_pass(self):
        self.assertEqual(m.evaluate_retrieval([self.case()], [])['status'], 'fail')

    def test_scope_leak(self):
        r = self.result()
        r['hits'][0]['scope_id'] = 'other'
        self.assertEqual(m.evaluate_retrieval([self.case()], [r])['violations']['unauthorized_hits'], 1)

    def test_stale_as_current(self):
        r = self.result()
        r['hits'][0]['state'] = 'superseded'
        self.assertEqual(m.evaluate_retrieval([self.case()], [r])['violations']['stale_as_current'], 1)

    def test_uncited_current_answer(self):
        r = self.result()
        r['answer_evidence_refs'] = []
        self.assertEqual(m.evaluate_retrieval([self.case()], [r])['violations']['uncited_current_answers'], 1)

    def test_malformed_result(self):
        r = self.result(); r.pop('used_note_ids')
        self.assertEqual(m.evaluate_retrieval([self.case()], [r])['violations']['missing_used_note_ids'], 1)


class IntegrationTests(unittest.TestCase):
    def test_frontmatter_preflight_uses_bounded_nonfixing_commands(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / 'sample.md').write_text('---\ntype: note\n---\nExample\n')
            responses = [
                {'returncode': 0, 'stdout': 'sample.md\0', 'stderr': ''},
                {'returncode': 0, 'stdout': '', 'stderr': ''},
                {'returncode': 0, 'stdout': '', 'stderr': ''},
            ]
            with mock.patch.object(m, 'run_readonly', side_effect=responses) as calls:
                report = m.check_changed_frontmatter(vault, timeout=5)
            self.assertEqual(report['status'], 'pass')
            self.assertEqual(report['markdown_files'], 1)
            self.assertEqual(calls.call_args_list[-1].args[0][:3], ['gbrain', 'frontmatter', 'validate'])
            self.assertNotIn('--fix', str(calls.call_args_list))

    def test_frontmatter_missing_command_not_green(self):
        with tempfile.TemporaryDirectory() as td:
            vault = Path(td)
            (vault / 'sample.md').write_text('test')
            responses = [
                {'returncode': 0, 'stdout': 'sample.md\0', 'stderr': ''},
                {'returncode': 0, 'stdout': '', 'stderr': ''},
                {'returncode': None, 'stdout': '', 'stderr': 'FileNotFoundError'},
            ]
            with mock.patch.object(m, 'run_readonly', side_effect=responses):
                self.assertEqual(m.check_changed_frontmatter(vault, timeout=5)['status'], 'unknown')

    def test_invalid_vault_does_not_execute_tools(self):
        with mock.patch.object(m, 'run_readonly') as call:
            report = m.audit(Path('/nonexistent/vault/path'), 'secondbrain', None, None, 1, 95)
        self.assertEqual(report['status'], 'unknown')
        call.assert_not_called()

    def test_main_validate_stdout_no_text_leak(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'observations.jsonl'
            path.write_text(json.dumps({'id':'test', 'scope_id':'test', 'project_id':'test',
                 'agent_id':'test', 'event_type':'lesson', 'occurred_at':'2026-11-17T21:00:00Z',
                 'rationale':'PRIVATE SAMPLE TOKEN', 'state':'verified',
                 'evidence_refs':['repo://commit/a1']}) + '\n')
            res = subprocess.run([sys.executable, str(FILE), 'validate', '--observations', str(path), '--json'],
                                 text=True, capture_output=True)
            self.assertEqual(res.returncode, 0)
            self.assertNotIn('PRIVATE SAMPLE TOKEN', res.stdout)

if __name__ == '__main__':
    unittest.main()
