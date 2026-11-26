"""Domain rule tests for the two-denoiser version comparison workflow.

Run with the standard library only:
    PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
"""

import unittest
from datetime import date, timedelta

from src.domain.calibration import fit_calibration
from src.domain.version_compare import (
    INSUFFICIENT, KEEP, SWITCH, Gate, compare_versions,
)
from src.domain.version_records import normalize_rows
from src.domain.version_split import split_version_records

BASE = date(2026, 11, 2)


def make_clip(rid, group, day, cur, label, cand=None, confirmed=None, pending=False):
    scored = (BASE + timedelta(days=day)).isoformat()
    if cand is None:
        cand = round(min(0.98, max(0.02, cur + (0.35 if label else -0.2))), 2)
    return {
        'id': f'VC-{rid:04d}', 'record_ref': f'ZL-V-{group:03d}',
        'side': 'A', 'start_seconds': 10, 'end_seconds': 30,
        'scored_on': scored,
        'raw_score_current': cur, 'raw_score_candidate': cand,
        'confirmed_on': None if pending else (confirmed or scored),
        'needs_relisten': None if pending else label,
    }


def build_dataset(groups=12, span=2):
    rows, rid = [], 0
    for group in range(groups):
        start_day = group % 6
        for j in range(span):
            rid += 1
            day = start_day + j
            cur = round(((group * 7 + j * 3) % 10) / 10 + 0.05, 2)
            label = 1 if (group + j) % 3 == 0 or (group % 4 == 0 and j > 0) else 0
            rows.append(make_clip(rid, group, day, cur, label))
    return rows


class VersionRecordsTests(unittest.TestCase):

    def _row(self, **overrides):
        row = make_clip(1, 0, 0, 0.4, 0)
        row.update(overrides)
        return row

    def test_requires_minimum_confirmed_and_two_days(self):
        rows = [make_clip(i, i % 2, 0, 0.3 + i * 0.01, i % 2) for i in range(11)]
        with self.assertRaisesRegex(ValueError, '至少需要 12 条'):
            normalize_rows(rows)
        rows.append(make_clip(12, 0, 0, 0.5, 1))
        with self.assertRaisesRegex(ValueError, '至少要覆盖两个不同打分日'):
            normalize_rows(rows)

    def test_pending_must_be_later_than_latest_confirmed(self):
        rows = build_dataset()
        rows.append(make_clip(99, 20, 1, 0.9, None, pending=True))
        with self.assertRaisesRegex(ValueError, '打分日必须晚于'):
            normalize_rows(rows)

    def test_dual_scores_independently_validated(self):
        row = self._row(raw_score_candidate=1.4)
        with self.assertRaisesRegex(ValueError, '新版原始风险分'):
            normalize_rows([row] + build_dataset()[1:])

    def test_duplicate_id_rejected(self):
        rows = build_dataset()
        rows[1] = {**rows[1], 'id': rows[0]['id']}
        with self.assertRaisesRegex(ValueError, '片段编号重复'):
            normalize_rows(rows)


class GroupSplitTests(unittest.TestCase):

    def test_record_ref_never_crosses_partitions(self):
        rows = normalize_rows(build_dataset())
        split = split_version_records(rows, 0.25)
        train_refs = {row['record_ref'] for row in split.train}
        holdout_refs = {row['record_ref'] for row in split.holdout}
        self.assertFalse(train_refs & holdout_refs)
        self.assertGreaterEqual(len(split.train), 8)
        self.assertGreaterEqual(len(split.holdout), 4)
        # A holdout group qualifies because at least one of its clips is scored
        # on/after the cutoff; the whole group then moves, including earlier clips.
        self.assertTrue(
            all(any(r['record_ref'] == ref and r['scored_on'] >= split.cutoff
                    for r in split.holdout) for ref in holdout_refs)
        )
        self.assertTrue(all(row['scored_on'] < split.cutoff for row in split.train))
        self.assertTrue(all(row['confirmed_on'] < split.cutoff for row in split.train))

    def test_late_confirmation_excluded_from_training(self):
        rows = build_dataset()
        split = split_version_records(normalize_rows(rows), 0.25)
        # Force one training-period clip to confirm on/after cutoff.
        target = next(row for row in rows if row['scored_on'] < split.cutoff)
        target['confirmed_on'] = split.cutoff
        split2 = split_version_records(normalize_rows(rows), 0.25)
        self.assertEqual(split2.late_confirmations, 1)
        self.assertNotIn(target['id'], [row['id'] for row in split2.train])

    def test_deterministic_cutoff_is_date_based(self):
        rows = normalize_rows(build_dataset())
        first = split_version_records(rows, 0.25)
        second = split_version_records(rows, 0.25)
        self.assertEqual(first.cutoff, second.cutoff)
        self.assertEqual([r['id'] for r in first.holdout], [r['id'] for r in second.holdout])

    def test_structural_failure_when_one_partition_empties(self):
        # One group spanning every day drags everything into the holdout.
        rows = build_dataset(groups=4, span=8)
        with self.assertRaisesRegex(ValueError, '训练档号或留出档号为空|训练 0 条'):
            split_version_records(normalize_rows(rows), 0.4)


class ComparisonTests(unittest.TestCase):

    def setUp(self):
        self.rows = normalize_rows(build_dataset())
        self.result = compare_versions(self.rows, 0.25)

    def test_switch_only_when_candidate_brier_strictly_lower(self):
        self.assertEqual(self.result['recommendation'], SWITCH)
        self.assertLess(
            self.result['versions']['candidate']['brier_calibrated'],
            self.result['versions']['current']['brier_calibrated'],
        )
        self.assertAlmostEqual(
            self.result['brier_delta_calibrated'],
            self.result['versions']['candidate']['brier_calibrated']
            - self.result['versions']['current']['brier_calibrated'],
        )

    def test_equal_brier_means_keep_not_switch(self):
        tied = [dict(row, raw_score_candidate=row['raw_score_current']) for row in self.rows]
        result = compare_versions(tied, 0.25)
        self.assertEqual(result['recommendation'], KEEP)
        self.assertEqual(result['brier_delta_calibrated'], 0.0)

    def test_both_versions_score_the_same_holdout_clips(self):
        ids_current = [row['id'] for row in self.result['holdout_rows']]
        split = split_version_records(self.rows, 0.25)
        self.assertEqual(ids_current, [row['id'] for row in split.holdout])
        for row in self.result['holdout_rows']:
            self.assertIsNotNone(row['probability_current'])
            self.assertIsNotNone(row['probability_candidate'])

    def test_pava_monotonic_for_each_version(self):
        split = split_version_records(self.rows, 0.25)
        for key in ('raw_score_current', 'raw_score_candidate'):
            model = fit_calibration((row[key], row['needs_relisten']) for row in split.train)
            probabilities = [model.predict(score) for score in sorted(model.scores)]
            self.assertEqual(probabilities, sorted(probabilities))

    def test_first_run_below_gate_raises_instead_of_recommending(self):
        small = normalize_rows(build_dataset(groups=6, span=2))  # 12 confirmed clips
        with self.assertRaisesRegex(ValueError, '低于原门槛'):
            compare_versions(small, 0.4)


class WithdrawalTests(unittest.TestCase):

    def setUp(self):
        self.rows = normalize_rows(build_dataset())
        self.first = compare_versions(self.rows, 0.25)
        self.gate = Gate(**self.first['gate'])

    def test_withdrawal_above_gate_marks_insufficient_evidence(self):
        withdrawn = [row['id'] for row in self.rows][:14]
        result = compare_versions(self.rows, 0.25, withdrawn_ids=withdrawn, gate=self.gate)
        self.assertEqual(result['recommendation'], INSUFFICIENT)
        self.assertFalse(result['sufficient_evidence'])
        self.assertTrue(any('低于原门槛' in w for w in result['warnings']))
        # No switch/keep numbers are asserted as a decision.
        self.assertIsNone(result['brier_delta_calibrated'])

    def test_light_withdrawal_still_decides_under_original_gate(self):
        result = compare_versions(
            self.rows, 0.25, withdrawn_ids=[self.rows[0]['id']], gate=self.gate,
        )
        self.assertTrue(result['sufficient_evidence'])
        self.assertIn(result['recommendation'], (SWITCH, KEEP))
        self.assertEqual(result['gate']['first_train_count'], self.gate.first_train_count)

    def test_withdrawn_clips_never_reappear_in_train_or_holdout(self):
        withdrawn = {self.rows[0]['id'], self.rows[5]['id']}
        result = compare_versions(self.rows, 0.25, withdrawn_ids=withdrawn, gate=self.gate)
        active_ids = {
            *(row['id'] for row in split_version_records(
                [r for r in self.rows if r['id'] not in withdrawn], 0.25).train),
            *(row['id'] for row in split_version_records(
                [r for r in self.rows if r['id'] not in withdrawn], 0.25).holdout),
        }
        self.assertFalse(withdrawn & active_ids)
        self.assertEqual(set(result['withdrawn_ids']), withdrawn)

    def test_recompute_keeps_original_gate_snapshot(self):
        result = compare_versions(
            self.rows, 0.25, withdrawn_ids=[self.rows[0]['id']], gate=self.gate,
        )
        self.assertEqual(result['gate']['min_train'], 8)
        self.assertEqual(result['gate']['min_holdout'], 4)
        self.assertEqual(result['gate']['first_train_count'], self.gate.first_train_count)


if __name__ == '__main__':
    unittest.main()
