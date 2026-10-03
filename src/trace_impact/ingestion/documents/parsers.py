"""Loss-preserving HTML/Markdown normalization and section splitting."""

from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markdownify import markdownify

from trace_impact.ingestion.models import Chunk, Document, RawDocument, stable_id

NORMALIZER_VERSION = "main-markdown-v1"


class HtmlParser:
    version = "html-main-markdown-v1"

    def parse(self, raw: RawDocument) -> Document:
        return Document(text=normalize(raw.content, "html"))


class MarkdownParser:
    version = "markdown-v1"

    def parse(self, raw: RawDocument) -> Document:
        return Document(text=normalize(raw.content, "markdown"))


def normalize(raw: bytes, fmt: str) -> str:
    text = raw.decode("utf-8-sig")
    if fmt == "html":
        soup = BeautifulSoup(text, "html.parser")
        root = soup.find("main") or soup.find("article")
        if root is None:
            raise ValueError("No main/article found; supply Markdown or a source-specific adapter")
        for node in root.select("script, style, nav, header, footer, button, svg"):
            node.decompose()
        text = markdownify(str(root), heading_style="ATX")
    text = text.replace("\r\n", "\n").strip()
    if len(text) < 40:
        raise ValueError("Document has too little usable text")
    return text


def make_chunks(text: str, snapshot_id: str, source_id: str, limit: int) -> list[Chunk]:
    # Markdown token maps prevent '# headings' inside fenced examples from splitting sections.
    tokens = MarkdownIt().parse(text)
    lines = text.splitlines(keepends=True)
    sections: list[tuple[str, str]] = []
    heading, start = "Document", 0
    stack: list[tuple[int, str]] = []
    for i, token in enumerate(tokens):
        if token.type != "heading_open" or token.map is None:
            continue
        pos = token.map[0]
        if pos > start:
            sections.append((heading, "".join(lines[start:pos]).strip()))
        depth = int(token.tag[1:])
        stack = [(d, title) for d, title in stack if d < depth]
        stack.append((depth, tokens[i + 1].content))
        heading = " > ".join(title for _, title in stack)
        start = pos
    sections.append((heading, "".join(lines[start:]).strip()))
    result: list[Chunk] = []
    for heading, section in sections:
        if not section:
            continue
        # Group top-level Markdown blocks; tables and code examples stay intact.
        block_tokens = MarkdownIt().parse(section)
        section_lines = section.splitlines(keepends=True)
        spans = []
        end = 0
        for token in block_tokens:
            if token.level == 0 and token.map and token.map[0] >= end:
                a, b = token.map
                spans.append("".join(section_lines[a:b]).strip())
                end = b
        current = ""
        for block in spans or [section]:
            if current and len(current) + len(block) + 2 > limit:
                ordinal = len(result)
                result.append(
                    Chunk(
                        id=stable_id(snapshot_id, str(ordinal), current),
                        snapshot_id=snapshot_id,
                        source_id=source_id,
                        heading=heading,
                        ordinal=ordinal,
                        text=current,
                        oversized=len(current) > limit,
                    )
                )
                current = ""
            current = f"{current}\n\n{block}".strip()
        if current:
            ordinal = len(result)
            result.append(
                Chunk(
                    id=stable_id(snapshot_id, str(ordinal), current),
                    snapshot_id=snapshot_id,
                    source_id=source_id,
                    heading=heading,
                    ordinal=ordinal,
                    text=current,
                    oversized=len(current) > limit,
                )
            )
    return result
