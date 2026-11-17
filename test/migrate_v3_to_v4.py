"""
migrate_v3_to_v4.py — jednorazowa migracja vaulta ImgNote z formatu v3 na v4
(FORMAT_V4.md). Robi KOPIĘ: źródło jest tylko czytane, nigdy modyfikowane.

Co robi:
  1. sprawdza katalogi, wolne miejsce i hasło (na pierwszej czytelnej notatce),
  2. każdą notatkę v3 odczytuje (własny czytnik v3 poniżej), zapisuje jako v4
     pod TĄ SAMĄ nazwą pliku w katalogu docelowym, z zachowaniem mtime,
  3. odczytuje zapisaną notatkę v4 i porównuje tytuł, folder, tekst, tagi
     i SHA-256 obrazu z oryginałem — plik, który się nie zgadza, jest usuwany
     z katalogu docelowego i trafia do raportu,
  4. buduje i zapisuje nowy indeks v2, sprawdza liczbę notatek.
Stary indeks v3 (_index.imgidx) nie jest kopiowany — v4 buduje własny.

Użycie (hasło jest pytane interaktywnie, nie podawaj go w linii poleceń):
    uv run python test/migrate_v3_to_v4.py --source /ścieżka/vault --dest /ścieżka/vault_v4

Po udanej migracji:
    1. otwórz w aplikacji katalog --dest i sprawdź kilka notatek,
    2. dopiero wtedy podmień katalogi, np.:
         mv /ścieżka/vault /ścieżka/vault_v3_kopia
         mv /ścieżka/vault_v4 /ścieżka/vault
    3. kopię v3 usuń dopiero, gdy będziesz pewien (zawiera te same dane, zaszyfrowane starym formatem).

Kody wyjścia: 0 = wszystko przeniesione, 1 = część notatek nie przeszła (lista w raporcie),
2 = migracja nie wystartowała (złe hasło, zły katalog, brak miejsca).
"""
import argparse
import getpass
import hashlib
import json
import os
import shutil
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.exceptions import InvalidTag  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # noqa: E402

from backendV4.image_note_file import ImageNoteFile  # noqa: E402
from backendV4.index_file import IndexFile  # noqa: E402
from backendV4.index_manager import IndexManager  # noqa: E402
from backendV4.vault_key import KdfParams, KeyRing, VaultKey  # noqa: E402

# ── czytnik formatu v3 (kopia logiki z backendV4/image_note_file.py sprzed F1) ──

V3_MAGIC = b"IMNG"
V3_VERSION = 3
V3_HEADER = "<4sBBBBBI"  # magic, version, flags, iv_len, salt_len, tag_len, enc_len
V3_HEADER_SIZE = struct.calcsize(V3_HEADER)


class MigrationError(Exception):
    pass


class V3WrongPassword(MigrationError):
    pass


def read_v3_note(path: Path, password: str) -> dict:
    """Odczyt notatki v3 -> dict(title, folder_path, text, tags, image). Pliki
    niezaszyfrowane są odrzucane (aplikacja v3 też ich nie otwierała w vaulcie z hasłem)."""
    with open(path, "rb") as f:
        header = f.read(V3_HEADER_SIZE)
        if len(header) != V3_HEADER_SIZE:
            raise MigrationError("za krótki nagłówek")
        magic, version, flags, iv_len, salt_len, tag_len, enc_len = struct.unpack(V3_HEADER, header)
        if magic != V3_MAGIC:
            raise MigrationError("to nie jest plik .imgnote (zły magic)")
        if version != V3_VERSION:
            raise MigrationError(f"wersja v{version}, oczekiwano v3")
        if not flags & 0x01:
            raise MigrationError("plik niezaszyfrowany — pominięty")
        if (iv_len, salt_len, tag_len) != (12, 16, 16):
            raise MigrationError("nieprawidłowe długości w nagłówku")
        salt, iv, tag = f.read(16), f.read(12), f.read(16)
        ciphertext = f.read(enc_len)
        if len(ciphertext) != enc_len:
            raise MigrationError("plik ucięty")
    key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))
    try:
        payload = AESGCM(key).decrypt(iv, ciphertext + tag, None)
    except InvalidTag:
        raise V3WrongPassword("nie udało się odszyfrować (złe hasło albo uszkodzony plik)") from None
    j = json.loads(payload.decode("utf-8"))
    return {
        "title": j.get("title", ""),
        "folder_path": j.get("folder_path", ""),
        "text": j.get("text", ""),
        "tags": list(j.get("tags", [])),
        "image": bytes.fromhex(j.get("image", "")),
    }


# ── migracja ──────────────────────────────────────────────────────────────

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check_dirs(src: Path, dst: Path) -> None:
    if not src.is_dir():
        raise MigrationError(f"katalog źródłowy nie istnieje: {src}")
    src_r, dst_r = src.resolve(), dst.resolve()
    if dst_r == src_r or src_r in dst_r.parents or dst_r in src_r.parents:
        raise MigrationError("katalog docelowy nie może być źródłem ani leżeć w nim (i odwrotnie)")
    if dst.exists() and (not dst.is_dir() or any(dst.iterdir())):
        raise MigrationError(f"katalog docelowy istnieje i nie jest pusty: {dst}")


def migrate(src: Path, dst: Path, password: str, params: KdfParams | None = None, log=print) -> dict:
    """Właściwa migracja (bez interakcji) — wołana z main() i z testów."""
    src, dst = Path(src), Path(dst)
    _check_dirs(src, dst)
    notes = sorted(p for p in src.iterdir() if p.is_file() and p.suffix == ".imgnote")
    others = sorted(p.name for p in src.iterdir()
                    if p.name != IndexFile.FILENAME and not (p.is_file() and p.suffix == ".imgnote"))
    if not notes:
        raise MigrationError("w katalogu źródłowym nie ma plików .imgnote")

    needed = sum(p.stat().st_size for p in notes)
    free = shutil.disk_usage(dst.parent if not dst.exists() else dst).free
    if free < needed * 1.1:
        raise MigrationError(f"za mało miejsca: potrzeba ~{needed / 2**20:.0f} MB, wolne {free / 2**20:.0f} MB")

    # hasło: pierwsza notatka, która się odszyfruje; jeśli żadna — przerywamy przed zapisem czegokolwiek
    probe_failures = 0
    for p in notes:
        try:
            read_v3_note(p, password)
            break
        except V3WrongPassword:
            probe_failures += 1
        except MigrationError:
            continue
    else:
        raise MigrationError("hasło nie pasuje do żadnej notatki" if probe_failures else "żadna notatka nie jest czytelna")

    dst.mkdir(parents=True, exist_ok=True)
    keyring = KeyRing(password)
    key = VaultKey.new(keyring, params)
    log(f"Nowy vault v4: Argon2id m={key.params.memory_kib // 1024} MiB, t={key.params.iterations}, p={key.params.lanes}")

    migrated, failed = [], []
    started, last_log = time.monotonic(), 0.0
    for i, src_path in enumerate(notes, start=1):
        dst_path = dst / src_path.name
        try:
            old = read_v3_note(src_path, password)
            note = ImageNoteFile(text=old["text"], image_data=old["image"], tags=old["tags"],
                                 title=old["title"], folder_path=old["folder_path"])
            note.save_to_file(str(dst_path), key)
            st = src_path.stat()
            os.utime(dst_path, (st.st_atime, st.st_mtime))  # kolejność "od najnowszych" bez zmian
            check = ImageNoteFile.load_from_file(str(dst_path), key)
            expected = (note.title, note.folder_path, old["text"], list(dict.fromkeys(old["tags"])), _sha(old["image"]))
            got = (check.title, check.folder_path, check.text, list(dict.fromkeys(check.tags)), _sha(check.image_data))
            if got != expected:
                raise MigrationError("odczyt kontrolny v4 nie zgadza się z oryginałem")
            if old["image"] and check.thumbnail_data is None:
                log(f"  uwaga: {src_path.name} — obraz nieczytelny dla Pillow, notatka bez miniatury")
            migrated.append(src_path.name)
        except Exception as e:  # noqa: BLE001 — każdy błąd pojedynczego pliku ląduje w raporcie
            failed.append((src_path.name, str(e) or type(e).__name__))
            try:
                dst_path.unlink()
            except FileNotFoundError:
                pass
        now = time.monotonic()
        if now - last_log > 2 or i == len(notes):
            last_log = now
            log(f"  {i}/{len(notes)}  ({now - started:.0f} s)")

    mgr = IndexManager(str(dst), keyring=keyring)
    mgr.load_or_rebuild()
    mgr.save()
    if len(mgr.index.notes) != len(migrated) or mgr.key.vault_salt != key.vault_salt:
        raise MigrationError("indeks po migracji nie zgadza się z liczbą przeniesionych notatek")
    keyring.wipe()
    return {"migrated": migrated, "failed": failed, "others": others,
            "seconds": time.monotonic() - started}


def main() -> int:
    parser = argparse.ArgumentParser(description="Migracja vaulta ImgNote v3 -> v4 (kopia).")
    parser.add_argument("--source", required=True, type=Path, help="katalog vaulta v3 (tylko odczyt)")
    parser.add_argument("--dest", required=True, type=Path, help="nowy, pusty katalog na vault v4")
    args = parser.parse_args()

    password = getpass.getpass("Hasło vaulta: ")
    try:
        report = migrate(args.source, args.dest, password)
    except MigrationError as e:
        print(f"\nMigracja przerwana: {e}\nNiczego nie zmieniono w {args.source}.")
        return 2
    finally:
        password = ""

    print(f"\nPrzeniesiono: {len(report['migrated'])} notatek w {report['seconds']:.0f} s -> {args.dest}")
    if report["others"]:
        print(f"Pominięte pliki/katalogi, które nie są notatkami ({len(report['others'])}): "
              + ", ".join(report["others"][:10]) + (" …" if len(report["others"]) > 10 else ""))
    if report["failed"]:
        print(f"\nNIE przeniesiono {len(report['failed'])} notatek (oryginały nietknięte w {args.source}):")
        for name, reason in report["failed"]:
            print(f"  {name}: {reason}")
        return 1
    print("\nGotowe. Otwórz w aplikacji katalog docelowy i sprawdź notatki, zanim podmienisz katalogi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
