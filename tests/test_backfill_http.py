import io

import pytest

from app.business import DomainIssue


def test_backfill_endpoint_recomputes_and_returns_revision(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    response = client.post(f"/api/runs/{run['id']}/backfills",
                           json={'revision': 0, 'parcel': 4, 'pickup_time': 4, 'note': '晚班补录取件'})
    assert response.status_code == 200
    payload = response.json
    assert payload['revision'] == 1
    ledger = payload['backfills'][0]
    assert ledger['seq'] == 0 and ledger['parcel'] == 4
    assert ledger['pickup_time'] == 4 and ledger['effect'] == 'IN_WINDOW'
    assert ledger['recorded']
    assert payload['revisions'][0]['reason'] == 'BACKFILL_RECOMPUTED'


def test_backfill_unknown_run_is_404(client):
    response = client.post('/api/runs/missing/backfills',
                           json={'revision': 0, 'parcel': 1, 'pickup_time': 4, 'note': '晚班补录取件'})
    assert response.status_code == 404 and response.json['code'] == 'RUN_NOT_FOUND'


def test_backfill_bad_payload_is_422(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    response = client.post(f"/api/runs/{run['id']}/backfills",
                           json={'revision': 0, 'parcel': 4, 'pickup_time': 'x', 'note': '晚班补录取件'})
    assert response.status_code == 422 and response.json['code'] == 'BACKFILL_FIELDS'


def test_backfill_unknown_parcel_is_409(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    response = client.post(f"/api/runs/{run['id']}/backfills",
                           json={'revision': 0, 'parcel': 99, 'pickup_time': 4, 'note': '晚班补录取件'})
    assert response.status_code == 409 and response.json['code'] == 'PARCEL_UNKNOWN'


def test_superseded_approval_reports_dedicated_reason(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    client.post(f"/api/runs/{run['id']}/backfills",
                json={'revision': 0, 'parcel': 4, 'pickup_time': 4, 'note': '晚班补录取件'})
    response = client.patch(f"/api/runs/{run['id']}",
                            json={'revision': 0, 'status': 'APPROVED', 'note': '按旧估计签发'})
    assert response.status_code == 409 and response.json['code'] == 'ESTIMATE_SUPERSEDED'


def test_run_detail_exposes_conclusion_and_reason_chain(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    client.post(f"/api/runs/{run['id']}/backfills",
                json={'revision': 0, 'parcel': 4, 'pickup_time': 14, 'note': '截止后补录'})
    detail = client.get(f"/api/runs/{run['id']}").json
    assert detail['status'] == 'PENDING'
    assert [item['reason'] for item in detail['revisions']] == ['BACKFILL_RECORDED']
    assert detail['backfills'][0]['effect'] == 'POST_CUTOFF'
    listed = client.get('/api/runs').json['items'][0]
    assert listed['reason'] == 'BACKFILL_RECORDED'


def test_evidence_contains_backfill_and_revision_ledger(client):
    run = client.post('/api/runs', data={'sample': 'clean'}).json
    client.post(f"/api/runs/{run['id']}/backfills",
                json={'revision': 0, 'parcel': 4, 'pickup_time': 14, 'note': '截止后补录'})
    packet = client.get(f"/api/runs/{run['id']}/evidence").json
    assert packet['backfills'][0]['effect'] == 'POST_CUTOFF'
    assert packet['revisions'][0]['detail']['estimate_changed'] is False
