"""Pure policy rules for version-switch sign-off and label withdrawal.

Kept free of SQLAlchemy so the exact guards used by the service layer can be
exercised with the standard library alone. Every function works on plain
objects matching the VersionBatch/VersionSignoff attribute shape.
"""

from src.constants.workflow import ComparisonStatus
from src.domain.version_compare import INSUFFICIENT


def derive_batch_status(result):
    if result is None:
        return 'failed'
    return (
        ComparisonStatus.INSUFFICIENT.value
        if result['recommendation'] == INSUFFICIENT
        else ComparisonStatus.ACTIVE.value
    )


def validate_withdrawal_request(batch, requested_ids, known_ids):
    """Returns the merged withdrawn id set, or raises with a client-facing message."""
    if batch.status == 'failed':
        raise PermissionError('该批次首发计算即失败，没有可撤回标签的有效结果。')
    if batch.status == ComparisonStatus.RECALCULATING.value:
        raise PermissionError('复算尚未完成，换版已暂停，请等待当前复算结束后再操作。')
    requested = sorted(set(requested_ids))
    unknown = [clip_id for clip_id in requested if clip_id not in known_ids]
    if unknown:
        raise ValueError(f'以下片段编号不在来源台账中：{"、".join(unknown)}')
    already = [clip_id for clip_id in requested if clip_id in batch.withdrawn_ids]
    if already:
        raise ValueError(f'以下片段标签此前已撤回：{"、".join(already)}')
    return sorted(set(batch.withdrawn_ids) | set(requested)), requested


def assert_signoff_allowed(batch, existing_signoff):
    """Concurrency guard: a result citing withdrawn labels can never be signed."""
    if existing_signoff is not None:
        raise PermissionError('同一批次只能签发一次；历史签发记录保留在审计信息中。')
    if batch.status == ComparisonStatus.RECALCULATING.value:
        raise PermissionError('复算期间暂停换版，不能签发，请等待复算批次生成后再签发。')
    if batch.status != ComparisonStatus.ACTIVE.value:
        raise PermissionError(f'批次状态为 {batch.status}，仅有效批次可签发。')
    if batch.error or not batch.result:
        raise PermissionError('该批次没有有效比对结果，不能签发。')
    # Last-line defence against a concurrent withdrawal that flipped labels
    # before the transaction committed.
    if batch.withdrawn_ids:
        raise PermissionError('该结果引用了已撤回的人工标签，不能签发；请改签发复算后的新批次。')
    if batch.result['recommendation'] != 'switch':
        raise PermissionError('仅当新版校准后 Brier 严格更低、建议换版时，才可签发换版。')


def build_snapshot(batch, stamp):
    result = batch.result
    return {
        'batch_id': batch.id,
        'cutoff': result['cutoff'],
        'recommendation': result['recommendation'],
        'recommendation_text': result['recommendation_text'],
        'brier_current': result['versions']['current']['brier_calibrated'],
        'brier_candidate': result['versions']['candidate']['brier_calibrated'],
        'brier_delta_calibrated': result['brier_delta_calibrated'],
        'train_count': result['train_count'],
        'holdout_count': result['holdout_count'],
        'holdout_groups': result['holdout_groups'],
        'withdrawn_ids': list(batch.withdrawn_ids),
        'gate': batch.gate,
        'signed_at': stamp,
    }
