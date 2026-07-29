"""Architecture boundaries introduced by the retrieval-evidence port."""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PRESENTER = ROOT / "argus/api/presenters.py"
PORTED_HTTP_MODULES = {
    ROOT / "argus/api/routes_search.py": set(),
    ROOT / "argus/api/routes_v2.py": set(),
    # The same module still owns the pure assess-content and cookie-health
    # compatibility routes. Those exact imports are the closed exception.
    ROOT / "argus/api/routes_extract.py": {
        "argus.extraction.completeness",
        "argus.extraction.cookies",
    },
}
LEGACY_ADAPTER_EXCEPTIONS = {
    ROOT / "argus/api/routes_admin.py",
    ROOT / "argus/api/routes_dashboard.py",
    ROOT / "argus/api/routes_health.py",
}
MCP_ADAPTERS = {
    ROOT / "argus/mcp/http_adapter.py",
    ROOT / "argus/mcp/server.py",
    ROOT / "argus/mcp/v2_tools.py",
}
MCP_ROOT = ROOT / "argus/mcp"
EXPECTED_ADAPTERS = {
    *PORTED_HTTP_MODULES,
    *LEGACY_ADAPTER_EXCEPTIONS,
    *MCP_ADAPTERS,
    ROOT / "argus/cli/main.py",
    ROOT / "argus/api/routes_workflows.py",
    ROOT / "argus/workflows/service.py",
}
FORBIDDEN = {
    "argus.providers",
    "argus.extraction",
    "argus.persistence",
    "argus.broker.cache",
    "argus.broker.ranking",
    "argus.broker.dedupe",
}
MCP_FORBIDDEN = {
    "argus.broker",
    "argus.extraction",
    "argus.persistence",
    "argus.provider_controls",
}
MCP_FORBIDDEN_PREFIXES = (
    "argus.authority.development_mcp_",
    "argus.development_mcp_",
)
CLI_MAIN = ROOT / "argus/cli/main.py"
CLI_STANDALONE = ROOT / "argus/standalone_cli.py"
CLI_FORBIDDEN_PREFIXES = (
    "argus.broker",
    "argus.extraction",
    "argus.persistence",
    "argus.providers",
    "argus.development_",
)
CLI_FORBIDDEN_CALLS = {
    "create_broker",
    "extract_url",
    "serve_development_mcp",
}
DYNAMIC_IMPORT_PRIMITIVES = {
    "__import__",
    "builtins.__import__",
    "importlib.import_module",
}


def _mcp_python_modules(root: Path) -> frozenset[Path]:
    return frozenset(root.rglob("*.py"))


MCP_MODULES = _mcp_python_modules(MCP_ROOT)


def _module_package(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    module = ".".join(relative.parts)
    return module.rpartition(".")[0]


def _imports(path: Path, *, package: str | None = None) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package = _module_package(path) if package is None else package
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name(
                    "." * node.level + module,
                    package,
                )
            if node.module is not None:
                imported.add(module)
            imported.update(f"{module}.{alias.name}" for alias in node.names)
    return imported


def _is_forbidden_mcp_reference(qualified_name: str) -> bool:
    return any(
        qualified_name == forbidden or qualified_name.startswith(f"{forbidden}.")
        for forbidden in MCP_FORBIDDEN
    ) or qualified_name.startswith(MCP_FORBIDDEN_PREFIXES)


def _cli_boundary_violations(path: Path) -> set[str]:
    """Fail closed on direct, aliased, or string-loaded CLI authority access."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    importlib_aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "importlib"
    }
    violations = {
        module
        for module in _imports(path, package="argus.cli")
        if module.startswith(CLI_FORBIDDEN_PREFIXES)
    }
    violations.update(
        violation
        for violation in _LexicalBoundaryVisitor(package="argus.cli").inspect(tree)
        if violation in {"dynamic-import", "wildcard-import"}
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith(CLI_FORBIDDEN_PREFIXES):
                violations.add(node.value)
        if isinstance(node, ast.Call):
            names = _qualified_names(node.func, {})
            if names & DYNAMIC_IMPORT_PRIMITIVES:
                violations.add("dynamic-import")
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in importlib_aliases
            ):
                violations.add("dynamic-import")
            if isinstance(node.func, ast.Name) and node.func.id in CLI_FORBIDDEN_CALLS:
                violations.add(node.func.id)
    return violations


def _qualified_names(
    node: ast.AST,
    aliases: dict[str, set[str]],
) -> set[str]:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, {node.id})
    if isinstance(node, ast.Attribute):
        return {
            f"{owner}.{node.attr}" for owner in _qualified_names(node.value, aliases)
        }
    return set()


def _assignment_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.List, ast.Tuple, ast.Starred)):
        elements = (
            target.elts if isinstance(target, (ast.List, ast.Tuple)) else [target.value]
        )
        return {name for element in elements for name in _assignment_names(element)}
    return set()


LEXICAL_SCOPES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
)


def _definition_header_nodes(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        arguments = node.args
        annotations = [
            argument.annotation
            for argument in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            )
            if argument.annotation is not None
        ]
        if arguments.vararg is not None and arguments.vararg.annotation is not None:
            annotations.append(arguments.vararg.annotation)
        if arguments.kwarg is not None and arguments.kwarg.annotation is not None:
            annotations.append(arguments.kwarg.annotation)
        return [
            *node.decorator_list,
            *arguments.defaults,
            *(default for default in arguments.kw_defaults if default is not None),
            *annotations,
            *(result for result in [node.returns] if result is not None),
            *getattr(node, "type_params", ()),
        ]
    if isinstance(node, ast.ClassDef):
        return [
            *node.decorator_list,
            *node.bases,
            *(keyword.value for keyword in node.keywords),
            *getattr(node, "type_params", ()),
        ]
    if isinstance(node, ast.Lambda):
        return [
            *node.args.defaults,
            *(default for default in node.args.kw_defaults if default is not None),
        ]
    return []


def _executable_scope_nodes(roots: list[ast.AST]) -> list[ast.AST]:
    """Walk one executable lexical scope without flattening nested scopes."""

    nodes: list[ast.AST] = []
    pending = list(reversed(roots))
    while pending:
        node = pending.pop()
        nodes.append(node)
        children = (
            _definition_header_nodes(node)
            if isinstance(node, LEXICAL_SCOPES)
            else list(ast.iter_child_nodes(node))
        )
        pending.extend(reversed(children))
    return nodes


def _import_bindings(
    node: ast.Import | ast.ImportFrom,
    *,
    package: str,
) -> list[tuple[str, str]]:
    if isinstance(node, ast.Import):
        return [
            (
                imported.asname or imported.name.split(".", 1)[0],
                imported.name if imported.asname else imported.name.split(".", 1)[0],
            )
            for imported in node.names
        ]

    module = node.module or ""
    if node.level:
        module = resolve_name("." * node.level + module, package)
    return [
        (imported.asname or imported.name, f"{module}.{imported.name}")
        for imported in node.names
        if imported.name != "*"
    ]


def _imported_qualified_names(
    node: ast.Import | ast.ImportFrom,
    *,
    package: str,
) -> set[str]:
    if isinstance(node, ast.Import):
        return {imported.name for imported in node.names}

    module = node.module or ""
    if node.level:
        module = resolve_name("." * node.level + module, package)
    return {
        module if imported.name == "*" else f"{module}.{imported.name}"
        for imported in node.names
    }


def _argument_names(arguments: ast.arguments) -> set[str]:
    names = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    }
    if arguments.vararg is not None:
        names.add(arguments.vararg.arg)
    if arguments.kwarg is not None:
        names.add(arguments.kwarg.arg)
    return names


def _scope_initial_bindings(scope: ast.AST) -> set[str]:
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return _argument_names(scope.args)
    return set()


def _lexical_scope_roots(scope: ast.AST) -> list[ast.AST]:
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return list(scope.body)
    if isinstance(scope, ast.Lambda):
        return [scope.body]
    raise TypeError(f"unsupported lexical scope: {type(scope).__name__}")


def _match_pattern_names(pattern: ast.pattern) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(pattern):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name is not None:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            names.add(node.rest)
    return names


def _scope_bound_names(
    nodes: list[ast.AST],
    initial: set[str],
    *,
    package: str,
) -> set[str]:
    bound = set(initial)
    declared_elsewhere: set[str] = set()
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(name for name, _ in _import_bindings(node, package=package))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            bound.update(
                name for target in node.targets for name in _assignment_names(target)
            )
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            bound.update(_assignment_names(node.target))
        elif isinstance(node, ast.Delete):
            bound.update(
                name for target in node.targets for name in _assignment_names(target)
            )
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound.update(_assignment_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            bound.update(
                name
                for item in node.items
                if item.optional_vars is not None
                for name in _assignment_names(item.optional_vars)
            )
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            bound.add(node.name)
        elif isinstance(node, ast.Match):
            bound.update(
                name
                for case in node.cases
                for name in _match_pattern_names(case.pattern)
            )
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            declared_elsewhere.update(node.names)
    return bound - declared_elsewhere


def _scope_aliases(
    nodes: list[ast.AST],
    inherited: dict[str, set[str]],
    *,
    package: str,
    initial: set[str],
) -> dict[str, set[str]]:
    local_names = _scope_bound_names(nodes, initial, package=package)
    aliases = {
        name: set(qualified)
        for name, qualified in inherited.items()
        if name not in local_names
    }
    assignments: list[tuple[set[str], ast.AST]] = []
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for bound, qualified in _import_bindings(node, package=package):
                aliases.setdefault(bound, set()).add(qualified)
        elif isinstance(node, ast.Assign):
            names = {
                name for target in node.targets for name in _assignment_names(target)
            }
            assignments.append((names, node.value))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            assignments.append((_assignment_names(node.target), node.value))
        elif isinstance(node, ast.NamedExpr):
            assignments.append((_assignment_names(node.target), node.value))

    for _ in range(len(assignments) + 1):
        changed = False
        for names, value in assignments:
            qualified = _qualified_names(value, aliases)
            for name in names:
                known = aliases.setdefault(name, set())
                if not qualified.issubset(known):
                    known.update(qualified)
                    changed = True
        if not changed:
            break
    return aliases


class _LexicalBoundaryVisitor:
    """Resolve authority references independently in every lexical scope."""

    def __init__(self, *, package: str) -> None:
        self.package = package
        self.violations: set[str] = set()

    def inspect(self, tree: ast.Module) -> set[str]:
        self._inspect_scope(
            list(tree.body),
            {},
            initial=set(),
            is_class_scope=False,
            recurse_definitions=True,
        )
        return self.violations

    def inspect_module_surface(self, tree: ast.Module) -> set[str]:
        self._inspect_scope(
            list(tree.body),
            {},
            initial=set(),
            is_class_scope=False,
            recurse_definitions=False,
        )
        return self.violations

    def _inspect_scope(
        self,
        roots: list[ast.AST],
        inherited: dict[str, set[str]],
        *,
        initial: set[str],
        is_class_scope: bool,
        recurse_definitions: bool,
    ) -> None:
        nodes = _executable_scope_nodes(roots)
        aliases = _scope_aliases(
            nodes,
            inherited,
            package=self.package,
            initial=initial,
        )
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and any(
                    imported.name == "*" for imported in node.names
                ):
                    self.violations.add("wildcard-import")
                for imported in _imported_qualified_names(
                    node,
                    package=self.package,
                ):
                    if _is_forbidden_mcp_reference(imported):
                        self.violations.add(imported)
                    if imported in DYNAMIC_IMPORT_PRIMITIVES:
                        self.violations.add("dynamic-import")
            for qualified in _qualified_names(node, aliases):
                if _is_forbidden_mcp_reference(qualified):
                    self.violations.add(qualified)
                if qualified in DYNAMIC_IMPORT_PRIMITIVES:
                    self.violations.add("dynamic-import")

        child_inherited = inherited if is_class_scope else aliases
        for child_scope in (node for node in nodes if isinstance(node, LEXICAL_SCOPES)):
            if isinstance(
                child_scope,
                (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
            ):
                self._inspect_comprehension(
                    child_scope,
                    enclosing=aliases,
                    inherited=child_inherited,
                    recurse_definitions=recurse_definitions,
                )
                continue
            if not recurse_definitions:
                continue
            self._inspect_scope(
                _lexical_scope_roots(child_scope),
                child_inherited,
                initial=_scope_initial_bindings(child_scope),
                is_class_scope=isinstance(child_scope, ast.ClassDef),
                recurse_definitions=True,
            )

    def _inspect_comprehension(
        self,
        scope: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        *,
        enclosing: dict[str, set[str]],
        inherited: dict[str, set[str]],
        recurse_definitions: bool,
    ) -> None:
        aliases = {name: set(values) for name, values in inherited.items()}
        for index, generator in enumerate(scope.generators):
            self._inspect_scope(
                [generator.iter],
                enclosing if index == 0 else aliases,
                initial=set(),
                is_class_scope=False,
                recurse_definitions=recurse_definitions,
            )
            for name in _assignment_names(generator.target):
                aliases.pop(name, None)
            for condition in generator.ifs:
                self._inspect_scope(
                    [condition],
                    aliases,
                    initial=set(),
                    is_class_scope=False,
                    recurse_definitions=recurse_definitions,
                )

        result_nodes = (
            [scope.key, scope.value] if isinstance(scope, ast.DictComp) else [scope.elt]
        )
        self._inspect_scope(
            result_nodes,
            aliases,
            initial=set(),
            is_class_scope=False,
            recurse_definitions=recurse_definitions,
        )


def _mcp_boundary_violations(path: Path) -> set[str]:
    """Resolve imports, aliases, calls, and authority references fail-closed."""

    package = _module_package(path) if path.is_relative_to(ROOT) else "argus.mcp"
    violations = {
        module
        for module in _imports(path, package=package)
        if _is_forbidden_mcp_reference(module)
    }
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations.update(_LexicalBoundaryVisitor(package=package).inspect(tree))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in {
            "create_broker",
            "extract_url",
            "create_search_ledger_repository",
        }:
            violations.add(node.func.id)
        if isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id in {"broker", "readiness"}:
                violations.add(f"{owner.id}.{node.func.attr}")
            if isinstance(owner, ast.Attribute) and owner.attr in {
                "broker",
                "readiness_service",
            }:
                violations.add(f"{owner.attr}.{node.func.attr}")
    return violations


def _mcp_namespace_violations(root: Path) -> dict[Path, set[str]]:
    return {
        path: violations
        for path in _mcp_python_modules(root)
        if (violations := _mcp_boundary_violations(path))
    }


def _development_authority_surface(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = _executable_scope_nodes(list(tree.body))
    exposed = {
        node.name
        for node in nodes
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("development_mcp_")
    }
    aliases = _scope_aliases(nodes, {}, package="argus", initial=set())
    exposed.update(
        violation
        for violation in _LexicalBoundaryVisitor(
            package="argus",
        ).inspect_module_surface(tree)
        if violation in {"dynamic-import", "wildcard-import"}
    )
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for imported in _imported_qualified_names(node, package="argus"):
                if imported.startswith("argus.development_mcp_"):
                    exposed.add(imported)
    for bound, qualifications in aliases.items():
        if bound.startswith("development_mcp_"):
            exposed.update(
                qualified
                for qualified in qualifications
                if qualified.startswith("argus.development_mcp_")
            )
            if not any(
                qualified.startswith("argus.development_mcp_")
                for qualified in qualifications
            ):
                exposed.add(bound)
        exposed.update(
            qualified
            for qualified in qualifications
            if qualified.startswith("argus.development_mcp_")
        )
    return exposed


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "from argus.authority import development_mcp_extract as run\n",
            "argus.authority.development_mcp_extract",
        ),
        (
            "from argus import development_mcp_server as launch\n",
            "argus.development_mcp_server",
        ),
        (
            "import importlib as loader\n"
            "loader.import_module('argus.' + 'development_mcp_server')\n",
            "dynamic-import",
        ),
        (
            "import importlib\nload = importlib.import_module\nload(dynamic_name)\n",
            "dynamic-import",
        ),
        (
            "import importlib\n"
            "(load,) = (importlib.import_module,)\n"
            "load(dynamic_name)\n",
            "dynamic-import",
        ),
        (
            "import importlib\n"
            "load = importlib.import_module\n"
            "def unrelated_scope():\n"
            "    load = harmless\n"
            "load(dynamic_name)\n",
            "dynamic-import",
        ),
        (
            "from importlib import import_module as load\n"
            "def unrelated_scope():\n"
            "    import harmless as load\n"
            "load(dynamic_name)\n",
            "dynamic-import",
        ),
        (
            "import harmless as load\n"
            "def local_scope():\n"
            "    from importlib import import_module as load\n"
            "    load(dynamic_name)\n",
            "dynamic-import",
        ),
        (
            "import importlib as load\n"
            "items = [\n"
            "    item\n"
            "    for load in load.import_module(dynamic_name)\n"
            "]\n",
            "dynamic-import",
        ),
        (
            "from importlib import *\nload = import_module\nload(dynamic_name)\n",
            "wildcard-import",
        ),
    ),
)
def test_mcp_boundary_rejects_review_bypass_fixtures(tmp_path, source, expected):
    module = tmp_path / "bypass.py"
    module.write_text(source, encoding="utf-8")

    assert expected in _mcp_boundary_violations(module)


@pytest.mark.parametrize(
    "source",
    (
        (
            "import importlib as load\n"
            "def local_scope():\n"
            "    import harmless as load\n"
            "    load.import_module(dynamic_name)\n"
        ),
        (
            "import harmless as load\n"
            "class LocalScope:\n"
            "    import importlib as load\n"
            "    def call(self):\n"
            "        load.import_module(dynamic_name)\n"
        ),
        (
            "import importlib as load\n"
            "def local_scope(value):\n"
            "    match value:\n"
            "        case load:\n"
            "            pass\n"
            "    load.import_module(dynamic_name)\n"
        ),
        (
            "import importlib as load\n"
            "def local_scope():\n"
            "    del load\n"
            "    load.import_module(dynamic_name)\n"
        ),
    ),
)
def test_mcp_boundary_respects_harmless_import_shadowing_in_child_scope(
    tmp_path,
    source,
):
    module = tmp_path / "harmless_shadow.py"
    module.write_text(source, encoding="utf-8")

    assert "dynamic-import" not in _mcp_boundary_violations(module)


def test_mcp_boundary_fails_closed_on_same_scope_alias_history(tmp_path):
    module = tmp_path / "conservative_alias_history.py"
    module.write_text(
        "import importlib as load\nload = harmless\nload.import_module(dynamic_name)\n",
        encoding="utf-8",
    )

    assert "dynamic-import" in _mcp_boundary_violations(module)


def test_mcp_namespace_scan_rejects_nested_module_fixture(tmp_path):
    mcp_root = tmp_path / "argus/mcp"
    nested = mcp_root / "nested/adapter.py"
    nested.parent.mkdir(parents=True)
    nested.write_text(
        "from argus import development_mcp_server as launch\n",
        encoding="utf-8",
    )

    violations = _mcp_namespace_violations(mcp_root)

    assert violations[nested] == {"argus.development_mcp_server"}


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            "from argus.development_mcp_tools import (\n"
            "    extract_content as development_mcp_extract,\n"
            ")\n",
            "argus.development_mcp_tools.extract_content",
        ),
        (
            "import argus.development_mcp_tools\n"
            "development_mcp_extract = getattr(\n"
            "    argus.development_mcp_tools,\n"
            '    "extract_content",\n'
            ")\n",
            "argus.development_mcp_tools",
        ),
        (
            "if development_enabled:\n"
            "    from argus.development_mcp_tools import (\n"
            "        extract_content as run,\n"
            "    )\n",
            "argus.development_mcp_tools.extract_content",
        ),
        (
            "try:\n"
            "    import argus.development_mcp_server as launch\n"
            "except ImportError:\n"
            "    launch = None\n",
            "argus.development_mcp_server",
        ),
        (
            "import importlib\n"
            "launch = importlib.import_module(\n"
            '    "argus.development_mcp_server",\n'
            ")\n",
            "dynamic-import",
        ),
        (
            "import importlib\n"
            "launchers = [\n"
            "    importlib.import_module(\n"
            '        "argus.development_mcp_server",\n'
            "    )\n"
            "    for _ in [0]\n"
            "]\n",
            "dynamic-import",
        ),
    ),
)
def test_general_authority_surface_rejects_imported_reexport_fixture(
    tmp_path,
    source,
    expected,
):
    authority = tmp_path / "authority.py"
    authority.write_text(source, encoding="utf-8")

    assert expected in _development_authority_surface(authority)


def test_general_authority_surface_ignores_function_local_import(tmp_path):
    authority = tmp_path / "authority.py"
    authority.write_text(
        "def local_scope():\n"
        "    from argus.development_mcp_tools import extract_content\n",
        encoding="utf-8",
    )

    assert not _development_authority_surface(authority)


def test_general_authority_module_exposes_no_development_mcp_execution_helpers():
    authority = ROOT / "argus/authority.py"
    assert not _development_authority_surface(authority)


def test_presenter_does_not_import_execution_or_persistence_authority():
    if not PRESENTER.exists():
        pytest.skip("S7 owns argus/api/presenters.py")

    imported = _imports(PRESENTER)
    violations = {
        module
        for module in imported
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN
        )
    }
    assert not violations


@pytest.mark.parametrize(
    ("path", "allowed_exceptions"),
    tuple(PORTED_HTTP_MODULES.items()),
)
def test_ported_http_modules_have_no_secondary_semantic_authority(
    path,
    allowed_exceptions,
):
    imported = _imports(path)
    violations = {
        module
        for module in imported
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN
        )
        and not any(
            module == allowed or module.startswith(f"{allowed}.")
            for allowed in allowed_exceptions
        )
    }
    assert not violations
    assert "argus.operations.accepted" in imported


def test_transport_adapter_inventory_has_only_closed_legacy_exceptions():
    discovered = {
        *ROOT.glob("argus/api/routes_*.py"),
        *ROOT.glob("argus/mcp/*.py"),
        *ROOT.glob("argus/cli/*.py"),
        *ROOT.glob("argus/workflows/*.py"),
    }
    non_adapters = {
        ROOT / "argus/mcp/__init__.py",
        ROOT / "argus/mcp/capabilities.py",
        ROOT / "argus/mcp/sessions.py",
        ROOT / "argus/cli/__init__.py",
        ROOT / "argus/workflows/__init__.py",
        ROOT / "argus/workflows/models.py",
        ROOT / "argus/workflows/summarizer.py",
    }
    assert discovered - non_adapters == EXPECTED_ADAPTERS

    for path in EXPECTED_ADAPTERS - LEGACY_ADAPTER_EXCEPTIONS:
        imported = _imports(path)
        violations = {
            module
            for module in imported
            if any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for forbidden in FORBIDDEN
            )
        }
        if path == ROOT / "argus/api/routes_extract.py":
            violations = {
                module
                for module in violations
                if not module.startswith(
                    (
                        "argus.extraction.completeness",
                        "argus.extraction.cookies",
                    )
                )
            }
        assert not violations, f"{path.relative_to(ROOT)}: {sorted(violations)}"


def test_production_cli_has_no_direct_or_dynamic_execution_authority():
    assert not _cli_boundary_violations(CLI_MAIN)


def test_cli_models_one_explicit_standalone_dispatch_seam():
    assert "argus.standalone_cli" in _imports(CLI_MAIN)
    standalone_imports = _imports(CLI_STANDALONE)
    assert any(module.startswith("argus.broker") for module in standalone_imports)
    assert any(module.startswith("argus.extraction") for module in standalone_imports)


@pytest.mark.parametrize(
    "source",
    (
        "from argus.broker.router import create_broker as build\nbuild()\n",
        "from importlib import import_module as load\nload(variable)\n",
        "import importlib as loader\nloader.import_module('argus.broker.router')\n",
        "module = 'argus.extraction'\n__import__(module)\n",
    ),
)
def test_cli_boundary_rejects_direct_aliased_and_dynamic_authority(tmp_path, source):
    module = tmp_path / "cli_bypass.py"
    module.write_text(source, encoding="utf-8")

    assert _cli_boundary_violations(module)


def test_contract_kernel_does_not_import_the_throwaway_prototype():
    outcomes = ROOT / "argus/contracts/outcomes.py"
    if not outcomes.exists():
        pytest.fail("accepted-operation contract kernel is missing")

    imported = _imports(outcomes)
    assert not any(module.startswith("docs.prototypes") for module in imported)
    assert "jsonschema" not in imported


@pytest.mark.parametrize("path", tuple(sorted(MCP_MODULES)))
def test_mcp_modules_have_no_execution_authority_imports_or_invocations(path):
    violations = _mcp_boundary_violations(path)
    assert not violations, (
        f"{path.relative_to(ROOT)}: MCP authority boundary violations "
        f"{sorted(violations)}"
    )


def test_import_parser_resolves_relative_reexport_seams(tmp_path):
    module = tmp_path / "presenters.py"
    module.write_text(
        "from .. import extraction\n"
        "from ..extraction import extractor\n"
        "from . import routes_search\n",
        encoding="utf-8",
    )

    assert _imports(module, package="argus.api") == {
        "argus.extraction",
        "argus.extraction.extractor",
        "argus.api.routes_search",
    }
