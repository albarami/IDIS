"""Prompt Registry - fail-closed loader and validators for prompt artifacts.

Implements the on-disk model from IDIS_Prompt_Registry_and_Model_Policy_v6_3.md:
- Prompt artifacts: prompts/<prompt_id>/<version>/prompt.md + metadata.json
- Environment pointers: prompts/registry.{dev,staging,prod}.json

Design requirements:
- Fail-closed: invalid JSON, missing files, missing gates, or schema ref issues hard-fail
- Deterministic: stable ordering for JSON output
- Strict SemVer validation (MAJOR.MINOR.PATCH)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# SemVer pattern: MAJOR.MINOR.PATCH (no pre-release or build metadata for simplicity)
_SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class PromptRegistryError(Exception):
    """Base exception for prompt registry errors."""

    pass


class PromptNotFoundError(PromptRegistryError):
    """Raised when a prompt is not found in the registry."""

    def __init__(self, prompt_id: str, env: str) -> None:
        self.prompt_id = prompt_id
        self.env = env
        super().__init__(f"Prompt '{prompt_id}' not found in {env} registry")


class InvalidPromptVersionError(PromptRegistryError):
    """Raised when a prompt version is invalid."""

    def __init__(self, version: str, reason: str) -> None:
        self.version = version
        self.reason = reason
        super().__init__(f"Invalid version '{version}': {reason}")


class SchemaRefNotFoundError(PromptRegistryError):
    """Raised when a schema ref path does not exist."""

    def __init__(self, schema_ref: str, prompt_id: str) -> None:
        self.schema_ref = schema_ref
        self.prompt_id = prompt_id
        super().__init__(f"Schema ref '{schema_ref}' not found for prompt '{prompt_id}'")


class RegistryFileError(PromptRegistryError):
    """Raised when registry file cannot be loaded."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Cannot load registry file '{path}': {reason}")


class PromptArtifactError(PromptRegistryError):
    """Raised when prompt artifact is invalid or missing."""

    def __init__(self, prompt_id: str, version: str, reason: str) -> None:
        self.prompt_id = prompt_id
        self.version = version
        self.reason = reason
        super().__init__(f"Invalid prompt artifact '{prompt_id}@{version}': {reason}")


class SchemaRefBypassError(PromptRegistryError):
    """Raised when schema refs exist but schemas_root is not configured (fail-closed)."""

    def __init__(self, prompt_id: str, schema_ref: str) -> None:
        self.prompt_id = prompt_id
        self.schema_ref = schema_ref
        super().__init__(
            f"Schema ref '{schema_ref}' in prompt '{prompt_id}' cannot be validated: "
            "schemas_root is not configured. This is a fail-closed violation."
        )


class MissingRequiredFieldError(PromptRegistryError):
    """Raised when a required PromptArtifact field is missing."""

    def __init__(self, field: str, prompt_id: str | None = None) -> None:
        self.field = field
        self.prompt_id = prompt_id
        if prompt_id:
            super().__init__(f"Missing required field '{field}' in prompt '{prompt_id}'")
        else:
            super().__init__(f"Missing required field '{field}' in PromptArtifact")


class PromptStatus(StrEnum):
    """Prompt lifecycle status."""

    DRAFT = "DRAFT"
    STAGING = "STAGING"
    PROD = "PROD"
    DEPRECATED = "DEPRECATED"


class RiskClass(StrEnum):
    """Prompt risk classification."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ModelRequirements(BaseModel):
    """Model requirements for a prompt."""

    min_context_window: int = Field(default=4096, description="Minimum context window")
    tool_calling_support: bool = Field(default=False, description="Requires tool calling")
    json_mode_support: bool = Field(default=False, description="Requires JSON mode")


class PromptArtifact(BaseModel):
    """Prompt artifact with all required fields per v6.3 spec.

    Required fields from IDIS_Prompt_Registry_and_Model_Policy_v6_3.md §2.1.
    All fields listed as required in spec MUST NOT have defaults (fail-closed).

    Required (no defaults):
    - prompt_id, name, version, owner, created_at, updated_at
    - status (DRAFT|STAGING|PROD|DEPRECATED)
    - risk_class (LOW|MEDIUM|HIGH)
    - validation_gates_required (list of integers 1-4)
    - evaluation_results_ref (non-empty string)
    """

    model_config = {"protected_namespaces": ()}

    prompt_id: str = Field(..., description="Stable identifier")
    name: str = Field(..., description="Human-readable name")
    version: str = Field(..., description="SemVer: MAJOR.MINOR.PATCH")
    status: PromptStatus = Field(
        ..., description="Lifecycle status (DRAFT|STAGING|PROD|DEPRECATED)"
    )
    owner: str = Field(..., description="Team + person responsible")
    created_at: str = Field(..., description="ISO-8601 creation timestamp")
    updated_at: str = Field(..., description="ISO-8601 last update timestamp")
    change_summary: str = Field(default="", description="Summary of changes in this version")
    risk_class: RiskClass = Field(..., description="Risk classification (LOW|MEDIUM|HIGH)")
    model_requirements: ModelRequirements = Field(
        default_factory=ModelRequirements, description="Model requirements"
    )
    tool_contracts: list[str] = Field(
        default_factory=list, description="List of tools the prompt may call"
    )
    input_schema_ref: str | None = Field(default=None, description="Path to input JSON schema")
    output_schema_ref: str | None = Field(default=None, description="Path to output JSON schema")
    validation_gates_required: list[int] = Field(
        ..., description="Required gates (integers 1-4, must be present)"
    )
    fallback_policy: list[str] = Field(default_factory=list, description="Fallback model list")
    evaluation_results_ref: str = Field(
        ..., description="Immutable link to evaluation results (required, non-empty)"
    )
    security_notes: str = Field(default="", description="PII exposure risks, redaction rules")

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        """Validate version is strict SemVer."""
        if not _SEMVER_PATTERN.match(v):
            raise ValueError(f"Version must be strict SemVer (MAJOR.MINOR.PATCH), got: {v}")
        return v

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        """Validate timestamp is ISO-8601."""
        try:
            if v.endswith("Z"):
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            else:
                datetime.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"Timestamp must be ISO-8601, got: {v}") from e
        return v

    @field_validator("validation_gates_required")
    @classmethod
    def validate_gates(cls, v: list[int]) -> list[int]:
        """Validate gates are integers 1-4."""
        valid_gates = {1, 2, 3, 4}
        for gate in v:
            if gate not in valid_gates:
                raise ValueError(f"Gate must be 1, 2, 3, or 4, got: {gate}")
        return sorted(set(v))

    @field_validator("evaluation_results_ref")
    @classmethod
    def validate_evaluation_results_ref(cls, v: str) -> str:
        """Validate evaluation_results_ref is non-empty."""
        if not v or not v.strip():
            raise ValueError("evaluation_results_ref must be a non-empty string")
        return v


class LoadedPrompt(BaseModel):
    """A loaded prompt with artifact, text, and content hash."""

    artifact: PromptArtifact
    prompt_text: str
    content_hash: str = Field(description="SHA256 hash of prompt_text")


class RegistryPointer(BaseModel):
    """Registry pointer file structure."""

    env: Literal["dev", "staging", "prod"]
    updated_at: str
    prompts: dict[str, str] = Field(default_factory=dict, description="Map of prompt_id -> version")

    @field_validator("updated_at")
    @classmethod
    def validate_iso8601(cls, v: str) -> str:
        """Validate timestamp is ISO-8601."""
        try:
            if v.endswith("Z"):
                datetime.fromisoformat(v.replace("Z", "+00:00"))
            else:
                datetime.fromisoformat(v)
        except ValueError as e:
            raise ValueError(f"Timestamp must be ISO-8601, got: {v}") from e
        return v


def validate_semver(version: str) -> bool:
    """Check if a version string is valid SemVer."""
    return bool(_SEMVER_PATTERN.match(version))


def compute_content_hash(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class PromptRegistry:
    """Fail-closed prompt registry loader and validator.

    Loads prompts from on-disk structure:
    - prompts/<prompt_id>/<version>/prompt.md
    - prompts/<prompt_id>/<version>/metadata.json
    - prompts/registry.<env>.json

    All loading operations are fail-closed:
    - Missing files raise appropriate exceptions
    - Invalid JSON raises RegistryFileError
    - Invalid schema refs raise SchemaRefNotFoundError
    """

    def __init__(
        self,
        prompts_root: Path | str,
        schemas_root: Path | str | None = None,
    ) -> None:
        """Initialize the registry.

        Args:
            prompts_root: Root directory for prompts/ structure.
            schemas_root: Root directory for schema refs validation.
                          If None, schema ref validation is skipped.
        """
        self._prompts_root = Path(prompts_root)
        self._schemas_root = Path(schemas_root) if schemas_root else None
        self._loaded_registry: RegistryPointer | None = None
        self._env: str | None = None

    @property
    def prompts_root(self) -> Path:
        """Return the prompts root directory."""
        return self._prompts_root

    @property
    def env(self) -> str | None:
        """Return the currently loaded environment."""
        return self._env

    def _registry_file_path(self, env: str) -> Path:
        """Get the registry file path for an environment."""
        return self._prompts_root / f"registry.{env}.json"

    def _prompt_dir(self, prompt_id: str, version: str) -> Path:
        """Get the prompt artifact directory."""
        return self._prompts_root / prompt_id / version

    def _validate_schema_ref(self, schema_ref: str | None, prompt_id: str) -> None:
        """Validate that a schema ref path exists.

        Fail-closed behavior:
        - If schema_ref is None, no validation needed
        - If schema_ref exists but schemas_root is None, FAIL (cannot bypass validation)
        - If schema_ref exists and schemas_root is set, validate path exists

        Raises:
            SchemaRefBypassError: If schema ref exists but schemas_root is not configured
            SchemaRefNotFoundError: If schema ref path doesn't exist
        """
        if schema_ref is None:
            return

        if self._schemas_root is None:
            raise SchemaRefBypassError(prompt_id, schema_ref)

        schema_path = self._schemas_root / schema_ref
        if not schema_path.exists():
            raise SchemaRefNotFoundError(schema_ref, prompt_id)

    def load(self, env: Literal["dev", "staging", "prod"]) -> RegistryPointer:
        """Load registry pointer for an environment.

        Fail-closed on:
        - Missing registry file
        - Invalid JSON
        - Invalid schema

        Args:
            env: Environment to load (dev, staging, prod)

        Returns:
            Loaded RegistryPointer

        Raises:
            RegistryFileError: If file cannot be loaded
        """
        registry_path = self._registry_file_path(env)

        if not registry_path.exists():
            raise RegistryFileError(str(registry_path), "File does not exist")

        try:
            with open(registry_path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise RegistryFileError(str(registry_path), f"Invalid JSON: {e}") from e
        except OSError as e:
            raise RegistryFileError(str(registry_path), f"Read error: {e}") from e

        try:
            registry = RegistryPointer.model_validate(data)
        except Exception as e:
            raise RegistryFileError(str(registry_path), f"Schema validation failed: {e}") from e

        if registry.env != env:
            raise RegistryFileError(
                str(registry_path),
                f"Environment mismatch: file says '{registry.env}', expected '{env}'",
            )

        self._loaded_registry = registry
        self._env = env

        return registry

    def _load_prompt_artifact(self, prompt_id: str, version: str) -> tuple[PromptArtifact, str]:
        """Load a prompt artifact from disk.

        Args:
            prompt_id: Prompt identifier
            version: SemVer version string

        Returns:
            Tuple of (PromptArtifact, prompt_text)

        Raises:
            InvalidPromptVersionError: If version is not valid SemVer
            PromptArtifactError: If artifact files are missing or invalid
        """
        if not validate_semver(version):
            raise InvalidPromptVersionError(version, "Must be MAJOR.MINOR.PATCH")

        prompt_dir = self._prompt_dir(prompt_id, version)
        metadata_path = prompt_dir / "metadata.json"
        prompt_path = prompt_dir / "prompt.md"

        if not prompt_dir.exists():
            raise PromptArtifactError(prompt_id, version, "Directory does not exist")

        if not metadata_path.exists():
            raise PromptArtifactError(prompt_id, version, "metadata.json missing")

        if not prompt_path.exists():
            raise PromptArtifactError(prompt_id, version, "prompt.md missing")

        try:
            with open(metadata_path, encoding="utf-8") as f:
                metadata = json.load(f)
        except json.JSONDecodeError as e:
            raise PromptArtifactError(prompt_id, version, f"metadata.json invalid JSON: {e}") from e
        except OSError as e:
            raise PromptArtifactError(prompt_id, version, f"metadata.json read error: {e}") from e

        try:
            artifact = PromptArtifact.model_validate(metadata)
        except Exception as e:
            raise PromptArtifactError(
                prompt_id, version, f"metadata.json schema validation failed: {e}"
            ) from e

        if artifact.prompt_id != prompt_id:
            raise PromptArtifactError(
                prompt_id,
                version,
                f"prompt_id mismatch: metadata says '{artifact.prompt_id}'",
            )

        if artifact.version != version:
            raise PromptArtifactError(
                prompt_id,
                version,
                f"version mismatch: metadata says '{artifact.version}'",
            )

        self._validate_schema_ref(artifact.input_schema_ref, prompt_id)
        self._validate_schema_ref(artifact.output_schema_ref, prompt_id)

        try:
            with open(prompt_path, encoding="utf-8") as f:
                prompt_text = f.read()
        except OSError as e:
            raise PromptArtifactError(prompt_id, version, f"prompt.md read error: {e}") from e

        return artifact, prompt_text

    def get_prompt(self, prompt_id: str) -> LoadedPrompt:
        """Get a prompt from the currently loaded registry.

        Args:
            prompt_id: Prompt identifier

        Returns:
            LoadedPrompt with artifact, text, and content hash

        Raises:
            PromptRegistryError: If registry not loaded
            PromptNotFoundError: If prompt not in registry
            PromptArtifactError: If artifact files are invalid
        """
        if self._loaded_registry is None or self._env is None:
            raise PromptRegistryError("Registry not loaded. Call load(env) first.")

        if prompt_id not in self._loaded_registry.prompts:
            raise PromptNotFoundError(prompt_id, self._env)

        version = self._loaded_registry.prompts[prompt_id]
        artifact, prompt_text = self._load_prompt_artifact(prompt_id, version)

        return LoadedPrompt(
            artifact=artifact,
            prompt_text=prompt_text,
            content_hash=compute_content_hash(prompt_text),
        )

    def list_prompts(self) -> list[str]:
        """List all prompt IDs in the currently loaded registry.

        Returns:
            Stable sorted list of prompt IDs

        Raises:
            PromptRegistryError: If registry not loaded
        """
        if self._loaded_registry is None:
            raise PromptRegistryError("Registry not loaded. Call load(env) first.")

        return sorted(self._loaded_registry.prompts.keys())

    def get_version(self, prompt_id: str) -> str:
        """Get the version of a prompt in the currently loaded registry.

        Args:
            prompt_id: Prompt identifier

        Returns:
            Version string

        Raises:
            PromptRegistryError: If registry not loaded
            PromptNotFoundError: If prompt not in registry
        """
        if self._loaded_registry is None or self._env is None:
            raise PromptRegistryError("Registry not loaded. Call load(env) first.")

        if prompt_id not in self._loaded_registry.prompts:
            raise PromptNotFoundError(prompt_id, self._env)

        return self._loaded_registry.prompts[prompt_id]

    def validate_all_prompts(self) -> dict[str, Any]:
        """Validate all prompts in the currently loaded registry.

        Loads and validates each prompt artifact.

        Returns:
            Dict with 'valid', 'invalid', and 'errors' keys

        Raises:
            PromptRegistryError: If registry not loaded
        """
        if self._loaded_registry is None:
            raise PromptRegistryError("Registry not loaded. Call load(env) first.")

        valid: list[str] = []
        invalid: list[str] = []
        errors: dict[str, str] = {}

        for prompt_id in self._loaded_registry.prompts:
            try:
                self.get_prompt(prompt_id)
                valid.append(prompt_id)
            except PromptRegistryError as e:
                invalid.append(prompt_id)
                errors[prompt_id] = str(e)

        return {
            "valid": sorted(valid),
            "invalid": sorted(invalid),
            "errors": errors,
        }
