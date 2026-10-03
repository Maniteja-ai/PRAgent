"""Add registered provider options to a stage configuration schema."""

import copy
import re


def add_stage_schemas(schema, registries):
    for key, registry in registries:
        stage = copy.deepcopy(schema["$defs"]["StageConfig"])
        stage["properties"]["provider"]["anyOf"] = [
            {"enum": registry.names(configured=True)},
            {"type": "string", "pattern": r"^\s*[A-Za-z0-9_-]+\s*$"},
        ]
        stage["allOf"] = []
        for name, definition in registry.definitions().items():
            options = definition["options_schema"]
            if options is None:
                continue
            definition_key = f"{key}_{name}_options"

            def relocate(value, definition_key=definition_key):
                if isinstance(value, dict):
                    return {
                        k: (
                            v.replace("#/$defs/", f"#/$defs/{definition_key}/$defs/")
                            if k == "$ref"
                            else relocate(v)
                        )
                        for k, v in value.items()
                    }
                return [relocate(v) for v in value] if isinstance(value, list) else value

            schema["$defs"][definition_key] = relocate(options)
            constraints = {"properties": {"options": {"$ref": f"#/$defs/{definition_key}"}}}
            if options.get("required"):
                constraints["required"] = ["options"]
            # JSON Schema has no normalization transform; match the runtime's spelling rules.
            name_pattern = (
                r"^\s*"
                + "".join(f"[{c.lower()}{c.upper()}]" if c.isalpha() else re.escape(c) for c in name)
                + r"\s*$"
            )
            stage["allOf"].append(
                {
                    "if": {"properties": {"provider": {"pattern": name_pattern}}, "required": ["provider"]},
                    "then": constraints,
                }
            )
        schema["properties"][key] = stage
    return schema
