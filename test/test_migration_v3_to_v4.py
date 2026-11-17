"""
test_migration_v3_to_v4.py — migracja na syntetycznym vaulcie v3: dane 1:1,
źródło nietknięte (SHA-256 każdego pliku), mtime zachowany, raport błędów,
odmowa przy złym haśle / niepustym celu, vault v4 otwiera się w Api.

Uruchomienie:  uv run python test/test_migration_v3_to_v4.py
"""
import hashlib
import io
import json
import os
import struct
import tempfile
from pathlib import Path

from _v4 import FAST, PASSWORD
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from PIL import Image

from backendV4.api import Api
from backendV4.image_note_file import ImageNoteFile
from backendV4.index_manager import IndexManager
from migrate_v3_to_v4 import MigrationError, migrate


def write_v3(path: Path, password: str, title, folder_path="", text="", tags=(), image=b"", plaintext=False):
    """Zapis w formacie v3 (kopia layoutu sprzed F1) — tylko do budowy danych testowych."""
    payload = json.dumps({"title": title, "folder_path": folder_path, "text": text,
                          "tags": list(tags), "image": image.hex()}).encode("utf-8")
    if plaintext:
        path.write_bytes(struct.pack("<4sBBBBBI", b"IMNG", 3, 0, 0, 0, 0, len(payload)) + payload)
        return
    salt, iv = os.urandom(16), os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))
    sealed = AESGCM(key).encrypt(iv, payload, None)
    ct, tag = sealed[:-16], sealed[-16:]
    path.write_bytes(struct.pack("<4sBBBBBI", b"IMNG", 3, 1, 12, 16, 16, len(ct)) + salt + iv + tag + ct)


def img(fmt, size=(900, 600), mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, (30, 140, 90) if mode == "RGB" else (30, 140, 90, 100)).save(buf, format=fmt)
    return buf.getvalue()


def snapshot(d: Path) -> dict:
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime) for p in d.iterdir()}


def build_vault(src: Path) -> dict:
    src.mkdir()
    specs = {
        "a" * 32: dict(title="Zdjęcie PNG", folder_path="Klienci/Kowalski", text="# Nagłówek\nżółć", tags=["foto", "klient"], image=img("PNG")),
        "b" * 32: dict(title="JPEG", folder_path="Klienci", text="tekst", tags=["foto"], image=img("JPEG")),
        "c" * 32: dict(title="Bez obrazu", folder_path="", text="sama treść", tags=[]),
        "d" * 32: dict(title="Przezroczysty", folder_path="Inne/Głęboko/Bardzo", tags=["alfa"], image=img("PNG", mode="RGBA")),
        "e" * 32: dict(title="Nie-obraz w polu obrazu", tags=["x"], image=b"to nie jest obraz"),
    }
    for i, (stem, spec) in enumerate(specs.items()):
        p = src / f"{stem}.imgnote"
        write_v3(p, PASSWORD, **spec)
        os.utime(p, (1_600_000_000 + i, 1_600_000_000 + i))
    (src / ("f" * 32 + ".imgnote")).write_bytes(b"IMNG\x03uszkodzony")          # uszkodzony
    write_v3(src / ("0" * 32 + ".imgnote"), PASSWORD, "jawny", plaintext=True)  # niezaszyfrowany
    write_v3(src / ("1" * 32 + ".imgnote"), "inne-haslo", "obcy")                # inne hasło
    (src / "_index.imgidx").write_bytes(b"IMIX\x01stary indeks v1")
    (src / "notatki.txt").write_text("nie notatka")
    return specs


def test_full_migration():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "vault", Path(tmp) / "vault_v4"
        specs = build_vault(src)
        before = snapshot(src)
        logs = []
        report = migrate(src, dst, PASSWORD, params=FAST, log=logs.append)

        assert snapshot(src) == before, "źródło zmienione!"
        assert sorted(report["migrated"]) == sorted(f"{s}.imgnote" for s in specs)
        failed = {name for name, _ in report["failed"]}
        assert failed == {"f" * 32 + ".imgnote", "0" * 32 + ".imgnote", "1" * 32 + ".imgnote"}, report["failed"]
        assert report["others"] == ["notatki.txt"]
        assert {p.name for p in dst.iterdir()} == {f"{s}.imgnote" for s in specs} | {"_index.imgidx"}

        mgr = IndexManager(str(dst), password=PASSWORD)
        mgr.load_or_rebuild()
        assert mgr.password_verified and len(mgr.index.notes) == len(specs)
        for i, (stem, spec) in enumerate(specs.items()):
            p = dst / f"{stem}.imgnote"
            n = ImageNoteFile.load_from_file(str(p), mgr.key)
            assert n.title == spec["title"] and n.text == spec.get("text", "")
            assert n.folder_path == spec.get("folder_path", "") and n.tags == spec.get("tags", [])
            assert n.image_data == spec.get("image", b"")
            assert p.stat().st_mtime == 1_600_000_000 + i, "mtime nie zachowany"
            has_real_image = spec.get("image", b"")[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0")
            assert (n.thumbnail_data is not None) == has_real_image, stem
        assert any("bez miniatury" in line for line in logs)
        assert mgr.index.tag_index["foto"] == {"a" * 32 + ".imgnote", "b" * 32 + ".imgnote"}

        api = Api()
        assert api.login(str(dst), PASSWORD)["password_verified"] is True
        assert len(api.list_all_notes()) == len(specs)
        thumb = api.get_note_thumbnail(str(dst / ("a" * 32 + ".imgnote")))
        assert thumb, "brak miniatury przez Api"
    print("OK  pełna migracja: dane 1:1, źródło nietknięte, mtime, raport, Api")


def test_refuses_bad_input():
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "vault", Path(tmp) / "out"
        build_vault(src)
        before = snapshot(src)
        for args, what in (
            ((src, dst, "zle-haslo"), "złe hasło"),
            ((src, src / "w_srodku", PASSWORD), "cel w źródle"),
            ((Path(tmp) / "brak", dst, PASSWORD), "brak źródła"),
        ):
            try:
                migrate(*args, params=FAST, log=lambda *_: None)
            except MigrationError:
                pass
            else:
                raise AssertionError(f"nie odmówiono: {what}")
        assert not dst.exists() and snapshot(src) == before
        dst.mkdir()
        (dst / "cos").write_text("x")
        try:
            migrate(src, dst, PASSWORD, params=FAST, log=lambda *_: None)
            raise AssertionError("nie odmówiono: niepusty cel")
        except MigrationError:
            pass
    print("OK  odmowa: złe hasło, cel w źródle, brak źródła, niepusty cel — nic nie zapisano")


def main():
    test_full_migration()
    test_refuses_bad_input()
    print("\nWszystkie testy przeszły.")


if __name__ == "__main__":
    main()
