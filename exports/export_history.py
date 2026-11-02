#!/usr/bin/env python3
"""Export one IDEA account using Python's standard library and Docker/psql."""
import argparse
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
import zipfile


STYLE = """<meta charset="utf-8"><meta name="viewport" content="width=device-width">
<style>body{max-width:1000px;margin:2em auto;padding:0 1em;font:16px system-ui}
article{border-top:1px solid #bbb;padding:1em 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}
img{max-width:100%}small{color:#555}</style>"""


def escaped(value):
    return html.escape(str(value or ""), quote=True)


def export_records(records, root):
    """Consume a snapshot stream without loading the account's history into memory."""
    conversations = {}
    user = None
    count = images = content_bytes = 0
    current = None
    json_file = html_file = None
    first = True
    completed = set()
    warnings = set()
    (root / "conversations").mkdir()
    (root / "images").mkdir()

    def close_conversation():
        nonlocal json_file, html_file
        if json_file:
            json_file.write("]}\n")
            html_file.write("</body></html>\n")
            json_file.close()
            html_file.close()
            completed.add(current)
            json_file = html_file = None

    def open_conversation(cid):
        nonlocal current, json_file, html_file, first
        close_conversation()
        current = cid
        first = True
        conv = conversations[cid]
        json_file = (root / "conversations" / f"{cid}.json").open("w", encoding="utf-8")
        json_file.write('{"conversation":' + json.dumps(conv, ensure_ascii=False) + ',"messages":[')
        html_file = (root / "conversations" / f"{cid}.html").open("w", encoding="utf-8")
        html_file.write(f'<!doctype html><html><head>{STYLE}<title>{escaped(conv["title"])}</title></head><body>')
        html_file.write(f'<a href="../index.html">All conversations</a><h1>{escaped(conv["title"] or "Untitled")}</h1>')
        html_file.write(f'<small>Created: {escaped(conv["created_at"])} UTC</small>')

    try:
        for record in records:
            kind, data = record["kind"], record["data"]
            if kind == "user":
                if user is not None:
                    raise ValueError("Multiple accounts match this email; refusing an ambiguous export")
                user = data
            elif kind == "conversation":
                conversations[data["id"]] = data
            elif kind == "message":
                cid = data["conversation_id"]
                if cid != current:
                    if cid in completed:
                        raise ValueError("Messages must be grouped by conversation")
                    open_conversation(cid)
                json_file.write(("" if first else ",") + json.dumps(data, ensure_ascii=False))
                first = False
                count += 1
                if count % 1000 == 0:
                    print(f"Saved {count:,} messages...", file=sys.stderr, flush=True)
                content = data["content"]
                content_bytes += len(content.encode("utf-8"))
                kind = data["message_type"].lower()
                fmt = (data.get("message_format") or "").lower()
                fmt = {"base64_png": "base64.png", "base64_jpeg": "base64.jpeg"}.get(fmt, fmt)
                html_file.write(f'<article><strong>{escaped(data["role"].lower())}</strong> <small>{escaped(data["created_at"])} UTC · {escaped(kind)} · {escaped(fmt)}</small>')
                if kind == "image" and fmt in ("base64.png", "base64.jpeg"):
                    try:
                        image_data = base64.b64decode(content, validate=True)
                        suffix = "png" if fmt == "base64.png" else "jpg"
                        filename = f'{data["id"]}.{suffix}'
                        (root / "images" / filename).write_bytes(image_data)
                        html_file.write(f'<p><img src="../images/{filename}" alt="Conversation image"></p>')
                        images += 1
                    except ValueError:
                        warnings.add("Some image data could not be decoded; original content remains in JSON.")
                        html_file.write("<p>Image could not be decoded; see JSON.</p>")
                else:
                    html_file.write(f'<pre>{escaped(content)}</pre>')
                    if kind in ("file", "image"):
                        warnings.add("Referenced files or non-base64 images are preserved as text; their external contents are not bundled.")
                html_file.write("</article>")
        close_conversation()
        if user is None:
            raise ValueError("No account matches that email")
        for cid in conversations:
            if cid not in completed:
                open_conversation(cid)
                close_conversation()
    finally:
        if json_file:
            json_file.close()
        if html_file:
            html_file.close()

    manifest = {
        "format_version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": user,
        "conversations": len(conversations), "messages": count,
        "message_content_bytes": content_bytes, "extracted_images": images,
        "warnings": sorted(warnings),
        "notes": ["Database timestamps are UTC.",
                  "HTML displays original text, Markdown, equations and code literally; it executes no message HTML or scripts.",
                  "JSON preserves all saved message fields, including original base64 images.",
                  "Deleted or unsaved history and separately stored uploads are not included."]}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with (root / "index.html").open("w", encoding="utf-8") as index:
        index.write(f'<!doctype html><html><head>{STYLE}<title>IDEA history</title></head><body><h1>IDEA conversation history</h1>')
        index.write(f'<p>{escaped(user["full_name"])} · {escaped(user["email"])} · {len(conversations)} conversations · {count} messages</p>')
        index.write('<p>Extract the ZIP before opening this index. Text and code are displayed literally. See manifest.json for export details and limitations.</p><ul>')
        for conv in sorted(conversations.values(), key=lambda c: (c["created_at"], c["id"])):
            cid = conv["id"]
            index.write(f'<li>{escaped(conv["created_at"])} UTC — <a href="conversations/{cid}.html">{escaped(conv["title"] or "Untitled")}</a> (<a href="conversations/{cid}.json">JSON</a>)</li>')
        index.write("</ul></body></html>\n")
    return manifest


def snapshot_sql(email):
    # Hex encodes input so that neither shell nor SQL syntax can be injected.
    email_hex = email.encode("utf-8").hex()
    predicate = f"lower(u.email)=lower(convert_from(decode('{email_hex}','hex'),'UTF8'))"
    return f"""BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL timezone = 'UTC';
SELECT json_build_object('kind','user','data',json_build_object('id',u.id,'email',u.email,'full_name',u.full_name,'created_at',u.created_at)) FROM \"user\" u WHERE {predicate};
SELECT json_build_object('kind','conversation','data',json_build_object('id',c.id,'user_id',c.user_id,'title',c.title,'is_shared',c.is_shared,'is_favorite',c.is_favorite,'created_at',c.created_at,'updated_at',c.updated_at)) FROM conversation c JOIN \"user\" u ON u.id=c.user_id WHERE {predicate} ORDER BY c.id;
SELECT json_build_object('kind','message','data',row_to_json(m)) FROM message m JOIN conversation c ON c.id=m.conversation_id JOIN \"user\" u ON u.id=c.user_id WHERE {predicate} ORDER BY m.conversation_id,m.created_at,m.id;
COMMIT;
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email", help="Exact account email (case insensitive)")
    parser.add_argument("--db-container", default="idea-db-1")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "archives")
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    name = f"idea-history-{hashlib.sha256(args.email.lower().encode()).hexdigest()[:12]}-{tag}"
    archive = args.output_dir / f"{name}.zip"
    partial = archive.with_suffix(".zip.partial")
    command = ["docker", "exec", "-i", args.db_container, "sh", "-c",
               'exec psql -X -q -A -t -v ON_ERROR_STOP=1 -v FETCH_COUNT=100 -U "$POSTGRES_USER" -d "$POSTGRES_DB"']
    try:
        with tempfile.TemporaryDirectory(prefix=".export-", dir=args.output_dir) as temp, tempfile.TemporaryFile(mode="w+t") as errors:
            root = Path(temp) / name
            root.mkdir()
            print("Reading a consistent database snapshot (large histories may take several minutes)...", file=sys.stderr, flush=True)
            with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8") as process:
                try:
                    process.stdin.write(snapshot_sql(args.email))
                    process.stdin.close()
                    def records():
                        for line in process.stdout:
                            yield json.loads(line)
                    manifest = export_records(records(), root)
                    if process.wait() != 0:
                        errors.seek(0)
                        raise RuntimeError(errors.read().strip())
                except BaseException:
                    process.terminate()
                    process.wait()
                    errors.seek(0)
                    detail = errors.read().strip()
                    if detail:
                        print(detail, file=sys.stderr)
                    raise
            print("Building and checking ZIP archive...", file=sys.stderr, flush=True)
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        bundle.write(path, path.relative_to(root.parent))
            with zipfile.ZipFile(partial) as bundle:
                bad = bundle.testzip()
                if bad:
                    raise RuntimeError(f"ZIP integrity check failed: {bad}")
            partial.rename(archive)
        print(f'Exported {manifest["conversations"]} conversations, {manifest["messages"]} messages, {manifest["extracted_images"]} images.')
        print(f"Archive: {archive.resolve()} ({archive.stat().st_size:,} bytes)")
        for warning in manifest["warnings"]:
            print(f"Warning: {warning}")
    finally:
        partial.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
