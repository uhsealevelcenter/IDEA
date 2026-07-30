"""
Knowledge Base Tool
Queries a user's uploaded papers ("Knowledge" library) via PaperQA2
(multi-tenant), returning a formatted answer with citations and any
extracted figure/image paths.
"""

import asyncio
import base64
import hashlib
import json
import os
import re
import time as _time
from pathlib import Path
from typing import Any, Callable, Optional

import nest_asyncio
from langchain_core.tools import tool

from ..pqa.pqa_multi_tenant import (
    compute_index_revision,
    get_user_settings,
    load_docs_from_disk,
    save_docs_to_disk,
)

nest_asyncio.apply()

# In-memory Docs cache keyed by trusted library scope.
_docs_cache: dict = {}

PQA_MEDIA_ROOT = Path(
    os.getenv("PQA_MEDIA_ROOT", "/app/data/.pqa/media")
)

_VISUAL_MEDIA_PATTERN = re.compile(
    r"\b(?:figures?|tables?|diagrams?|images?|plots?|charts?|maps?|"
    r"photographs?|illustrations?|panels?)\b|\bfigs?\.",
    re.IGNORECASE,
)
_DIRECT_ATTACHMENT_PATTERN = re.compile(
    r"\b(?:attached|attachment|attachments|uploaded|upload)\b|"
    r"\bthis\s+(?:pdf|paper|document|file)\b|"
    r"\bthe\s+(?:attached|uploaded)\s+(?:pdf|paper|document|file)\b",
    re.IGNORECASE,
)
_MIXED_SOURCE_PATTERN = re.compile(
    r"\b(?:compare|comparison|contrast|both|versus)\b|\bvs\.",
    re.IGNORECASE,
)


def _select_knowledge_scope(
    query: str,
    combined_scope_id: str | None,
    direct_scope_id: str | None,
    direct_file_names: tuple[str, ...],
) -> str | None:
    """Route attachment-specific queries away from collection documents."""
    if not direct_scope_id or _MIXED_SOURCE_PATTERN.search(query):
        return combined_scope_id

    normalized_query = query.casefold()
    for filename in direct_file_names:
        normalized_name = filename.casefold().strip()
        if not normalized_name:
            continue
        if (
            normalized_name in normalized_query
            or Path(normalized_name).stem in normalized_query
        ):
            return direct_scope_id

    if _DIRECT_ATTACHMENT_PATTERN.search(query):
        return direct_scope_id
    return combined_scope_id


def _selected_media_context_ids(query: str, session: Any) -> set[str]:
    """Return cited context IDs when the question or answer concerns media."""
    answer_parts = [
        getattr(session, attribute, "")
        for attribute in ("raw_answer", "answer", "formatted_answer")
    ]
    media_text = " ".join(
        part for part in (query, *answer_parts) if isinstance(part, str)
    )
    if not _VISUAL_MEDIA_PATTERN.search(media_text):
        return set()

    # PaperQA computes used_contexts from context IDs cited in raw_answer.
    # An empty set is not evidence that every retrieved context was used.
    return set(getattr(session, "used_contexts", set()) or ())


def _save_base64_image(data_url: str, output_dir: Path, prefix: str = "kb_figure") -> Optional[Path]:
    """Save a base64 data URL to an image file. Returns the saved path or None."""
    try:
        if not data_url or not data_url.startswith("data:image"):
            return None

        header, b64_data = data_url.split(",", 1)
        mime_type = header.split(":")[1].split(";")[0]

        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/gif": ".gif",
            "image/webp": ".webp",
        }
        ext = ext_map.get(mime_type, ".png")

        content_hash = hashlib.md5(b64_data.encode()).hexdigest()[:12]
        filename = f"{prefix}_{content_hash}{ext}"
        filepath = output_dir / filename

        if filepath.exists():
            return filepath

        image_data = base64.b64decode(b64_data)
        output_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_bytes(image_data)
        return filepath
    except Exception as e:
        print(f"[PQA] Warning: Failed to save image: {e}")
        return None


async def _query_knowledge_base_async(
    query: str,
    scope_id: str,
    session_id: Optional[str],
    end_user_id: str,
    publish_media: Callable[[Path], str] | None = None,
) -> dict:
    global _docs_cache
    from paperqa import Docs
    from paperqa.agents.search import get_directory_index

    t_start = _time.perf_counter()

    print("[PQA] Step 1: Loading user settings...")
    settings = get_user_settings(scope_id, end_user_id=end_user_id)
    print(f"[PQA] Settings loaded. LLM: {settings.llm}, Embedding: {settings.embedding}")

    print("[PQA] Step 2: Building/loading index...")
    t_idx = _time.perf_counter()
    index = await get_directory_index(settings=settings)
    print(f"[PQA] Index loaded in {_time.perf_counter() - t_idx:.2f}s.")

    index_files = await index.index_files
    if not index_files:
        return {"answer": "No papers found in your Knowledge base. Please upload papers first.", "images": []}
    print(f"[PQA] Found {len(index_files)} indexed files.")

    paper_directory = settings.agent.index.paper_directory
    revision = compute_index_revision(index_files, paper_directory)
    cache_key = str(scope_id)
    cached = _docs_cache.get(cache_key)

    if cached and cached["revision"] == revision:
        docs = cached["docs"]
        print("[PQA] Step 3: Reusing cached Docs object (in-memory cache hit).")
    else:
        disk_docs = load_docs_from_disk(scope_id, revision)

        if disk_docs is not None:
            docs = disk_docs
            _docs_cache[cache_key] = {"docs": docs, "revision": revision}
            print("[PQA] Step 3: Loaded Docs from disk cache.")
        else:
            print("[PQA] Step 3: Building Docs object (no cache available)...")
            t_docs = _time.perf_counter()
            docs = Docs()
            for file_path in index_files.keys():
                full_path = paper_directory / file_path
                if full_path.exists():
                    print(f"[PQA]   Adding: {file_path}")
                    await docs.aadd(full_path, settings=settings)

            _docs_cache[cache_key] = {"docs": docs, "revision": revision}
            save_docs_to_disk(scope_id, docs, revision)
            print(f"[PQA] Docs built and cached in {_time.perf_counter() - t_docs:.2f}s.")

    print(f"[PQA] Step 4: Querying with: '{query}'...")
    t_query = _time.perf_counter()
    session = await docs.aquery(query=query, settings=settings)
    print(f"[PQA] Query complete in {_time.perf_counter() - t_query:.2f}s.")

    print("[PQA] Step 5: Selecting cited images from contexts...")
    static_dir = PQA_MEDIA_ROOT
    if session_id:
        session_key = hashlib.sha256(
            session_id.encode("utf-8")
        ).hexdigest()
        output_dir = static_dir / scope_id / session_key
    else:
        output_dir = static_dir / scope_id

    saved_images = []
    seen_hashes: set = set()
    selected_context_ids = _selected_media_context_ids(query, session)

    for context in session.contexts:
        if context.id not in selected_context_ids:
            continue

        if not hasattr(context, "text") or not hasattr(context.text, "media"):
            continue

        for media in context.text.media:
            try:
                data_url = media.to_image_url()
                if not data_url:
                    continue

                if "," in data_url:
                    b64_part = data_url.split(",", 1)[1]
                    content_hash = hashlib.md5(b64_part.encode()).hexdigest()
                    if content_hash in seen_hashes:
                        continue
                    seen_hashes.add(content_hash)

                saved_path = _save_base64_image(data_url, output_dir)
                if saved_path:
                    info = getattr(media, "info", {}) or {}
                    page_num = info.get("page_num", info.get("page"))
                    media_type = info.get("type", "image")
                    description = info.get("enriched_description", "")

                    published_path = (
                        publish_media(saved_path)
                        if publish_media is not None
                        else str(saved_path)
                    )

                    saved_images.append({
                        "path": published_path,
                        "page": page_num,
                        "type": media_type,
                        "description": description,
                        "used_in_answer": True,
                    })
                    print(f"[PQA] Saved: {saved_path.name} (page {page_num})")
            except Exception as e:
                print(f"[PQA] Warning: Failed to process media: {e}")
                continue

    print(f"[PQA] Extracted {len(saved_images)} unique images.")
    print(f"[PQA] Total query_knowledge_base time: {_time.perf_counter() - t_start:.2f}s")

    return {
        "answer": str(session),
        "images": saved_images,
    }


def make_query_knowledge_base_tool(
    scope_getter: Callable[[], str | None],
    *,
    session_id: str,
    end_user_id: str,
    publish_media: Callable[[Path], str] | None = None,
    direct_scope_getter: Callable[[], str | None] | None = None,
    direct_file_names_getter: (
        Callable[[], tuple[str, ...]] | None
    ) = None,
):
    """Create a PaperQA tool bound to trusted server-side identity."""

    @tool("query_knowledge_base")
    def query_knowledge_base(query: str) -> str:
        """Query the attached scientific literature using PaperQA2.

        Use this for literature review, methods, findings, citations, or
        questions about figures and tables in the Assistant's attached
        Knowledge collection or PDFs attached in this chat. Make the query
        specific enough to retrieve strong primary-source evidence.

        Args:
            query: The research question to ask about the attached papers.
        """
        scope_id = _select_knowledge_scope(
            query,
            scope_getter(),
            direct_scope_getter() if direct_scope_getter else None,
            (
                direct_file_names_getter()
                if direct_file_names_getter
                else ()
            ),
        )
        if not scope_id:
            return json.dumps({
                "answer": (
                    "The PaperQA library could not be prepared for this "
                    "request."
                ),
                "images": [],
            })
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            _query_knowledge_base_async(
                query,
                scope_id,
                session_id or None,
                end_user_id,
                publish_media,
            )
        )
        return json.dumps(result, indent=2, default=str)

    return query_knowledge_base
