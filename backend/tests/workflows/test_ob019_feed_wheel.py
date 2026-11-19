"""ОБ.019 «Подъём подающих роликов» (key feed_wheel, one switch bit, runner 1.3.0+): read-only display, a write as one
bit, the old runner's refusal. The register is D604.0 (Jog «Wheel», owner bench probe 19.11), one constant per side."""

import re
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from tests.identity.test_api_tokens import _csrf, tokens_api  # noqa: F401
from tests.workflows.test_machines_api import WORDS, _bridge, _report, machines_api  # noqa: F401

CARD = "/api/production/machines/card/ОБ.019"
NEXT = "/api/production/machines/agent/writes/next"
AGENT = {"inventory": "ОБ.019", "machine_id": "guid-108"}
AGENT_12 = {**AGENT, "runner_version": "1.2.0"}
AGENT_13 = {**AGENT, "runner_version": "1.3.0"}
ROOT = Path(__file__).parents[3] / "tools" / "machine-agent"


def _post(client, path, body):
    return client.post(path, json=body, headers=_csrf(client))


def _result(bridge, command_id, readback, agent, ok=True, error=""):
    return TestClient(app).post(f"/api/production/machines/agent/writes/{command_id}/result", headers=bridge,
                                json={**agent, "ok": ok, "readback": readback, "error": error})


def test_the_register_is_one_constant_and_the_agent_mirrors_it():
    field = rules.OB019_FIELDS["feed_wheel"]
    assert (field.label, field.kind, field.address) == ("Подъём подающих роликов", "bit0", 604)
    assert field.address == rules.OB019_FEED_WHEEL_ADDRESS and (604, 0) in rules.OB019_WRITTEN_BITS
    assert 604 in rules.OB019_ALLOWED and 604 not in rules.OB019_WRITTEN_WORDS
    bridge = (ROOT / "ob019-bridge.ps1").read_text(encoding="ascii")
    assert re.search(r"^\$script:FeedWheelAddress = (\d+)$", bridge, re.M).group(1) == str(rules.OB019_FEED_WHEEL_ADDRESS)
    assert "@(20, 74, 142, 197, 604" in bridge
    assert "$script:RunnerVersion = '1.3.0'" in (ROOT / "ob019-writer.ps1").read_text(encoding="ascii")
    assert commands.runner_current("1.3.0", commands.RUNNER_FEED_WHEEL) and not commands.runner_current("1.2.0", "1.3.0")
    assert commands.field_needs_runner(field)


def test_the_snapshot_shows_the_switch_read_only_on_any_runner(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    bridge = _bridge(admin)
    _report(bridge, registers={**WORDS, "604": 1})
    fields = {f["label"]: f for g in admin.get(CARD).json()["settings"] for f in g["fields"]}
    assert fields["Подъём подающих роликов"]["value"] is True
    assert fields["Подъём подающих роликов"]["key"] == "feed_wheel" and fields["Подъём подающих роликов"]["read"]
    _report(bridge, seq=2, registers={**WORDS, "604": 0x0002})
    fields = {f["label"]: f for g in admin.get(CARD).json()["settings"] for f in g["fields"]}
    assert fields["Подъём подающих роликов"]["value"] is False


def test_the_lift_is_claimed_as_one_bit_and_confirmed_by_that_bit_only(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    commands._runners.clear()
    bridge = _bridge(admin)
    _report(bridge, registers={**WORDS, "604": 0x0A00})
    first = _post(admin, CARD + "/write", {"values": {"feed_wheel": "1"}}).json()["id"]
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT_13).json()["command"]
    assert claimed["blocks"] == [] and claimed["bits"] == [{"addr": 604, "bit": 0, "value": 1}]
    assert _result(bridge, first, {"604": 0x0A01}, AGENT_13).json() == {"status": "done", "error": ""}
    second = _post(admin, CARD + "/write", {"values": {"feed_wheel": "0"}}).json()["id"]
    TestClient(app).post(NEXT, headers=bridge, json=AGENT_13)
    assert _result(bridge, second, {"604": 0x0A01}, AGENT_13).json() == {
        "status": "failed", "error": "Станок не принял: D604.0"}


def test_a_runner_older_than_1_3_0_refuses_the_lift_at_once_and_at_the_claim(machines_api):
    sessions, clock, admin, ob019, _ = machines_api
    commands._runners.clear()
    bridge = _bridge(admin)
    _report(bridge)
    queued = _post(admin, CARD + "/write", {"values": {"feed_wheel": "1"}}).json()["id"]
    assert TestClient(app).post(NEXT, headers=bridge, json=AGENT_12).json() == {"command": None}
    with sessions() as session:
        row = session.get(commands.MachineWriteRow, UUID(queued))
        assert (row.status, row.error, row.taken_at) == ("failed", commands.RUNNER_FEED_WHEEL_OUTDATED, None)
    control = admin.get(CARD + "/control").json()
    assert "feed_wheel" in control["runner_keys"]
    refused = _post(admin, CARD + "/write", {"values": {"feed_wheel": "1"}})
    assert refused.status_code == 409 and refused.json()["detail"] == commands.RUNNER_FEED_WHEEL_OUTDATED
    # The 1.2.0 runner still gets the pages it can do whole.
    ok = _post(admin, CARD + "/write", {"values": {"marking": "1"}})
    assert ok.status_code == 200, ok.text
    claimed = TestClient(app).post(NEXT, headers=bridge, json=AGENT_12).json()["command"]
    assert claimed["bits"] == [{"addr": 400, "bit": 0, "value": 1}]
