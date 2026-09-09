"""Observed campaign evidence, kept separate from the trusted formal headline."""


def summarize(campaign):
    result = {'trust': 'observed job results; not a trusted formal verdict',
              'formal': [], 'native': [], 'controls': [], 'supplemental': [],
              'unresolved': [], 'execution_issues': []}
    for job, status in campaign['statuses'].items():
        if status != 'completed':
            result['execution_issues'].append({'job': job, 'status': status})
    for job, record in campaign['jobs'].items():
        if record.get('status') != 'completed' or not isinstance(record.get('result'), list):
            continue
        kind = record.get('evidence_kind')
        for row in record['result']:
            if not isinstance(row, dict) or not isinstance(row.get('name'), str):
                continue
            observation = {'job': job, 'case': row['name']}
            if kind == 'formal' and isinstance(row.get('o2t'), dict):
                formal = row['o2t']
                status = formal.get('status')
                observation.update(status=status if isinstance(status, str) else 'unknown', reason=formal.get('reason', ''),
                                   independent=row.get('alive2'), scope=row.get('scope', 'reported per-input obligation'))
                result['formal'].append(observation)
                if observation['status'] not in ('proved', 'refuted'):
                    result['unresolved'].append(dict(observation))
            elif kind == 'native':
                observation.update({k: row[k] for k in ('status', 'batches', 'values_compared', 'mismatches', 'witness', 'scope') if k in row})
                result['native'].append(observation)
            elif kind == 'negative-control':
                observation.update({k: row[k] for k in ('status', 'detail', 'scope', 'o2t') if k in row})
                result['controls'].append(observation)
    for attempt in campaign.get('gap_checks', {}).get('attempts', []):
        validation = attempt['validation']
        baseline = validation.get('runs', {}).get('baseline', {}).get('trace', {})
        result['supplemental'].append({'case': attempt['gap'], 'status': validation['status'],
            'reason': validation.get('reason'), 'checker': attempt['candidate']['path'],
            'native_observation': baseline, 'trust': 'finite native evidence; formal status unchanged'})
    return result
