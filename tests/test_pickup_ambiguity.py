"""同一柜件两个取件时刻：估值相同照常复核，估值不同暂缓整批。"""
import pytest

from app.business import DomainIssue
from app.modules.pipeline import run
from app.modules.prepare import cohort_observations, prepare
from conftest import csv_data


def arrival(record, parcel, entry=0, case='C1'):
    return [record, case, 'arrival', str(parcel), str(entry), '0', '0', '0', '入柜']


def pickup(record, parcel, exit_time, case='C1'):
    return [record, case, 'pickup', str(parcel), '0', str(exit_time), '0', '0', '取件']


def observation(cutoff=10, horizon=10, expected=0, case='C1'):
    return ['O1', case, 'observation', '0', '0', str(cutoff), str(horizon), str(expected), '观察']


def build(rows, expected=0):
    return csv_data([*rows, observation(expected=expected)])


def test_two_pickup_times_with_equal_estimate_stay_ready():
    # 柜件 2 截止后才取（龄期 10 删失）：柜件 1 无论在 4 还是 8 取，KM 估出的滞留率同为 5000
    data = build([
        arrival('A1', 1), arrival('A2', 2),
        pickup('P1a', 1, 4), pickup('P1b', 1, 8), pickup('P2', 2, 12),
    ], expected=5000)
    report = run(data)
    case = report['cases'][0]
    assert case['status'] == 'READY'
    assert case['prep']['details']['equivalent_pickups'] == [
        {'parcel': 1, 'times': [4, 8], 'remaining_bp': 5000}]
    assert case['measure']['value'] == 5000


def test_two_pickup_times_with_diverging_estimates_hold_whole_batch():
    # 柜件 2 在 6 取、柜件 3 在 6 删失：取在 4 估得 3333，取在 8 估得 0，估值不同
    data = build([
        arrival('A1', 1), arrival('A2', 2), arrival('A3', 3, entry=4),
        pickup('P1a', 1, 4), pickup('P1b', 1, 8),
        pickup('P2', 2, 6), pickup('P3', 3, 12),
    ], expected=3333)
    report = run(data)
    case = report['cases'][0]
    assert case['status'] == 'PICKUP_AMBIGUOUS'
    assert case['issue']['ambiguous_pickups'][0]['parcel'] == 1
    assert case['issue']['ambiguous_pickups'][0]['remaining_bp'] == {4: 3333, 8: 0}
    assert not report['balance']['releasable']


def test_prepare_raises_ambiguous_issue_with_details():
    data = build([
        arrival('A1', 1), arrival('A2', 2), arrival('A3', 3, entry=4),
        pickup('P1a', 1, 4), pickup('P1b', 1, 8),
        pickup('P2', 2, 6), pickup('P3', 3, 12),
    ])
    from app.intake.service import inspect
    from app.modules.grouping import case_groups
    rows = inspect(data)['rows']
    with pytest.raises(DomainIssue, match='PICKUP_AMBIGUOUS') as exc:
        prepare(next(iter(case_groups(rows)))[1])
    assert exc.value.details['ambiguous_pickups'][0]['times'] == [4, 8]


def test_identical_duplicate_pickup_is_not_conflict():
    # 完全相同的重复取件时刻不构成歧义；观察时点设为 6，取件事件都在范围内不触发超界标记
    data = csv_data([
        arrival('A1', 1), arrival('A2', 2),
        pickup('P1a', 1, 5), pickup('P1b', 1, 5), pickup('P2', 2, 6),
        observation(horizon=6),
    ])
    case = run(data)['cases'][0]
    assert case['status'] == 'READY' and not case['prep']['details']['equivalent_pickups']


def test_cohort_observations_treat_post_cutoff_as_unpicked():
    entry = {'1': 0, '2': 0}
    observations = cohort_observations(entry, {'1': 12, '2': 6}, cutoff=10)
    assert sorted(observations) == sorted([(10, False), (6, True)])


def test_post_cutoff_pickups_listed_in_prep_details():
    data = build([
        arrival('A1', 1), arrival('A2', 2),
        pickup('P1', 1, 12), pickup('P2', 2, 6),
    ])
    details = run(data)['cases'][0]['prep']['details']
    assert details['post_cutoff_pickups'] == [{'parcel': 1, 'exit': 12}]
    assert details['censored'] == 1


def test_two_ambiguous_parcels_are_judged_independently():
    # 两个柜件各有两个取件时刻；任一柜件估值不一致即整批暂缓，且各自证据独立
    data = build([
        arrival('A1', 1), arrival('A2', 2), arrival('A3', 3, entry=4),
        pickup('P1a', 1, 4), pickup('P1b', 1, 8),
        pickup('P2a', 2, 6), pickup('P2b', 2, 9),
        pickup('P3', 3, 12),
    ])
    report = run(data)
    case = report['cases'][0]
    assert case['status'] == 'PICKUP_AMBIGUOUS'
    parcels = {item['parcel'] for item in case['issue']['ambiguous_pickups']}
    assert parcels == {1, 2}
