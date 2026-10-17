"""Ручки генератора.

Ядро проверено отдельно (test_coax.py, test_harness.py) — здесь только
то, что добавляет веб-слой: разбор входа, отсев опечаток и форма выдачи.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    return TestClient(app)


def test_жгут_отдаёт_маршрут(client):
    r = client.post(
        "/api/route/harness",
        json={
            "wires": [{"name": "МК 26-13", "length_mm": 150, "qty": 2}],
            "side_a": {"name": "ZHR-5", "contacts": 3},
            "side_b": {"name": "PHR-2", "contacts": 2},
            "heatshrink": True,
        },
    )
    assert r.status_code == 200, r.text
    шаги = r.json()["steps"]
    коды = [ш["code"] for ш in шаги]

    assert коды[-1] == "BOX", "упаковка замыкает маршрут"
    assert "OTK_AUTO" in коды
    # Позиции идут подряд: по ним человек называет операцию вслух.
    assert [ш["position"] for ш in шаги] == list(range(1, len(шаги) + 1))


def test_коакс_отдаёт_маршрут(client):
    r = client.post(
        "/api/route/coax",
        json={
            "cable_article": "LMR-100",
            "cable_length_mm": 600,
            "side_a": {"article": "SMA-male"},
            "side_b": {"article": "N-female"},
            "tut_article": "ТУТ 6/3",
        },
    )
    assert r.status_code == 200, r.text
    имена = [ш["name"] for ш in r.json()["steps"]]
    assert any("Резка кабеля" in n for n in имена)
    assert any("Усадка" in n for n in имена)


def test_зависимости_отдаются_наружу(client):
    """По ним видно, что можно делать параллельно. Без них маршрут
    выглядит строго последовательным, и смена планируется хуже."""
    r = client.post(
        "/api/route/harness",
        json={
            "wires": [
                {"name": "первый", "length_mm": 100},
                {"name": "второй", "length_mm": 200},
            ],
            "side_a": {"name": "X", "contacts": 2},
        },
    )
    шаги = r.json()["steps"]
    резки = [ш for ш in шаги if "Резка" in ш["name"]]
    assert len(резки) == 2
    assert all(ш["depends_on"] == [] for ш in резки), "резки независимы"

    отк = next(ш for ш in шаги if "ОТК" in ш["name"])
    assert отк["depends_on"], "ОТК ждёт сборку"


def test_предупреждения_доходят_до_человека(client):
    """То, что генератор решил сам, должно быть видно: молча
    подставленное умолчание в карте — брак в цехе."""
    r = client.post(
        "/api/route/harness",
        json={"wires": [{"name": "провод"}], "side_a": {"name": "X"}},
    )
    преды = r.json()["warnings"]
    assert any("не задана длина" in п for п in преды)
    assert any("Сторона B" in п for п in преды)


def test_опечатка_в_типе_пина_отвергается(client):
    """От типа посадки зависит и монтаж корпуса, и момент надевания
    термоусадки. Принять «пайка внутри» как есть значило бы выдать
    правдоподобный, но неверный маршрут."""
    r = client.post(
        "/api/route/coax",
        json={
            "cable_article": "RG-316",
            "cable_length_mm": 300,
            "side_a": {"article": "SMA", "pin_type": "пайка внутри"},
        },
    )
    assert r.status_code == 422


def test_жгут_без_проводов_отвергается(client):
    """Пустая сборка — это не маршрут из нуля шагов, а незаполненная
    форма. Отвечать на неё пустым успехом нельзя."""
    r = client.post("/api/route/harness", json={"wires": []})
    assert r.status_code == 422


def test_коакс_без_разъёма_объясняет_причину(client):
    """Кабель без разъёмов — отрезок кабеля. Ответ пустой, но с
    объяснением: иначе человек решит, что сломалось приложение."""
    r = client.post(
        "/api/route/coax",
        json={"cable_article": "RG-316", "cable_length_mm": 300},
    )
    assert r.status_code == 200, r.text
    assert r.json()["steps"] == []
    assert r.json()["warnings"], "пустой маршрут обязан объяснить себя"


def test_отрицательная_длина_отвергается(client):
    r = client.post(
        "/api/route/harness",
        json={"wires": [{"name": "п", "length_mm": -5}], "side_a": {"name": "X"}},
    )
    assert r.status_code == 422


def test_здоровье(client):
    assert client.get("/api/health").json()["ok"] is True
