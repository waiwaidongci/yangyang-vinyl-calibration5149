import hashlib
import json

from sqlalchemy import select

from src.constants.workflow import Role
from src.domain.version_compare import Gate, compare_versions
from src.domain.version_policy import (
    assert_signoff_allowed,
    build_snapshot,
    derive_batch_status,
    validate_withdrawal_request,
)
from src.domain.version_records import COLUMNS, normalize_rows
from src.domain.version_reporting import comparison_tables
from src.models.dataset import Dataset
from src.models.versioning import VersionBatch, VersionSignoff
from src.utils.csv_files import read_rows
from src.utils.time import utc_stamp
from .catalog import add_dataset, get_dataset

HOLDOUT_RANGE = (0.15, 0.40)


def validate_fraction(value):
    if type(value) not in (int, float) or not HOLDOUT_RANGE[0] <= value <= HOLDOUT_RANGE[1]:
        raise ValueError('按日期留出比例须在 0.15 至 0.40 之间')
    return float(value)


def import_version_dataset(session, name, csv_text, owner):
    rows = normalize_rows(read_rows(csv_text, COLUMNS))
    encoded = json.dumps(rows, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    dataset = add_dataset(session, name, rows, owner)
    session.commit()
    return {'id': dataset.id, 'name': dataset.name,
            'checksum': hashlib.sha256(encoded).hexdigest(), 'count': len(rows)}


def batch_info(batch, signoff=None):
    return {
        'id': batch.id,
        'label': batch.label,
        'dataset_id': batch.dataset_id,
        'created_at': batch.created_at,
        'status': batch.status,
        'holdout_fraction': batch.holdout_fraction,
        'withdrawn_ids': batch.withdrawn_ids,
        'superseded_by': batch.superseded_by,
        'recompute_reason': batch.recompute_reason,
        'recommendation': batch.result.get('recommendation') if batch.result else None,
        'signoff': _signoff_info(signoff) if signoff else None,
    }


def list_batches(session, actor):
    statement = (
        select(VersionBatch, VersionSignoff)
        .outerjoin(VersionSignoff, VersionBatch.id == VersionSignoff.batch_id)
        .order_by(VersionBatch.id.desc()).limit(100)
    )
    rows = session.execute(statement)
    if actor.role == Role.VIEWER:
        # Viewers only read results whose switch decision is still effective.
        rows = [(batch, signoff) for batch, signoff in rows
                if signoff is not None and signoff.status == 'effective']
    return [batch_info(batch, signoff) for batch, signoff in rows]


def get_batch(session, batch_id, actor=None):
    batch = session.get(VersionBatch, batch_id)
    if batch is None:
        raise LookupError('找不到换版比对批次')
    signoff = session.scalar(select(VersionSignoff).where(VersionSignoff.batch_id == batch_id))
    if actor is not None and actor.role == Role.VIEWER and (
        signoff is None or signoff.status != 'effective'
    ):
        raise LookupError('找不到可查看的换版比对结果')
    return batch, signoff


def create_batch(session, payload, actor):
    dataset = get_dataset(session, payload.dataset_id)
    fraction = validate_fraction(payload.holdout_fraction)
    try:
        result = compare_versions(dataset.rows, fraction)
        error = None
    except ValueError as failure:
        result, error = None, str(failure)
    batch = VersionBatch(
        label=payload.label, dataset_id=payload.dataset_id, created_by=actor.id,
        created_at=utc_stamp(), status=derive_batch_status(result), holdout_fraction=fraction,
        withdrawn_ids=[], gate=result['gate'] if result else None,
        result=result, error=error,
    )
    session.add(batch)
    session.commit()
    return batch_info(batch, None)


def withdraw_labels(session, batch_id, payload, actor):
    batch, signoff = get_batch(session, batch_id, actor)
    dataset = session.get(Dataset, batch.dataset_id)
    known_ids = {row['id'] for row in dataset.rows}
    withdrawn_ids, requested = validate_withdrawal_request(batch, payload.clip_ids, known_ids)

    # Phase 1: make the recalculation window durable and visible first, so any
    # concurrent sign-off observes 'recalculating' and is rejected. Switching
    # stays paused even if the recompute below takes time or later fails.
    batch.status = 'recalculating'
    session.commit()

    try:
        result = compare_versions(
            dataset.rows, batch.holdout_fraction,
            withdrawn_ids=withdrawn_ids, gate=Gate(**batch.gate),
        )
    except ValueError as failure:
        # Leave the batch visibly paused in 'recalculating'; an operator retries
        # the withdrawal after fixing the ledger instead of silently resuming.
        raise ValueError(f'标签撤回复算失败，批次 {batch_id} 保持复算暂停状态：{failure}') from failure

    # Phase 2: the affected result is invalidated but retained; the recompute
    # lands in a new batch, and any effective sign-off is superseded yet readable.
    batch.status = 'invalidated'
    replacement = VersionBatch(
        label=f'{batch.label} · 标签撤回复算', dataset_id=batch.dataset_id, created_by=actor.id,
        created_at=utc_stamp(), status=derive_batch_status(result),
        holdout_fraction=batch.holdout_fraction, withdrawn_ids=withdrawn_ids,
        gate=batch.gate, result=result, error=None,
        recompute_reason=(
            f'撤回片段 {", ".join(requested)} 的人工标签（原因：{payload.reason}），'
            '依据规则对换版结果失效重算。'
        ),
    )
    session.add(replacement)
    session.flush()
    batch.superseded_by = replacement.id
    if signoff is not None and signoff.status == 'effective':
        signoff.status = 'superseded'
        signoff.superseded_at = utc_stamp()
    session.commit()
    return batch_info(replacement, None)


def signoff(session, batch_id, payload, actor):
    batch, existing = get_batch(session, batch_id, actor)
    assert_signoff_allowed(batch, existing)
    record = VersionSignoff(
        batch_id=batch_id, reviewer_id=actor.id, decision=payload.decision, note=payload.note,
        status='effective', created_at=utc_stamp(), superseded_at=None,
        recommendation_snapshot=build_snapshot(batch, utc_stamp()),
    )
    session.add(record)
    session.commit()
    return _signoff_info(record)


def list_signoffs(session, actor):
    rows = session.scalars(select(VersionSignoff).order_by(VersionSignoff.id.desc()).limit(100))
    return [_signoff_info(row) for row in rows]


def export_table(session, batch_id, key, actor):
    batch, signoff = get_batch(session, batch_id, actor)
    if batch.error or not batch.result:
        raise LookupError('该批次没有可导出的比对结果')
    for table in comparison_tables(batch.result):
        if table['key'] == key:
            return table
    raise LookupError('找不到结果表')


def _signoff_info(signoff):
    return {
        'id': signoff.id,
        'batch_id': signoff.batch_id,
        'decision': signoff.decision,
        'note': signoff.note,
        'status': signoff.status,
        'created_at': signoff.created_at,
        'superseded_at': signoff.superseded_at,
        'snapshot': signoff.recommendation_snapshot,
    }
