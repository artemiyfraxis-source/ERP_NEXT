"""Structural architecture guard: the import graph of ``backend/app`` is parsed with ``ast``.

Rules (ARCHITECTURE.md, «один модуль владеет своими таблицами»):

* a domain module never imports another module, and neither third-party transport/persistence
  (fastapi, sqlalchemy, googleapiclient); the same holds for the pure rule files in ``PURE_FILES``;
* inside a module: domain imports nothing from application/infrastructure/api, application imports no api;
* a module reaches another module only through its ``domain`` (shared vocabulary), through ``app/workflows``
  ports, or through an entry of ``CROSS_MODULE_ALLOWLIST`` (frozen debt, new entries fail the test);
* a module never imports ``app.main``.

Absolute, relative and aliased imports are all resolved to dotted module names; comments and strings
are invisible to the check.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

THIRD_PARTY_FORBIDDEN_IN_PURE = ("fastapi", "sqlalchemy", "googleapiclient")

# Pure rule files besides every module's domain: no transport / persistence imports.
PURE_FILES = frozenset(
    {
        "app.modules.production.traceability",
        "app.modules.production.genealogy",
        "app.modules.production.plan",
        "app.modules.production.scheduling",
        "app.modules.production.worklog",
        "app.modules.techcards.grapher_import",
        "app.modules.references.technology",
    }
)

# долг, см. аудит U07 (docs/audit/2026-10-10-system-review.md): прямые связи между модулями мимо
# app/workflows. Существующие допущены и видны здесь; новые ломают тест, ушедшие обязаны выйти из списка.
CROSS_MODULE_ALLOWLIST = frozenset(
    {
        ("app.modules.fulfillment.api", "app.modules.requests.application"),
        ("app.modules.fulfillment.infrastructure", "app.modules.identity.infrastructure"),
        ("app.modules.fulfillment.infrastructure", "app.modules.procurement.infrastructure"),
        ("app.modules.fulfillment.infrastructure", "app.modules.production.infrastructure"),
        ("app.modules.fulfillment.infrastructure", "app.modules.warehouse.infrastructure"),
        ("app.modules.operations.api", "app.modules.production.plan"),
        ("app.modules.operations.infrastructure", "app.modules.identity.audience_directory"),
        ("app.modules.production.api", "app.modules.techcards.application"),
        ("app.modules.production.api", "app.modules.techcards.etk_read"),
        ("app.modules.production.api", "app.modules.techcards.project_cards"),
        ("app.modules.production.autoplan_api", "app.modules.techcards.application"),
        ("app.modules.production.autoplan_api", "app.modules.techcards.project_cards"),
        ("app.modules.production.infrastructure", "app.modules.fulfillment.infrastructure"),
        ("app.modules.production.infrastructure", "app.modules.identity.infrastructure"),
        ("app.modules.production.infrastructure", "app.modules.shipping.infrastructure"),
        ("app.modules.production.launchable", "app.modules.techcards.etk_builder"),
        ("app.modules.production.plan_api", "app.modules.fulfillment.application"),
        ("app.modules.production.plan_api", "app.modules.procurement.application"),
        ("app.modules.production.plan_api", "app.modules.requests.application"),
        ("app.modules.production.plan_api", "app.modules.shipping.application"),
        ("app.modules.production.plan_api", "app.modules.techcards.application"),
        ("app.modules.techcards.api", "app.modules.references.application"),
        ("app.modules.techcards.harness_api", "app.modules.references.application"),
        ("app.modules.techcards.harness_catalog_api", "app.modules.references.application"),
        ("app.modules.techcards.import_kd_from_drive", "app.modules.requests.google_drive"),
        ("app.modules.techcards.kitset_api", "app.modules.fulfillment.application"),
        ("app.modules.techcards.kitset_api", "app.modules.production.plan"),
        ("app.modules.techcards.kitset_api", "app.modules.production.plan_application"),
        ("app.modules.techcards.kitset_api", "app.modules.requests.application"),
    }
)


def module_name(path: Path, app_root: Path) -> tuple[str, bool]:
    parts = list(path.relative_to(app_root.parent).with_suffix("").parts)
    is_package = parts[-1] == "__init__"
    return ".".join(parts[:-1] if is_package else parts), is_package


def collect_imports(app_root: Path) -> tuple[dict[str, set[str]], set[str]]:
    """Return ``(imports, known)``: dotted candidates imported by each module, and all module names."""
    sources: dict[str, tuple[str, bool]] = {}
    for path in sorted(app_root.rglob("*.py")):
        name, is_package = module_name(path, app_root)
        sources[name] = (path.read_text(encoding="utf-8"), is_package)
    imports: dict[str, set[str]] = {}
    for name, (text, is_package) in sources.items():
        package = name if is_package else name.rpartition(".")[0]
        found: set[str] = set()
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package.split(".")
                    base = base[: len(base) - (node.level - 1)]
                    target = ".".join(base + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                found.add(target)
                found.update(f"{target}.{alias.name}" for alias in node.names)
        imports[name] = found
    return imports, set(sources)


def module_of(name: str) -> str | None:
    parts = name.split(".")
    if len(parts) >= 3 and parts[:2] == ["app", "modules"]:
        return parts[2]
    return None


def layer_of(name: str) -> str:
    parts = name.split(".")
    if len(parts) < 4:
        return "package"
    stem = parts[3]
    if stem == "domain":
        return "domain"
    if stem == "application" or stem.endswith("_application"):
        return "application"
    if stem == "infrastructure" or stem.endswith("_infrastructure"):
        return "infrastructure"
    if stem == "api" or stem.endswith("_api"):
        return "api"
    return "other"


def find_violations(
    imports: dict[str, set[str]],
    known: set[str],
    allowlist: frozenset[tuple[str, str]],
    pure_files: frozenset[str] = frozenset(),
) -> tuple[list[str], set[tuple[str, str]]]:
    """Return ``(violations, used_allowlist_entries)``."""
    violations: list[str] = []
    used: set[tuple[str, str]] = set()
    for importer, targets in sorted(imports.items()):
        own = module_of(importer)
        if own is None:
            continue
        importer_layer = layer_of(importer)
        pure = importer_layer == "domain" or importer in pure_files
        for target in sorted(targets):
            if pure and target.split(".")[0] in THIRD_PARTY_FORBIDDEN_IN_PURE:
                violations.append(f"{importer} (чистый слой) импортирует {target}")
            if target not in known:
                continue
            if target == "app.main" or target.startswith("app.main."):
                violations.append(f"{importer} импортирует app.main")
            other = module_of(target)
            if other is None:
                continue
            target_layer = layer_of(target)
            if other == own:
                if importer_layer == "domain" and target_layer in {"application", "infrastructure", "api"}:
                    violations.append(f"{importer}: domain импортирует слой {target_layer} ({target})")
                if importer_layer == "application" and target_layer == "api":
                    violations.append(f"{importer}: application импортирует api ({target})")
                continue
            if importer_layer == "domain" or importer in pure_files:
                violations.append(f"{importer}: чистый слой импортирует другой модуль ({target})")
            elif target_layer in {"domain", "package"}:
                continue
            elif (importer, target) in allowlist:
                used.add((importer, target))
            else:
                violations.append(
                    f"{importer} -> {target}: межмодульный импорт мимо app/workflows; "
                    "вынести в порт или (временно) в CROSS_MODULE_ALLOWLIST"
                )
    return violations, used


def real_graph() -> tuple[dict[str, set[str]], set[str]]:
    return collect_imports(APP_ROOT)


def test_real_graph_obeys_module_boundaries_and_layers() -> None:
    imports, known = real_graph()
    violations, _ = find_violations(imports, known, CROSS_MODULE_ALLOWLIST, PURE_FILES)
    assert not violations, "\n".join(violations)


def test_allowlist_has_no_stale_entries() -> None:
    imports, known = real_graph()
    _, used = find_violations(imports, known, CROSS_MODULE_ALLOWLIST, PURE_FILES)
    stale = sorted(CROSS_MODULE_ALLOWLIST - used)
    assert not stale, f"долг погашен, уберите из CROSS_MODULE_ALLOWLIST: {stale}"


def test_procurement_never_reaches_warehouse_directly() -> None:
    assert not [pair for pair in CROSS_MODULE_ALLOWLIST if pair[0].startswith("app.modules.procurement.")]
    imports, _ = real_graph()
    for importer, targets in imports.items():
        if importer.startswith("app.modules.procurement."):
            assert not [t for t in targets if t.startswith("app.modules.warehouse")], importer


def test_pure_files_exist() -> None:
    _, known = real_graph()
    assert PURE_FILES <= known


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    app = tmp_path / "app"
    for rel, text in files.items():
        path = app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    for directory in [app, *[p for p in app.rglob("*") if p.is_dir()]]:
        (directory / "__init__.py").touch()
    return app


def _check(tmp_path: Path, files: dict[str, str]) -> list[str]:
    imports, known = collect_imports(_tree(tmp_path, files))
    return find_violations(imports, known, frozenset(), frozenset())[0]


def test_catches_relative_import_of_other_module_infrastructure(tmp_path: Path) -> None:
    violations = _check(
        tmp_path,
        {
            "modules/warehouse/infrastructure.py": "",
            "modules/procurement/application.py": "from ..warehouse import infrastructure\n",
        },
    )
    assert violations and "app.modules.warehouse.infrastructure" in violations[0]


def test_catches_aliased_and_absolute_imports(tmp_path: Path) -> None:
    violations = _check(
        tmp_path,
        {
            "modules/warehouse/infrastructure.py": "",
            "modules/procurement/application.py": "import app.modules.warehouse.infrastructure as wi\n",
            "modules/procurement/api.py": "from app.modules.warehouse import infrastructure as repo\n",
            "modules/procurement/infrastructure.py": "from ...modules.warehouse.infrastructure import X\n",
        },
    )
    assert len(violations) == 3


def test_ignores_comments_strings_and_allowed_edges(tmp_path: Path) -> None:
    violations = _check(
        tmp_path,
        {
            "modules/warehouse/infrastructure.py": "",
            "modules/warehouse/domain.py": "",
            "workflows/port.py": "from app.modules.warehouse import infrastructure\n",
            "modules/procurement/application.py": (
                "# from app.modules.warehouse import infrastructure\n"
                's = "import app.modules.warehouse.infrastructure"\n'
                "from app.workflows import port\n"
                "from app.modules.warehouse.domain import Lot\n"
            ),
        },
    )
    assert violations == []


def test_catches_layer_violations(tmp_path: Path) -> None:
    violations = _check(
        tmp_path,
        {
            "modules/a/domain.py": "from . import infrastructure\nimport sqlalchemy\n",
            "modules/a/infrastructure.py": "",
            "modules/a/api.py": "",
            "modules/a/application.py": "from .api import router\n",
            "modules/b/domain.py": "from app.modules.a.domain import X\n",
        },
    )
    assert len(violations) == 4, violations
