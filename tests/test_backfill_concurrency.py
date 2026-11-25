"""补录与签发并发提交：补录先生效时旧估计的签发必须失败。"""
import pytest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.business import DomainIssue
from app.backfill.service import apply_backfill
from app.review.service import execute, review


def test_backfill_first_blocks_older_approval(store, clean):
    # 确定性时序：补录提交并生效后，同刻到达的旧估计签发只能得到 ESTIMATE_SUPERSEDED
    run = execute(store, clean)
    apply_backfill(store, run['id'], dict(revision=0, parcel=4, pickup_time=4, note='晚到取件补录'))
    with pytest.raises(DomainIssue, match='ESTIMATE_SUPERSEDED'):
        review(store, run['id'], dict(revision=0, status='APPROVED', note='按旧估计签发'))


def test_approval_first_finalizes_and_rejects_backfill(store, clean):
    # 反向时序：签发先生效后，补录不能再改动已签发批次
    run = execute(store, clean)
    review(store, run['id'], dict(revision=0, status='APPROVED', note='按交班估计签发'))
    with pytest.raises(DomainIssue, match='RUN_FINALIZED'):
        apply_backfill(store, run['id'], dict(revision=1, parcel=4, pickup_time=4, note='晚到取件补录'))


def test_parallel_backfills_have_single_winner(store, clean):
    run = execute(store, clean)
    start = Barrier(2)

    def attempt(pickup_time):
        start.wait(3)
        try:
            return apply_backfill(store, run['id'],
                                  dict(revision=0, parcel=4, pickup_time=pickup_time, note='并发补录'))['revision']
        except DomainIssue as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(attempt, [4, 5]))
    assert sorted(results, key=lambda item: str(item)) == [1, 'STALE_REVIEW']
    assert len(store.load(run['id'])['backfills']) == 1


def test_parallel_approval_and_backfill_keeps_single_revision(store, clean):
    seen = set()
    for _ in range(6):
        run = execute(store, clean)
        start = Barrier(2)

        def sign():
            start.wait(3)
            try:
                return review(store, run['id'],
                              dict(revision=0, status='APPROVED', note='按交班估计签发'))['status']
            except DomainIssue as error:
                return error.code

        def late_pickup():
            start.wait(3)
            try:
                return apply_backfill(store, run['id'],
                                      dict(revision=0, parcel=4, pickup_time=4, note='晚到取件补录'))['status']
            except DomainIssue as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as threads:
            seen.update(threads.map(lambda fn: fn(), [sign, late_pickup]))
        final = store.load(run['id'])
        assert final['revision'] == 1 and len(final['revisions']) == 1

    # 无论哪种交错，失败原因都必须落在并发冲突的受控集合内
    assert seen <= {'APPROVED', 'PENDING', 'STALE_REVIEW', 'ESTIMATE_SUPERSEDED', 'RUN_FINALIZED'}
