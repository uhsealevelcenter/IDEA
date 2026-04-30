import base64
import io
from typing import List, Dict, Any, Tuple

from PIL import Image


_MAX_INLINE_IMAGE_BYTES = 4_900_000  # ~4.9 MB budget for data URL payload


def _pil_format_from_ext(ext: str) -> str:
    ext = (ext or "").lower()
    return {
        "jpg": "JPEG",
        "jpeg": "JPEG",
        "png": "PNG",
        "webp": "WEBP",
        "bmp": "BMP",
        "tiff": "TIFF",
        "tif": "TIFF",
        "gif": "PNG",   # avoid animated GIF pitfalls; re-encode a static frame
    }.get(ext, "PNG")


def _mime_ext(ext: str) -> str:
    ext = (ext or "").lower()
    return {
        "jpg": "jpeg",
        "jpeg": "jpeg",
        "png": "png",
        "webp": "webp",
        "bmp": "bmp",
        "tiff": "tiff",
        "tif": "tiff",
        "gif": "png",   # since we re-encode as PNG
    }.get(ext, "png")


def _b64_size_bytes(b64_str: str) -> int:
    # Base64 inflates by ~4/3; reverse that to estimate raw size
    return (len(b64_str) * 3) // 4


def _encode_image_to_b64(path_or_bytes: Tuple[str, bytes], fmt: str) -> str:
    """
    Accept either (path, None) or (None, raw_bytes). Return base64 string
    encoded with Pillow to ensure correct format/metadata.
    """
    path, raw = path_or_bytes
    if path is not None:
        with open(path, "rb") as f:
            raw = f.read()
    img = Image.open(io.BytesIO(raw)).convert("RGBA" if fmt in ("PNG", "WEBP") else "RGB")
    buf = io.BytesIO()
    save_kwargs = {}
    if fmt == "JPEG":
        save_kwargs.update(dict(quality=85, optimize=True, progressive=True))
    img.save(buf, format=fmt, **save_kwargs)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _shrink_b64_if_needed(b64: str, fmt: str) -> str:
    """
    Iteratively resizes and re-encodes to keep the Base64 payload under the limit.
    """
    if _b64_size_bytes(b64) <= _MAX_INLINE_IMAGE_BYTES:
        return b64

    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    for _ in range(10):
        if _b64_size_bytes(b64) <= _MAX_INLINE_IMAGE_BYTES:
            break
        new_w = max(1, int(img.width * 0.85))
        new_h = max(1, int(img.height * 0.85))
        img = img.resize((new_w, new_h))

        buf = io.BytesIO()
        save_kwargs = {}
        if fmt == "JPEG":
            save_kwargs.update(dict(quality=80, optimize=True, progressive=True))
        img.save(buf, format=fmt, **save_kwargs)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return b64


def _image_to_data_url_from_path(path: str) -> str:
    ext = (path.split(".")[-1] if "." in path else "png").lower()
    pil_fmt = _pil_format_from_ext(ext)
    b64 = _encode_image_to_b64((path, None), pil_fmt)
    b64 = _shrink_b64_if_needed(b64, pil_fmt)
    return f"data:image/{_mime_ext(ext)};base64,{b64}"


def _image_to_data_url_from_b64(b64: str, ext_hint: str = "png") -> str:
    ext = (ext_hint or "png").lower()
    pil_fmt = _pil_format_from_ext(ext)
    # Re-encode through PIL so we can resize/normalize if needed
    raw = base64.b64decode(b64)
    b64_norm = _encode_image_to_b64((None, raw), pil_fmt)
    b64_norm = _shrink_b64_if_needed(b64_norm, pil_fmt)
    return f"data:image/{_mime_ext(ext)};base64,{b64_norm}"


def _parts_for_text(role: str, text: str) -> List[Dict[str, Any]]:
    t = "output_text" if role == "assistant" else "input_text"
    return [{"type": t, "text": text if isinstance(text, str) else str(text)}]


def _normalize_role(role: str) -> str:
    if role in ("tool", "function", "computer", None):
        return "user"
    if role not in ("user", "assistant", "system", "developer"):
        return "user"
    return role


def convert_to_openai_responses_messages(
    messages: List[Dict[str, Any]],
    shrink_images: bool = True,
    interpreter=None,
) -> List[Dict[str, Any]]:
    """
    Convert your LMC-style messages into OpenAI Responses input items:
      - Always returns a list of {role, content=[parts...]}
      - Uses input_text/output_text for strings
      - Emits input_image with a data URL string (never a dict payload)
      - Does NOT drop images based on any "supports_vision" heuristic
    """
    out: List[Dict[str, Any]] = []
    emitted_tool_outputs: set[str] = set()
    tool_outputs_by_call_id: Dict[str, str] = {}
    console_outputs_by_call_id: Dict[str, List[str]] = {}
    normalized_messages: List[Dict[str, Any]] = []
    active_call_id = None

    for m in messages:
        normalized = dict(m)
        role = normalized.get("role")
        mtype = normalized.get("type")

        if role in ("user", "assistant") and mtype == "message":
            active_call_id = None
        if mtype == "code" and normalized.get("call_id"):
            active_call_id = normalized["call_id"]
        elif (
            active_call_id
            and role == "computer"
            and mtype in ("console", "image", "file", "code")
            and not normalized.get("call_id")
        ):
            normalized["call_id"] = active_call_id
        if (
            role == "computer"
            and mtype == "console"
            and normalized.get("format") == "active_line"
            and normalized.get("content") is None
        ):
            active_call_id = None

        normalized_messages.append(normalized)

    for m in normalized_messages:
        call_id = m.get("call_id")
        if not call_id:
            continue

        mtype = m.get("type")
        if mtype == "console" and m.get("format") == "output":
            text = m.get("content") or ""
            if not isinstance(text, str):
                text = str(text)
            text = text if text.strip() else "No output"
            console_outputs_by_call_id.setdefault(call_id, []).append(text)
            tool_outputs_by_call_id[call_id] = "\n".join(
                console_outputs_by_call_id[call_id]
            )
        elif mtype == "image" and call_id not in tool_outputs_by_call_id:
            fmt = m.get("format", "")
            if fmt == "path":
                tool_outputs_by_call_id[call_id] = (
                    f"Output: tool produced an image at path: {m.get('content', '')}"
                )
            else:
                tool_outputs_by_call_id[call_id] = (
                    f"Output: tool produced an image ({fmt or 'unknown format'})."
                )
        elif (
            mtype == "code"
            and m.get("role") in ("computer", "tool", "function")
            and call_id not in tool_outputs_by_call_id
        ):
            lang = m.get("format", "") or "code"
            tool_outputs_by_call_id[call_id] = f"Output: tool produced {lang} output."

    def _emit_function_call_output(call_id: str):
        if call_id in emitted_tool_outputs:
            return
        out.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": tool_outputs_by_call_id.get(call_id, "Output received."),
            }
        )
        emitted_tool_outputs.add(call_id)

    for m in normalized_messages:
        # Skip messages not intended for the assistant
        if "recipient" in m and m["recipient"] != "assistant":
            continue

        mtype = m.get("type")
        role = _normalize_role(m.get("role", "user"))

        if mtype == "message":
            text = m.get("content", "")
            # Apply user_message_template to the last user message if your app relies on it
            if role == "user" and interpreter and (
                m == [x for x in messages if x.get("role") == "user"][-1]
                or getattr(interpreter, "always_apply_user_message_template", False)
            ):
                text = interpreter.user_message_template.replace("{content}", text)
            out.append({"role": role, "content": _parts_for_text(role, text)})
            continue

        if mtype == "console" and m.get("format") == "output":
            # Tool/executor output → user input
            text = m.get("content") or ""
            if not isinstance(text, str):
                text = str(text)
            if text.strip() == "":
                text = "No output"
            if m.get("call_id"):
                _emit_function_call_output(m["call_id"])
                continue
            parts = _parts_for_text("user", text)
            out.append({"role": "user", "content": parts})
            if m.get("role") in ("computer", "tool", "function"):
                out.append(
                    {
                        "role": "developer",
                        "content": _parts_for_text(
                            "developer",
                            "The previous User message contains console STDOUT/STDERR from code execution. Use it to decide whether to continue with execute(), provide an answer, explain its meaning, or otherwise communicate with the User.",
                        ),
                    }
                )
            continue

        if mtype == "code":
            # Code messages are execution requests, not assistant prose. Do not
            # replay them as assistant messages when reconstructing history.
            if m.get("call_id"):
                if m.get("role") in ("computer", "tool", "function"):
                    _emit_function_call_output(m["call_id"])
                continue
            code = m.get("content", "")
            lang = m.get("format", "") or ""
            text = f"Previously requested tool execution ({lang}):\n```{lang}\n{code}\n```"
            out.append({"role": "user", "content": _parts_for_text("user", text)})
            continue

        if mtype == "image":
            fmt = m.get("format", "")
            parts: List[Dict[str, Any]] = []

            if m.get("call_id"):
                _emit_function_call_output(m["call_id"])

            if fmt == "description":
                parts += _parts_for_text(role, m.get("content", ""))

            else:
                # Build a data URL string (Responses needs a string, not {"url":...})
                if "base64" in fmt:
                    # try to guess extension from "base64.<ext>"
                    ext_hint = fmt.split(".")[-1] if "." in fmt else "png"
                    data_url = _image_to_data_url_from_b64(m.get("content", ""), ext_hint)
                elif fmt == "path":
                    data_url = _image_to_data_url_from_path(m.get("content", ""))
                else:
                    raise Exception(f"Unrecognized image format for Responses: {fmt}")

                parts.append({"type": "input_image", "image_url": data_url})

                # (Optional) annotate tool-produced images
                if m.get("role") in ("computer", "tool", "function"):
                    parts += _parts_for_text("user", "Image output from the execute tool.")
                    if fmt == "path":
                        parts += _parts_for_text("user", f"This image is at this path: {m.get('content')}")

            out.append({"role": role, "content": parts if parts else _parts_for_text(role, "")})
            continue

        if mtype == "file":
            out.append({"role": "user", "content": _parts_for_text("user", m.get("content", ""))})
            continue

        if mtype == "error":
            # Drop internal error messages
            continue

        # Unknown type → stringify as user text
        out.append({"role": "user", "content": _parts_for_text("user", str(m))})

    return out
