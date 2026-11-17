🇵🇱 [Polska wersja](README.pl.md)

# ImgNote v3

A local, encrypted note manager for images and documents that need to stay protected — medical scans with descriptions, blueprints, ID documents, anything sensitive you still want to tag, search, and browse like a normal gallery instead of burying in an encrypted zip.

## Why

Most of what you'd otherwise store this way ends up as an unsearchable pile of encrypted archives, or unencrypted and just hoping no one looks. This keeps every item individually encrypted, but still taggable, searchable, and viewable through a normal app.

## Screenshots

| Login | Gallery |
|---|---|
| ![Password screen](docs/screenshot-login.png) | ![Main gallery view](docs/screenshot-main.png) |

## How it works

- Each item is saved as a single `.imgnote` file in a custom binary format (v4, spec in [FORMAT_V4.md](FORMAT_V4.md)). It has three separately encrypted sections: **metadata** (title, folder, tags, text), **thumbnail** (WebP) and the **image** itself, stored as raw bytes. Filenames on disk are random. Titles and folder structure live only inside the encrypted metadata and never appear on the filesystem.
- **Keys:** your password goes through **Argon2id** (256 MiB, 3 passes) **once per session**. Each file then gets its own key via **HKDF-SHA256**, which takes microseconds. Every section is sealed with **AES-256-GCM**, and the file header is authenticated as associated data, so a modified header, swapped sections or a section pasted from another file are all rejected on read. KDF parameters stored in a file are bounds-checked before use, so a planted file can't make the app allocate gigabytes.
- **Partial reads** are what keep it fast. The gallery decrypts only thumbnails, while the index and search decrypt only metadata. Changing a tag, folder or text re-encrypts just the metadata and copies the encrypted image byte for byte. On 100 notes with ~2.8 MB photos, rebuilding the index went from 4.6 s to 0.01 s, cold thumbnails from 9.1 s to 0.01 s, and retagging every note from 9.9 s to 0.14 s.
- A separate encrypted index (same master key) tracks tags and folder paths for fast lookups. On startup only files changed since the last session are re-read, and only their metadata. A damaged index is simply rebuilt from the notes. A single damaged note doesn't block login either: it is skipped and reported.
- Non-image documents (`.pdf`, `.docx`, `.doc`, `.odt`, `.rtf`, `.txt`, `.md`) get their text extracted on import, so the content itself becomes searchable even though the file is encrypted.
- **Search** has two modes:
  - boolean tag queries (`project AND important`, `(draft OR sketch) AND important`, partial tag matching, with autocomplete), which run against the index;
  - text search (exact phrases, AND word-lists, plain substrings), which decrypts only the metadata of candidate notes.
- **Folders are virtual**: they exist only as an encrypted field on each note, not as real directories on disk. Create, rename, move and delete them from a right-click menu on the folder tree. Renaming or moving cascades to every note underneath.
- Every secondary view (viewer, editor, tag manager, settings, confirmations) opens as a layered popup over the gallery instead of a separate screen, so context is never lost. Unsaved edits ask before closing. Escape always closes only the topmost layer.
- The viewer supports zoom/pan on the image, keyboard navigation between notes, and inline Markdown editing without leaving the popup.
- The tag manager can rename, merge or delete a tag across every note that uses it, with a live usage count and progress feedback for the bulk rewrite.
- The gallery is keyboard-friendly: Tab to a card, Enter to open it, Ctrl/Shift for multi-select, arrows in context menus.
- A settings popup controls thumbnail size, note font size and the auto-lock timeout (persisted locally).

## Security

- **Session lock:** a 🔒 button, plus auto-lock after inactivity (10 min by default; configurable or off in Settings). Locking drops the password, the master keys and all decrypted data cached in memory.
- **No unencrypted mode:** every note and the index are always encrypted.
- **Markdown is sanitized** server-side (`nh3`, allow-listed tags, links limited to `http`/`https`/`mailto`, no images). A Content-Security-Policy additionally blocks inline scripts and any remote resources in the UI.
- **The JS bridge accepts only note paths inside the open vault.** Imported files must have an allowed extension and stay within size limits. Exports go only where you picked in the dialog, and downloads from the web are capped and limited to `http`/`https`.
- **Atomic writes:** temp file + `fsync` + rename, so a crash or a full disk can't truncate a note.
- **Core dumps are disabled** for the app process, since they would contain decrypted images.

## Migrating from the v3 file format

Vaults created before the v4 format must be migrated once. The script works on a **copy**: the source folder is only read, never modified.

```
uv run python test/migrate_v3_to_v4.py --source /path/to/vault --dest /path/to/vault_v4
```

- It asks for the password interactively, so the password never lands in your shell history.
- It checks free space and the password before writing anything.
- Every note keeps its filename and modification time. Each one is read back after writing and compared with the original (title, folder, text, tags, SHA-256 of the image).
- At the end it prints a list of anything it couldn't migrate.
- Open the new folder in the app and check it before swapping the folders. Keep the old one as a backup until you're sure.
- Opening an unmigrated vault in the app shows a clear "v3 format — run the migration" error, not an empty gallery.

## Tests

```
bash test/run_all.sh                                          # unit + API tests
QT_QPA_PLATFORM=offscreen uv run python test/e2e_offscreen.py  # real app, headless
```

The suites cover:
- the v4 format, including tampering tests (header, every section, swapped or spliced sections, truncated files, hostile KDF parameters);
- the index, cache, search and API;
- the migration;
- an end-to-end run of the real pywebview app in headless Qt.

## Tech stack

Python backend:
- pywebview, PyQt6;
- `cryptography` for AES-256-GCM, Argon2id and HKDF;
- `nh3` for sanitizing rendered Markdown;
- `Pillow` for WebP thumbnails;
- `cachetools` for bounded LRU caches (metadata, thumbnails, images with a byte budget);
- PyMuPDF / python-docx / odfpy / striprtf for document text extraction.

Frontend in HTML/CSS/vanilla JS.

## Status

Personal project, actively developed.

1. Started as a PyQt6 GUI, then migrated to this pywebview-based app.
2. Rebuilt from a single ~700-line God object into a modular backend (encrypted index instead of a linear scan, bounded LRU caches, dedicated folder/search/index managers) and a redesigned, popup-based frontend.
3. A security and performance audit (November 2026) brought in the v4 file format, session lock, sanitized rendering, path validation and atomic writes.
