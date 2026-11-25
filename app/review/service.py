from hashlib import sha256
from time import monotonic

from app.backfill.service import estimate_fingerprint
from app.business import DomainIssue
from app.clock import now
from app.config import RULE_VERSION
from app.modules.pipeline import inspect_source, run


def execute(store, data):
    start = monotonic()
    fingerprint = sha256(data).hexdigest()
    run_id = sha256(f'{RULE_VERSION}:{fingerprint}'.encode()).hexdigest()
    try:
        return store.load(run_id)
    except DomainIssue as error:
        if error.code != 'RUN_NOT_FOUND':
            raise
    intake = inspect_source(data)
    report = run(data)
    report['manifest'] = dict(sha256=fingerprint, version=RULE_VERSION, created=now(),
                              duration_ms=round((monotonic()-start)*1000, 3), bytes=len(data),
                              run_id=run_id, backfills=0)
    return store.register(run_id, report, intake['rows'], estimate_fingerprint(report['cases']))


def review(store, run_id, values):
    if not isinstance(values, dict):
        raise DomainIssue('REVIEW_FIELDS')
    revision, status, note = (values.get(key) for key in ('revision', 'status', 'note'))
    if type(revision) is not int or revision < 0 or status not in ('APPROVED', 'HELD') or not isinstance(note, str) or not (3 <= len(note.strip()) <= 200):
        raise DomainIssue('REVIEW_FIELDS')
    with store.run_lock(run_id):
        current = store.load(run_id)
        # 补录已先生效：基于旧估计的签发不能再提交（优先于普通乐观锁冲突）
        if status == 'APPROVED' and revision < current['revision'] and store.has_estimate_change_after(run_id, revision):
            raise DomainIssue('ESTIMATE_SUPERSEDED')
        if revision != current['revision']:
            raise DomainIssue('STALE_REVIEW')
        if current['status'] == 'APPROVED':
            raise DomainIssue('RUN_FINALIZED')
        if status == 'APPROVED' and not current['report']['balance']['releasable']:
            raise DomainIssue('UNRESOLVED_ISSUES')
        return store.revise(run_id, revision, status, note.strip(), now())
