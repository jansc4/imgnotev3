# ImgNote — format pliku v4 (F1)

Stan: 2026-11-17. Decyzje Jana: Argon2id, miniatura w pliku, migracja skryptem na kopii.

## Cele

1. Klucz z hasła liczony **raz na sesję** (mocno), nie raz na plik.
2. Odczyt **częściowy**: galeria czyta tylko miniaturę, indeks i wyszukiwanie tylko metadane.
3. Zmiana taga/folderu/tekstu szyfruje ponownie **tylko metadane**, zaszyfrowany obraz kopiowany bajt w bajt.
4. Uwierzytelniony nagłówek (AAD), brak hex (obraz binarnie).
5. Plik dalej samowystarczalny: odzyskiwalny bez indeksu, z samym hasłem.

## Klucze

```
master    = Argon2id(hasło, salt = vault_salt, m, t, p) -> 32 B     (raz na sesję i vault_salt)
file_key  = HKDF-SHA256(master, salt = file_salt, info = "imgnote/v4/note")  -> 32 B
index_key = HKDF-SHA256(master, salt = file_salt, info = "imgnote/v4/index") -> 32 B
```

- `vault_salt` (16 B) wspólny dla vaulta, zapisany w nagłówku **każdego** pliku.
- `file_salt` (16 B) losowy przy każdym pełnym zapisie pliku.
- Parametry Argon2id zapisane w nagłówku. Domyślnie m = 256 MiB, t = 3, p = 4 (~400 ms na maszynie Jana).
- Parametry z nagłówka są danymi z dysku, więc są ograniczane przed liczeniem: m ≤ 1 GiB, t ≤ 16, p ≤ 16 (ochrona przed DoS podłożonym plikiem).
- `KeyRing` (jeden na sesję) trzyma hasło i cache `master` per (vault_salt, parametry). Plik z innym vault_salt (np. skopiowany z innego vaulta z tym samym hasłem) nadal się odczyta, kosztem jednego dodatkowego Argon2id.
- `VaultKey` = KeyRing + vault_salt/parametry vaulta, używane do zapisu nowych plików.
- Blokada sesji (S7) porzuca KeyRing.

## Plik notatki `.imgnote` v4

Little-endian. Nagłówek 96 B, potem trzy sekcje jedna za drugą.

| offset | rozmiar | pole |
|---|---|---|
| 0 | 4 | magic `IMNG` |
| 4 | 1 | version = 4 |
| 5 | 1 | flags = 0 (zarezerwowane) |
| 6 | 1 | kdf_id = 1 (Argon2id) |
| 7 | 1 | lanes (p) |
| 8 | 4 | memory_kib (m) |
| 12 | 4 | iterations (t) |
| 16 | 16 | vault_salt |
| 32 | 16 | file_salt |
| 48 | 3 × 16 | tablica sekcji: nonce 12 B + długość szyfrogramu u32 (z tagiem 16 B) |
| 96 | … | szyfrogram meta, potem miniatura, potem obraz |

- Bajty 0–47 to **prefiks**. Każda sekcja szyfrowana AES-256-GCM kluczem `file_key`, własnym losowym nonce, z AAD = prefiks + id sekcji (1 = meta, 2 = miniatura, 3 = obraz).
  - Zmiana dowolnego bajtu prefiksu, zamiana sekcji miejscami albo wklejenie sekcji z innego pliku (inny file_salt, więc inny klucz) kończy się błędem uwierzytelnienia.
  - Tablica sekcji nie jest w AAD celowo: dzięki temu przepisanie meta (nowy nonce i długość) nie unieważnia szyfrogramu obrazu. Zmiana nonce lub długości i tak psuje odszyfrowanie tej sekcji.
- **meta**: JSON `{title, folder_path, text, tags}`.
- **miniatura**: WebP q80, dłuższy bok ≤ 320 px, z alfą; pusta, gdy brak obrazu lub obraz nieczytelny.
- **obraz**: oryginalne bajty; puste, gdy brak.
- Puste sekcje też są szyfrowane (sam tag 16 B), więc ich brak też jest uwierzytelniony.
- Walidacja przy odczycie: magic, wersja (3 = komunikat o migracji), kdf_id, limity parametrów, długości ≥ 16, rozmiar pliku = 96 + suma długości.
- Szyfrowanie obowiązkowe. Tryb bez hasła z v3 znika (aplikacja i tak wymaga hasła).

## Indeks `_index.imgidx` v2

Ten sam prefiks 48 B (magic `IMIX`, version = 2), potem jedna sekcja (nonce + długość) i szyfrogram JSON.

- Payload: `{"notes": {filename: {title, folder_path, mtime, tags}}}`. `tag_index` nie jest zapisywany: odtwarzany w pamięci jako `dict[tag, set[filename]]` (P3), więc usunięcie notatki dotyka tylko jej tagów.
- Indeks to nadal pochodny cache. Uszkodzony albo stary (v1) jest odbudowywany z notatek.
- `vault_salt` vaulta czytany jest z nagłówka indeksu. Bez indeksu bierzemy go z pierwszej notatki v4, a w pustym vaulcie losujemy nowy.

## Operacje

| operacja | v3 | v4 |
|---|---|---|
| miniatura w galerii | Scrypt + cały plik + dekodowanie + resize | HKDF + sekcja miniatury |
| reperacja indeksu / wyszukiwanie tekstu | Scrypt + cały plik | HKDF + sekcja meta |
| zmiana taga / folderu / tekstu | Scrypt + cały plik + nowy zapis całości | HKDF + nowa sekcja meta, obraz i miniatura kopiowane bez zmian |
| zapis indeksu | Scrypt | HKDF |
| podgląd notatki | Scrypt + cały plik + hex | HKDF + cały plik binarnie |

## Migracja v3 → v4 (`test/migrate_v3_to_v4.py`)

- Źródła nigdy nie modyfikuje. Zapisuje do nowego katalogu, który nie może istnieć albo musi być pusty.
- Hasło podawane przez `getpass`, nie w linii poleceń (historia powłoki).
- Przed startem sprawdza hasło i wolne miejsce (≥ rozmiar źródła).
- Każda notatka:
  1. odczyt v3 (własny czytnik v3 w skrypcie),
  2. zapis v4 pod **tą samą nazwą pliku**, z zachowaniem mtime,
  3. odczyt kontrolny v4 i porównanie tytułu, folderu, tekstu, tagów oraz SHA-256 obrazu.
- Na końcu budowa i zapis nowego indeksu oraz kontrola liczby notatek.
- Raport z listą plików, których nie dało się przenieść. Jeśli jakiś plik się nie przeniósł, kod wyjścia ≠ 0.
- Podmianę katalogów robi Jan ręcznie po sprawdzeniu w aplikacji, a stary katalog zostaje jako kopia.
- Aplikacja v4 otwierająca niezmigrowany vault zgłasza czytelny błąd „format v3 — uruchom migrację” zamiast pokazywać pusty vault.
