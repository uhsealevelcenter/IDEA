"""Pure helpers for per-turn IDEA output synchronization."""

import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup


HTML_OUTPUT_EXTENSIONS = {".htm", ".html"}
HTML_RESOURCE_ATTRIBUTES = {
    "a": ("href",),
    "audio": ("src",),
    "embed": ("src",),
    "iframe": ("src",),
    "img": ("src", "srcset"),
    "input": ("src",),
    "link": ("href",),
    "object": ("data",),
    "script": ("src",),
    "source": ("src", "srcset"),
    "track": ("src",),
    "video": ("src", "poster"),
}
CSS_URL_RE = re.compile(
    r"url\(\s*(?P<quote>['\"]?)(?P<url>.*?)(?P=quote)\s*\)",
    re.IGNORECASE,
)


def parse_file_metadata_output(output: str) -> dict[str, str]:
    """Parse `find -printf` rows into path -> size/mtime signatures."""
    snapshot: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        path, size, modified_at = parts
        snapshot[path] = f"{size}:{modified_at}"
    return snapshot


def changed_output_paths(
    before: dict[str, str],
    after: dict[str, str],
) -> list[str]:
    """Return sorted files that are new or have a changed signature."""
    return sorted(
        filepath
        for filepath, signature in after.items()
        if before.get(filepath) != signature
    )


def is_html_output(filepath: str) -> bool:
    return PurePosixPath(filepath).suffix.lower() in HTML_OUTPUT_EXTENSIONS


def resolve_local_html_reference(
    reference: str,
    html_path: str,
) -> tuple[str, str, str] | None:
    """
    Resolve a local HTML URL relative to the output document.

    Returns ``(path, query, fragment)`` for any local file/server reference.
    External/data/blob URLs and document anchors return None.
    """
    value = reference.strip()
    if not value or value.startswith("#"):
        return None

    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None

    decoded_path = unquote(parsed.path)
    if decoded_path.startswith("/"):
        resolved = posixpath.normpath(decoded_path)
    else:
        resolved = posixpath.normpath(
            posixpath.join(posixpath.dirname(html_path), decoded_path)
        )

    return resolved, parsed.query, parsed.fragment


def _srcset_references(srcset: str) -> list[str]:
    """Return URL tokens from an ordinary comma-separated srcset."""
    if srcset.lstrip().lower().startswith("data:"):
        return []
    references: list[str] = []
    for candidate in srcset.split(","):
        candidate = candidate.strip()
        if candidate:
            references.append(candidate.split(None, 1)[0])
    return references


def discover_html_output_references(
    html: bytes,
    html_path: str,
) -> set[str]:
    """Return normalized /outputs paths referenced by an HTML document."""
    soup = BeautifulSoup(html, "html.parser")
    references: set[str] = set()
    for tag_name, attributes in HTML_RESOURCE_ATTRIBUTES.items():
        for tag in soup.find_all(tag_name):
            for attribute in attributes:
                value = tag.get(attribute)
                if not isinstance(value, str):
                    continue
                values = (
                    _srcset_references(value)
                    if attribute == "srcset"
                    else [value]
                )
                for reference in values:
                    resolved = resolve_local_html_reference(reference, html_path)
                    if resolved:
                        references.add(resolved[0])

    css_fragments = [
        tag.get("style")
        for tag in soup.find_all(style=True)
        if isinstance(tag.get("style"), str)
    ]
    css_fragments.extend(
        tag.get_text()
        for tag in soup.find_all("style")
    )
    for css in css_fragments:
        for match in CSS_URL_RE.finditer(css):
            resolved = resolve_local_html_reference(
                match.group("url"),
                html_path,
            )
            if resolved:
                references.add(resolved[0])
    return references
