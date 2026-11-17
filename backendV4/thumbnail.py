"""
thumbnail.py — generowanie miniatur dla backendV4.

Czysta funkcja: bajty w bajty, zero zależności od NoteCache/kryptografii/
IndexManagera — testowalna w izolacji.

Format v4 (FORMAT_V4.md): miniatura zawsze WebP q80 — mniejsza niż PNG,
zachowuje przezroczystość (RGBA), więc nie trzeba trzymać formatu wejściowego.
Zapisywana w zaszyfrowanej sekcji pliku .imgnote, nie na dysku otwartym tekstem.

Rozmiar: dłuższy bok skalowany do MAX_DIMENSION px, proporcje zachowane,
Image.thumbnail() nigdy nie powiększa (małe obrazki zostają bez zmian).
"""
import io

from PIL import Image, ImageOps

MAX_DIMENSION = 320  # siatka wyświetla do 320px (suwak w Ustawieniach)
WEBP_QUALITY = 80
# S8: bomba dekompresyjna -> brak miniatury zamiast GB RAM (100 MPx starcza na aparaty)
MAX_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def generate_thumbnail(image_data: bytes) -> bytes | None:
    """Zwraca miniaturę WebP, albo None jeśli image_data jest puste albo nie da
    się go rozpoznać jako obrazu. Nigdy nie rzuca — brak miniatury nie może
    wywalić zapisu notatki ani migracji."""
    if not image_data:
        return None

    try:
        img = Image.open(io.BytesIO(image_data))  # czyta tylko nagłówek
        # sprawdzenie przed dekodowaniem; bez warnings.catch_warnings (nie jest thread-safe)
        if img.width * img.height > MAX_PIXELS:
            return None
        if img.format == "JPEG":
            # dekodowanie JPEG od razu w zmniejszonej skali (DCT) — wielokrotnie szybciej
            img.draft(img.mode, (MAX_DIMENSION * 2, MAX_DIMENSION * 2))
        img.load()  # wymusza pełne zdekodowanie TERAZ, w try/except, nie leniwie później
        img = ImageOps.exif_transpose(img)  # zdjęcia z telefonu: orientacja z EXIF
        has_alpha = img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION))
        buf = io.BytesIO()
        img.save(buf, format="WEBP", quality=WEBP_QUALITY, method=4)
        return buf.getvalue()
    except Exception:
        return None
