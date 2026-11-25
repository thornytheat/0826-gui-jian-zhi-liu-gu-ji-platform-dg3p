import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from threading import RLock

from app.business import DomainIssue

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, report TEXT NOT NULL, created TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', revision INTEGER NOT NULL DEFAULT 0,
    note TEXT NOT NULL DEFAULT '', reviewed TEXT, estimate_fingerprint TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS cohort_rows (
    run_id TEXT NOT NULL, row_index INTEGER NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (run_id, row_index)
);
CREATE TABLE IF NOT EXISTS backfills (
    run_id TEXT NOT NULL, seq INTEGER NOT NULL, parcel TEXT NOT NULL,
    pickup_time INTEGER NOT NULL, effect TEXT NOT NULL, payload TEXT NOT NULL,
    recorded TEXT NOT NULL, PRIMARY KEY (run_id, seq)
);
CREATE TABLE IF NOT EXISTS revisions (
    run_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
    reason TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', recorded TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (run_id, revision)
);
'''


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self._locks = {}
        self._locks_guard = RLock()

    def run_lock(self, run_id):
        """补录与签发并发时的串行点：同批的补录、复核与签发共用一把可重入锁。"""
        with self._locks_guard:
            return self._locks.setdefault(run_id, RLock())

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=15)) as db, db:
            db.row_factory = sqlite3.Row
            db.executescript(SCHEMA)
            columns = {row['name'] for row in db.execute('PRAGMA table_info(runs)')}
            if 'estimate_fingerprint' not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN estimate_fingerprint TEXT NOT NULL DEFAULT ''")
            yield db

    def register(self, run_id, report, rows, fingerprint):
        with self.connect() as db:
            db.execute('INSERT INTO runs(id,report,created,estimate_fingerprint) VALUES (?,?,?,?) '
                       'ON CONFLICT(id) DO NOTHING',
                       (run_id, json.dumps(report, ensure_ascii=False), report['manifest']['created'], fingerprint))
            db.executemany('INSERT OR IGNORE INTO cohort_rows(run_id,row_index,payload) VALUES (?,?,?)',
                           ((run_id, index, json.dumps(row, ensure_ascii=False))
                            for index, row in enumerate(rows)))
        return self.load(run_id)

    def load(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        result = dict(row)
        result['report'] = json.loads(result['report'])
        result['revisions'] = self._revisions(run_id)
        result['backfills'] = self._backfills(run_id)
        return result

    def cohort(self, run_id):
        with self.connect() as db:
            rows = db.execute('SELECT payload FROM cohort_rows WHERE run_id=? ORDER BY row_index',
                              (run_id,)).fetchall()
        if not rows:
            raise DomainIssue('RUN_NOT_FOUND')
        return [json.loads(row['payload']) for row in rows]

    def list(self):
        with self.connect() as db:
            items = [dict(r) for r in db.execute(
                'SELECT id,created,status,revision,note FROM runs ORDER BY created DESC,id LIMIT 50')]
        for item in items:
            item['reason'] = self._last_reason(item['id'])
        return items

    def estimate_fingerprint(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT estimate_fingerprint FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise DomainIssue('RUN_NOT_FOUND')
        return row['estimate_fingerprint']

    def has_estimate_change_after(self, run_id, revision):
        with self.connect() as db:
            row = db.execute(
                'SELECT 1 FROM revisions WHERE run_id=? AND revision>? AND detail LIKE ? LIMIT 1',
                (run_id, revision, '%"estimate_changed": true%')).fetchone()
        return row is not None

    def replace_report(self, run_id, report, rows, fingerprint):
        with self.connect() as db:
            db.execute('UPDATE runs SET report=?, estimate_fingerprint=? WHERE id=?',
                       (json.dumps(report, ensure_ascii=False), fingerprint, run_id))
            db.execute('DELETE FROM cohort_rows WHERE run_id=?', (run_id,))
            db.executemany('INSERT INTO cohort_rows(run_id,row_index,payload) VALUES (?,?,?)',
                           ((run_id, index, json.dumps(row, ensure_ascii=False))
                            for index, row in enumerate(rows)))

    def add_backfill(self, run_id, seq, entry, recorded):
        payload = dict(entry, seq=seq)
        with self.connect() as db:
            db.execute('INSERT INTO backfills(run_id,seq,parcel,pickup_time,effect,payload,recorded) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (run_id, seq, str(entry['parcel']), entry['pickup_time'], entry['effect'],
                        json.dumps(payload, ensure_ascii=False), recorded))

    def next_backfill_seq(self, run_id):
        with self.connect() as db:
            row = db.execute('SELECT COALESCE(MAX(seq)+1,0) AS seq FROM backfills WHERE run_id=?',
                             (run_id,)).fetchone()
        return row['seq']

    def append_revision(self, run_id, revision, status, reason, note, recorded, detail=None):
        with self.connect() as db:
            cursor = db.execute('UPDATE runs SET status=?,note=?,reviewed=?,revision=revision+1 '
                                'WHERE id=? AND revision=?',
                                (status, note, recorded, run_id, revision))
            if cursor.rowcount != 1:
                raise DomainIssue('STALE_REVIEW')
            db.execute('INSERT INTO revisions(run_id,revision,status,reason,note,recorded,detail) '
                       'VALUES (?,?,?,?,?,?,?)',
                       (run_id, revision + 1, status, reason, note, recorded,
                        json.dumps(detail or {}, ensure_ascii=False)))
        return self.load(run_id)

    def revise(self, run_id, revision, status, note, now):
        return self.append_revision(run_id, revision, status, 'REVIEW', note, now)

    def _revisions(self, run_id):
        with self.connect() as db:
            rows = db.execute(
                'SELECT revision,status,reason,note,recorded,detail FROM revisions '
                'WHERE run_id=? ORDER BY revision', (run_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item['detail'] = json.loads(item['detail'])
            result.append(item)
        return result

    def _backfills(self, run_id):
        with self.connect() as db:
            rows = db.execute(
                'SELECT payload,recorded FROM backfills WHERE run_id=? ORDER BY seq', (run_id,)).fetchall()
        result = []
        for row in rows:
            entry = json.loads(row['payload'])
            result.append({'seq': entry['seq'], 'parcel': entry['parcel'],
                           'pickup_time': entry['pickup_time'], 'effect': entry['effect'],
                           'recorded': row['recorded']})
        return result
    def _last_reason(self, run_id):
        with self.connect() as db:
            row = db.execute(
                'SELECT reason FROM revisions WHERE run_id=? ORDER BY revision DESC LIMIT 1',
                (run_id,)).fetchone()
        return row['reason'] if row else None
