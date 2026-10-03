"""Editor suggestions generated from the real model and registered component names."""

from trace_impact.ingestion.config import Project
from trace_impact.ingestion.metadata import MetadataConfig
from trace_impact.shared.registry import Components

# Presentation definitions accompany the generated type/default/constraint definitions.
FIELD_HELP = {
    "Project": {
        "project_id": "Unique project key. Use lowercase letters, digits, underscores or hyphens.",
        "name": "A friendly name for this application.",
        "baseline_url": "Deployed baseline application. Collection does not browse this URL.",
        "scope": "Product behaviours to focus on when extracting requirements.",
        "allowed_document_hosts": "Exact HTTPS hostnames permitted for web documents and redirects.",
        "max_chunk_chars": "Target character limit per section. Oversized indivisible blocks are flagged.",
        "excluded_inputs": "Recorded exclusions. These labels are provenance notes, not automatic content filters.",
        "sources": "Explicit inputs to ingest. The library does not recursively crawl a website.",
    },
    "Repository": {
        "url": "Repository URL recorded for provenance; this stage does not ingest source code.",
        "baseline_commit": "Full 40-character commit SHA for the baseline application.",
        "code_roots": "Repository-relative code directories for subsequent code analysis.",
    },
    "Source": {
        "id": "Unique source key used in artifacts and citations.",
        "location": "HTTPS URL or a local path relative to the project JSON directory.",
        "version": "Pinned commit/version, or an explicit label for unpinned online documentation.",
        "scope": "Source topic labels retained as provenance.",
        "authority": "What this document can establish: frontend behaviour, backend rules or an API contract.",
        "options": "Fields declared by the selected loader implementation.",
    },
    "ExtractionConfig": {
        "model": "Exact model ID supported by your provider account. Configuration validation does not verify account access.",
        "max_output_tokens": "Maximum generated tokens per extraction request.",
        "thinking_level": "Optional Gemini reasoning setting. Leave unset for other providers.",
        "requests_per_minute": "Space application requests; 0 disables pacing. SDK retries and account quotas are separate.",
    },
    "EmbeddingConfig": {
        "model": "Exact embedding model ID. Gemini currently supports gemini-embedding-2.",
        "dimensions": "Vector size supported by the selected embedding model.",
        "requests_per_minute": "Space embedding requests; 0 disables pacing.",
    },
}


def project_schema(components: Components, metadata: MetadataConfig | None = None) -> dict:
    schema = Project.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Trace Impact ingestion project"
    schema["description"] = "Select components from suggestions, or name a custom registered implementation."

    def suggest(field, registry, *, configured=None):
        # anyOf is intentional: known choices also match the open custom-name branch.
        choices = registry.names(configured=configured)
        alternatives = field.pop("anyOf", None)
        if alternatives is None:
            alternatives = [{key: field.pop(key) for key in ("type", "pattern") if key in field}]
        field["anyOf"] = [{"type": "string", "enum": choices}, *alternatives]
        field["description"] = (
            "Choose a registered component. Names ignore case and surrounding spaces. "
            "Custom names must be registered in Python; unknown names fail before ingestion."
        )

    definitions = schema["$defs"]
    for name, help_fields in FIELD_HELP.items():
        target = schema if name == "Project" else definitions[name]
        for key, description in help_fields.items():
            target["properties"][key]["description"] = description
    suggest(definitions["Source"]["properties"]["loader"], components.loaders)
    for key in ("parser", "format"):
        suggest(definitions["Source"]["properties"][key], components.parsers)
    for key, registry in (
        ("graph", components.graphs),
        ("vector", components.vectors),
        ("artifacts", components.artifacts),
    ):
        suggest(definitions["StorageConfig"]["properties"][key], registry)
    suggest(schema["properties"]["chunker"], components.chunkers)
    suggest(schema["properties"]["extractor"], components.extractors, configured=False)
    suggest(schema["properties"]["embedding_provider"], components.embeddings, configured=False)
    suggest(definitions["ExtractionConfig"]["properties"]["provider"], components.extractors, configured=True)
    suggest(definitions["EmbeddingConfig"]["properties"]["provider"], components.embeddings, configured=True)
    schema["x-components"] = {name: registry.definitions() for name, registry in vars(components).items()}
    # A loader/provider selection determines its options definition in JSON editors.
    for group, model, selector in (
        ("loaders", "Source", "loader"),
        ("extractors", "ExtractionConfig", "provider"),
        ("embeddings", "EmbeddingConfig", "provider"),
    ):
        for name, definition in schema["x-components"][group].items():
            options = definition["options_schema"]
            if options is None:
                continue
            import copy

            key = f"{group}_{name}_options"

            def relocate(value, key=key):
                if isinstance(value, dict):
                    return {
                        k: (v.replace("#/$defs/", f"#/$defs/{key}/$defs/") if k == "$ref" else relocate(v))
                        for k, v in value.items()
                    }
                return [relocate(v) for v in value] if isinstance(value, list) else value

            definitions[key] = relocate(copy.deepcopy(options))
            constraints = {"properties": {"options": {"$ref": f"#/$defs/{key}"}}}
            if options.get("required"):
                constraints["required"] = ["options"]
            definitions[model].setdefault("allOf", []).append(
                {
                    "if": {"properties": {selector: {"const": name}}, "required": [selector]},
                    "then": constraints,
                }
            )
    if metadata is not None:
        properties = {field.name: field.value_schema() for field in metadata.fields}
        values = {"type": "object", "properties": properties, "additionalProperties": False}
        definitions["MetadataConfig"]["properties"]["defaults"] = {
            **values,
            "description": "Shared metadata values inherited by each source.",
        }
        required = [f.name for f in metadata.fields if f.required and f.name not in metadata.defaults]
        definitions["Source"]["properties"]["metadata"] = {
            **values,
            "required": required,
            "description": "Source overrides for the project's metadata defaults.",
        }
        if required:
            definitions["Source"]["required"].append("metadata")
    return schema
