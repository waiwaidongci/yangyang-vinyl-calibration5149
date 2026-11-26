"""Chinese-labelled table layouts for version comparison exports."""

from .version_records import FIELDS

VERSION_METRIC_FIELDS = {
    'version': '去噪器版本', 'fit_ok': '保序拟合成功', 'distinct_scores': '训练不同分值数',
    'pool_count': '保序分段数', 'brier_raw': '原始分 Brier', 'brier_calibrated': '校准后 Brier',
}
VERSION_HOLDOUT_FIELDS = {
    'id': FIELDS['id'], 'record_ref': FIELDS['record_ref'], 'side': FIELDS['side'],
    'start_seconds': FIELDS['start_seconds'], 'end_seconds': FIELDS['end_seconds'],
    'scored_on': FIELDS['scored_on'], 'confirmed_on': FIELDS['confirmed_on'],
    'needs_relisten': FIELDS['needs_relisten'],
    'raw_score_current': FIELDS['raw_score_current'], 'probability_current': '现版校准概率',
    'outside_support_current': '现版超出训练范围',
    'raw_score_candidate': FIELDS['raw_score_candidate'], 'probability_candidate': '新版校准概率',
    'outside_support_candidate': '新版超出训练范围',
}


def comparison_tables(result):
    metric_rows = [
        {
            'version': label, 'fit_ok': '是' if result['versions'][key]['fit_ok'] else '否',
            'distinct_scores': result['versions'][key]['distinct_scores'],
            'pool_count': result['versions'][key]['pool_count'],
            'brier_raw': result['versions'][key]['brier_raw'],
            'brier_calibrated': result['versions'][key]['brier_calibrated'],
        }
        for key, label in (('current', '现版去噪器'), ('candidate', '新版去噪器'))
    ]
    return [
        _table('version_metrics', '两版留出 Brier 对比', VERSION_METRIC_FIELDS, metric_rows),
        _table('version_holdout', '同批留出逐片段两版评分', VERSION_HOLDOUT_FIELDS, result['holdout_rows']),
    ]


def _table(key, title, fields, rows):
    return {'key': key, 'title': title, 'columns': list(fields), 'labels': fields.copy(), 'rows': rows}
