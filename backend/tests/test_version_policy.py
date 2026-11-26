"""Policy and lifecycle tests shared verbatim with the SQLAlchemy service layer.

Uses plain attribute objects instead of ORM rows so the exact guards in
src/domain/version_policy.py and the recompute orchestration are covered with
the standard library alone.
"""

import unittest
from dataclasses import dataclass, field

from src.domain.version_compare import Gate, compare_versions
from src.domain.version_policy import (
    assert_signoff_allowed,
    build_snapshot,
    derive_batch_status,
    validate_withdrawal_request,
)
from src.domain.version_reporting import comparison_tables

from test_version_domain import build_dataset, normalize_rows


@dataclass
class FakeBatch:
    id: int
    label: str
    status: str
    result: dict | None
    error: str | None = None
    withdrawn_ids: list = field(default_factory=list)
    gate: dict | None = None
    holdout_fraction: float = 0.25
    superseded_by: int | None = None
    recompute_reason: str | None = None


@dataclass
class FakeSignoff:
    id: int
    batch_id: int
    status: str = 'effective'
    superseded_at: str | None = None
    recommendation_snapshot: dict | None = None


class SignoffPolicyTests(unittest.TestCase):

    def setUp(self):
        self.rows = normalize_rows(build_dataset())
        self.result = compare_versions(self.rows, 0.25)
        self.assertEqual(self.result['recommendation'], 'switch')

    def test_switch_batch_can_be_signed_once(self):
        batch = FakeBatch(1, '首发', derive_batch_status(self.result), self.result,
                          gate=self.result['gate'])
        assert_signoff_allowed(batch, None)  # does not raise
        snapshot = build_snapshot(batch, '2026-11-26T00:00:00+00:00')
        self.assertEqual(snapshot['recommendation'], 'switch')
        self.assertLess(snapshot['brier_candidate'], snapshot['brier_current'])
        existing = FakeSignoff(1, 1)
        with self.assertRaisesRegex(PermissionError, '只能签发一次'):
            assert_signoff_allowed(batch, existing)

    def test_insufficient_evidence_cannot_be_signed(self):
        gate = Gate(**self.result['gate'])
        weak = compare_versions(self.rows, 0.25,
                                withdrawn_ids=[r['id'] for r in self.rows][:14], gate=gate)
        batch = FakeBatch(2, '证据不足', derive_batch_status(weak), weak, gate=weak['gate'])
        self.assertEqual(batch.status, 'insufficient')
        with self.assertRaisesRegex(PermissionError, '仅有效批次可签发'):
            assert_signoff_allowed(batch, None)

    def test_recalculating_blocks_signoff(self):
        batch = FakeBatch(3, '复算中', 'recalculating', self.result, gate=self.result['gate'])
        with self.assertRaisesRegex(PermissionError, '复算期间暂停换版'):
            assert_signoff_allowed(batch, None)

    def test_withdrawn_result_never_signed_even_if_status_active(self):
        # Simulates the concurrent race: status still active but labels withdrawn.
        batch = FakeBatch(4, '并发撤回', 'active', self.result,
                          withdrawn_ids=[self.rows[0]['id']], gate=self.result['gate'])
        with self.assertRaisesRegex(PermissionError, '引用了已撤回'):
            assert_signoff_allowed(batch, None)

    def test_keep_recommendation_cannot_be_signed_for_switch(self):
        tied = [dict(r, raw_score_candidate=r['raw_score_current']) for r in self.rows]
        keep_result = compare_versions(tied, 0.25)
        batch = FakeBatch(5, '维持现版', derive_batch_status(keep_result), keep_result,
                          gate=keep_result['gate'])
        with self.assertRaisesRegex(PermissionError, '严格更低'):
            assert_signoff_allowed(batch, None)


class WithdrawalLifecycleTests(unittest.TestCase):

    def setUp(self):
        self.rows = normalize_rows(build_dataset())
        self.first = compare_versions(self.rows, 0.25)
        self.known = {row['id'] for row in self.rows}
        self.batch = FakeBatch(1, '首发', derive_batch_status(self.first), self.first,
                               gate=self.first['gate'])
        self.signoff = FakeSignoff(1, 1)

    def _withdraw(self, ids):
        withdrawn_ids, requested = validate_withdrawal_request(self.batch, ids, self.known)
        # Mirror services/versioning.withdraw_labels orchestration.
        self.batch.status = 'recalculating'
        result = compare_versions(self.rows, self.batch.holdout_fraction,
                                  withdrawn_ids=withdrawn_ids, gate=Gate(**self.batch.gate))
        self.batch.status = 'invalidated'
        replacement = FakeBatch(
            2, '首发 · 标签撤回复算', derive_batch_status(result), result,
            withdrawn_ids=withdrawn_ids, gate=self.batch.gate,
            recompute_reason=f'撤回片段 {", ".join(requested)} 的人工标签，依据规则对换版结果失效重算。',
        )
        self.batch.superseded_by = replacement.id
        if self.signoff.status == 'effective':
            self.signoff.status = 'superseded'
            self.signoff.superseded_at = '2026-11-26T01:00:00+00:00'
        return result, replacement

    def test_withdrawal_invalidates_and_recomputes_while_preserving_history(self):
        result, replacement = self._withdraw([self.rows[0]['id']])
        self.assertEqual(self.batch.status, 'invalidated')
        self.assertEqual(self.batch.superseded_by, replacement.id)
        self.assertEqual(self.signoff.status, 'superseded')
        self.assertIsNotNone(self.signoff.superseded_at)
        # Original sign-off record still exists and is readable, only marked superseded.
        self.assertEqual(self.signoff.id, 1)
        self.assertTrue(replacement.result['sufficient_evidence'])

    def test_heavy_withdrawal_lands_on_insufficient_evidence(self):
        result, replacement = self._withdraw([r['id'] for r in self.rows][:14])
        self.assertEqual(replacement.status, 'insufficient')
        self.assertEqual(result['recommendation'], 'insufficient_evidence')
        with self.assertRaisesRegex(PermissionError, '仅有效批次可签发'):
            assert_signoff_allowed(replacement, None)

    def test_unknown_or_duplicate_withdrawal_rejected(self):
        with self.assertRaisesRegex(ValueError, '不在来源台账'):
            validate_withdrawal_request(self.batch, ['NOPE-9'], self.known)
        self.batch.withdrawn_ids = [self.rows[0]['id']]
        with self.assertRaisesRegex(ValueError, '此前已撤回'):
            validate_withdrawal_request(self.batch, [self.rows[0]['id']], self.known)

    def test_recomputed_gate_equals_original_thresholds(self):
        _, replacement = self._withdraw([self.rows[1]['id']])
        self.assertEqual(replacement.gate['min_train'], self.first['gate']['min_train'])
        self.assertEqual(replacement.gate['first_train_count'],
                         self.first['gate']['first_train_count'])


class ReportingTests(unittest.TestCase):

    def test_comparison_tables_have_stable_columns_and_rows(self):
        rows = normalize_rows(build_dataset())
        result = compare_versions(rows, 0.25)
        tables = comparison_tables(result)
        keys = {table['key'] for table in tables}
        self.assertEqual(keys, {'version_metrics', 'version_holdout'})
        metrics = next(t for t in tables if t['key'] == 'version_metrics')
        holdout = next(t for t in tables if t['key'] == 'version_holdout')
        self.assertEqual(len(metrics['rows']), 2)
        self.assertEqual(len(holdout['rows']), result['holdout_count'])
        self.assertEqual(metrics['columns'], list(metrics['labels']))
        self.assertEqual(holdout['columns'], list(holdout['labels']))


if __name__ == '__main__':
    unittest.main()
