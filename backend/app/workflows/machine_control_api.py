"""HTTP: ОБ.019 machine commands from the card (plan «Станки», slice 3). Rules: production/machine_control.py.

- `POST /api/production/machines/card/{ОБ}/command` `{command, confirm_state?, hands_clear?}` → the queued command
  (the same view as the card's writes in `GET …/control`, which also says `can_control`).
  `stop` — the card's gate only (Производство or Задания, view), no confirmation. `reset` / `start` / `alarm_clear` —
  also «Управление станками» on edit (403), `hands_clear: true` and the D766 state the person confirmed (409 when it
  changed, when the state does not allow the command or another command is open).
The runner on the machine PC takes them through `…/agent/writes/next` (workflows/machine_writes_api.py)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.modules.production import machine_control
from app.modules.production import machine_writes as commands
from app.modules.production import machines as rules
from app.modules.production.api import production_uow_factory
from app.shared.clock import moscow_now

from .machine_writes_api import _closed, _journal, _link, can_control
from .machines_api import Reader

router = APIRouter(prefix="/api/production/machines", tags=["production"])
Factory = Annotated[Callable, Depends(production_uow_factory)]
Now = Annotated[datetime, Depends(moscow_now)]


class CommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: Literal["stop", "reset", "start", "alarm_clear"]
    confirm_state: int | None = Field(default=None, ge=0, le=rules.WORD_MAX)
    hands_clear: bool = False


@router.post("/card/{inventory}/command")
def send_command(inventory: str, payload: CommandPayload, principal: Reader, factory: Factory, now: Now) -> dict:
    with factory() as uow:
        link = _link(uow.session, inventory)
        try:
            row, closed = machine_control.request(
                uow.session, equipment_id=link.equipment_id, command=payload.command, actor_id=principal.actor.id,
                actor_name=principal.actor.name, can_control=can_control(principal), state_seen=payload.confirm_state,
                hands_clear=payload.hands_clear, now=now)
        except machine_control.CommandRefused as error:
            uow.commit()  # what expired meanwhile stays expired
            raise HTTPException(error.status, str(error)) from error
        _closed(uow, closed, principal.actor, now)
        state = row.payload.get("state_seen")
        state_text = rules.OB019_STATES.get(state, (f"код {state}", ""))[0] if state is not None else "—"
        _journal(uow, link.equipment_id, "command_requested", principal.actor, now,
                 f"{row.summary}: станок «{state_text}»" + (", руки убраны — подтверждено" if payload.hands_clear else ""),
                 command_id=str(row.id), command=row.command or "", state_seen="" if state is None else str(state))
        view = commands.command_view(row)
        uow.commit()
    return view
