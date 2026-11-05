from uuid import uuid4

import pytest

from app.modules.production.resource_assignments import MachineBinding, PhysicalAllocation
from app.modules.production.resource_claims import ResourceConflict


def test_independent_people_are_not_implicitly_a_crew():
    with pytest.raises(ResourceConflict, match="бригад"):
        PhysicalAllocation(uuid4(), uuid4(), (uuid4(), uuid4()), ())


def test_explicit_crew_and_multiple_etk_machines_are_preserved():
    bindings = (MachineBinding(1, "ОБ.027.2"), MachineBinding(3, "ОБ.098"))
    allocation = PhysicalAllocation(uuid4(), uuid4(), (uuid4(), uuid4()), bindings, crew=True)
    assert allocation.machines == bindings


def test_duplicate_position_cannot_silently_replace_a_machine():
    with pytest.raises(ResourceConflict, match="дважды"):
        PhysicalAllocation(uuid4(), uuid4(), (uuid4(),), (MachineBinding(1, "ОБ.1"), MachineBinding(1, "ОБ.2")))


@pytest.mark.parametrize("position", [True, 0, -1, 1.5])
def test_position_is_an_explicit_positive_integer(position):
    with pytest.raises(ResourceConflict):
        MachineBinding(position, "ОБ.027.2")
