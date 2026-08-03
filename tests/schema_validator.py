"""
Schema validation utilities for Kardenwort configuration domain models.

Loads JSON Schema (Draft 2020-12) specification files from the schemas/config/
directory and provides programmatic validation helpers for runtime configuration
dictionaries. Used exclusively during test execution and validation runs —
not loaded in production processing pipelines.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
from typing import Any

import jsonschema
import jsonschema.validators

# Root of the project (one level above this tests/ directory)
_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
_SCHEMAS_DIR = _PROJECT_ROOT / "schemas" / "config"

# Registry: config class name → schema filename
_SCHEMA_FILES: dict[str, str] = {
    "RuntimeTokenConfig": "runtime_token_config.json",
    "BatchMergeConfig": "batch_merge_config.json",
    "DeGCSConfig": "de_gcs_config.json",
    "SentenceBoundaryConfig": "sentence_boundary_config.json",
    "SentencesModeConfig": "sentences_mode_config.json",
}


def load_schema(schema_name: str) -> dict[str, Any]:
    """
    Load a JSON Schema specification by config class name.

    Args:
        schema_name: The name of the config class (e.g. 'RuntimeTokenConfig').

    Returns:
        The parsed JSON Schema as a dict.

    Raises:
        KeyError: If no schema is registered for the given class name.
        FileNotFoundError: If the schema file does not exist on disk.
        json.JSONDecodeError: If the schema file contains invalid JSON.
    """
    filename = _SCHEMA_FILES[schema_name]
    schema_path = _SCHEMAS_DIR / filename
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_config_dict(config_dict: dict[str, Any], schema_name: str) -> None:
    """
    Validate a configuration dictionary against its JSON Schema contract.

    Uses the jsonschema Draft202012Validator which implements JSON Schema
    Draft 2020-12 semantics.

    Args:
        config_dict: A plain dict representation of the configuration.
        schema_name: The config class name identifying which schema to use.

    Raises:
        jsonschema.ValidationError: If the dict does not conform to the schema.
        KeyError: If no schema is registered for the given class name.
        FileNotFoundError: If the schema file does not exist on disk.
    """
    schema = load_schema(schema_name)
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)
    validator = validator_cls(schema)
    validator.validate(config_dict)


def dataclass_to_schema_dict(instance: Any) -> dict[str, Any]:
    """
    Convert a frozen dataclass instance to a plain dict containing only the
    fields declared in its corresponding JSON Schema.

    Fields that are not declared in the schema (e.g. computed runtime fields
    like ``abbrev_set`` on ``SentenceBoundaryConfig``) are intentionally excluded
    because they represent derived state with no schema property definition.

    Args:
        instance: A frozen dataclass instance (e.g. RuntimeTokenConfig).

    Returns:
        A dict containing only the schema-declared (JSON-serialisable) fields.
    """
    schema_name = type(instance).__name__
    schema = load_schema(schema_name)
    declared_keys = set(schema.get("properties", {}).keys())
    raw = dataclasses.asdict(instance)
    return {key: value for key, value in raw.items() if key in declared_keys}


def validate_dataclass(instance: Any) -> None:
    """
    Convenience helper: convert a dataclass instance to a schema-compatible dict
    and validate it against its registered JSON Schema.

    Args:
        instance: A frozen dataclass instance whose class name is registered in
                  the schema registry (e.g. RuntimeTokenConfig()).

    Raises:
        jsonschema.ValidationError: If the instance does not conform to its schema.
        KeyError: If no schema is registered for the instance's class name.
    """
    schema_name = type(instance).__name__
    config_dict = dataclass_to_schema_dict(instance)
    validate_config_dict(config_dict, schema_name)
