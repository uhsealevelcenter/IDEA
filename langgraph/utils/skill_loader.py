"""Authoritative, non-terminal loading for IDEA and Open WebUI skills."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import quote

import requests
import yaml
from langchain_core.tools import tool


BUILTIN_SKILLS_DIR = Path(__file__).parent / "skills"
MAX_SKILL_BYTES = int(os.getenv("MAX_SKILL_BYTES", "100000"))
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


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


def _validate_content_size(content: str, skill_id: str) -> None:
    byte_count = len(content.encode("utf-8"))
    if byte_count > MAX_SKILL_BYTES:
        raise SkillLoadError(
            f"Skill '{skill_id}' is {byte_count} bytes; the maximum supported "
            f"size is {MAX_SKILL_BYTES} bytes. The skill was not partially loaded."
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
            entries.append(
                "  <skill>\n"
                "    <source>builtin</source>\n"
                f"    <id>{html.escape(document.skill_id)}</id>\n"
                f"    <name>{html.escape(document.name)}</name>\n"
                f"    <description>{html.escape(document.description)}</description>\n"
                "  </skill>"
            )
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


def make_view_skill_tool(
    builtin_loader: BuiltinSkillLoader,
    workspace_loader: OpenWebUISkillLoader,
):
    """Create one source-aware tool that always returns complete skill text."""

    @tool
    def view_skill(
        source: Literal["builtin", "workspace"],
        id: str,
    ) -> str:
        """
        Load the complete instructions for an available skill.

        Use source="builtin" for entries in <available_builtin_skills> and
        source="workspace" for entries in Open WebUI's <available_skills>.
        Pass the exact manifest id. The result includes the complete Markdown
        content and is never silently truncated.
        """
        loader = builtin_loader if source == "builtin" else workspace_loader
        return loader.load(id).to_tool_result()

    return view_skill


def summarize_skill_result(result: str) -> str:
    """Return safe metadata for logs/UI without exposing private instructions."""
    try:
        payload = json.loads(result)
    except (TypeError, ValueError):
        return "Skill load failed before structured content was returned"
    if not isinstance(payload, dict) or "content" not in payload:
        return "Skill load failed before structured content was returned"
    return (
        f"Loaded {payload.get('source', 'unknown')} skill "
        f"{payload.get('id', 'unknown')!r} "
        f"({payload.get('bytes', 'unknown')} bytes, "
        f"sha256={payload.get('sha256', 'unknown')})"
    )
