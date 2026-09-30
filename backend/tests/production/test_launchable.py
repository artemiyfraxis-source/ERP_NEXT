from app.modules.production.launchable import operation_code, route_from_document, stage_name


def test_route_names_stages_in_russian_instead_of_generator_types():
    route = route_from_document({"build_config": {"ops": [
        {"type": "cutWireStrip", "label": "Резка с разделкой (3 ст.)", "opNumber": "БЕЗ КОДА", "tOp": 10},
        {"type": "prsTerm", "opNumber": "OP-2", "tOp": 5},
    ]}})

    assert [(step.stage, step.code) for step in route] == [("Резка с разделкой (3 ст.)", ""), ("Опрессовка терминалов", "OP-2")]


def test_already_launched_stages_and_codes_read_in_russian():
    assert stage_name("insWrap") == "Обмотка"
    assert stage_name("Пайка") == "Пайка"
    assert operation_code("без кода") == ""
    assert operation_code("OP-01") == "OP-01"
