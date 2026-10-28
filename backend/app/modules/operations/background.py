"""Health of the background worker (audit U08, 10.10.2026): judged by age and result, not by message counts.

The worker (`app/outbox_worker.py`) stamps a heartbeat per job group after every run — when it started, when it last
succeeded, how long the run took, the last error and how many runs in a row failed. The queue gives the age of its
oldest pending message per topic. This module turns both into one verdict:

- a group is RED when it has not succeeded for longer than its `max_silence` (or never) — the worker is down, its
  thread is stuck, or every run fails;
- the queue is RED when a message has waited longer than `PENDING_ALERT_AFTER` — nothing works it off;
- a group whose last run failed but which succeeded recently is a WARNING (a passing conflict, one bad site).

Pure: no database, no web. Read by «Системный журнал» and by /api/health?detail=1 (administrators).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

Status = Literal["ok", "warn", "red"]


@dataclass(frozen=True)
class JobGroup:
    key: str
    label: str
    max_silence: timedelta


# The worker's job groups, in the order they are shown. `max_silence` is several beats of the group plus its longest
# honest run: the dispatch loop turns every 2 s, the leave calendar and the invoice status every minute, the autofill thread every 3 s with up
# to 10 sites × 15 s, the kitset names once a minute with up to 15 sites × 15 s.
JOB_GROUPS: tuple[JobGroup, ...] = (
    JobGroup("dispatch", "Очередь событий модулей", timedelta(minutes=5)),
    JobGroup("timers", "Таймеры приёмки и закупки", timedelta(minutes=5)),
    JobGroup("owner", "Сводки и оповещения", timedelta(minutes=5)),
    JobGroup("arbiter", "Задачи модулям по проектам", timedelta(minutes=5)),
    JobGroup("leave_calendar", "Отпуска в календаре цеха", timedelta(minutes=10)),
    JobGroup("invoice_status", "Статус счетов из закупки", timedelta(minutes=10)),
    JobGroup("autofill", "Автозаполнение строк закупки", timedelta(minutes=15)),
    JobGroup("kitset_names", "Наименования наборов с сайтов", timedelta(minutes=30)),
)
JOB_GROUP_KEYS = frozenset(group.key for group in JOB_GROUPS)

# A message pending longer than this is not being worked off: the retry backoff of a failing one tops out at 5 min
# and dead-letters it after 5 attempts, so a healthy queue never holds anything this old.
PENDING_ALERT_AFTER = timedelta(minutes=15)


@dataclass(frozen=True)
class Heartbeat:
    group: str
    started_at: datetime | None = None
    success_at: datetime | None = None
    failure_at: datetime | None = None
    error: str | None = None
    duration_ms: int = 0
    failures: int = 0  # runs in a row that failed


@dataclass(frozen=True)
class PendingTopic:
    topic: str
    count: int
    oldest_at: datetime


@dataclass(frozen=True)
class GroupHealth:
    key: str
    label: str
    status: Status
    success_at: datetime | None
    failure_at: datetime | None
    error: str | None
    duration_ms: int
    failures: int
    max_silence: timedelta


@dataclass(frozen=True)
class TopicHealth:
    topic: str
    count: int
    oldest_at: datetime
    age: timedelta
    status: Status


@dataclass(frozen=True)
class BackgroundHealth:
    ok: bool
    groups: tuple[GroupHealth, ...]
    topics: tuple[TopicHealth, ...]
    problems: tuple[str, ...]

    @property
    def oldest_pending(self) -> TopicHealth | None:
        return max(self.topics, key=lambda topic: topic.age, default=None)


def _minutes(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} мин"
    return f"{minutes // 60} ч {minutes % 60} мин"


def assess(
    heartbeats: Mapping[str, Heartbeat],
    pending: Sequence[PendingTopic],
    now: datetime,
    *,
    pending_alert_after: timedelta = PENDING_ALERT_AFTER,
) -> BackgroundHealth:
    """The verdict over every known group and every topic that has pending messages. `now` and the stamps are aware."""
    groups: list[GroupHealth] = []
    problems: list[str] = []
    for group in JOB_GROUPS:
        beat = heartbeats.get(group.key, Heartbeat(group.key))
        if beat.success_at is None:
            status: Status = "red"
            problems.append(f"«{group.label}»: ни одного успешного запуска")
        elif now - beat.success_at > group.max_silence:
            status = "red"
            problems.append(f"«{group.label}»: нет успешного запуска {_minutes(now - beat.success_at)}")
        elif beat.failures:
            status = "warn"
        else:
            status = "ok"
        groups.append(GroupHealth(
            key=group.key, label=group.label, status=status, success_at=beat.success_at, failure_at=beat.failure_at,
            error=beat.error, duration_ms=beat.duration_ms, failures=beat.failures, max_silence=group.max_silence,
        ))
    topics: list[TopicHealth] = []
    for item in sorted(pending, key=lambda item: item.oldest_at):
        age = max(now - item.oldest_at, timedelta(0))
        status = "red" if age > pending_alert_after else "ok"
        if status == "red":
            problems.append(f"Очередь «{item.topic}»: сообщение ждёт {_minutes(age)}")
        topics.append(TopicHealth(topic=item.topic, count=item.count, oldest_at=item.oldest_at, age=age, status=status))
    return BackgroundHealth(ok=not problems, groups=tuple(groups), topics=tuple(topics), problems=tuple(problems))
