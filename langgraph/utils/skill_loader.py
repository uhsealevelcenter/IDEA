"""Authoritative, non-terminal loading for IDEA and Open WebUI skills."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import quote

import requests
import yaml
from langchain_core.tools import tool
from idea_config import IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES


BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"
MAX_SKILL_BYTES = int(os.getenv("MAX_SKILL_BYTES", "100000"))
MAX_SKILL_COMPONENT_BYTES = int(
    os.getenv("MAX_SKILL_COMPONENT_BYTES", str(MAX_SKILL_BYTES))
)
MAX_SKILL_BUNDLE_BYTES = int(os.getenv("MAX_SKILL_BUNDLE_BYTES", "200000"))
MAX_SKILL_COMPONENTS = int(os.getenv("MAX_SKILL_COMPONENTS", "32"))
MAX_SKILL_DEPENDENCY_DEPTH = int(
    os.getenv("MAX_SKILL_DEPENDENCY_DEPTH", "16")
)
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PACKAGE_MANIFEST_NAME = "manifest.yaml"


class SkillLoadError(ValueError):
    """A safe, user-facing skill loading failure."""


@dataclass(frozen=True)
class SkillDocument:
    source: Literal["builtin", "workspace"]
    skill_id: str
    name: str
    description: str
    content: str

    @property
    def byte_count(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def to_tool_result(self) -> str:
        """Serialize the complete skill without applying terminal truncation."""
        return json.dumps(
            {
                "source": self.source,
                "id": self.skill_id,
                "name": self.name,
                "description": self.description,
                "bytes": self.byte_count,
                "sha256": self.sha256,
                "content": self.content,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class SkillComponentDefinition:
    component_id: str
    kind: Literal["reference", "skill"]
    path: Path
    description: str
    requires: tuple[str, ...]


@dataclass(frozen=True)
class SkillRouteDefinition:
    route_id: str
    description: str
    components: tuple[str, ...]


@dataclass(frozen=True)
class SkillPackage:
    skill_id: str
    name: str
    description: str
    status: str
    root: Path
    entrypoint: Path
    components: dict[str, SkillComponentDefinition]
    routes: dict[str, SkillRouteDefinition]
    external_requirements: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class SkillBundleDocument:
    component_id: str
    kind: Literal["root", "reference", "skill"]
    description: str
    content: str

    @property
    def byte_count(self) -> int:
        return len(self.content.encode("utf-8"))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SkillBundle:
    source: Literal["builtin", "workspace"]
    skill_id: str
    name: str
    description: str
    status: str
    route: str | None
    requested_components: tuple[str, ...]
    documents: tuple[SkillBundleDocument, ...]
    external_requirements: tuple[dict[str, Any], ...] = ()

    @property
    def byte_count(self) -> int:
        return sum(document.byte_count for document in self.documents)

    @property
    def sha256(self) -> str:
        digest = hashlib.sha256()
        for document in self.documents:
            digest.update(document.component_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(document.sha256.encode("ascii"))
            digest.update(b"\0")
        return digest.hexdigest()

    def to_tool_result(self) -> str:
        """Serialize a complete, atomically resolved package selection."""
        return json.dumps(
            {
                "source": self.source,
                "id": self.skill_id,
                "name": self.name,
                "description": self.description,
                "status": self.status,
                "route": self.route,
                "requested_components": list(self.requested_components),
                "load_order": [
                    document.component_id for document in self.documents
                ],
                "component_count": len(self.documents),
                "bytes": self.byte_count,
                "sha256": self.sha256,
                "external_requirements": list(self.external_requirements),
                "documents": [
                    {
                        "component": document.component_id,
                        "kind": document.kind,
                        "description": document.description,
                        "bytes": document.byte_count,
                        "sha256": document.sha256,
                        "content": document.content,
                    }
                    for document in self.documents
                ],
            },
            ensure_ascii=False,
        )

    def to_plan_result(self) -> str:
        """Serialize routing metadata without injecting every instruction."""
        return json.dumps(
            {
                "source": self.source,
                "id": self.skill_id,
                "name": self.name,
                "description": self.description,
                "status": self.status,
                "detail": "plan",
                "route": self.route,
                "requested_components": list(self.requested_components),
                "load_order": [
                    document.component_id for document in self.documents
                ],
                "component_count": len(self.documents),
                "bytes_if_loaded": self.byte_count,
                "sha256": self.sha256,
                "external_requirements": list(self.external_requirements),
                "documents": [
                    {
                        "component": document.component_id,
                        "kind": document.kind,
                        "description": document.description,
                        "bytes": document.byte_count,
                        "sha256": document.sha256,
                    }
                    for document in self.documents
                ],
                "next_step": (
                    "Load only the components needed now with detail='full' "
                    "and components=['component-id']."
                ),
            },
            ensure_ascii=False,
        )


def _validate_content_size(
    content: str,
    skill_id: str,
    maximum: int | None = None,
) -> None:
    maximum = MAX_SKILL_BYTES if maximum is None else maximum
    byte_count = len(content.encode("utf-8"))
    if byte_count > maximum:
        raise SkillLoadError(
            f"Skill '{skill_id}' is {byte_count} bytes; the maximum supported "
            f"size is {maximum} bytes. The skill was not partially loaded."
        )


def _parse_frontmatter(content: str, skill_id: str) -> tuple[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' is missing YAML frontmatter"
        )
    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' has unterminated YAML frontmatter"
        ) from exc

    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing_index])) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' has invalid YAML frontmatter"
        ) from exc
    if not isinstance(metadata, dict):
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' frontmatter must be a mapping"
        )

    name = metadata.get("name")
    description = metadata.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' frontmatter is missing name"
        )
    if name.strip() != skill_id:
        raise SkillLoadError(
            f"Built-in skill directory '{skill_id}' does not match "
            f"frontmatter name '{name.strip()}'"
        )
    if not isinstance(description, str) or not description.strip():
        raise SkillLoadError(
            f"Built-in skill '{skill_id}' frontmatter is missing description"
        )
    return name.strip(), description.strip()


class BuiltinSkillLoader:
    """Loads IDEA-maintained skills from the LangGraph service filesystem."""

    def __init__(self, root: Path = BUILTIN_SKILLS_DIR):
        self.root = root.resolve()

    def _skill_path(self, skill_id: str) -> Path:
        if not SKILL_ID_RE.fullmatch(skill_id or ""):
            raise SkillLoadError(f"Invalid built-in skill id: {skill_id!r}")

        candidate = self.root / skill_id / "SKILL.md"
        if candidate.parent.is_symlink() or candidate.is_symlink():
            raise SkillLoadError(
                f"Built-in skill '{skill_id}' may not use symbolic links"
            )
        resolved = candidate.resolve()
        if self.root not in resolved.parents or not resolved.is_file():
            raise SkillLoadError(f"Unknown built-in skill: {skill_id}")
        return resolved

    def _package_root(self, skill_id: str) -> Path:
        return self._skill_path(skill_id).parent

    @staticmethod
    def _validate_local_id(value: Any, label: str) -> str:
        if not isinstance(value, str) or not SKILL_ID_RE.fullmatch(value):
            raise SkillLoadError(f"Invalid {label}: {value!r}")
        return value

    @staticmethod
    def _string_list(value: Any, label: str) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item for item in value
        ):
            raise SkillLoadError(f"{label} must be a list of ids")
        if len(set(value)) != len(value):
            raise SkillLoadError(f"{label} contains duplicate ids")
        return tuple(value)

    @staticmethod
    def _safe_package_file(
        package_root: Path,
        relative_path: Any,
        label: str,
    ) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise SkillLoadError(f"{label} path is required")
        relative = Path(relative_path)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative_path.startswith(("/", "\\"))
        ):
            raise SkillLoadError(f"{label} has an unsafe path")

        candidate = package_root / relative
        current = candidate
        while current != package_root:
            if current.is_symlink():
                raise SkillLoadError(f"{label} may not use symbolic links")
            if package_root not in current.parents:
                raise SkillLoadError(f"{label} escapes its package")
            current = current.parent

        resolved = candidate.resolve()
        if package_root not in resolved.parents or not resolved.is_file():
            raise SkillLoadError(f"{label} file was not found")
        return resolved

    @staticmethod
    def _mapping(value: Any, label: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise SkillLoadError(f"{label} must be a mapping")
        return value

    def package(self, skill_id: str) -> SkillPackage | None:
        """Return and fully validate a package manifest, or None for a flat skill."""
        package_root = self._package_root(skill_id)
        manifest_path = package_root / PACKAGE_MANIFEST_NAME
        if not manifest_path.exists():
            return None
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' has an invalid manifest"
            )

        manifest_content = manifest_path.read_text(encoding="utf-8")
        _validate_content_size(
            manifest_content,
            f"{skill_id}/{PACKAGE_MANIFEST_NAME}",
            MAX_SKILL_COMPONENT_BYTES,
        )
        try:
            manifest = yaml.safe_load(manifest_content) or {}
        except yaml.YAMLError as exc:
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' has invalid YAML"
            ) from exc
        if not isinstance(manifest, dict):
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' manifest must be a mapping"
            )
        if manifest.get("schema_version") != 1:
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' requires schema_version 1"
            )
        if manifest.get("id") != skill_id:
            raise SkillLoadError(
                f"Built-in skill package directory '{skill_id}' does not match "
                f"manifest id '{manifest.get('id')}'"
            )

        entrypoint = self._safe_package_file(
            package_root,
            manifest.get("entrypoint", "SKILL.md"),
            f"Package '{skill_id}' entrypoint",
        )
        root_content = entrypoint.read_text(encoding="utf-8")
        _validate_content_size(
            root_content,
            skill_id,
            MAX_SKILL_COMPONENT_BYTES,
        )
        name, frontmatter_description = _parse_frontmatter(
            root_content,
            skill_id,
        )
        description = manifest.get("description", frontmatter_description)
        if not isinstance(description, str) or not description.strip():
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' is missing description"
            )
        status = manifest.get("status", "")
        if status is not None and not isinstance(status, str):
            raise SkillLoadError(
                f"Built-in skill package '{skill_id}' status must be text"
            )

        components: dict[str, SkillComponentDefinition] = {}

        def add_definitions(raw: Any, kind: Literal["reference", "skill"]):
            label = "references" if kind == "reference" else "components"
            for component_id, definition in self._mapping(
                raw,
                f"Package '{skill_id}' {label}",
            ).items():
                component_id = self._validate_local_id(
                    component_id,
                    f"{skill_id} {kind} id",
                )
                if component_id in components or component_id == "root":
                    raise SkillLoadError(
                        f"Package '{skill_id}' has duplicate component "
                        f"'{component_id}'"
                    )
                if not isinstance(definition, dict):
                    raise SkillLoadError(
                        f"Package '{skill_id}' component '{component_id}' "
                        "must be a mapping"
                    )
                path = self._safe_package_file(
                    package_root,
                    definition.get("path"),
                    f"Package '{skill_id}' component '{component_id}'",
                )
                content = path.read_text(encoding="utf-8")
                _validate_content_size(
                    content,
                    f"{skill_id}/{component_id}",
                    MAX_SKILL_COMPONENT_BYTES,
                )
                description_value = definition.get("description", "")
                if description_value is not None and not isinstance(
                    description_value,
                    str,
                ):
                    raise SkillLoadError(
                        f"Package '{skill_id}' component '{component_id}' "
                        "description must be text"
                    )
                if kind == "skill":
                    module_name, module_description = _parse_frontmatter(
                        content,
                        component_id,
                    )
                    if module_name != component_id:
                        raise SkillLoadError(
                            f"Package '{skill_id}' component '{component_id}' "
                            "frontmatter name does not match"
                        )
                    if not description_value:
                        description_value = module_description
                components[component_id] = SkillComponentDefinition(
                    component_id=component_id,
                    kind=kind,
                    path=path,
                    description=(description_value or "").strip(),
                    requires=self._string_list(
                        definition.get("requires"),
                        (
                            f"Package '{skill_id}' component "
                            f"'{component_id}' requires"
                        ),
                    ),
                )

        add_definitions(manifest.get("references"), "reference")
        add_definitions(manifest.get("components"), "skill")
        if len(components) > MAX_SKILL_COMPONENTS:
            raise SkillLoadError(
                f"Package '{skill_id}' declares {len(components)} components; "
                f"the maximum is {MAX_SKILL_COMPONENTS}"
            )

        for component in components.values():
            for dependency in component.requires:
                if dependency not in components:
                    raise SkillLoadError(
                        f"Package '{skill_id}' component "
                        f"'{component.component_id}' requires unknown component "
                        f"'{dependency}'"
                    )

        visiting: list[str] = []
        visited: set[str] = set()

        def validate_graph(component_id: str, depth: int = 1) -> None:
            if depth > MAX_SKILL_DEPENDENCY_DEPTH:
                raise SkillLoadError(
                    f"Package '{skill_id}' dependency depth exceeds "
                    f"{MAX_SKILL_DEPENDENCY_DEPTH}"
                )
            if component_id in visiting:
                cycle = " -> ".join(
                    [*visiting[visiting.index(component_id):], component_id]
                )
                raise SkillLoadError(
                    f"Package '{skill_id}' has a dependency cycle: {cycle}"
                )
            if component_id in visited:
                return
            visiting.append(component_id)
            for dependency in components[component_id].requires:
                validate_graph(dependency, depth + 1)
            visiting.pop()
            visited.add(component_id)

        for component_id in components:
            validate_graph(component_id)

        routes: dict[str, SkillRouteDefinition] = {}
        for route_id, definition in self._mapping(
            manifest.get("routes"),
            f"Package '{skill_id}' routes",
        ).items():
            route_id = self._validate_local_id(route_id, f"{skill_id} route id")
            if not isinstance(definition, dict):
                raise SkillLoadError(
                    f"Package '{skill_id}' route '{route_id}' must be a mapping"
                )
            route_components = self._string_list(
                definition.get("components"),
                f"Package '{skill_id}' route '{route_id}' components",
            )
            if not route_components:
                raise SkillLoadError(
                    f"Package '{skill_id}' route '{route_id}' is empty"
                )
            for component_id in route_components:
                if component_id not in components:
                    raise SkillLoadError(
                        f"Package '{skill_id}' route '{route_id}' selects "
                        f"unknown component '{component_id}'"
                    )
            route_description = definition.get("description")
            if (
                not isinstance(route_description, str)
                or not route_description.strip()
            ):
                raise SkillLoadError(
                    f"Package '{skill_id}' route '{route_id}' is missing "
                    "description"
                )
            routes[route_id] = SkillRouteDefinition(
                route_id=route_id,
                description=route_description.strip(),
                components=route_components,
            )

        external_requirements = manifest.get("external_requirements", [])
        if not isinstance(external_requirements, list) or not all(
            isinstance(requirement, dict)
            for requirement in external_requirements
        ):
            raise SkillLoadError(
                f"Package '{skill_id}' external_requirements must be a list "
                "of mappings"
            )
        try:
            json.dumps(external_requirements)
        except (TypeError, ValueError) as exc:
            raise SkillLoadError(
                f"Package '{skill_id}' external_requirements must contain "
                "JSON-compatible values"
            ) from exc

        return SkillPackage(
            skill_id=skill_id,
            name=name,
            description=description.strip(),
            status=(status or "").strip(),
            root=package_root,
            entrypoint=entrypoint,
            components=components,
            routes=routes,
            external_requirements=tuple(external_requirements),
        )

    def load(self, skill_id: str) -> SkillDocument:
        path = self._skill_path(skill_id)
        content = path.read_text(encoding="utf-8")
        _validate_content_size(content, skill_id)
        name, description = _parse_frontmatter(content, skill_id)
        return SkillDocument(
            source="builtin",
            skill_id=skill_id,
            name=name,
            description=description,
            content=content,
        )

    def load_bundle(
        self,
        skill_id: str,
        *,
        route: str | None = None,
        components: list[str] | tuple[str, ...] | None = None,
    ) -> SkillBundle:
        """Resolve a package route/component closure and return it atomically."""
        if route and components:
            raise SkillLoadError(
                "Specify either a skill package route or components, not both"
            )
        if components is not None and not isinstance(components, (list, tuple)):
            raise SkillLoadError(
                "Skill package components must be provided as a list of ids"
            )
        package = self.package(skill_id)
        if package is None:
            root_document = self.load(skill_id)
            if route or components:
                raise SkillLoadError(
                    f"Built-in skill '{skill_id}' is not a hierarchical package"
                )
            return SkillBundle(
                source="builtin",
                skill_id=skill_id,
                name=root_document.name,
                description=root_document.description,
                status="",
                route=None,
                requested_components=(),
                documents=(
                    SkillBundleDocument(
                        component_id="root",
                        kind="root",
                        description=root_document.description,
                        content=root_document.content,
                    ),
                ),
            )

        root_content = package.entrypoint.read_text(encoding="utf-8")
        _validate_content_size(
            root_content,
            skill_id,
            MAX_SKILL_COMPONENT_BYTES,
        )
        root_document = SkillDocument(
            source="builtin",
            skill_id=skill_id,
            name=package.name,
            description=package.description,
            content=root_content,
        )

        if route:
            route = self._validate_local_id(route, f"{skill_id} route id")
            route_definition = package.routes.get(route)
            if route_definition is None:
                raise SkillLoadError(
                    f"Unknown route '{route}' for built-in package '{skill_id}'"
                )
            requested = route_definition.components
        else:
            requested = tuple(components or ())
            for component_id in requested:
                self._validate_local_id(
                    component_id,
                    f"{skill_id} component id",
                )
                if component_id not in package.components:
                    raise SkillLoadError(
                        f"Unknown component '{component_id}' for built-in "
                        f"package '{skill_id}'"
                    )
            if len(set(requested)) != len(requested):
                raise SkillLoadError(
                    f"Package '{skill_id}' selection contains duplicate components"
                )

        resolved: list[str] = []
        included: set[str] = set()

        def include(component_id: str, depth: int = 1) -> None:
            if component_id in included:
                return
            if depth > MAX_SKILL_DEPENDENCY_DEPTH:
                raise SkillLoadError(
                    f"Package '{skill_id}' dependency depth exceeds "
                    f"{MAX_SKILL_DEPENDENCY_DEPTH}"
                )
            definition = package.components[component_id]
            for dependency in definition.requires:
                include(dependency, depth + 1)
            included.add(component_id)
            resolved.append(component_id)

        for component_id in requested:
            include(component_id)

        documents = [
            SkillBundleDocument(
                component_id="root",
                kind="root",
                description=root_document.description,
                content=root_document.content,
            )
        ]
        for component_id in resolved:
            definition = package.components[component_id]
            content = definition.path.read_text(encoding="utf-8")
            _validate_content_size(
                content,
                f"{skill_id}/{component_id}",
                MAX_SKILL_COMPONENT_BYTES,
            )
            documents.append(
                SkillBundleDocument(
                    component_id=component_id,
                    kind=definition.kind,
                    description=definition.description,
                    content=content,
                )
            )

        if len(documents) > MAX_SKILL_COMPONENTS + 1:
            raise SkillLoadError(
                f"Package '{skill_id}' resolved to {len(documents)} documents; "
                f"the maximum is {MAX_SKILL_COMPONENTS + 1}"
            )
        total_bytes = sum(document.byte_count for document in documents)
        if total_bytes > MAX_SKILL_BUNDLE_BYTES:
            raise SkillLoadError(
                f"Package '{skill_id}' selection is {total_bytes} bytes; the "
                f"maximum bundle size is {MAX_SKILL_BUNDLE_BYTES} bytes. "
                "No package component was partially loaded."
            )

        return SkillBundle(
            source="builtin",
            skill_id=skill_id,
            name=package.name,
            description=package.description,
            status=package.status,
            route=route,
            requested_components=tuple(requested),
            documents=tuple(documents),
            external_requirements=package.external_requirements,
        )

    def catalog(self) -> list[SkillDocument]:
        documents = []
        if not self.root.is_dir():
            raise SkillLoadError(
                f"Built-in skill directory does not exist: {self.root}"
            )
        for skill_dir in sorted(self.root.iterdir()):
            if not skill_dir.is_dir() or skill_dir.is_symlink():
                continue
            skill_path = skill_dir / "SKILL.md"
            if skill_path.is_file():
                documents.append(self.load(skill_dir.name))
        return documents

    def render_manifest(self) -> str:
        entries = []
        for document in self.catalog():
            package = self.package(document.skill_id)
            package_lines = []
            if package is not None:
                package_lines.extend(
                    [
                        "    <kind>package</kind>",
                        "    <routes>",
                    ]
                )
                for route in package.routes.values():
                    package_lines.extend(
                        [
                            "      <route>",
                            f"        <id>{html.escape(route.route_id)}</id>",
                            "        <description>"
                            f"{html.escape(route.description)}"
                            "</description>",
                            "      </route>",
                        ]
                    )
                package_lines.append("    </routes>")
            entry_lines = [
                "  <skill>",
                "    <source>builtin</source>",
                f"    <id>{html.escape(document.skill_id)}</id>",
                f"    <name>{html.escape(document.name)}</name>",
                (
                    "    <description>"
                    f"{html.escape(document.description)}"
                    "</description>"
                ),
            ]
            entry_lines.extend(package_lines)
            entry_lines.append("  </skill>")
            entries.append("\n".join(entry_lines))
        return (
            "<available_builtin_skills>\n"
            + "\n".join(entries)
            + "\n</available_builtin_skills>"
        )


class OpenWebUISkillLoader:
    """Loads an access-controlled Workspace skill through Open WebUI."""

    def __init__(
        self,
        base_url: str,
        authorization: str | None,
        http_get: Callable = requests.get,
        timeout: tuple[float, float] = (5.0, 15.0),
    ):
        self.base_url = base_url.rstrip("/")
        self.authorization = authorization
        self.http_get = http_get
        self.timeout = timeout

    def load(self, skill_id: str) -> SkillDocument:
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise SkillLoadError("Workspace skill id is required")
        skill_id = skill_id.strip()
        if len(skill_id) > 200:
            raise SkillLoadError("Workspace skill id is too long")
        if not self.authorization:
            raise SkillLoadError(
                "Open WebUI Workspace skills are unavailable without the "
                "current user's Open WebUI authorization"
            )

        url = (
            f"{self.base_url}/api/v1/skills/id/"
            f"{quote(skill_id, safe='')}"
        )
        try:
            response = self.http_get(
                url,
                headers={"Authorization": self.authorization},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' could not be reached"
            ) from exc

        if response.status_code in {401, 403}:
            raise SkillLoadError(
                f"Access denied for Open WebUI Workspace skill '{skill_id}'"
            )
        if response.status_code == 404:
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' was not found"
            )
        try:
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' returned an invalid response"
            ) from exc
        if not isinstance(payload, dict):
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' returned an invalid response"
            )
        if payload.get("is_active", True) is not True:
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' is inactive"
            )

        content = payload.get("content")
        name = payload.get("name")
        description = payload.get("description") or ""
        response_id = payload.get("id") or skill_id
        if not isinstance(content, str) or not isinstance(name, str):
            raise SkillLoadError(
                f"Open WebUI Workspace skill '{skill_id}' returned incomplete data"
            )
        _validate_content_size(content, skill_id)
        return SkillDocument(
            source="workspace",
            skill_id=str(response_id),
            name=name,
            description=str(description),
            content=content,
        )

    def load_bundle(
        self,
        skill_id: str,
        *,
        route: str | None = None,
        components: list[str] | tuple[str, ...] | None = None,
    ) -> SkillBundle:
        """
        Return a flat Workspace skill as a one-document bundle.

        Open WebUI currently stores one authorized text document per skill,
        not an atomic package. Reject hierarchical selections explicitly
        until its persistence/API can enforce one package-level ACL/version.
        """
        if route or components:
            raise SkillLoadError(
                "Open WebUI Workspace skills do not yet support hierarchical "
                "package routes or components"
            )
        document = self.load(skill_id)
        return SkillBundle(
            source="workspace",
            skill_id=document.skill_id,
            name=document.name,
            description=document.description,
            status="",
            route=None,
            requested_components=(),
            documents=(
                SkillBundleDocument(
                    component_id="root",
                    kind="root",
                    description=document.description,
                    content=document.content,
                ),
            ),
        )


def _skill_result_page(
    result: str,
    *,
    document: str = "",
    section: str = "",
    offset: int = 0,
    expected_sha256: str = "",
) -> str:
    """Fit skill responses inside the agent's observation limit, without losing
    JSON structure or the ability to retrieve omitted instructions.

    Loaders still validate and authorize the entire selection before this
    presentation layer selects a document, heading, or character range.
    """
    payload = json.loads(result)
    selection_hash = payload["sha256"]
    if expected_sha256 and expected_sha256 != selection_hash:
        raise SkillLoadError("Skill content changed; review the current skill before continuing.")
    if offset < 0:
        raise SkillLoadError("Skill offset must be non-negative")
    limit = IDEA_MAX_MODEL_TOOL_OBSERVATION_BYTES
    if (
        not document and not section and offset == 0
        and len(result.encode("utf-8")) <= limit
    ):
        return result

    is_plan = payload.get("detail") == "plan"
    documents = payload.get("documents") or []
    selected_index = 0
    if is_plan:
        if document or section:
            raise SkillLoadError("Use detail='full' to read a package document or section.")
        # Large plans are paged as metadata, never passed off as instructions.
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        document_id = ""
        document_hash = selection_hash
    elif documents:
        document_id = document or "root"
        selected_index = next((
            index for index, item in enumerate(documents)
            if item["component"] == document_id
        ), -1)
        if selected_index < 0:
            raise SkillLoadError(f"Document {document_id!r} is not in this package selection")
        selected = documents[selected_index]
        text = selected["content"]
        document_hash = selected["sha256"]
    else:
        if document and document != "root":
            raise SkillLoadError("Flat skills have only the 'root' document")
        document_id = "root"
        text = payload["content"]
        document_hash = selection_hash

    # Ignore headings inside fenced examples. Offsets are Unicode character
    # indices into the original document, not UTF-8 byte offsets.
    headings = []
    cursor = 0
    fence = ""
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if not fence:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = ""
        elif not fence and not is_plan:
            match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.rstrip())
            if match:
                headings.append({"heading": match.group(2), "offset": cursor, "level": len(match.group(1))})
        cursor += len(line)

    start, end = 0, len(text)
    if section:
        matches = [item for item in headings if item["heading"] == section]
        if len(matches) != 1:
            raise SkillLoadError("Section must match one unique heading exactly; use an offset for duplicate headings.")
        heading = matches[0]
        start = heading["offset"]
        end = next((
            item["offset"] for item in headings
            if item["offset"] > start and item["level"] <= heading["level"]
        ), len(text))
    position = offset or start
    if position < start or position > end:
        raise SkillLoadError("Skill offset is outside the requested document or section")
    page = {
        "source": payload["source"], "id": payload["id"],
        "sha256": selection_hash, "document": document_id,
        "document_sha256": document_hash,
        "detail": "plan" if is_plan else "page",
        "content_format": "json metadata" if is_plan else "markdown",
        "total_chars": len(text), "section": section or None,
        "offset": position, "end_offset": position,
        "next_offset": None, "range_complete": False,
        "document_count": len(documents) if documents else 1,
        "next_document": (
            documents[selected_index + 1]["component"]
            if not is_plan and selected_index + 1 < len(documents) else None
        ),
        "sections": [
            {"heading": item["heading"], "offset": item["offset"]}
            for item in headings if item["offset"] >= position
        ][:12],
        "content": "",
    }

    def encode(stop: int) -> str:
        page.update(
            content=text[position:stop], end_offset=stop,
            next_offset=stop if stop < end else None,
            range_complete=stop == end,
        )
        return json.dumps(page, ensure_ascii=False)

    # Heading indexes are optional navigation; retain room for actual content.
    while page["sections"] and len(encode(position).encode("utf-8")) > limit // 2:
        page["sections"].pop()
    low, high = position, min(end, position + limit)
    if len(encode(low).encode("utf-8")) > limit:
        raise SkillLoadError("Skill metadata exceeds the model observation limit")
    while low < high:
        middle = (low + high + 1) // 2
        if len(encode(middle).encode("utf-8")) <= limit:
            low = middle
        else:
            high = middle - 1
    if low == position and position < end:
        raise SkillLoadError("Model observation limit leaves no room for skill content")
    return encode(low)


def make_view_skill_tool(
    builtin_loader: BuiltinSkillLoader,
    workspace_loader: OpenWebUISkillLoader,
):
    """Create a source-aware tool with progressive package loading."""

    @tool
    def view_skill(
        source: Literal["builtin", "workspace"],
        id: str,
        route: str = "",
        components: list[str] | None = None,
        detail: Literal["plan", "full"] = "plan",
        document: str = "",
        section: str = "",
        offset: int = 0,
        expected_sha256: str = "",
    ) -> str:
        """
        Inspect or load instructions for an available skill/package.

        Use source="builtin" for entries in <available_builtin_skills> and
        source="workspace" for entries in Open WebUI's <available_skills>.
        Pass the exact manifest id. For a built-in package, pass either an
        exact advertised route with detail="plan" first. This returns the
        route checklist, component metadata, dependencies, byte cost, and
        external prerequisites without injecting every instruction. Then load
        only needed exact components with detail="full" and components=[...].
        Dependencies are resolved automatically. Use detail="full" on a whole
        route only when every component is immediately necessary. Do not pass
        both route and components. Review the task-wide constraints and next
        step, then start work; defer unrelated references. Reuse inline or
        already loaded instructions instead of fetching another copy.

        Long responses return bounded pages with heading offsets and hashes.
        To read further, keep source/id/route/components/detail unchanged and
        pass section (an exact heading) or offset (a Unicode character index).
        For a package use document='root' or an exact component id from its
        plan, with detail='full'. Dependencies remain validated automatically.
        Pass the returned sha256 as expected_sha256 to detect changed content.
        next_offset continues the requested range; next_document identifies
        the next dependency-ordered document in a large full selection.
        range_complete covers only the requested document/section, not the
        whole package. Plan pages contain metadata, not execution instructions.
        Do not read every page or reference unless the task requires it.
        """
        loader = builtin_loader if source == "builtin" else workspace_loader
        normalized_route = route.strip() if isinstance(route, str) else ""
        if normalized_route and components:
            raise SkillLoadError(
                "Specify either a skill package route or components, not both"
            )
        if normalized_route or components:
            bundle = loader.load_bundle(
                id,
                route=normalized_route or None,
                components=components,
            )
            result = (
                bundle.to_tool_result()
                if detail == "full"
                else bundle.to_plan_result()
            )
        else:
            result = loader.load(id).to_tool_result()
        return _skill_result_page(
            result, document=document, section=section, offset=offset,
            expected_sha256=expected_sha256,
        )

    return view_skill


def summarize_skill_result(result: str) -> str:
    """Return safe metadata for logs/UI without exposing private instructions."""
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return "Skill load failed before structured content was returned"
    if not isinstance(payload, dict):
        return "Skill load failed before structured content was returned"
    if "end_offset" in payload:
        return (
            f"Loaded {payload.get('source', 'unknown')} skill {payload.get('id')!r} "
            f"document {payload.get('document')!r} "
            f"(characters {payload.get('offset')}:{payload.get('end_offset')}, "
            f"detail={payload.get('detail')}, sha256={payload.get('sha256')})"
        )
    if "documents" in payload:
        documents = payload.get("documents")
        if not isinstance(documents, list) or not all(
            isinstance(document, dict) and "component" in document
            for document in documents
        ):
            return "Skill package load failed before structured content was returned"
        selection = (
            f" route {payload.get('route')!r}"
            if payload.get("route")
            else ""
        )
        action = "Planned" if payload.get("detail") == "plan" else "Loaded"
        byte_label = "bytes if fully loaded" if payload.get("detail") == "plan" else "bytes"
        byte_value = payload.get("bytes_if_loaded", payload.get("bytes", "unknown"))
        return (
            f"{action} {payload.get('source', 'unknown')} skill package "
            f"{payload.get('id', 'unknown')!r}{selection} "
            f"({payload.get('component_count', len(documents))} documents, "
            f"{byte_value} {byte_label}, "
            f"sha256={payload.get('sha256', 'unknown')})"
        )
    if "content" not in payload:
        return "Skill load failed before structured content was returned"
    return (
        f"Loaded {payload.get('source', 'unknown')} skill "
        f"{payload.get('id', 'unknown')!r} "
        f"({payload.get('bytes', 'unknown')} bytes, "
        f"sha256={payload.get('sha256', 'unknown')})"
    )
