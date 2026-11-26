"""CSV schema and typed normalization for two-denoiser version comparisons.

Every confirmed clip carries one risk score per denoiser version plus the single
human relisten label shared by both versions. Only clips with a confirmed label
can enter calibration; pending clips are accepted but never participate.
"""

from .records import MAX_SIDE_SECONDS, _blank, _day, _label, _number, _text

CURRENT = 'current'
CANDIDATE = 'candidate'
VERSIONS = (CURRENT, CANDIDATE)
VERSION_LABELS = {CURRENT: '现版去噪器', CANDIDATE: '新版去噪器'}

FIELDS = {
    'id': '片段编号',
    'record_ref': '唱片档号',
    'side': '盘面',
    'start_seconds': '片段起点（秒）',
    'end_seconds': '片段终点（秒）',
    'scored_on': '质控打分日',
    'raw_score_current': '现版原始风险分',
    'raw_score_candidate': '新版原始风险分',
    'confirmed_on': '人工确认日',
    'needs_relisten': '人工返听确认（1 是 / 0 否）',
}
COLUMNS = list(FIELDS)
MIN_TRAIN = 8
MIN_HOLDOUT = 4


def normalize_rows(rows):
    normalized = []
    seen = set()
    for line, source in enumerate(rows, start=2):
        if not isinstance(source, dict) or source.keys() != FIELDS.keys():
            raise ValueError(f'第 {line} 行字段有误，应包含：' + '、'.join(COLUMNS))
        try:
            row = _normalize_record(source)
            if row['id'] in seen:
                raise ValueError(f"片段编号重复（{row['id']}）")
        except ValueError as error:
            raise ValueError(f'第 {line} 行：{error}') from error
        seen.add(row['id'])
        normalized.append(row)

    confirmed = [row for row in normalized if row['needs_relisten'] is not None]
    if len(confirmed) < MIN_TRAIN + MIN_HOLDOUT:
        raise ValueError(f'至少需要 {MIN_TRAIN + MIN_HOLDOUT} 条双评分且人工确认的片段，才能按档号留出并训练两版模型')
    if len({row['scored_on'] for row in confirmed}) < 2:
        raise ValueError('双评分确认片段至少要覆盖两个不同打分日')
    latest_scored_day = max(row['scored_on'] for row in confirmed)
    record_days = {}
    for row in confirmed:
        days = record_days.setdefault(row['record_ref'], set())
        days.add(row['scored_on'])
    spread_groups = sum(1 for days in record_days.values() if len(days) > 1)
    if spread_groups < 2:
        raise ValueError('至少两个档号需要跨打分日出现片段，否则无法在不拆档号的前提下按日期留出')
    for row in normalized:
        if row['needs_relisten'] is None and row['scored_on'] <= latest_scored_day:
            raise ValueError(f"待判片段 {row['id']} 的打分日必须晚于确认片段的末次打分日")
    return normalized


def _normalize_record(source):
    record_id = _text(source['id'], '片段编号')
    record_ref = _text(source['record_ref'], '唱片档号')
    side = _text(source['side'], '盘面').upper()
    if side not in ('A', 'B'):
        raise ValueError('盘面必须是 A 或 B')
    start = _number(source['start_seconds'], '片段起点', MAX_SIDE_SECONDS)
    end = _number(source['end_seconds'], '片段终点', MAX_SIDE_SECONDS)
    if end <= start:
        raise ValueError('片段终点必须大于起点')
    scored = _day(source['scored_on'], '质控打分日')
    score_current = _number(source['raw_score_current'], '现版原始风险分', 1)
    score_candidate = _number(source['raw_score_candidate'], '新版原始风险分', 1)

    confirmed = None if _blank(source['confirmed_on']) else _day(source['confirmed_on'], '人工确认日')
    label = _label(source['needs_relisten'])
    if (confirmed is None) != (label is None):
        raise ValueError('人工确认日和 0/1 返听结论必须同时填写或同时留空')
    if confirmed is not None and confirmed < scored:
        raise ValueError('人工确认日必须等于或晚于质控打分日')

    return {
        'id': record_id,
        'record_ref': record_ref,
        'side': side,
        'start_seconds': start,
        'end_seconds': end,
        'scored_on': scored,
        'raw_score_current': score_current,
        'raw_score_candidate': score_candidate,
        'confirmed_on': confirmed,
        'needs_relisten': label,
    }
