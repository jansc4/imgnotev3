🇵🇱 [Polska wersja](README.pl.md)

# ImgNote v3

A local, encrypted note manager for images and documents that need to stay protected — medical scans with descriptions, blueprints, ID documents, anything sensitive you still want to tag, search, and browse like a normal gallery instead of burying in an encrypted zip.

## Why

Most of what you'd otherwise store this way ends up as an unsearchable pile of encrypted archives, or unencrypted and just hoping no one looks. This keeps every item individually encrypted, but still taggable, searchable, and viewable through a normal app.

## How it works

- Each item is saved as a single `.imgnote` file — a custom binary format (`IMNG` header) storing the image, a text note, and tags, encrypted with **AES-GCM** using a key derived from your password via **Scrypt**. Nothing is stored or transmitted unencrypted.
- Non-image documents (`.pdf`, `.docx`, `.doc`, `.odt`, `.rtf`, `.txt`, `.md`) get their text extracted on import, so the content itself becomes searchable even though the file is encrypted.
- **Search** supports boolean tag queries (`project AND important`, `(draft OR sketch) AND important`, partial tag matching) and text search (exact phrases, AND word-lists, plain substrings).
- Gallery, viewer, editor, and tag manager views, all in one app.

## Tech stack

Python backend (pywebview, PyQt6, `cryptography` for AES-GCM/Scrypt, PyMuPDF/python-docx/odfpy/striprtf for document text extraction) · HTML/CSS/vanilla JS frontend.

## Status

Personal project, actively developed (currently mid-migration from a PyQt6 GUI to this pywebview one — see `MIGRATION_PLAN.md` for the in-progress plan and design decisions).
