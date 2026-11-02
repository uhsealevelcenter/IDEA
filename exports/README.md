# One-time conversation exports

Run from the existing IDEA host with Python 3.10+ and permission to use Docker:

```bash
cd /path/to/IDEA
python3 exports/export_history.py user@example.com
```

The script matches an exact email address, case insensitively. It reads PostgreSQL
through `docker exec` and `psql` using the database container's existing credentials;
no Python packages, account password, or running web application are required.
It uses a consistent, read-only snapshot and excludes password hashes and sharing
tokens. It does not change the database or transfer any files.

Options:

```bash
python3 exports/export_history.py user@example.com --db-container idea-db-1 --output-dir /path/to/private/exports
```

Completed ZIP files are saved in `exports/archives/` by default. That directory is
ignored by Git. New files and directories are private to the operator. Temporary
files are removed on normal completion or failure; partial ZIPs are not published.
Allow several GB of free space for large histories: original base64 image content
is retained in JSON, while decoded images are also included for HTML viewing.

Each archive contains:

- `index.html`: conversation list; extract the ZIP before opening it.
- `conversations/<id>.html`: offline transcripts with text, code and console output.
- `conversations/<id>.json`: conversation metadata and every saved message field.
- `images/`: decoded embedded PNG/JPEG images.
- `manifest.json`: account identity, record counts, content size, warnings and limitations.

HTML renders original text literally, including Markdown and equation notation.
Message HTML/scripts are escaped, not executed. No internet connection is required.
Non-base64 images, separately stored uploads and linked files are not bundled; their
references remain in the messages and a warning is included for file/image records.
Deleted and unsaved history cannot be exported from the current database.

The script checks the ZIP's CRC integrity before reporting success. The counts in
the manifest describe the same database snapshot as the exported records. It fails
if the account is missing or multiple accounts match. Do not run during active
schema migrations.

The operator handles transfer and sharing, for example:

```bash
scp exports/archives/idea-history-<account-hash>-<timestamp>.zip operator@destination:/private/path/
```

Verify the intended recipient and share privately. ZIP files are not encrypted.
Remove local archives when no longer needed. A hard kill or machine failure may
leave `.export-*` temporary directories or `.zip.partial` files in the output
directory; remove those after confirming no export is running.

Run the small offline checks with:

```bash
python3 -m unittest discover -s exports -p 'test_*.py'
```
