from uuid import uuid4

import pytest

from app.modules.production.resource_assignments import MachineBinding, PhysicalAllocation
from app.modules.production.resource_claims import ResourceConflict


def test_independent_people_are_not_implicitly_a_crew():
    with pytest.raises(ResourceConflict, match="бригад"):
        PhysicalAllocation(uuid4(), uuid4(), (uuid4(), uuid4()), ())


def test_explicit_crew_and_multiple_etk_machines_are_preserved():
    bindings = (MachineBinding(1, str(uuid4())), MachineBinding(3, str(uuid4())))
    allocation = PhysicalAllocation(uuid4(), uuid4(), (uuid4(), uuid4()), bindings, crew=True)
    assert allocation.machines == bindings


def test_duplicate_position_cannot_silently_replace_a_machine():
    with pytest.raises(ResourceConflict, match="дважды"):
        PhysicalAllocation(uuid4(), uuid4(), (uuid4(),), (MachineBinding(1, str(uuid4())), MachineBinding(1, str(uuid4()))))


@pytest.mark.parametrize("position", [True, 0, -1, 1.5])
def test_position_is_an_explicit_positive_integer(position):
    with pytest.raises(ResourceConflict):
        MachineBinding(position, str(uuid4()))


@pytest.mark.parametrize("machine", ["ОБ.027.2", "", "  ", None, "00000000-0000-0000-0000-000000000000"])
def test_machine_is_the_unit_uuid_never_an_inventory_number(machine):
    with pytest.raises(ResourceConflict, match="UUID"):
        MachineBinding(1, machine)


def test_unit_uuid_is_kept_canonical():
    unit = uuid4()
    assert MachineBinding(1, str(unit).upper()).machine_id == str(unit) == MachineBinding(1, unit).machine_id
