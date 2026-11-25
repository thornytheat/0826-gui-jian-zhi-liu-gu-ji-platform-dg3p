"""晚到取件补录：截止时或更早→重算；截止后→仍算未取；补录先生效则旧估计拒签。"""
import pytest

from app.business import DomainIssue
from app.review.service import execute, review


def case_0001(report):
    return next(case for case in report['report']['cases'] if case['case'] == '0001')


def backfill(store, run_id, revision, parcel, pickup_time, note='晚班补录取件'):
    from app.backfill.service import apply_backfill
    return apply_backfill(store, run_id, dict(revision=revision, parcel=parcel,
                                              pickup_time=pickup_time, note=note))


# ready.csv 的 0001 组：4 件入柜（柜件 1-4），观察截止 exit=10，原滞留率 7500bp
def test_in_window_backfill_recomputes_estimate_and_logs(store, clean):
    run = execute(store, clean)
    assert case_0001(run)['measure']['value'] == 7500
    updated = backfill(store, run['id'], 0, parcel=4, pickup_time=4)
    case = case_0001(updated)
    # 柜件 4 在截止前补取：新增龄期 4 事件，重算后滞留率由 7500 降至 5000
    assert case['measure']['value'] == 5000
    assert updated['revision'] == 1
    assert updated['backfills'][0]['effect'] == 'IN_WINDOW'
    assert updated['revisions'][0]['reason'] == 'BACKFILL_RECOMPUTED'
    assert updated['revisions'][0]['detail']['estimate_changed'] is True


def test_post_cutoff_backfill_keeps_estimate_and_marks_unpicked(store, clean):
    run = execute(store, clean)
    updated = backfill(store, run['id'], 0, parcel=4, pickup_time=14)
    case = case_0001(updated)
    # 观察截止后才取：本批仍算未取，滞留率不变
    assert case['measure']['value'] == 7500
    assert updated['backfills'][0]['effect'] == 'POST_CUTOFF'
    assert updated['revisions'][0]['reason'] == 'BACKFILL_RECORDED'
    assert updated['revisions'][0]['detail']['estimate_changed'] is False
    assert {'parcel': 4, 'exit': 14} in case['prep']['details']['post_cutoff_pickups']
    # 估计未变，仍可正常签发
    approved = review(store, run['id'], dict(revision=1, status='APPROVED', note='复核无误签发'))
    assert approved['status'] == 'APPROVED'


def test_backfill_rejects_stale_revision(store, clean):
    run = execute(store, clean)
    backfill(store, run['id'], 0, parcel=4, pickup_time=14)
    with pytest.raises(DomainIssue, match='STALE_REVIEW'):
        backfill(store, run['id'], 0, parcel=3, pickup_time=9)


def test_backfill_unknown_parcel_and_bad_fields(store, clean):
    run = execute(store, clean)
    with pytest.raises(DomainIssue, match='PARCEL_UNKNOWN'):
        backfill(store, run['id'], 0, parcel=99, pickup_time=5)
    for payload in (dict(revision=0, parcel=4, pickup_time=-1, note='x' * 3),
                    dict(revision=0, parcel=4, pickup_time=5, note='短'),
                    dict(revision=-1, parcel=4, pickup_time=5, note='合法意见'),
                    None):
        with pytest.raises(DomainIssue, match='BACKFILL_FIELDS'):
            from app.backfill.service import apply_backfill
            apply_backfill(store, run['id'], payload)


def test_backfill_before_entry_rejected(store, clean):
    run = execute(store, clean)
    with pytest.raises(DomainIssue, match='PICKUP_BEFORE_ENTRY'):
        backfill(store, run['id'], 0, parcel=2, pickup_time=1)  # 柜件 2 入柜在 3


def test_backfill_then_stale_approval_is_refused(store, clean):
    # 补录与签发同时提交：补录先生效后，拿着旧 revision 的签发必须拒绝
    run = execute(store, clean)
    backfill(store, run['id'], 0, parcel=4, pickup_time=4)
    with pytest.raises(DomainIssue, match='ESTIMATE_SUPERSEDED'):
        review(store, run['id'], dict(revision=0, status='APPROVED', note='按旧估计签发'))
    # 刷新到新 revision 后，新估计与参考值不符仍不可签发；先暂缓留痕
    refreshed = store.load(run['id'])
    held = review(store, run['id'], dict(revision=refreshed['revision'],
                                         status='HELD', note='按重算结果暂缓'))
    assert held['status'] == 'HELD'


def test_backfill_without_estimate_change_does_not_block_old_approval(store, clean):
    run = execute(store, clean)
    backfill(store, run['id'], 0, parcel=4, pickup_time=14)  # 截止后，估计未变
    # 旧 revision 已失效（乐观锁），但 estimate_changed=False 时不触发 supersede 专用原因
    with pytest.raises(DomainIssue, match='STALE_REVIEW'):
        review(store, run['id'], dict(revision=0, status='APPROVED', note='旧请求'))


def test_backfill_on_finalized_batch_rejected(store, clean):
    run = execute(store, clean)
    review(store, run['id'], dict(revision=0, status='APPROVED', note='完成签发'))
    with pytest.raises(DomainIssue, match='RUN_FINALIZED'):
        backfill(store, run['id'], 1, parcel=4, pickup_time=8)


def test_backfill_ambiguity_holds_batch(store, clean):
    # 柜件 1 原在 5 取；再补一个不同的取件时刻，两个候选估值不一致，整批暂缓
    run = execute(store, clean)
    updated = backfill(store, run['id'], 0, parcel=1, pickup_time=8)
    case = case_0001(updated)
    assert case['status'] == 'PICKUP_AMBIGUOUS'
    assert updated['status'] == 'HELD'
    assert updated['revisions'][-1]['reason'] == 'BACKFILL_AMBIGUOUS_HOLD'
    assert not updated['report']['balance']['releasable']


def test_full_width_numeric_accepted_in_backfill(store, clean):
    run = execute(store, clean)
    updated = backfill(store, run['id'], 0, parcel=4, pickup_time='８')
    assert updated['backfills'][0]['pickup_time'] == 8


def test_workbench_list_exposes_conclusion_reason(store, clean):
    run = execute(store, clean)
    backfill(store, run['id'], 0, parcel=4, pickup_time=4)
    item = next(row for row in store.list() if row['id'] == run['id'])
    assert item['status'] == 'PENDING' and item['reason'] == 'BACKFILL_RECOMPUTED'
    review(store, run['id'], dict(revision=1, status='HELD', note='交班暂缓核对'))
    item = next(row for row in store.list() if row['id'] == run['id'])
    assert item['reason'] == 'REVIEW'
