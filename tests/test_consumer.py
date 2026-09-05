"""The consumer deduplicates on event_id, so at-least-once redelivery is safe.

These test the Deduplicator contract directly, without a live subscription.
"""

from scripts.consumer import Deduplicator


def test_first_delivery_is_not_a_duplicate():
    dedup = Deduplicator()
    assert dedup.is_duplicate("evt-1") is False
    dedup.mark("evt-1")
    assert len(dedup) == 1


def test_redelivery_of_the_same_event_is_a_duplicate():
    dedup = Deduplicator()
    dedup.mark("evt-1")
    assert dedup.is_duplicate("evt-1") is True
    # A duplicate does not grow the processed set.
    assert len(dedup) == 1


def test_independent_events_are_not_duplicates():
    dedup = Deduplicator()
    dedup.mark("evt-1")
    assert dedup.is_duplicate("evt-2") is False
    dedup.mark("evt-2")
    assert len(dedup) == 2


def test_marking_is_idempotent():
    dedup = Deduplicator()
    dedup.mark("evt-1")
    dedup.mark("evt-1")
    assert len(dedup) == 1
