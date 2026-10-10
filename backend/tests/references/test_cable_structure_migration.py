"""0096_cable_structure: new tables over real БД.ПРОВ rows; nothing existing changes."""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config


def _config(database_path: Path) -> Config:
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")
    return config


def _record(connection, tab: str, fields: dict, edited: bool = False) -> None:
    display = next(value for value in fields.values() if value)
    connection.execute(
        "INSERT INTO tech_operation_records (id, tab, display_text, normalized_search, source_sheet, sort_key, fields,"
        " mirror_key, edited_in_erp) VALUES (?, ?, ?, ?, '', ?, ?, '', ?)",
        (uuid4().hex, tab, display, display.casefold(), display, json.dumps(fields, ensure_ascii=False), int(edited)),
    )


def _entry(connection, kind: str, key: str, normalized: str, value: str, position: int) -> None:
    connection.execute(
        "INSERT INTO reference_entries (id, kind, key, normalized_key, value, position) VALUES (?, ?, ?, ?, ?, ?)",
        (uuid4().hex, kind, key, normalized, value, position),
    )


def _wire(mark, kind="монтажный", section="", awg="", cores="1", outer="", voltage="", t_min="", t_max=""):
    return {"Марка": mark, "Тип провода": kind, "Сечение, мм2": section, "AWG": awg, "Число жил": cores,
            "Наружный диаметр, мм": outer, "Изоляция": "ПВХ", "Напряжение, В": voltage, "Темп. от, °C": t_min,
            "Темп. до, °C": t_max}


def test_migration_structures_parsable_hookup_wires_and_keeps_every_existing_row(tmp_path, monkeypatch):
    monkeypatch.delenv("ERP_DATABASE_URL", raising=False)
    database = tmp_path / "erp.db"
    config = _config(database)
    command.upgrade(config, "0088_api_tokens_assembly_drafts")
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM reference_entries WHERE kind = 'gost_awg'")
        _record(connection, "prov", _wire("ПВАМ 0,50", "силовой", "0,5", outer="1,6", voltage="48 (ном.) / 1000 (испыт.)",
                                          t_min="-40", t_max="105"), edited=True)
        _record(connection, "prov", _wire("UL1061 28AWG", section="0,08", awg="28", outer="0,89", t_min="-10", t_max="80"))
        _record(connection, "prov", _wire("AGR2 Core 0,3", "силиконовый", "0,3", cores=""))  # size not in the table
        _record(connection, "prov", _wire("ПВАМ 1,0", "силовой", "1"))  # moved to «прочие» by hand
        _record(connection, "prov", _wire("КСПВГ 4x0,20", "многожильный", "0,20", cores="4"))
        _record(connection, "prov", _wire("RG316", "коаксиальный"))
        _record(connection, "kab_coax", {"Кабель": "RG-316", "D1": "0,5", "D2": "1,48", "D3": "1,95"}, edited=True)
        _entry(connection, "gost_awg", "0,35", "0.35", "23", 0)  # an admin's own AWG for 0,35 stays
        _entry(connection, "cable_kind", "ПВАМ 1,0", "пвам1,0", "other", 0)
        before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("tech_operation_records", "reference_entries")}
        records_before = connection.execute("SELECT fields, edited_in_erp FROM tech_operation_records ORDER BY id").fetchall()

    command.upgrade(config, "0096_cable_structure")

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT fields, edited_in_erp FROM tech_operation_records ORDER BY id").fetchall() == records_before
        wires = dict(connection.execute(
            "SELECT h.mark, g.awg || '|' || h.series || '|' || COALESCE(h.outer_d_mm, '') || '|' || COALESCE(h.voltage_v, '')"
            " || '|' || COALESCE(h.temp_min_c, '') || '|' || COALESCE(h.temp_max_c, '')"
            " FROM hookup_wires h JOIN wire_gauges g ON g.id = h.gauge_id").fetchall())
        assert wires == {"ПВАМ 0,50": "20|ПВАМ|1.6|48|-40|105", "UL1061 28AWG": "28|UL1061|0.89||-10|80"}
        assert connection.execute("SELECT COUNT(*) FROM wire_gauges").fetchone()[0] == 24
        gost = dict(connection.execute("SELECT normalized_key, value FROM reference_entries WHERE kind = 'gost_awg'").fetchall())
        assert gost["0.35"] == "23" and gost["0.5"] == "20" and gost["0.08"] == "28" and "0.07" not in gost
        assert connection.execute("SELECT COUNT(*) FROM reference_entries WHERE kind = 'cable_kind'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM tech_operation_records").fetchone()[0] == before["tech_operation_records"]
        assert connection.execute("SELECT COUNT(*) FROM coax_cables").fetchone()[0] == 0  # cards come from load_cables
        # constraints, not rollbacks
        gauge = connection.execute("SELECT id FROM wire_gauges WHERE awg = '20'").fetchone()[0]
        for bad in (
            "INSERT INTO wire_gauges (id, awg, section_mm2, strands, position) VALUES ('x1', '99', 0, '', 0)",
            "INSERT INTO wire_gauges (id, awg, section_mm2, strands, position) VALUES ('x2', '20', 0.6, '', 0)",
            f"INSERT INTO hookup_wires (id, mark, mark_key, series, gauge_id, insulation, outer_d_mm, updated_at)"
            f" VALUES ('x3', 'М', 'м', 'М', '{gauge}', '', -1, '2026-10-10')",
            "INSERT INTO coax_cable_attenuation (id, cable_id, freq_mhz, kind, value, unit, ref_length_m, loss_db_m)"
            " VALUES ('x4', 'none', 100, 'guess', 1, 'dB/m', 1, 1)",
        ):
            try:
                connection.execute(bad)
            except sqlite3.IntegrityError:
                continue
            raise AssertionError(f"accepted: {bad}")

    command.downgrade(config, "0088_api_tokens_assembly_drafts")
    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert not tables & {"wire_gauges", "hookup_wires", "coax_cables", "coax_cable_markers"}
        assert connection.execute("SELECT COUNT(*) FROM tech_operation_records").fetchone()[0] == before["tech_operation_records"]
