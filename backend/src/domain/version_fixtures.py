"""Deterministic dual-score demo ledger for the denoiser switch workflow."""

from datetime import date, timedelta
from random import Random

FIRST_DAY = date(2026, 11, 2)
GROUPS = 10
CLIPS_PER_GROUP = 3


def demo_version_rows():
    """30 confirmed dual-score clips across 10 record groups, plus 4 pending.

    The candidate denoiser is built to track the human label more closely than
    the current one, so the seeded comparison normally recommends a switch on
    its shared holdout; the numbers remain deterministic for demos.
    """
    rng = Random(261126)
    rows = []
    clip_id = 0
    for group in range(GROUPS):
        start_day = group % 6
        for j in range(CLIPS_PER_GROUP):
            clip_id += 1
            day = start_day + j
            scored = (FIRST_DAY + timedelta(days=day)).isoformat()
            current = round(0.05 + ((group * 7 + j * 3) % 10) / 10, 2)
            label = int(rng.random() < (0.2 + 0.6 * current))
            candidate = round(min(0.98, max(0.02, current + (0.30 if label else -0.25))), 2)
            confirmed = (FIRST_DAY + timedelta(days=day + (group + j) % 2)).isoformat()
            rows.append(_row(clip_id, group, day, current, candidate, scored, confirmed, label))
    for k in range(4):
        clip_id += 1
        day = 9 + k
        scored = (FIRST_DAY + timedelta(days=day)).isoformat()
        current = round(0.1 + 0.15 * k, 2)
        candidate = round(min(0.98, current + 0.2), 2)
        rows.append(_row(clip_id, GROUPS + k, day, current, candidate, scored, None, None))
    return rows


def _row(clip_id, group, day, current, candidate, scored, confirmed, label):
    start = 30 + (group % 4) * 120 + day * 4
    return {
        'id': f'VNV-{clip_id:04d}',
        'record_ref': f'ZL-VR-{group:03d}',
        'side': 'A' if clip_id % 2 else 'B',
        'start_seconds': start,
        'end_seconds': start + 20,
        'scored_on': scored,
        'raw_score_current': current,
        'raw_score_candidate': candidate,
        'confirmed_on': confirmed,
        'needs_relisten': label,
    }
