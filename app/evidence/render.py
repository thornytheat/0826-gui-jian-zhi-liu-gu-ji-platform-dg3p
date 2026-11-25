import json


def artifact(run):
    packet = {
        'schema': 'review-evidence/1',
        'manifest': run['report']['manifest'],
        'balance': run['report']['balance'],
        'cases': run['report']['cases'],
        'intake': run['report']['intake'],
        'backfills': run.get('backfills', []),
        'revisions': run.get('revisions', []),
        'review': {key: run[key] for key in ('status', 'revision', 'note', 'reviewed')},
    }
    return (json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
