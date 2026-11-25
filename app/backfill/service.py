"""晚到取件记录的补录与复核重算。

补录的取件发生在观察截止时或更早，复核需重算滞留率；观察截止后才取的柜件，
在本批里仍算未取（右删失）。补录先生效后，基于旧估计的签发必须拒绝。
"""
import re
import unicodedata
from hashlib import sha256

from app.business import DomainIssue
from app.clock import now
from app.config import MAX_ROWS
from app.intake.schema import HEADER
from app.modules.grouping import case_groups
from app.modules.pipeline import evaluate
from app.modules.reconcile import balance

MISSING = {'', '-', 'N/A', 'NULL', '未知'}


def integer(value):
    if type(value) is bool:
        raise DomainIssue('BACKFILL_FIELDS')
    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        text = unicodedata.normalize('NFKC', value).strip()
        if text.upper() in MISSING or not re.fullmatch(r'(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})', text):
            raise DomainIssue('BACKFILL_FIELDS')
        number = int(text.replace(',', ''))
    else:
        raise DomainIssue('BACKFILL_FIELDS')
    if number < 0:
        raise DomainIssue('BACKFILL_FIELDS')
    return number


def parse_request(values):
    if not isinstance(values, dict):
        raise DomainIssue('BACKFILL_FIELDS')
    revision = values.get('revision')
    if type(revision) is not int or revision < 0:
        raise DomainIssue('BACKFILL_FIELDS')
    raw_parcel = str(values.get('parcel', '')).strip()
    if not re.fullmatch(r'[0-9]{1,12}|[A-Za-z0-9][A-Za-z0-9_.-]{0,35}', raw_parcel):
        raise DomainIssue('BACKFILL_FIELDS')
    parcel = int(raw_parcel) if re.fullmatch(r'[0-9]{1,12}', raw_parcel) else raw_parcel
    pickup_time = integer(values.get('pickup_time'))
    note = values.get('note', '')
    if not isinstance(note, str) or not (3 <= len(note.strip()) <= 200):
        raise DomainIssue('BACKFILL_FIELDS')
    return revision, parcel, pickup_time, note.strip()


def estimate_fingerprint(cases):
    """按分组的计算结论生成估计指纹，只随重算后的业务结论变化。"""
    digest = sha256()
    for case in sorted(cases, key=lambda item: item['case']):
        measure = case.get('measure')
        value = None if measure is None else measure['value']
        digest.update(f"{case['case']}:{case['status']}:{value}\n".encode())
    return digest.hexdigest()


def _intake_summary(rows):
    return dict(input=len(rows), accepted=len(rows), repeated=0, blank=0, rejected=0,
                reasons={}, isolation=[])


def _rebuild_report(prior, rows):
    cases = [evaluate(group_rows, 5) for _, group_rows in case_groups(rows)]
    intake = _intake_summary(rows)
    return {'intake': intake, 'cases': cases, 'balance': balance(intake, cases)}


def apply_backfill(store, run_id, values):
    revision, parcel, pickup_time, note = parse_request(values)
    with store.run_lock(run_id):
        current = store.load(run_id)
        if revision != current['revision']:
            raise DomainIssue('STALE_REVIEW')
        if current['status'] == 'APPROVED':
            raise DomainIssue('RUN_FINALIZED')

        rows = store.cohort(run_id)
        parcel_rows = [row for row in rows if row['parcel'] == parcel and row['kind'] == 'arrival']
        if not parcel_rows:
            raise DomainIssue('PARCEL_UNKNOWN')
        case_id = parcel_rows[0]['case']
        case_cutoff = next(row['exit'] for row in rows
                           if row['case'] == case_id and row['kind'] == 'observation')
        entry_time = parcel_rows[0]['entry']
        if pickup_time < entry_time:
            raise DomainIssue('PICKUP_BEFORE_ENTRY')
        if len(rows) >= MAX_ROWS:
            raise DomainIssue('ROW_LIMIT')

        seq = store.next_backfill_seq(run_id)
        effect = 'IN_WINDOW' if pickup_time <= case_cutoff else 'POST_CUTOFF'
        synthetic = dict(zip(HEADER, (f'BF{seq:04d}', case_id, 'pickup', parcel, 0, pickup_time, 0, 0, note)))
        merged = [*rows, synthetic]

        before = estimate_fingerprint(current['report']['cases'])
        rebuilt = _rebuild_report(current['report'], merged)
        after = estimate_fingerprint(rebuilt['cases'])
        estimate_changed = before != after
        rebuilt['manifest'] = dict(current['report']['manifest'])
        rebuilt['manifest']['backfills'] = rebuilt['manifest'].get('backfills', 0) + 1

        held = any(case['status'] == 'PICKUP_AMBIGUOUS' for case in rebuilt['cases'])
        stamp = now()
        store.replace_report(run_id, rebuilt, merged, after)
        store.add_backfill(run_id, seq, {'parcel': parcel, 'pickup_time': pickup_time,
                                         'effect': effect, 'note': note,
                                         'case': case_id, 'record': synthetic['record']}, stamp)
        reason = 'BACKFILL_AMBIGUOUS_HOLD' if held else ('BACKFILL_RECOMPUTED' if estimate_changed
                                                         else 'BACKFILL_RECORDED')
        status = 'HELD' if held else current['status']
        detail = {'effect': effect, 'estimate_changed': estimate_changed, 'backfill_seq': seq}
        return store.append_revision(run_id, revision, status, reason, note, stamp, detail)
