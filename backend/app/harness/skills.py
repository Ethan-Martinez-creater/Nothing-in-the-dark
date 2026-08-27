"""Skill manifest registry.

Each skill lives in its own directory next to a ``SKILL.md`` file whose YAML
frontmatter is the manifest: name, version, description, declared tools,
permissions, input/output contract, cost estimate and cancellation policy.
The frontmatter is parsed with a controlled, dependency-free parser (no PyYAML);
the body after the frontmatter is the instruction text loaded on demand.

``SkillRegistry.scan`` loads every skill directory from disk, so the catalog
and its validation always reflect the actual files. Tool and permission
dependencies are validated against the real registry so a manifest cannot
silently reference a tool that does not exist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import ApplicationError

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_CANCELLATION_POLICIES = frozenset({"restartable", "abortable", "checkpointed"})
_REQUIRED_FIELDS = ("name", "version", "tools")


@dataclass(frozen=True, slots=True)
class SkillIO:
    """One input/output contract entry: a parameter name and its meaning."""

    name: str
    description: str = ""
    required: bool = True


@dataclass(frozen=True, slots=True)
class SkillCost:
    estimated_tokens: int
    currency: str = "CNY"
    max_cost: float = 0  # 0 = no hard budget


@dataclass(frozen=True, slots=True)
class SkillManifest:
    name: str
    version: str
    description: str
    tools: tuple[str, ...]
    permissions: tuple[str, ...] = ()
    inputs: tuple[SkillIO, ...] = ()
    outputs: tuple[SkillIO, ...] = ()
    cost: SkillCost | None = None
    cancellation: str = "restartable"
    instruction_path: Path | None = None


def parse_skill_manifest(path: Path) -> SkillManifest:
    """Parse the SKILL.md frontmatter at ``path`` into a validated manifest."""
    text = path.read_text(encoding="utf-8")
    parsed = _parse_frontmatter(text)
    if parsed is None:
        raise ApplicationError(
            f"Skill file '{path}' has no YAML frontmatter",
            code="invalid_skill_manifest",
        )
    fields, body = parsed

    missing = [field for field in _REQUIRED_FIELDS if field not in fields]
    if missing:
        raise ApplicationError(
            f"Skill '{path.name}' manifest is missing fields: {missing}",
            code="invalid_skill_manifest",
        )
    name = str(fields["name"]).strip()
    version = str(fields["version"]).strip()
    if not _VERSION_RE.match(version):
        raise ApplicationError(
            f"Skill '{name}' has invalid version '{version}' (expected X.Y.Z)",
            code="invalid_skill_version",
        )
    tools = _string_tuple(fields["tools"], field="tools")
    if not tools:
        raise ApplicationError(
            f"Skill '{name}' declares no tools", code="invalid_skill_manifest"
        )

    description = str(fields.get("description") or "").strip() or _title_from(body)
    permissions = _string_tuple(fields.get("permissions"), field="permissions")
    inputs = _io_tuple(fields.get("inputs"))
    outputs = _io_tuple(fields.get("outputs"))
    cost = _parse_cost(name, fields.get("cost_tokens"))
    cancellation = str(fields.get("cancellation") or "restartable").strip()
    if cancellation not in _CANCELLATION_POLICIES:
        raise ApplicationError(
            f"Skill '{name}' has unknown cancellation policy '{cancellation}'",
            code="invalid_skill_cancellation",
        )
    return SkillManifest(
        name=name,
        version=version,
        description=description,
        tools=tools,
        permissions=permissions,
        inputs=inputs,
        outputs=outputs,
        cost=cost,
        cancellation=cancellation,
        instruction_path=path,
    )


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str] | None:
    """Controlled frontmatter parser: ``key: value`` and ``key: [a, b]`` lines
    between leading ``---`` markers. Returns (fields, body) or None when the
    file has no frontmatter block."""
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end == -1:
        raise ApplicationError(
            "Unterminated YAML frontmatter", code="invalid_skill_manifest"
        )
    block = text[4:end]
    body = text[end + 4 :].lstrip("\n")
    fields: dict[str, Any] = {}
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        value = raw.strip()
        if not key or not value:
            raise ApplicationError(
                f"Malformed frontmatter line: '{line}'",
                code="invalid_skill_manifest",
            )
        if value.startswith("["):
            if not value.endswith("]"):
                raise ApplicationError(
                    f"Unterminated list in frontmatter: '{line}'",
                    code="invalid_skill_manifest",
                )
            items = [
                item.strip().strip('"').strip("'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
            fields[key] = items
        else:
            fields[key] = value
    return fields, body


def _string_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ApplicationError(
            f"'{field}' must be a list like [a, b]", code="invalid_skill_manifest"
        )
    return tuple(str(item).strip() for item in value if str(item).strip())


def _io_tuple(value: Any) -> tuple[SkillIO, ...]:
    if value is None:
        return ()
    return tuple(SkillIO(name=item) for item in _string_tuple(value, field="io"))


def _parse_cost(name: str, value: Any) -> SkillCost | None:
    if value is None:
        return None
    try:
        tokens = int(value)
    except (TypeError, ValueError) as exc:
        raise ApplicationError(
            f"Skill '{name}' has non-integer cost_tokens '{value}'",
            code="invalid_skill_cost",
        ) from exc
    if tokens < 0:
        raise ApplicationError(
            f"Skill '{name}' has negative cost_tokens", code="invalid_skill_cost"
        )
    return SkillCost(estimated_tokens=tokens)


def _title_from(body: str) -> str:
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return ""


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, SkillManifest] = {}

    @classmethod
    def scan(cls, skill_root: Path) -> SkillRegistry:
        """Load every skill directory under ``skill_root`` from disk."""
        registry = cls()
        if not skill_root.is_dir():
            raise ApplicationError(
                f"Skill root '{skill_root}' does not exist",
                code="skill_root_missing",
            )
        for skill_file in sorted(skill_root.glob("*/SKILL.md")):
            registry.register(parse_skill_manifest(skill_file))
        return registry

    def register(self, manifest: SkillManifest) -> None:
        if manifest.name in self._skills:
            raise ApplicationError(
                f"Skill '{manifest.name}' is already registered",
                code="duplicate_skill",
            )
        self._skills[manifest.name] = manifest

    def get(self, name: str) -> SkillManifest:
        manifest = self._skills.get(name)
        if manifest is None:
            raise ApplicationError(f"Unknown skill '{name}'", code="skill_not_found")
        return manifest

    def load(self, name: str) -> str:
        manifest = self.get(name)
        if manifest.instruction_path is None or not manifest.instruction_path.is_file():
            raise ApplicationError(
                f"Skill '{name}' has no readable instruction file",
                code="skill_unavailable",
            )
        text = manifest.instruction_path.read_text(encoding="utf-8")
        parsed = _parse_frontmatter(text)
        return parsed[1] if parsed is not None else text

    def describe(self) -> list[dict[str, object]]:
        return [self._describe_one(manifest) for manifest in self._skills.values()]

    def describe_one(self, name: str) -> dict[str, object]:
        return self._describe_one(self.get(name))

    def validate_tools(self, available: set[str]) -> list[str]:
        """Names of declared tools that do not exist in the real tool set."""
        return sorted(
            {
                tool
                for manifest in self._skills.values()
                for tool in manifest.tools
                if tool not in available
            }
        )

    def validate_permissions(self, allowed: set[str]) -> list[str]:
        """Permission names declared in any manifest that are not allowed."""
        return sorted(
            {
                permission
                for manifest in self._skills.values()
                for permission in manifest.permissions
                if permission not in allowed
            }
        )

    def tool_names(self) -> set[str]:
        """Union of all tools declared across skill manifests."""
        return {
            tool
            for manifest in self._skills.values()
            for tool in manifest.tools
        }

    def estimate_cost(self, name: str) -> int:
        """Estimated tokens for one execution (0 when the manifest has none)."""
        manifest = self.get(name)
        return manifest.cost.estimated_tokens if manifest.cost else 0

    @staticmethod
    def _describe_one(manifest: SkillManifest) -> dict[str, object]:
        return {
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "tools": list(manifest.tools),
            "permissions": list(manifest.permissions),
            "inputs": [io.name for io in manifest.inputs],
            "outputs": [io.name for io in manifest.outputs],
            "cost_tokens": manifest.cost.estimated_tokens if manifest.cost else 0,
            "cancellation": manifest.cancellation,
            "loadable": manifest.instruction_path is not None,
        }
