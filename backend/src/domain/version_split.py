"""Chronological holdout that keeps every record group in one partition.

A record group is all clips sharing the same ``record_ref``. The cutoff day is
chosen from the clip-level date ordering as in the single-version workflow, but
the assignment afterwards is group based: any group containing a clip scored on
or after the cutoff day moves to the holdout as a whole. Groups that stay in the
training period still lose clips whose human confirmation arrives on or after
the cutoff day; those are counted separately as late confirmations.
"""

from dataclasses import dataclass
from decimal import Decimal
from math import ceil

from .version_records import MIN_HOLDOUT


@dataclass(frozen=True)
class VersionSplit:
    train: list[dict]
    holdout: list[dict]
    pending: list[dict]
    cutoff: str
    late_confirmations: int
    holdout_groups: int
    train_groups: int


def split_version_records(rows, fraction):
    history = sorted(
        (row for row in rows if row['needs_relisten'] is not None),
        key=lambda row: (row['scored_on'], row['id']),
    )
    desired_holdout = max(MIN_HOLDOUT, ceil(len(history) * Decimal(str(fraction))))
    cutoff = history[-desired_holdout]['scored_on']

    groups: dict[str, list[dict]] = {}
    for row in history:
        groups.setdefault(row['record_ref'], []).append(row)

    train, holdout = [], []
    late_confirmations = 0
    holdout_refs = set()
    for record_ref, clips in groups.items():
        if any(row['scored_on'] >= cutoff for row in clips):
            holdout_refs.add(record_ref)
            holdout.extend(clips)
    for record_ref, clips in groups.items():
        if record_ref in holdout_refs:
            continue
        for row in clips:
            if row['confirmed_on'] < cutoff:
                train.append(row)
            else:
                late_confirmations += 1

    train.sort(key=lambda row: (row['scored_on'], row['id']))
    holdout.sort(key=lambda row: (row['scored_on'], row['id']))
    pending = [row for row in rows if row['needs_relisten'] is None]

    _structural_gate(cutoff, train, holdout, holdout_refs, groups)
    return VersionSplit(
        train=train,
        holdout=holdout,
        pending=pending,
        cutoff=cutoff,
        late_confirmations=late_confirmations,
        holdout_groups=len(holdout_refs),
        train_groups=len(groups) - len(holdout_refs),
    )


def _structural_gate(cutoff, train, holdout, holdout_refs, groups):
    """Feasibility only. Business minimums are enforced as evidence gates later
    so label withdrawals that shrink the sample produce 'insufficient evidence'
    rather than a hard failure."""
    if not train or not holdout:
        raise ValueError(
            f'{cutoff} 按档号整体切分后训练 {len(train)} 条、留出 {len(holdout)} 条，'
            '至少各需 1 条才能比较两版；请调小留出比例或补充确认记录。'
        )
    if not holdout_refs or len(groups) - len(holdout_refs) < 1:
        raise ValueError('按档号切分后训练档号或留出档号为空，无法完成同批留出比较。')
