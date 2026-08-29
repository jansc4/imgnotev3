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

- Każdy element zapisywany jest jako pojedynczy plik `.imgnote` — własny format binarny (nagłówek `IMNG`) przechowujący obraz, notatkę tekstową, tagi, tytuł i ścieżkę folderu, szyfrowany **AES-GCM** z kluczem wyprowadzonym z hasła przez **Scrypt**. Nic nie jest zapisywane ani przesyłane bez szyfrowania. Nazwy plików na dysku są losowe — tytuły i struktura folderów żyją wyłącznie wewnątrz zaszyfrowanego payloadu, nigdy nie są widoczne w samym systemie plików.
- Osobny, zaszyfrowany indeks (własna sól, ta sama koperta formatu) trzyma tagi i ścieżki folderów do szybkiego odczytu — otwarcie galerii czy wyszukiwanie nie wymaga deszyfrowania każdej notatki; przy starcie odszyfrowywane i doindeksowywane są tylko pliki zmienione od ostatniej sesji.
- Dokumenty inne niż obrazy (`.pdf`, `.docx`, `.doc`, `.odt`, `.rtf`, `.txt`, `.md`) mają tekst wyciągany przy imporcie, więc treść jest przeszukiwalna mimo że plik jest zaszyfrowany.
- **Wyszukiwanie** wspiera boolowskie zapytania po tagach (`projekt AND ważne`, `(szkic OR draft) AND ważne`, częściowe dopasowanie tagu, z podpowiedziami) i wyszukiwanie tekstowe (dokładne frazy, listy słów AND, zwykłe podciągi) — filtrowanie po tagach działa na indeksie; wyszukiwanie tekstowe nadal deszyfruje.
- **Foldery są wirtualne** — całość organizacji to zaszyfrowane pole w każdej notatce, nie realne katalogi na dysku. Tworzenie, zmiana nazwy, przenoszenie i usuwanie z menu kontekstowego (prawy klik) na drzewie folderów; zmiana nazwy/przeniesienie kaskadowo obejmuje wszystkie notatki pod daną ścieżką.
- Każdy dodatkowy widok — podgląd, edytor, menedżer tagów, ustawienia — otwiera się jako warstwowy popup nad galerią, nie osobny ekran, więc kontekst nigdy nie ginie (np. edycja tagów notatki z poziomu podglądu wraca dokładnie w to samo miejsce po zamknięciu).
- Podgląd wspiera zoom/przesuwanie obrazu, nawigację strzałkami klawiatury między notatkami i edycję Markdown wprost w popupie, bez opuszczania go.
- Menedżer tagów potrafi zmienić nazwę, scalić lub usunąć tag we wszystkich notatkach, które go używają, z podglądem liczby użyć na żywo i paskiem postępu przy masowym przepisywaniu.
- Popup ustawień kontroluje rozmiar miniatur i rozmiar czcionki treści notatek (zapisywane lokalnie).

## Stos technologiczny

Backend Python (pywebview, PyQt6, `cryptography` do AES-GCM/Scrypt, `Pillow` do miniatur, `cachetools` do ograniczonego cache'a notatek, PyMuPDF/python-docx/odfpy/striprtf do ekstrakcji tekstu z dokumentów) · frontend HTML/CSS/vanilla JS.

## Status

Projekt osobisty, aktywnie rozwijany. Zaczynał jako GUI w PyQt6, został przeniesiony na to pywebview, a następnie przebudowany z jednego ~700-liniowego God objectu na modularny backend (zaszyfrowany indeks zamiast liniowego skanu, ograniczony cache LRU, dedykowane klasy folderów/wyszukiwania/indeksu) i przeprojektowany, oparty na popupach frontend.
