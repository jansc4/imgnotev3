"""
thumbnail.py — generowanie miniatur dla backendV4.

Czysta funkcja: bajty w bajty, zero zależności od NoteCache/kryptografii/
IndexManagera — testowalna w izolacji, bez tempdirów ani plików .imgnote.

Format wyjściowy = format wejściowy (PNG zostaje PNG, JPEG zostaje JPEG) —
świadomie unikamy wymuszonej konwersji do jednego formatu, która ucięłaby
przezroczystość PNG-om.

Rozmiar: dłuższy bok skalowany do MAX_DIMENSION px, proporcje zachowane,
Image.thumbnail() nigdy nie powiększa (małe obrazki zostają bez zmian).
"""
import io

from PIL import Image

MAX_DIMENSION = 320  # siatka wyświetla 140px, zapas na ekrany retina


def generate_thumbnail(image_data: bytes) -> bytes | None:
    """Zwraca zmniejszone bajty obrazka, albo None jeśli image_data jest
    puste albo nie da się go rozpoznać jako obrazu. Nigdy nie rzuca — brak
    miniatury nie powinien wywalić całej reperacji/zapisu notatki."""
    if not image_data:
        return None

    try:
        img = Image.open(io.BytesIO(image_data))
        img.load()  # wymusza pełne zdekodowanie TERAZ, w try/except, nie leniwie później
    except Exception:
        return None

    fmt = (img.format or "PNG").upper()
    img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    buf = io.BytesIO()
    try:
        # JPEG nie obsługuje kanału alfa - RGBA/LA/paleta z przezroczystością
        # trzeba spłaszczyć, inaczej img.save() rzuci OSError
        if fmt == "JPEG" and img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format=fmt)
        return buf.getvalue()
    except Exception:
        # fallback: PNG obsługuje każdy tryb, zawsze się uda
        buf = io.BytesIO()
        img.convert("RGBA").save(buf, format="PNG")
        return buf.getvalue()
