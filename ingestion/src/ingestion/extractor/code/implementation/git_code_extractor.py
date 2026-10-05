import hashlib
import subprocess

from ingestion.beans.decorators import component
from ingestion.config_loader.models import CodeInputConfig
from ingestion.domain.models import CodeGraph, GraphRecord, RawDocument
from ingestion.extractor.code.interface import CodeExtractor


@component(contract=CodeExtractor, name="git")
class GitCodeExtractor:
    def extract(self, config: CodeInputConfig) -> CodeGraph:
        result = subprocess.run(  # noqa: S603 - fixed executable and argument vector; no shell
            ["git", "-C", str(config.repository_path), "ls-tree", "-r", "--name-only", config.revision],  # noqa: S607 - resolved by the operating system
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        paths = [
            path for path in result.stdout.splitlines() if path.endswith((".ts", ".tsx", ".js", ".jsx"))
        ]
        documents: list[RawDocument] = []
        for path in paths:
            file_result = subprocess.run(  # noqa: S603 - fixed executable and argument vector
                ["git", "-C", str(config.repository_path), "show", f"{config.revision}:{path}"],  # noqa: S607
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            content = file_result.stdout
            documents.append(
                RawDocument(
                    source_id=f"code:{config.revision}:{path}",
                    content=content,
                    media_type="text/typescript",
                    metadata={
                        "kind": "code",
                        "code_file_id": f"file:{path}",
                        "path": path,
                        "revision": config.revision,
                        "sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "language": "typescript" if path.endswith((".ts", ".tsx")) else "javascript",
                    },
                )
            )
        return CodeGraph(
            nodes=tuple(
                GraphRecord(
                    id=f"file:{path}",
                    kind="CodeFile",
                    properties={"path": path, "revision": config.revision},
                )
                for path in paths
            ),
            source_documents=tuple(documents),
        )
