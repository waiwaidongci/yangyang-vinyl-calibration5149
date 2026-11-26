"""Fit both denoiser versions on the same clips and compare holdout Brier.

Each version gets its own weighted PAVA calibration from the same training
clips, and both are scored on the *same* holdout clips. The switch rule uses
only the calibrated Brier on that shared holdout: the candidate version is
recommended only when its Brier is strictly lower. Raw-score Brier stays in the
result for reference but never drives the decision.

After human-label withdrawals the same function reruns on the surviving clips.
When the remaining sample no longer clears the gate the original result was
issued under, the recommendation becomes ``insufficient_evidence`` instead of a
switch/keep call.
"""

from dataclasses import dataclass

from .calibration import fit_calibration
from .diagnostics import brier_score
from .version_records import CURRENT, CANDIDATE, MIN_HOLDOUT, MIN_TRAIN, VERSION_LABELS
from .version_split import split_version_records

SWITCH = 'switch'
KEEP = 'keep'
INSUFFICIENT = 'insufficient_evidence'
RECOMMENDATION_TEXT = {
    SWITCH: '新版校准后 Brier 在同一批留出片段上严格更低，建议换版。',
    KEEP: '新版校准后 Brier 未严格低于现版，不建议换版，维持现版去噪器。',
    INSUFFICIENT: '标签撤回后可用样本达不到原门槛，证据不足，暂不给出换版结论。',
}
SCORE_KEYS = {CURRENT: 'raw_score_current', CANDIDATE: 'raw_score_candidate'}


@dataclass(frozen=True)
class Gate:
    min_train: int
    min_holdout: int
    first_train_count: int
    first_holdout_count: int


def default_gate(split):
    return Gate(MIN_TRAIN, MIN_HOLDOUT, len(split.train), len(split.holdout))


def compare_versions(rows, fraction, withdrawn_ids=(), gate=None):
    withdrawn = set(withdrawn_ids)
    active_rows = [row for row in rows if row['id'] not in withdrawn]
    split = split_version_records(active_rows, fraction)
    first_run = gate is None
    if gate is None:
        gate = default_gate(split)

    warnings = []
    if split.late_confirmations:
        warnings.append(
            f'切分日前打分的 {split.late_confirmations} 条片段在切分日或之后才确认，'
            '已排除出两版训练和留出验证。'
        )
    if withdrawn:
        warnings.append(f'已撤回 {len(withdrawn)} 条人工标签，本次结果按剩余片段复算。')

    sufficient, gate_messages = _check_gate(split, gate, first_run)
    warnings.extend(gate_messages)
    if first_run and not sufficient:
        # 首发样本不达标属于台账不可用，直接报错，不生成误导性的换版建议。
        raise ValueError('；'.join(gate_messages) + '请调小留出比例或补充较早档号的确认记录。')

    versions = {}
    holdout_rows = []
    fit_failed = []
    for version in (CURRENT, CANDIDATE):
        score_key = SCORE_KEYS[version]
        try:
            model = fit_calibration((row[score_key], row['needs_relisten']) for row in split.train)
            scored = [
                (row['id'], model.predict(row[score_key]), model.outside_support(row[score_key]))
                for row in split.holdout
            ]
            calibrated = [(probability, row['needs_relisten'])
                          for (_, probability, _), row in zip(scored, split.holdout)]
            raw = [(row[score_key], row['needs_relisten']) for row in split.holdout]
            versions[version] = {
                'fit_ok': True,
                'distinct_scores': len(model.scores),
                'pool_count': len(model.pools),
                'brier_raw': brier_score(raw),
                'brier_calibrated': brier_score(calibrated),
            }
        except ValueError as error:
            fit_failed.append(f'{VERSION_LABELS[version]}：{error}')
            versions[version] = {
                'fit_ok': False, 'distinct_scores': 0, 'pool_count': 0,
                'brier_raw': None, 'brier_calibrated': None,
            }
            scored = [(row['id'], None, None) for row in split.holdout]
        versions[version]['predictions'] = scored

    if fit_failed:
        sufficient = False
        warnings.append('保序拟合失败，' + '；'.join(fit_failed) + '。按证据不足处理。')

    holdout_rows = _holdout_rows(split, versions)
    recommendation, brier_delta = _recommendation(versions, sufficient)
    if recommendation == SWITCH:
        warnings.append(
            f"新版 Brier {versions[CANDIDATE]['brier_calibrated']:.4f} 低于现版 "
            f"{versions[CURRENT]['brier_calibrated']:.4f}，换版建议仅对本次同批留出有效。"
        )
    elif recommendation == KEEP:
        warnings.append('两版使用完全相同的训练与留出片段，差异仅来自去噪器打分本身。')

    return {
        'cutoff': split.cutoff,
        'train_count': len(split.train),
        'holdout_count': len(split.holdout),
        'pending_count': len(split.pending),
        'late_confirmations': split.late_confirmations,
        'train_groups': split.train_groups,
        'holdout_groups': split.holdout_groups,
        'withdrawn_ids': sorted(withdrawn),
        'gate': {
            'min_train': gate.min_train,
            'min_holdout': gate.min_holdout,
            'first_train_count': gate.first_train_count,
            'first_holdout_count': gate.first_holdout_count,
        },
        'sufficient_evidence': sufficient,
        'recommendation': recommendation,
        'recommendation_text': RECOMMENDATION_TEXT[recommendation],
        'brier_delta_calibrated': brier_delta,
        'versions': {key: {k: v for k, v in value.items() if k != 'predictions'}
                     for key, value in versions.items()},
        'holdout_rows': holdout_rows,
        'warnings': warnings,
    }


def _check_gate(split, gate, first_run):
    messages = []
    ok = True
    minimums = (
        (len(split.train), gate.min_train, '训练'),
        (len(split.holdout), gate.min_holdout, '留出'),
    )
    for count, minimum, label in minimums:
        if count < minimum:
            ok = False
            messages.append(f'{label}片段 {count} 条，低于原门槛 {minimum} 条。')
    if not first_run and (
        len(split.train) < gate.first_train_count or len(split.holdout) < gate.first_holdout_count
    ):
        messages.append(
            f'复算样本（训练 {len(split.train)}、留出 {len(split.holdout)}）少于首发时的样本'
            f'（训练 {gate.first_train_count}、留出 {gate.first_holdout_count}）。'
        )
    return ok, messages


def _recommendation(versions, sufficient):
    if not sufficient or not all(value['fit_ok'] for value in versions.values()):
        return INSUFFICIENT, None
    current_brier = versions[CURRENT]['brier_calibrated']
    candidate_brier = versions[CANDIDATE]['brier_calibrated']
    delta = candidate_brier - current_brier
    return (SWITCH if candidate_brier < current_brier else KEEP), delta


def _holdout_rows(split, versions):
    rows = []
    current_pred = {record_id: (probability, outside)
                    for record_id, probability, outside in versions[CURRENT]['predictions']}
    candidate_pred = {record_id: (probability, outside)
                      for record_id, probability, outside in versions[CANDIDATE]['predictions']}
    for row in split.holdout:
        current_probability, current_outside = current_pred[row['id']]
        candidate_probability, candidate_outside = candidate_pred[row['id']]
        rows.append({
            'id': row['id'],
            'record_ref': row['record_ref'],
            'side': row['side'],
            'start_seconds': row['start_seconds'],
            'end_seconds': row['end_seconds'],
            'scored_on': row['scored_on'],
            'confirmed_on': row['confirmed_on'],
            'needs_relisten': row['needs_relisten'],
            'raw_score_current': row['raw_score_current'],
            'probability_current': current_probability,
            'outside_support_current': _yes_no(current_outside),
            'raw_score_candidate': row['raw_score_candidate'],
            'probability_candidate': candidate_probability,
            'outside_support_candidate': _yes_no(candidate_outside),
        })
    return rows


def _yes_no(value):
    if value is None:
        return ''
    return '是' if value else '否'
