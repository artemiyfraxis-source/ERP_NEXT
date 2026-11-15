"""Bounded joint allocation of two RF paths, including a realizable calibration."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from itertools import product
from uuid import UUID

from .rf_interfaces import RfDetails, RfInterface, mates


@dataclass(frozen=True)
class RfAsset:
    id: UUID
    label: str
    rf: RfDetails
    quantity: int = 1
    available: bool = True
    characterization_file_id: UUID | None = None


@dataclass(frozen=True)
class Leg:
    asset: RfAsset
    entry: int

    @property
    def end(self):
        return self.asset.rf.interfaces[1 - self.entry]


def _covers(asset, band):
    rf = asset.rf
    return (asset.available and asset.quantity >= 1 and rf.confirmed and bool(rf.interfaces)
            and rf.f_min_ghz is not None and rf.f_min_ghz <= band[0]
            and all(p.f_confirmed and p.impedance_ohm == 50 and p.f_max_ghz is not None
                    and p.f_max_ghz >= band[1] for p in rf.interfaces))


def _paths(start, target, assets, *, max_depth, max_states, max_paths):
    queue = deque([(start, ())])
    paths, visited = [], 0
    while queue and visited < max_states and len(paths) < max_paths:
        end, path = queue.popleft()
        visited += 1
        if mates(end, target):
            paths.append(path)
        if len(path) >= max_depth:
            continue
        counts = Counter(leg.asset.id for leg in path)
        for asset in assets:
            if counts[asset.id] >= asset.quantity:
                continue
            for entry, connector in enumerate(asset.rf.interfaces):
                if mates(end, connector):
                    # Reversing an electrically symmetric item produces the same route.
                    if entry == 1 and asset.rf.interfaces[0] == asset.rf.interfaces[1]:
                        continue
                    queue.append((asset.rf.interfaces[1 - entry], (*path, Leg(asset, entry))))
                    if len(queue) + visited >= max_states:
                        break
            if len(queue) + visited >= max_states:
                break
    return paths, bool(queue)


def _kit_connections(kit, ends):
    rf = kit.rf
    if rf.role == "auto_calibration":
        for order in ((0, 1), (1, 0)):
            if all(mates(end, rf.interfaces[p]) for end, p in zip(ends, order)):
                return "АКМ", [dict(kind="АКМ", port=i + 1, standard=f"Порт {p + 1} АКМ",
                                    kit_id=str(kit.id), kit_label=kit.label) for i, p in enumerate(order)]
        return None
    if rf.role != "solt_kit" or rf.kit_definition != "6650F27-full":
        return None
    # A kit label alone never implies its contents. Both interfaces and the full definition must agree.
    by_body = {p.body: p for p in rf.interfaces if p.series == "3.5mm"
               and p.center == ("штырь" if p.body == "вилка" else "гнездо")}
    if len(by_body) != 2:
        return None
    standards = []
    genders = []
    for i, end in enumerate(ends, 1):
        matching = next((p for p in by_body.values() if mates(end, p)), None)
        if matching is None:
            return None
        genders.append(matching.body)
        for kind, label in (("OPEN", "Холостой ход"), ("SHORT", "Короткое замыкание"), ("LOAD", "Согласованная нагрузка")):
            standards.append(dict(kind=kind, port=i, standard=f"{label}, {matching.label}",
                                  kit_id=str(kit.id), kit_label=kit.label))
    thru = "MA6666A-11" if genders == ["вилка", "вилка"] else "MA6666B-11" if genders == ["розетка", "розетка"] else "MA6666C-11"
    standards.append(dict(kind="THRU", ports=[1, 2], standard=thru, kit_id=str(kit.id), kit_label=kit.label,
                          interfaces=genders, directions=["1 → 2", "2 → 1"]))
    return "SOLT 6650F27", standards


def _two_port_plan(instrument: RfAsset, dut: tuple[RfInterface, RfInterface], assets: list[RfAsset],
                     band_ghz: tuple[float, float], *, max_depth=4, max_states=4000, max_paths=96) -> dict:
    def blocked(message, **extra):
        return dict(status="blocked", problems=[message], calibration_connections=[], measurement_connections=[],
                    calibration_plane=[], method=None, automatic_acceptance=False, **extra)

    if len(dut) != 2 or any(p.impedance_ohm != 50 or p.f_max_ghz is None or p.f_max_ghz < band_ghz[1] for p in dut):
        return blocked("Интерфейсы изделия должны иметь подтверждённые 50 Ом и покрывать всю полосу нормы")
    if instrument.rf.role != "vna" or len(instrument.rf.interfaces) != 2 or not _covers(instrument, band_ghz):
        return blocked("ВАЦ недоступен либо его ВЧ-интерфейсы и рабочая полоса не подтверждены")
    eligible = sorted((a for a in assets if a.id != instrument.id and _covers(a, band_ghz)), key=lambda a: (
        a.rf.role != "test_cable", a.label, str(a.id)))
    links = [a for a in eligible if a.rf.role in ("adapter", "test_cable") and len(a.rf.interfaces) == 2]
    kits = sorted((a for a in eligible if a.rf.role in ("auto_calibration", "solt_kit")),
                  key=lambda a: (a.rf.role != "auto_calibration", a.label))
    if not kits:
        return blocked("Нет доступного подтверждённого АКМ или полного 6650F27 на всю полосу нормы")
    routes = [_paths(p, d, links, max_depth=max_depth, max_states=max_states, max_paths=max_paths)
              for p, d in zip(instrument.rf.interfaces, dut)]
    truncated = any(result[1] for result in routes)
    best = None
    for paths in product(routes[0][0], routes[1][0]):
        counts = Counter(leg.asset.id for path in paths for leg in path)
        if any(counts[leg.asset.id] > leg.asset.quantity for path in paths for leg in path):
            continue
        # Every prefix is a possible calibration plane; remaining legs are post-calibration adapters.
        for planes in product(*(range(len(path), -1, -1) for path in paths)):
            if any(any(leg.asset.rf.role != "adapter" for leg in path[n:]) for path, n in zip(paths, planes)):
                continue
            ends = tuple(path[n - 1].end if n else port for path, n, port in zip(paths, planes, instrument.rf.interfaces))
            for kit in kits:
                calibration = _kit_connections(kit, ends)
                if calibration is None:
                    continue
                method, standards = calibration
                after = sum(len(path) - n for path, n in zip(paths, planes))
                cables = sum(any(leg.asset.rf.role == "test_cable" for leg in path) for path in paths)
                adapters = sum(leg.asset.rf.role == "adapter" for path in paths for leg in path)
                score = (after > 0, method != "АКМ", -cables, adapters, after, sum(map(len, paths)))
                if best is None or score < best[0]:
                    best = (score, paths, planes, ends, method, standards, kit)
    if best is None:
        return blocked("Не найдены две совместимые цепи с доступным количеством принадлежностей и калибровочным проходом",
                       search_limited=truncated)
    _, paths, planes, ends, method, standards, kit = best
    slots = Counter()
    measurements, calibration_paths, plane_labels = [], [], []
    for i, (path, plane, end, target) in enumerate(zip(paths, planes, ends, dut), 1):
        steps = []
        for leg in path:
            slots[leg.asset.id] += 1
            steps.append(dict(instrument_id=str(leg.asset.id), unit_slot=slots[leg.asset.id], label=leg.asset.label,
                              entry=leg.entry + 1, from_interface=leg.asset.rf.interfaces[leg.entry].label,
                              to_interface=leg.end.label, role=leg.asset.rf.role))
        measurements.append(dict(port=i, label=f"Порт {i} → сторона {'А' if i == 1 else 'Б'} изделия", steps=steps,
                                 dut_interface=target.label, after_calibration=steps[plane:]))
        calibration_paths.append(dict(port=i, steps=steps[:plane], interface=end.label))
        plane_labels.append(dict(port=i, after_items=plane, interface=end.label,
                                 label=f"Порт {i}: {end.label} — плоскость калибровки"))
    qualified = any(len(path) != plane for path, plane in zip(paths, planes))
    post_adapters = [leg.asset for path, plane in zip(paths, planes) for leg in path[plane:]]
    characterized = bool(post_adapters) and all(_has_characterization(a) for a in post_adapters)
    return dict(status="qualified" if qualified else "ready", mode="two_port", one_attempt=True, problems=[], search_limited=truncated,
                method="с исключением переходника (де-эмбеддинг)" if characterized else "через переходник после калибровки" if qualified else method,
                qualification="Характеризация требует применения и проверки" if characterized else "через переходник, вклад не исключён" if qualified else "На интерфейсах изделия",
                characterizations=_characterizations(post_adapters), calibration_method=method,
                automatic_acceptance=not qualified, calibration_plane=plane_labels,
                calibration_connections=dict(chains=calibration_paths, standards=standards,
                                             kit_id=str(kit.id), unit_slot=1),
                measurement_connections=measurements,
                warnings=["Де-эмбеддинг запланирован; подтвердите ориентацию файлов, примените и проверьте коррекцию"] if characterized else
                         ["Вклад переходников после калибровки не исключён; автоматическая приёмка запрещена"] if qualified else [],
                allocations=[dict(instrument_id=str(key), quantity=value) for key, value in sorted(slots.items(), key=lambda p: str(p[0]))])


def _has_characterization(asset):
    # A file belongs to a physical piece, never all slots of a pooled registry row.
    return asset.quantity == 1 and asset.characterization_file_id is not None


def _characterizations(assets):
    return [dict(instrument_id=str(a.id), file_id=str(a.characterization_file_id), requires_orientation_confirmation=True)
            for a in {a.id: a for a in assets}.values() if _has_characterization(a)]


def _one_port_calibration(kit, end):
    if kit.rf.role == "auto_calibration":
        for i, connector in enumerate(kit.rf.interfaces, 1):
            if mates(end, connector):
                return "АКМ, один порт", [dict(kind="АКМ", port=1, standard=f"Порт {i} АКМ", kit_id=str(kit.id), kit_label=kit.label)]
    if kit.rf.role == "solt_kit" and kit.rf.kit_definition == "6650F27-full":
        # Reuse the verified full-set check; only the port-1 SOL steps are acquired.
        calibration = _kit_connections(kit, (end, end))
        if calibration:
            return "SOL 6650F27", [s for s in calibration[1] if s.get("port") == 1]
    return None


def _steps(path, slots):
    result = []
    for leg in path:
        slots[leg.asset.id] += 1
        result.append(dict(instrument_id=str(leg.asset.id), unit_slot=slots[leg.asset.id], label=leg.asset.label,
                           entry=leg.entry + 1, from_interface=leg.asset.rf.interfaces[leg.entry].label,
                           to_interface=leg.end.label, role=leg.asset.rf.role))
    return result


def _one_orientation(port, measured, far_end, links, kits, loads, limits):
    paths, truncated = _paths(port, measured, links, **limits)
    adapters = [a for a in links if a.rf.role == "adapter"]
    terminations = []
    for load in loads:
        for connector in load.rf.interfaces:
            tails, limited = _paths(far_end, connector, adapters, **{**limits, "max_depth": min(2, limits["max_depth"]), "max_paths": 24})
            truncated |= limited
            terminations.extend((load, connector, tail) for tail in tails)
    best = None
    for path, (load, connector, tail) in product(paths, terminations):
        counts = Counter(leg.asset.id for leg in (*path, *tail))
        if any(counts[leg.asset.id] > leg.asset.quantity for leg in (*path, *tail)):
            continue
        for plane in range(len(path), -1, -1):
            if any(leg.asset.rf.role != "adapter" for leg in path[plane:]):
                continue
            end = path[plane - 1].end if plane else port
            for kit in kits:
                cal = _one_port_calibration(kit, end)
                if cal is None:
                    continue
                score = (plane != len(path), kit.rf.role != "auto_calibration", bool(tail),
                         not any(leg.asset.rf.role == "test_cable" for leg in path), len(path) + len(tail))
                if best is None or score < best[0]:
                    best = (score, path, plane, end, kit, cal, load, connector, tail)
    return best, truncated


def plan_connections(instrument: RfAsset, dut: tuple[RfInterface, RfInterface], assets: list[RfAsset],
                     band_ghz: tuple[float, float], *, max_depth=4, max_states=4000, max_paths=96) -> dict:
    limits = dict(max_depth=max_depth, max_states=max_states, max_paths=max_paths)
    two_port = _two_port_plan(instrument, dut, assets, band_ghz, **limits)
    if two_port["status"] != "blocked":
        return two_port  # Two-port measurement is the owner's default, including qualified adapter methods.
    if (len(dut) != 2 or instrument.rf.role != "vna" or len(instrument.rf.interfaces) != 2 or
            not _covers(instrument, band_ghz) or any(p.impedance_ohm != 50 or p.f_max_ghz is None or p.f_max_ghz < band_ghz[1] for p in dut)):
        return two_port
    eligible = sorted((a for a in assets if a.id != instrument.id and _covers(a, band_ghz)), key=lambda a: (a.label, str(a.id)))
    links = [a for a in eligible if a.rf.role in ("adapter", "test_cable") and len(a.rf.interfaces) == 2]
    kits = [a for a in eligible if a.rf.role in ("auto_calibration", "solt_kit")]
    # Reference SOLT standards are never DUT terminations. SMA-MF06 contributes only its explicitly described loads.
    loads = [a for a in eligible if a.rf.role == "load" or
             (a.rf.role == "load_kit" and a.rf.kit_definition == "SMA-MF06-loads" and all(p.series == "SMA" for p in a.rf.interfaces))]
    if not loads:
        return {**two_port, "problems": [*two_port["problems"], "Для однопортового измерения нужна нагрузка SMA 50 Ом либо согласованная нагрузка серии изделия; стандарты 6650F27 использовать нельзя"]}
    orientations = []
    truncated = two_port.get("search_limited", False)
    allocations = Counter()
    characterizations = []
    all_characterized = True
    has_post_adapters = False
    for index, (measured, far_end) in enumerate((dut, tuple(reversed(dut)))):
        best, limited = _one_orientation(instrument.rf.interfaces[0], measured, far_end, links, kits, loads, limits)
        truncated |= limited
        if best is None:
            return {**two_port, "search_limited": truncated, "problems": [*two_port["problems"],
                f"Сторона {'А' if index == 0 else 'Б'}: не хватает совместимых переходников для измерительной цепи и нагрузки 50 Ом одновременно"]}
        _, path, plane, end, kit, cal, load, connector, tail = best
        slots = Counter()
        steps, termination_steps = _steps(path, slots), _steps(tail, slots)
        slots[load.id] += 1
        allocations |= slots  # Orientations are sequential, unlike the two ends within each orientation.
        post_adapters = [leg.asset for leg in path[plane:]]
        has_post_adapters |= bool(post_adapters)
        characterizations.extend(_characterizations(post_adapters))
        all_characterized &= all(_has_characterization(a) for a in post_adapters)
        orientations.append(dict(side="А" if index == 0 else "Б", label="Измерить сторону А" if index == 0 else "Перевернуть изделие и измерить сторону Б",
            measurement=dict(port=1, steps=steps, dut_interface=measured.label, after_calibration=steps[plane:]),
            termination=dict(label="Составная нагрузка 50 Ом" if tail else "Согласованная нагрузка 50 Ом", steps=termination_steps,
                             load_id=str(load.id), load_label=load.label, unit_slot=1, interface=connector.label,
                             contribution="Вклад оконечной нагрузки и её переходников не исключён"),
            calibration=dict(method=cal[0], steps=steps[:plane], standards=cal[1], kit_id=str(kit.id), unit_slot=1),
            calibration_plane=dict(port=1, after_items=plane, interface=end.label, label=f"Порт 1: {end.label} — плоскость калибровки")))
    deembed = has_post_adapters and all_characterized
    return dict(status="qualified", mode="one_port_reversal", one_attempt=True, automatic_acceptance=False,
                method="с исключением переходника (де-эмбеддинг)" if deembed else
                       "через переходник, вклад не исключён" if has_post_adapters else "Однопортовое измерение с переворотом",
                calibration_method="; ".join(dict.fromkeys(o["calibration"]["method"] for o in orientations)),
                problems=[], search_limited=truncated, orientations=orientations, characterizations=characterizations,
                calibration_connections=[o["calibration"] for o in orientations],
                measurement_connections=[o["measurement"] for o in orientations],
                calibration_plane=[o["calibration_plane"] for o in orientations],
                warnings=["Обе стороны измеряются с переворотом в одной попытке; сохраняйте состав оконечной нагрузки",
                          "Вклад оконечной нагрузки не исключён; автоматическая приёмка запрещена"] +
                         (["Де-эмбеддинг запланирован; подтвердите ориентацию файлов, примените и проверьте коррекцию"] if deembed else []),
                allocations=[dict(instrument_id=str(key), quantity=value) for key, value in sorted(allocations.items(), key=lambda p: str(p[0]))])
