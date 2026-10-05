"""Pinned-revision TypeScript import graph extraction."""

import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tarfile
from collections import defaultdict
from io import BytesIO

from pydantic import BaseModel, ConfigDict, Field

from ingestion.beans.decorators import component
from ingestion.config_loader.models import CodeInputConfig, InputConfig
from ingestion.domain.models import CodeGraph, GraphRecord, GraphRelationship, RawDocument
from ingestion.extractor.code.implementation.typescript_dependency_parser import (
    FileAnalysis,
    TypeScriptDependencyParser,
)
from ingestion.extractor.code.interface import CodeExtractor
from ingestion.mapping.ui_candidate_tagger import UiCandidateTagger

SUPPORTED_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".graphql",
    ".css",
    ".svg",
    ".json",
)
PARSABLE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")


class TypeScriptOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_prefixes: tuple[str, ...] = ("src/",)
    max_files: int = Field(default=2000, ge=1)
    max_bytes: int = Field(default=30_000_000, ge=1)
    timeout_seconds: int = Field(default=90, ge=1)
    tsconfig: str = "tsconfig.json"


@component(contract=CodeExtractor, name="typescript")
class TypeScriptCodeExtractor:
    def __init__(self, config: InputConfig, ui_tagger: UiCandidateTagger) -> None:
        if config.code is None:
            raise ValueError("TypeScript extraction requires input.code")
        self._options = TypeScriptOptions.model_validate(config.code.analyzer.options)
        self._ui_tagging = config.code.ui_tagging
        self._ui_tagger = ui_tagger

    def extract(self, config: CodeInputConfig) -> CodeGraph:
        revision = self._git(config, "rev-parse", "--verify", f"{config.revision}^{{commit}}")
        if revision.strip() != config.revision:
            raise ValueError("Code revision must be an exact commit SHA")
        paths = tuple(
            path
            for path in self._git(config, "ls-tree", "-r", "--name-only", config.revision).splitlines()
            if path.endswith(SUPPORTED_SUFFIXES) and path.startswith(self._options.source_prefixes)
        )
        if len(paths) > self._options.max_files:
            raise ValueError("Code snapshot exceeds the configured file limit")
        archive_paths = tuple(dict.fromkeys((*paths, self._options.tsconfig)))
        contents = self._read_revision_files(config, archive_paths)
        aliases, base_url = self._read_path_aliases(contents.get(self._options.tsconfig))
        parser = TypeScriptDependencyParser()
        analyses = {
            path: parser.parse(path, contents[path])
            for path in paths
            if path.endswith(PARSABLE_SUFFIXES)
        }
        if sum(len(content.encode()) for content in contents.values()) > self._options.max_bytes:
            raise ValueError("Code snapshot exceeds the configured byte limit")
        file_nodes = tuple(
            GraphRecord(
                id=f"file:{path}",
                kind="CodeFile",
                properties={
                    "path": path,
                    "revision": config.revision,
                    "sha256": hashlib.sha256(contents[path].encode()).hexdigest(),
                },
            )
            for path in paths
        )
        source_documents = tuple(
            RawDocument(
                source_id=f"code:{config.revision}:{path}",
                content=contents[path],
                media_type="text/plain",
                metadata={
                    "kind": "code",
                    "code_file_id": f"file:{path}",
                    "path": path,
                    "revision": config.revision,
                    "sha256": hashlib.sha256(contents[path].encode()).hexdigest(),
                    "language": self._language(path),
                },
            )
            for path in paths
        )
        relationships = self._build_relationships(analyses, paths, aliases, base_url)
        route_nodes, route_relationships = self._route_graph(
            paths, contents, config.revision, relationships
        )
        symbol_nodes = tuple(
            GraphRecord(
                id=symbol.id,
                kind="CodeSymbol",
                properties={
                    "name": symbol.name,
                    "symbol_kind": symbol.kind,
                    "path": path,
                    "revision": config.revision,
                    "file_id": f"file:{path}",
                    "start_line": str(symbol.line),
                    "end_line": str(symbol.end_line),
                },
            )
            for path, analysis in analyses.items()
            for symbol in analysis.symbols
        )
        graph = CodeGraph(
            nodes=file_nodes + symbol_nodes + route_nodes,
            relationships=relationships + route_relationships,
            source_documents=source_documents,
        )
        return self._ui_tagger.tag(graph, self._ui_tagging)

    @classmethod
    def _route_graph(
        cls,
        paths: tuple[str, ...],
        contents: dict[str, str],
        revision: str,
        code_relationships: tuple[GraphRelationship, ...] = (),
    ) -> tuple[tuple[GraphRecord, ...], tuple[GraphRelationship, ...]]:
        route_nodes: list[GraphRecord] = []
        relationships: list[GraphRelationship] = []
        imports_by_file: dict[str, list[str]] = defaultdict(list)
        for relationship in code_relationships:
            if relationship.kind == "IMPORTS":
                imports_by_file[relationship.source_id].append(relationship.target_id)
        for path in paths:
            route_path = cls._route_path(path)
            if route_path is None:
                continue
            route_id = f"route:{revision}:{route_path}"
            query_parameters = {
                name: (required, value_type)
                for name, required, value_type in cls._query_parameters(contents[path])
            }
            for dependency_path in cls._imported_paths(f"file:{path}", imports_by_file):
                source = contents.get(dependency_path.removeprefix("file:"))
                if source is None:
                    continue
                query_parameters.update(
                    {
                        name: (required, value_type)
                        for name, required, value_type in cls._client_query_parameters(source)
                        if name not in query_parameters
                    }
                )
            route_nodes.append(
                GraphRecord(
                    id=route_id,
                    kind="Route",
                    properties={
                        "path": route_path,
                        "route_file_id": f"file:{path}",
                        "revision": revision,
                        "framework": "nextjs_app_router",
                        "query_parameter_names": ",".join(query_parameters),
                    },
                )
            )
            relationships.append(cls._graph_relationship(f"file:{path}", route_id, "DECLARES_ROUTE"))
            for name, (required, value_type) in query_parameters.items():
                parameter_id = f"route-parameter:{revision}:{route_path}:query:{name}"
                route_nodes.append(
                    GraphRecord(
                        id=parameter_id,
                        kind="RouteParameter",
                        properties={
                            "name": name,
                            "location": "query",
                            "required": str(required).lower(),
                            "value_type": value_type,
                            "revision": revision,
                        },
                    )
                )
                relationships.append(
                    cls._graph_relationship(route_id, parameter_id, "ACCEPTS_QUERY_PARAMETER")
                )
        return tuple(route_nodes), tuple(relationships)

    @staticmethod
    def _route_path(path: str) -> str | None:
        if not path.startswith("src/app/") or not path.endswith(
            ("/page.ts", "/page.tsx", "/page.js", "/page.jsx")
        ):
            return None
        relative = path.removeprefix("src/app/")
        segments = relative.rsplit("/", 1)[0].split("/") if "/" in relative else []
        route_segments = [
            "{" + segment[1:-1] + "}" if segment.startswith("[") and segment.endswith("]") else segment
            for segment in segments
            if segment and not (segment.startswith("(") and segment.endswith(")"))
        ]
        return "/" + "/".join(route_segments)

    @staticmethod
    def _query_parameters(source: str) -> tuple[tuple[str, bool, str], ...]:
        declaration = re.search(r"\bsearchParams\s*:\s*(?:Promise\s*<\s*)?", source)
        if declaration is None:
            return ()
        type_start = declaration.end()
        if type_start < len(source) and source[type_start] == "{":
            body = TypeScriptCodeExtractor._braced_type_body(source, type_start)
        else:
            alias = re.match(r"([A-Za-z_$][\w$]*)", source[type_start:])
            body = (
                TypeScriptCodeExtractor._named_object_type(source, alias.group(1))
                if alias is not None
                else None
            )
        if body is None:
            return ()

        fields: list[tuple[str, bool, str]] = []
        for field in TypeScriptCodeExtractor._split_type_fields(body):
            match = re.match(
                r"\s*(?:readonly\s+)?([A-Za-z_$][\w$]*)\s*(\?)?\s*:\s*(.+?)\s*$",
                field,
                re.DOTALL,
            )
            if match is None:
                continue
            value_type = match.group(3).split("|")[0].strip().removesuffix("[]").strip()
            fields.append((match.group(1), match.group(2) is None, value_type))
        return tuple(fields)

    @staticmethod
    def _client_query_parameters(source: str) -> tuple[tuple[str, bool, str], ...]:
        variables = re.findall(
            r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*useSearchParams\s*\(",
            source,
        )
        names: set[str] = set()
        for variable in variables:
            names.update(
                re.findall(
                    rf"\b{re.escape(variable)}\s*\.\s*(?:get|getAll|has)\s*\(\s*['\"]([^'\"]+)['\"]",
                    source,
                )
            )
        names.update(
            re.findall(
                r"\buseSearchParams\s*\(\s*\)\s*\.\s*(?:get|getAll|has)\s*\(\s*['\"]([^'\"]+)['\"]",
                source,
            )
        )
        return tuple((name, False, "string") for name in sorted(names))

    @staticmethod
    def _imported_paths(page_id: str, imports_by_file: dict[str, list[str]]) -> tuple[str, ...]:
        visited = {page_id}
        pending = [page_id]
        while pending:
            current = pending.pop()
            for imported_id in imports_by_file.get(current, []):
                if imported_id not in visited:
                    visited.add(imported_id)
                    pending.append(imported_id)
        return tuple(sorted(visited - {page_id}))

    @classmethod
    def _named_object_type(cls, source: str, name: str) -> str | None:
        declaration = re.search(
            rf"\btype\s+{re.escape(name)}\s*=\s*\{{|"
            rf"\binterface\s+{re.escape(name)}(?:\s+extends\s+[\w.$, ]+)?\s*\{{",
            source,
        )
        if declaration is None:
            return None
        opening_brace = declaration.end() - 1
        return cls._braced_type_body(source, opening_brace)

    @staticmethod
    def _braced_type_body(source: str, opening_brace: int) -> str | None:
        depth = 0
        for index in range(opening_brace, len(source)):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
                if depth == 0:
                    return source[opening_brace + 1 : index]
        return None

    @staticmethod
    def _split_type_fields(body: str) -> tuple[str, ...]:
        fields: list[str] = []
        start = 0
        nesting = {"{": 0, "[": 0, "(": 0, "<": 0}
        closing = {"}": "{", "]": "[", ")": "(", ">": "<"}
        for index, character in enumerate(body):
            if character in nesting:
                nesting[character] += 1
            elif character in closing:
                opening = closing[character]
                nesting[opening] = max(0, nesting[opening] - 1)
            elif character in ";," and not any(nesting.values()):
                fields.append(body[start:index].strip())
                start = index + 1
        fields.append(body[start:].strip())
        return tuple(field for field in fields if field)

    @staticmethod
    def _graph_relationship(source_id: str, target_id: str, kind: str) -> GraphRelationship:
        identifier = hashlib.sha256(f"{source_id}:{kind}:{target_id}".encode()).hexdigest()
        return GraphRelationship(id=identifier, source_id=source_id, target_id=target_id, kind=kind)

    @staticmethod
    def _language(path: str) -> str:
        return {
            ".ts": "typescript",
            ".tsx": "tsx",
            ".js": "javascript",
            ".jsx": "jsx",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".graphql": "graphql",
            ".css": "css",
            ".svg": "svg",
            ".json": "json",
        }[posixpath.splitext(path)[1]]

    def _build_relationships(
        self,
        analyses: dict[str, FileAnalysis],
        paths: tuple[str, ...],
        aliases: dict[str, tuple[str, ...]],
        base_url: str,
    ) -> tuple[GraphRelationship, ...]:
        known_paths = set(paths)
        relationships: dict[str, GraphRelationship] = {}
        symbols_by_file: dict[str, dict[str, list[str]]] = {}
        for path, analysis in analyses.items():
            names: dict[str, list[str]] = defaultdict(list)
            for symbol in analysis.symbols:
                names[symbol.name].append(symbol.id)
                self._add_relationship(
                    relationships,
                    f"file:{path}",
                    symbol.id,
                    "DECLARES",
                    symbol.line,
                )
            symbols_by_file[path] = names

        imports_by_file: dict[str, dict[str, tuple[str, str]]] = {}
        for path, analysis in analyses.items():
            bindings: dict[str, tuple[str, str]] = {}
            for imported in analysis.imports:
                relation = self._relationship(path, imported.module, known_paths, aliases, base_url)
                if relation is None:
                    continue
                relationships[relation.id] = relation
                target_path = relation.target_id.removeprefix("file:")
                if imported.local_name:
                    bindings[imported.local_name] = (target_path, imported.imported_name)
            imports_by_file[path] = bindings

        for path, analysis in analyses.items():
            for reference in analysis.references:
                binding = imports_by_file[path].get(reference.target_name)
                if binding is None:
                    candidate_paths = (path,)
                    target_name = reference.target_name
                else:
                    candidate_paths = (binding[0],)
                    target_name = binding[1]
                    if target_name == "default":
                        possible = [
                            symbol_id
                            for symbol_ids in symbols_by_file[binding[0]].values()
                            for symbol_id in symbol_ids
                        ]
                        target_ids = possible if len(possible) == 1 else []
                    else:
                        target_ids = symbols_by_file[binding[0]].get(target_name, [])
                if binding is None:
                    target_ids = [
                        symbol_id
                        for target_path in candidate_paths
                        for symbol_id in symbols_by_file[target_path].get(target_name, [])
                    ]
                if len(target_ids) != 1:
                    continue
                target_id = target_ids[0]
                self._add_relationship(
                    relationships,
                    reference.source_symbol_id,
                    target_id,
                    reference.relationship,
                    reference.line,
                )
                source_file_id = f"file:{path}"
                target_file_id = f"file:{candidate_paths[0]}"
                self._add_relationship(
                    relationships,
                    source_file_id,
                    target_file_id,
                    reference.relationship,
                    reference.line,
                    properties={
                        "source_symbol_id": reference.source_symbol_id,
                        "target_symbol_id": target_id,
                    },
                )
        return tuple(relationships.values())

    @staticmethod
    def _add_relationship(
        relationships: dict[str, GraphRelationship],
        source_id: str,
        target_id: str,
        kind: str,
        line: int,
        *,
        properties: dict[str, str] | None = None,
    ) -> None:
        details = {"line": str(line), "resolution": "tree_sitter_static"}
        details.update(properties or {})
        identifier = hashlib.sha256(f"{source_id}:{kind}:{target_id}:{line}".encode()).hexdigest()
        relationships[identifier] = GraphRelationship(
            id=identifier,
            source_id=source_id,
            target_id=target_id,
            kind=kind,
            properties=details,
        )

    def _git(self, config: CodeInputConfig, *arguments: str) -> str:
        git_executable = shutil.which("git")
        if git_executable is None:
            raise RuntimeError("Git executable was not found on PATH")
        result = subprocess.run(  # noqa: S603 - fixed executable and argument vector
            [git_executable, "-C", str(config.repository_path), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._options.timeout_seconds,
        )
        return result.stdout

    def _read_revision_files(self, config: CodeInputConfig, paths: tuple[str, ...]) -> dict[str, str]:
        git_executable = shutil.which("git")
        if git_executable is None:
            raise RuntimeError("Git executable was not found on PATH")
        result = subprocess.run(  # noqa: S603 - fixed executable and argument vector
            [
                git_executable,
                "-C",
                str(config.repository_path),
                "archive",
                "--format=tar",
                config.revision,
                *paths,
            ],
            check=True,
            capture_output=True,
            timeout=self._options.timeout_seconds,
        )
        files: dict[str, str] = {}
        with tarfile.open(fileobj=BytesIO(result.stdout), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                content = archive.extractfile(member)
                if content is not None:
                    files[member.name] = content.read().decode("utf-8", errors="replace")
        missing = set(paths) - files.keys()
        missing_code = missing - {self._options.tsconfig}
        if missing_code:
            raise ValueError(f"Pinned source files are missing from git archive: {sorted(missing_code)}")
        return files

    def _read_path_aliases(self, source: str | None) -> tuple[dict[str, tuple[str, ...]], str]:
        try:
            if source is None:
                raise FileNotFoundError(self._options.tsconfig)
            raw = json.loads(source)
        except (FileNotFoundError, json.JSONDecodeError) as error:
            raise ValueError(
                f"Could not read TypeScript path aliases from {self._options.tsconfig}"
            ) from error
        if not isinstance(raw, dict):
            raise ValueError("tsconfig must contain a JSON object")
        compiler_options = raw.get("compilerOptions", {})
        if not isinstance(compiler_options, dict):
            raise ValueError("tsconfig compilerOptions must be an object")
        base_url = posixpath.normpath(
            posixpath.join(
                posixpath.dirname(self._options.tsconfig),
                str(compiler_options.get("baseUrl", ".")),
            )
        )
        raw_aliases = compiler_options.get("paths", {})
        if not isinstance(raw_aliases, dict):
            raise ValueError("tsconfig compilerOptions.paths must be an object")
        aliases = {
            pattern: tuple(targets)
            for pattern, targets in raw_aliases.items()
            if isinstance(pattern, str)
            and isinstance(targets, list)
            and all(isinstance(target, str) for target in targets)
        }
        return aliases, base_url

    @staticmethod
    def _relationship(
        source: str,
        imported: str,
        known_paths: set[str],
        aliases: dict[str, tuple[str, ...]],
        base_url: str,
    ) -> GraphRelationship | None:
        bases: list[str] = []
        if imported.startswith("."):
            bases.append(posixpath.normpath(posixpath.join(posixpath.dirname(source), imported)))
        else:
            for pattern, targets in aliases.items():
                prefix, separator, suffix = pattern.partition("*")
                if (not separator and imported == pattern) or (
                    separator and imported.startswith(prefix) and imported.endswith(suffix)
                ):
                    wildcard = imported[len(prefix) : len(imported) - len(suffix) if suffix else None]
                    for target_pattern in targets:
                        target_prefix, target_separator, target_suffix = target_pattern.partition("*")
                        if target_separator:
                            if not wildcard:
                                continue
                            target = target_prefix + wildcard + target_suffix
                        else:
                            if separator:
                                continue
                            target = target_pattern
                        bases.append(posixpath.normpath(posixpath.join(base_url, target)))
        candidates = tuple(
            candidate
            for base in bases
            for candidate in (
                base,
                *(base + suffix for suffix in SUPPORTED_SUFFIXES),
                *(posixpath.join(base, "index" + suffix) for suffix in SUPPORTED_SUFFIXES),
            )
        )
        resolved_target = next((candidate for candidate in candidates if candidate in known_paths), None)
        if resolved_target is None:
            return None
        identifier = hashlib.sha256(f"{source}:IMPORTS:{resolved_target}".encode()).hexdigest()
        return GraphRelationship(
            id=identifier,
            source_id=f"file:{source}",
            target_id=f"file:{resolved_target}",
            kind="IMPORTS",
        )
