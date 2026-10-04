"""Read immutable Git blobs and send them to the trusted TypeScript compiler helper."""

import hashlib
import json
import subprocess
from pathlib import Path

from trace_impact.ingestion.models import stable_id
from trace_impact.shared.errors import ConfigurationError, SourceReadError
from trace_impact.shared.graph_models import GraphSnapshot


class TypeScriptAnalyzer:
    def __init__(self, options):
        self.options = options

    def analyze(self, config):
        def git(*args, input=None):
            try:
                result = subprocess.run(
                    ["git", "-C", config.repository_path, *args],
                    input=input,
                    capture_output=True,
                    timeout=self.options.timeout_seconds,
                    check=True,
                )
                return result.stdout
            except (OSError, subprocess.SubprocessError):
                raise SourceReadError("Cannot read pinned Git revision") from None

        commit = git("rev-parse", "--verify", config.revision + "^{commit}").decode().strip()
        if commit != config.revision:
            raise ValueError("Repository revision did not resolve to the configured commit")
        entries = []
        for entry in git("ls-tree", "-r", "--long", "-z", commit).split(b"\0"):
            if not entry:
                continue
            header, raw_path = entry.split(b"\t", 1)
            mode, kind, oid, size = header.split()
            path = raw_path.decode("utf-8")
            if mode not in (b"100644", b"100755") or kind != b"blob":
                continue
            if path.endswith(".json") or (
                path.endswith((".ts", ".tsx", ".js", ".jsx"))
                and path.startswith(self.options.source_prefixes)
            ):
                entries.append((oid.decode(), path, int(size)))
        if len(entries) > self.options.max_files or sum(e[2] for e in entries) > self.options.max_bytes:
            raise ValueError("Code snapshot exceeds configured file/byte budget")
        if not entries:
            raise ValueError("No supported source files found at this revision")
        data = git("cat-file", "--batch", input="".join(e[0] + "\n" for e in entries).encode())
        files, cursor = {}, 0
        for oid, path, size in entries:
            end = data.index(b"\n", cursor)
            if data[cursor:end].decode() != f"{oid} blob {size}":
                raise ValueError("Git blob response did not match its tree entry")
            raw = data[end + 1 : end + 1 + size]
            files[path] = raw.decode("utf-8-sig")
            cursor = end + size + 2
        if self.options.tsconfig not in files:
            raise ConfigurationError("Configured tsconfig is not present at the pinned commit")
        helper = Path(__file__).parent / "typescript" / "analyze.cjs"
        payload = {"files": files, "tsconfig": self.options.tsconfig}
        try:
            result = subprocess.run(
                ["node", str(helper)],
                input=json.dumps(payload).encode(),
                capture_output=True,
                timeout=self.options.timeout_seconds,
                check=False,
            )
            if result.returncode:
                error = result.stderr.decode("utf-8", errors="replace")
                marker = "IMPACT_TAG_ERROR:"
                if marker in error:
                    detail = error.split(marker, 1)[1].splitlines()[0].strip()
                    raise SourceReadError(f"Invalid stable UI tag: {detail}")
                raise SourceReadError(
                    "TypeScript analysis failed; install the pinned helper dependencies and check syntax/configuration"
                )
            extracted = json.loads(result.stdout)
        except SourceReadError:
            raise
        except (OSError, subprocess.SubprocessError, ValueError):
            raise SourceReadError(
                "TypeScript analysis failed; install the pinned helper dependencies and check syntax/configuration"
            ) from None
        scope = {"project_id": config.project_id, "revision": commit}
        for node in extracted["nodes"]:
            node.update(scope)
            path = node["properties"]["path"]
            node["properties"]["sha256"] = hashlib.sha256(files[path].encode()).hexdigest()
        return GraphSnapshot(
            id=stable_id(
                config.project_id, commit, "typescript-static-v1", json.dumps(extracted, sort_keys=True)
            ),
            origin="PINNED_GIT_TYPESCRIPT_STATIC_REFERENCES_NOT_RUNTIME_CALL_PROOF",
            nodes=tuple(extracted["nodes"]),
            edges=tuple(extracted["edges"]),
            diagnostics=tuple(extracted["diagnostics"]),
        )
