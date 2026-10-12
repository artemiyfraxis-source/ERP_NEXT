"""Dry-run report: manual «Статус» marks that disagree with the derived status (owner 12.10)."""

from datetime import date, datetime

from app.modules.production.plan import PLAN_KINDS, PlanEvent, PlanProjectSnapshot
from app.workflows import plan_status_drift as cli
from app.workflows.production_plan import build_production_plan

TODAY = date(2026, 10, 12)


def _mark(project, value, who="Мастер", day=10):
    return PlanEvent(project=project, occurred_at=datetime(2026, 10, day, 9, 30), who=who,
                     kind=PLAN_KINDS["mark"], field="status", value=value)


def _block(project, blocker):
    return PlanEvent(project=project, occurred_at=datetime(2026, 10, 9), who="Мастер",
                     kind=PLAN_KINDS["block"], blocker=blocker, text="ждём")


def _drift(events, projects):
    rows = build_production_plan(
        cards=(), tasks=(), fulfillment=(), goods=(), requests=(), procurement=(),
        projects=[PlanProjectSnapshot(project=name, status="Новый") for name in projects],
        events=events, today=TODAY,
    )
    return cli.status_drift(rows, events)


def test_reports_only_marks_that_disagree_with_stage_and_blocker():
    events = [
        _block("1К.1", "ХОЛД"), _mark("1К.1", "холд"),               # derived «ХОЛД, Новый» -> disagrees
        _block("2К.2", "ХОЛД"), _mark("2К.2", "ХОЛД, Новый"),        # same set as derived -> agrees
        _mark("3К.3", "В РАБОТЕ", who="Витя"),                       # stage is «Новый» -> disagrees
        _mark("4К.4", "ГОТОВО"), _mark("4К.4", "авто", day=11),      # cleared -> not reported
        _mark("5К.5", "ГОТОВО"), _mark("5К.5", "Новый", day=11),     # the last mark agrees
    ]
    drift = _drift(events, ["1К.1", "2К.2", "3К.3", "4К.4", "5К.5"])
    assert [item.project for item in drift] == ["1К.1", "3К.3"]
    third = drift[1]
    assert (third.mark, third.derived, third.who) == ("В РАБОТЕ", "Новый", "Витя")


def test_report_text_lists_project_mark_derived_who_when_and_empty_case():
    text = cli.format_drift(_drift([_mark("3К.3", "В РАБОТЕ", who="Витя")], ["3К.3"]))
    assert "3К.3: В РАБОТЕ | Новый | Витя | 10.10.2026 09:30" in text
    assert "без записи" in text
    assert "Расхождений нет" in cli.format_drift([])


def test_cli_needs_database_url(monkeypatch, capsys):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    assert cli.main([]) == 2
    assert "ERP_DATABASE_URL" in capsys.readouterr().err
