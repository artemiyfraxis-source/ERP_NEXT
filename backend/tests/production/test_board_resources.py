import pytest
from sqlalchemy import select

from app.main import app
from app.modules.production.infrastructure import SqlAlchemyProductionUnitOfWork
from app.modules.production.resource_assignment_infrastructure import ResourceAllocationRow, SqlAlchemyProductionResources
from app.modules.production.resource_infrastructure import ResourceGenerationRow
from app.modules.references.equipment import PhysicalEquipment
from app.modules.production.resource_claims import ResourceConflict
from app.workflows.production_resource_context import SqlAlchemyProductionResourceContext
from app.shared.http_auth import Principal, authenticated_principal
from app.shared.identity import Actor
from tests.production.test_production_board import (
    ANNA, assign, board, client, future_items, issue_route, launch, reissue, tasks_of,
)


def preview(http, items):
    return http.post('/api/production/board/resources', json={
        'operator_id': str(ANNA), 'items': items,
    })


def resources(monkeypatch, *, reject=False):
    calls = []
    original = app.state.production_uow_factory

    class Resources:
        def describe(self, task, operation_id, operators):
            calls.append(('describe', task, operation_id, operators))
            return {'enabled': True, 'requirements': [], 'blocked_reason': None}

        def ensure_assignment(self, task, operation_id, operators, machines):
            calls.append(('assign', task, operation_id, operators, machines))
            if reject:
                raise ResourceConflict('Станок занят')

        def flush(self):
            pass

    class Wrapped:
        def __init__(self):
            self.inner = original()

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def __enter__(self):
            self.inner.__enter__()
            self.inner.resources = Resources()
            return self

        def __exit__(self, *args):
            return self.inner.__exit__(*args)

    monkeypatch.setattr(app.state, 'production_uow_factory', Wrapped)
    return calls


def test_future_preview_is_pinned_read_only_and_stale_card_is_rejected(client, monkeypatch):
    http, _ = client
    card = issue_route('КБ1')
    [future] = board(http)['future']
    items = future_items(future, 2)
    calls = resources(monkeypatch)
    response = preview(http, items)
    assert response.status_code == 200, response.text
    assert response.json()['items'][0]['index'] == 0
    _, task, operation_id, operators = calls[0]
    assert task.techcard_id == card.id and task.techcard_revision == card.revision
    assert task.operation(operation_id).position == 2 and operators == (ANNA,)
    assert tasks_of(http) == {}
    reissue(card)
    assert preview(http, items).status_code == 409
    assert len(calls) == 1


@pytest.mark.parametrize('reject', [False, True])
def test_future_assignment_passes_explicit_binding_in_same_transaction(client, monkeypatch, reject):
    http, _ = client
    issue_route('КБ1')
    [future] = board(http)['future']
    items = future_items(future, 2)
    items[0]['machine_bindings'] = [{'etk_position': 2, 'machine_id': 'ОБ.027.2'}]
    calls = resources(monkeypatch, reject=reject)
    response = assign(http, ANNA, items)
    assert response.status_code == (409 if reject else 200), response.text
    [call] = [call for call in calls if call[0] == 'assign']
    assert call[4][0].etk_position == 2 and call[4][0].machine_id == 'ОБ.027.2'
    assert call[1].operation(call[2]).position == 2
    assert bool(tasks_of(http)) is not reject


def test_disabled_runtime_does_not_silently_drop_board_binding(client):
    http, _ = client
    issue_route('КБ1')
    [future] = board(http)['future']
    items = future_items(future, 2)
    assert preview(http, items).json()['items'][0]['resources']['enabled'] is False
    items[0]['machine_bindings'] = [{'etk_position': 2, 'machine_id': 'ОБ.027.2'}]
    assert assign(http, ANNA, items).status_code == 422
    assert tasks_of(http) == {}


def test_board_binding_payload_is_validated(client):
    http, _ = client
    issue_route('КБ1')
    [future] = board(http)['future']
    items = future_items(future, 2)
    items[0]['machine_bindings'] = [{'etk_position': 0, 'machine_id': ''}]
    assert assign(http, ANNA, items).status_code == 422


def test_existing_preview_checks_version_and_keeps_input_order(client, monkeypatch):
    http, _ = client
    task = launch(http, 'КБ1')
    items = [{'task_id': task['id'], 'task_version': task['version'], 'operation_id': row['id']}
             for row in reversed(task['operations'])]
    calls = resources(monkeypatch)
    response = preview(http, items)
    assert response.status_code == 200, response.text
    assert [row['index'] for row in response.json()['items']] == list(range(len(items)))
    assert [str(call[2]) for call in calls] == [row['operation_id'] for row in items]
    items[0]['task_version'] += 1
    assert preview(http, items).status_code == 409


def test_resource_preview_requires_overview_permission(client):
    http, _ = client
    task = launch(http, 'КБ1')
    app.dependency_overrides[authenticated_principal] = lambda: Principal(
        actor=Actor(id=ANNA, name='Читатель', department='Производство'), email='reader@example.test',
        roles=frozenset({'staff'}), permissions={'myops': 'view'},
    )
    assert preview(http, [{'task_id': task['id'], 'task_version': task['version'],
                          'operation_id': task['operations'][0]['id']}]).status_code == 403


def test_board_uses_real_sql_resource_adapter_for_future_task(client, monkeypatch):
    http, sessions = client
    issue_route('КБ1')
    [future] = board(http)['future']
    items = future_items(future, 2)
    with sessions() as session:
        session.add(ResourceGenerationRow(id=1, version=0))
        session.commit()

    class Catalog(SqlAlchemyProductionResourceContext):
        def catalog(self):
            return (PhysicalEquipment('ОБ.027.2', 'ОБ.027.2', 'Пресс', 'Модель', 'ЦАР'),)

    monkeypatch.setattr(app.state, 'production_uow_factory', lambda: SqlAlchemyProductionUnitOfWork(
        sessions, lambda session: SqlAlchemyProductionResources(session, Catalog(session)),
    ))
    response = preview(http, items)
    assert response.status_code == 200, response.text
    choice = response.json()['items'][0]['resources']
    assert choice['blocked_reason'] is None
    assert choice['requirements'][0]['machines'][0]['id'] == 'ОБ.027.2'
    assert assign(http, ANNA, items).status_code == 409
    assert tasks_of(http) == {}
    items[0]['machine_bindings'] = [{'etk_position': 2, 'machine_id': 'ОБ.027.2'}]
    response = assign(http, ANNA, items)
    assert response.status_code == 200, response.text
    with sessions() as session:
        [allocation] = session.scalars(select(ResourceAllocationRow)).all()
        assert allocation.machines == [{'etk_position': 2, 'machine_id': 'ОБ.027.2'}]
