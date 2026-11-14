import pytest

from app.modules.production.vna_characterization import characterization_band


@pytest.mark.parametrize("encoding", ["RI", "MA", "DB"])
def test_two_port_characterization_requires_explicit_full_records_and_50_ohms(encoding):
    content = f"! Переходник\n# MHz S {encoding} R 50\n1 .1 0 .9 0 .9 0 .1 0\n18000 .1 0 .9 0 .9 0 .1 0\n".encode()
    assert characterization_band(content) == (.001, 18)
    assert characterization_band(content.replace(b"R 50", b"R 75")) is None


@pytest.mark.parametrize("content", [b"", b"1 .1 0\n18 .1 0", b"# GHz S RI R 50\n1 .1 0\n18 .1 0",
    b"# GHz S RI R 50\n1 .1 0 .9 0 .9 0 .1 0\n1 .1 0 .9 0 .9 0 .1 0",
    b"# GHz S RI R 50\n1 nan 0 .9 0 .9 0 .1 0\n18 .1 0 .9 0 .9 0 .1 0",
    b"# GHz S RI R 50\n[Version] 2.0"])
def test_incomplete_or_unsupported_characterization_is_not_claimed(content):
    assert characterization_band(content) is None
