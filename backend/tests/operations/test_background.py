"""Audit U08: the background is judged by age and result — heartbeats per job group and the age of the queue."""

from datetime import UTC, datetime, timedelta

from app.modules.operations.background import (
    JOB_GROUPS,
    PENDING_ALERT_AFTER,
    Heartbeat,
    PendingTopic,
    assess,
)

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)


def _all_fresh(**overrides: Heartbeat) -> dict[str, Heartbeat]:
    beats = {group.key: Heartbeat(group.key, started_at=NOW, success_at=NOW - timedelta(seconds=5)) for group in JOB_GROUPS}
    beats.update(overrides)
    return beats


def test_fresh_heartbeats_and_a_young_queue_are_healthy():
    health = assess(_all_fresh(), [PendingTopic("notification.requested", 3, NOW - timedelta(minutes=1))], NOW)
    assert health.ok and health.problems == ()
    assert {group.status for group in health.groups} == {"ok"}
    assert health.oldest_pending.age == timedelta(minutes=1)


def test_a_group_that_never_ran_or_went_silent_is_red():
    health = assess(_all_fresh(dispatch=Heartbeat("dispatch"), autofill=Heartbeat(
        "autofill", success_at=NOW - timedelta(minutes=16), failure_at=NOW, error="boom", failures=200,
    )), [], NOW)
    statuses = {group.key: group.status for group in health.groups}
    assert statuses["dispatch"] == "red" and statuses["autofill"] == "red" and statuses["timers"] == "ok"
    assert not health.ok
    assert health.problems == (
        "«Очередь событий модулей»: ни одного успешного запуска",
        "«Автозаполнение строк закупки»: нет успешного запуска 16 мин",
    )


def test_each_group_has_its_own_silence_limit():
    """The kitset names run once a minute and may take minutes; 20 minutes of silence is still fine for them."""
    late = NOW - timedelta(minutes=20)
    health = assess(_all_fresh(kitset_names=Heartbeat("kitset_names", success_at=late)), [], NOW)
    assert health.ok
    health = assess(_all_fresh(timers=Heartbeat("timers", success_at=NOW - timedelta(minutes=6))), [], NOW)
    assert not health.ok


def test_a_recent_failure_after_a_recent_success_is_a_warning_not_red():
    health = assess(_all_fresh(arbiter=Heartbeat(
        "arbiter", success_at=NOW - timedelta(seconds=30), failure_at=NOW, error="task auto-close: StaleDataError", failures=1,
    )), [], NOW)
    (arbiter,) = [group for group in health.groups if group.key == "arbiter"]
    assert arbiter.status == "warn" and arbiter.error.startswith("task auto-close")
    assert health.ok


def test_a_message_waiting_past_the_threshold_is_red():
    old = NOW - PENDING_ALERT_AFTER - timedelta(minutes=1)
    health = assess(_all_fresh(), [
        PendingTopic("receiving.expected", 1, old),
        PendingTopic("notification.requested", 2, NOW - timedelta(seconds=10)),
    ], NOW)
    assert not health.ok
    assert [(topic.topic, topic.status) for topic in health.topics] == [
        ("receiving.expected", "red"), ("notification.requested", "ok"),
    ]
    assert health.oldest_pending.topic == "receiving.expected"
    assert health.problems == ("Очередь «receiving.expected»: сообщение ждёт 16 мин",)


def test_a_clock_skew_never_gives_a_negative_age():
    health = assess(_all_fresh(), [PendingTopic("arbiter.trigger", 1, NOW + timedelta(seconds=30))], NOW)
    assert health.topics[0].age == timedelta(0) and health.ok
