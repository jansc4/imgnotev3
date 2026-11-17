🇬🇧 [English version](README.md)

# ImgNote v3

Lokalny, szyfrowany menedżer notatek dla obrazów i dokumentów, które muszą pozostać chronione — zdjęcia medyczne z opisami, plany/blueprinty, dokumenty tożsamości, cokolwiek wrażliwego, co mimo to chcesz tagować, przeszukiwać i przeglądać jak zwykłą galerię, zamiast zakopywać w zaszyfrowanym zipie.

## Po co

Rzeczy tego typu zwykle kończą jako nieprzeszukiwalna sterta zaszyfrowanych archiwów, albo w ogóle bez szyfrowania, "bo nikt nie zajrzy". Tu każdy element jest szyfrowany osobno, ale wciąż tagowalny, przeszukiwalny i przeglądalny przez zwykłą aplikację.

## Zrzuty ekranu

| Logowanie | Galeria |
|---|---|
| ![Ekran hasła](docs/screenshot-login.png) | ![Widok galerii](docs/screenshot-main.png) |

## Jak to działa

- Każdy element zapisywany jest jako pojedynczy plik `.imgnote` we własnym formacie binarnym (v4, specyfikacja w [FORMAT_V4.md](FORMAT_V4.md)). Plik ma trzy osobno szyfrowane sekcje: **metadane** (tytuł, folder, tagi, tekst), **miniaturę** (WebP) i sam **obraz**, zapisany binarnie. Nazwy plików na dysku są losowe. Tytuły i struktura folderów żyją wyłącznie w zaszyfrowanych metadanych i nigdy nie są widoczne w systemie plików.
- **Klucze:** hasło przechodzi przez **Argon2id** (256 MiB, 3 przebiegi) **raz na sesję**. Każdy plik dostaje potem własny klucz przez **HKDF-SHA256**, co trwa mikrosekundy. Każda sekcja jest zabezpieczona **AES-256-GCM**, a nagłówek pliku jest uwierzytelniany jako dane powiązane. Dlatego zmieniony nagłówek, zamienione sekcje albo sekcja wklejona z innego pliku są odrzucane przy odczycie. Parametry KDF zapisane w pliku są ograniczane przed użyciem, więc podłożony plik nie zmusi aplikacji do alokacji gigabajtów.
- **Odczyty częściowe** dają szybkość. Galeria odszyfrowuje tylko miniatury, a indeks i wyszukiwanie tylko metadane. Zmiana taga, folderu albo tekstu szyfruje ponownie same metadane, a zaszyfrowany obraz jest kopiowany bajt w bajt. Na 100 notatkach ze zdjęciami ~2,8 MB odbudowa indeksu spadła z 4,6 s do 0,01 s, miniatury na zimno z 9,1 s do 0,01 s, a zmiana taga we wszystkich notatkach z 9,9 s do 0,14 s.
- Osobny, zaszyfrowany indeks (ten sam klucz główny) trzyma tagi i ścieżki folderów do szybkiego odczytu. Przy starcie czytane są tylko pliki zmienione od ostatniej sesji, i tylko ich metadane. Uszkodzony indeks jest po prostu odbudowywany z notatek. Pojedyncza uszkodzona notatka też nie blokuje logowania: jest pomijana i zgłaszana.
- Dokumenty inne niż obrazy (`.pdf`, `.docx`, `.doc`, `.odt`, `.rtf`, `.txt`, `.md`) mają tekst wyciągany przy imporcie, więc treść jest przeszukiwalna, mimo że plik jest zaszyfrowany.
- **Wyszukiwanie** ma dwa tryby:
  - boolowskie zapytania po tagach (`projekt AND ważne`, `(szkic OR draft) AND ważne`, częściowe dopasowanie tagu, z podpowiedziami), działające na indeksie;
  - wyszukiwanie tekstowe (dokładne frazy, listy słów AND, zwykłe podciągi), które odszyfrowuje tylko metadane kandydatów.
- **Foldery są wirtualne**: istnieją tylko jako zaszyfrowane pole w każdej notatce, nie jako realne katalogi na dysku. Tworzenie, zmiana nazwy, przenoszenie i usuwanie są w menu kontekstowym (prawy klik) na drzewie folderów. Zmiana nazwy albo przeniesienie obejmuje kaskadowo wszystkie notatki pod daną ścieżką.
- Każdy dodatkowy widok (podgląd, edytor, menedżer tagów, ustawienia, potwierdzenia) otwiera się jako warstwowy popup nad galerią, a nie osobny ekran, więc kontekst nigdy nie ginie. Niezapisane zmiany wymagają potwierdzenia przed zamknięciem. Escape zawsze zamyka tylko wierzchnią warstwę.
- Podgląd ma zoom i przesuwanie obrazu, nawigację strzałkami między notatkami i edycję Markdown wprost w popupie.
- Menedżer tagów potrafi zmienić nazwę, scalić lub usunąć tag we wszystkich notatkach, które go używają. Pokazuje na żywo liczbę użyć, a przy masowym przepisywaniu pasek postępu.
- Galerię da się obsługiwać klawiaturą: Tab do karty, Enter otwiera, Ctrl/Shift zaznacza wiele, strzałki działają w menu kontekstowym.
- Popup ustawień kontroluje rozmiar miniatur, rozmiar czcionki treści i czas autoblokady (zapisywane lokalnie).

## Bezpieczeństwo

- **Blokada sesji:** przycisk 🔒 i automatyczna blokada po bezczynności (domyślnie 10 min; do zmiany albo wyłączenia w Ustawieniach). Blokada porzuca hasło, klucze główne i wszystkie odszyfrowane dane z pamięci.
- **Brak trybu bez szyfrowania:** każda notatka i indeks są zawsze szyfrowane.
- **Markdown jest sanityzowany** po stronie Pythona (`nh3`, biała lista tagów, linki tylko `http`/`https`/`mailto`, bez obrazków). Content-Security-Policy dodatkowo blokuje w interfejsie skrypty inline i zasoby zdalne.
- **Most JS przyjmuje tylko ścieżki notatek z otwartego vaulta.** Importowane pliki muszą mieć dozwolone rozszerzenie i mieścić się w limicie rozmiaru. Eksport trafia tylko tam, gdzie wskazano w oknie dialogowym, a pobieranie z sieci ma limit rozmiaru i dopuszcza tylko `http`/`https`.
- **Zapis atomowy:** plik tymczasowy + `fsync` + zamiana, więc crash albo brak miejsca nie utnie notatki.
- **Zrzuty pamięci (core dump) są wyłączone** dla procesu aplikacji, bo zawierałyby odszyfrowane obrazy.

## Migracja z formatu v3

Vaulty utworzone przed formatem v4 trzeba jednorazowo zmigrować. Skrypt działa na **kopii**: katalog źródłowy jest tylko czytany, nigdy modyfikowany.

```
uv run python test/migrate_v3_to_v4.py --source /ścieżka/do/vaulta --dest /ścieżka/do/vaulta_v4
```

- Pyta o hasło interaktywnie, więc hasło nie trafi do historii powłoki.
- Przed zapisem czegokolwiek sprawdza wolne miejsce i hasło.
- Każda notatka zachowuje nazwę pliku i datę modyfikacji. Po zapisie jest odczytywana i porównywana z oryginałem (tytuł, folder, tekst, tagi, SHA-256 obrazu).
- Na koniec pokazuje listę tego, czego nie dało się przenieść.
- Otwórz nowy katalog w aplikacji i sprawdź go, zanim podmienisz katalogi. Stary zostaw jako kopię, dopóki nie będziesz pewien.
- Otwarcie niezmigrowanego vaulta w aplikacji pokazuje czytelny błąd „format v3 — uruchom migrację”, a nie pustą galerię.

## Testy

```
bash test/run_all.sh                                          # testy jednostkowe i API
QT_QPA_PLATFORM=offscreen uv run python test/e2e_offscreen.py  # prawdziwa aplikacja bez okna
```

Testy obejmują:
- format v4, w tym testy manipulacji (nagłówek, każda sekcja, zamienione lub wklejone sekcje, ucięte pliki, wrogie parametry KDF);
- indeks, cache, wyszukiwanie i API;
- migrację;
- przebieg end-to-end prawdziwej aplikacji pywebview w Qt bez okna.

## Stos technologiczny

Backend Python:
- pywebview, PyQt6;
- `cryptography` do AES-256-GCM, Argon2id i HKDF;
- `nh3` do sanityzacji renderowanego Markdownu;
- `Pillow` do miniatur WebP;
- `cachetools` do ograniczonych cache LRU (metadane, miniatury, obrazy z budżetem w bajtach);
- PyMuPDF / python-docx / odfpy / striprtf do ekstrakcji tekstu z dokumentów.

Frontend HTML/CSS/vanilla JS.

## Status

Projekt osobisty, aktywnie rozwijany.

1. Zaczynał jako GUI w PyQt6, potem został przeniesiony na pywebview.
2. Przebudowany z jednego ~700-liniowego God objectu na modularny backend (zaszyfrowany indeks zamiast liniowego skanu, ograniczone cache LRU, dedykowane klasy folderów/wyszukiwania/indeksu) i przeprojektowany, oparty na popupach frontend.
3. Audyt bezpieczeństwa i wydajności (listopad 2026) wprowadził format pliku v4, blokadę sesji, sanityzowane renderowanie, walidację ścieżek i zapis atomowy.
