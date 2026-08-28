"""
doc_extractor.py — ekstrakcja tekstu z popularnych formatów do plain text/Markdown.

Port 1:1 z v2 (imgnotev2/doc_extractor.py) — brak zależności od Qt.
Obsługuje: .txt .pdf .docx .doc .odt .rtf .md
"""
from pathlib import Path

SUPPORTED = {".txt", ".pdf", ".docx", ".doc", ".odt", ".rtf", ".md"}


def extract(path: str) -> tuple[str, str]:
    """
    Zwraca (tekst, info) gdzie info to komunikat o metodzie/błędzie.
    Tekst jest zawsze stringiem (może być pusty przy błędzie).
    """
    p = Path(path)
    ext = p.suffix.lower()
    try:
        if ext == ".md":
            return _txt(p), "Markdown"
        if ext == ".txt":
            return _txt(p), "plain text"
        if ext == ".pdf":
            return _pdf(p), "PDF (PyMuPDF)"
        if ext == ".docx":
            return _docx(p), "DOCX"
        if ext in (".doc",):
            return _doc(p), "DOC"
        if ext == ".odt":
            return _odt(p), "ODT"
        if ext == ".rtf":
            return _rtf(p), "RTF"
        return "", f"Nieobsługiwany format: {ext}"
    except Exception as e:
        return "", f"Błąd ekstrakcji: {e}"


def _txt(p: Path) -> str:
    for enc in ("utf-8", "cp1250", "latin-1"):
        try:
            return p.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return p.read_bytes().decode("utf-8", errors="replace")


def _pdf(p: Path) -> str:
    import fitz
    doc = fitz.open(str(p))
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            pages.append(f"## Strona {i + 1}\n\n{text.strip()}")
    return "\n\n".join(pages)


def _docx(p: Path) -> str:
    from docx import Document
    doc = Document(str(p))
    parts = []
    for para in doc.paragraphs:
        if para.style.name.startswith("Heading"):
            level = para.style.name.split()[-1]
            try:
                level = min(int(level), 6)
            except ValueError:
                level = 2
            parts.append(f"{'#' * level} {para.text}")
        elif para.text.strip():
            parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(cells))
    return "\n\n".join(parts)


def _doc(p: Path) -> str:
    import subprocess, tempfile
    try:
        r = subprocess.run(["antiword", str(p)], capture_output=True, timeout=15)
        if r.returncode == 0:
            return r.stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run(
                ["soffice", "--headless", "--convert-to", "txt:Text",
                 "--outdir", tmp, str(p)],
                capture_output=True, timeout=30
            )
            txts = list(Path(tmp).glob("*.txt"))
            if txts:
                return _txt(txts[0])
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    raise RuntimeError("Potrzebny antiword lub LibreOffice")


def _odt(p: Path) -> str:
    from odf.opendocument import load
    doc = load(str(p))
    def collect(node) -> str:
        if isinstance(node, str):
            return node
        result = ""
        if hasattr(node, "childNodes"):
            for child in node.childNodes:
                result += collect(child)
        return result
    return collect(doc.body)


def _rtf(p: Path) -> str:
    from striprtf.striprtf import rtf_to_text
    raw = p.read_bytes().decode("latin-1", errors="replace")
    return rtf_to_text(raw)
