"""
search_engine.py — silnik wyszukiwania dla backendV4.

Parser zapytań (tokenizer + AST) i match_text() to WIERNY PORT
backend/search_engine.py — ta sama składnia, żeby pole wyszukiwania w
appce nie zmieniło się dla użytkownika. Skopiowane, nie zaimportowane z
`backend/` — backendV4 ma być samodzielnym pakietem, niezależnym od
modułu, który docelowo zniknie po zakończeniu migracji (ta sama zasada
co przy image_note_file.py).

Co się REALNIE zmienia względem starego search_notes(): ewaluacja
zapytania o tagi. Stary kod ewaluował to samo drzewo AST osobno DLA
KAŻDEJ notatki (tokenizacja + parsowanie od nowa za każdym razem — to
diagnoza #1 z V4_REDESIGN_PLAN.md). Tu: drzewo parsowane RAZ, ewaluowane
RAZ względem całego IndexFile.tag_index (indeks odwrócony) — wynikiem
każdego węzła AST jest ZBIÓR nazw plików, nie bool per notatka. Złożoność
schodzi z O(notatki × rozmiar_AST) do O(unikalne_tagi + rozmiar_AST) —
unikalnych tagów jest zwykle znacznie mniej niż notatek.

Wyszukiwanie tekstowe NIE korzysta z indeksu — treść notatek celowo nie
jest nigdzie indeksowana jawnym tekstem (to by było trzymanie odszyfrowanej
treści poza plikiem .imgnote, realny regres bezpieczeństwa). text_query
zawsze wymaga odszyfrowania kandydatów. Wygrana: filtr po tagach zawęża
kandydatów PRZED odszyfrowaniem.
"""
from __future__ import annotations
from typing import Callable
import os

from .index_file import IndexFile


# ------------------------------------------------------------------ #
#  Parser tagów (wierny port backend/search_engine.py, bez zmian)      #
# ------------------------------------------------------------------ #

class _TagToken:
    WORD = "WORD"
    AND = "AND"
    OR = "OR"
    NOT = "NOT"
    LPAREN = "("
    RPAREN = ")"
    EOF = "EOF"

    def __init__(self, kind: str, value: str = ""):
        self.kind = kind
        self.value = value


def _tokenize_tags(query: str) -> list[_TagToken]:
    tokens = []
    i = 0
    q = query.strip()
    while i < len(q):
        if q[i].isspace():
            i += 1
            continue
        if q[i] == "(":
            tokens.append(_TagToken(_TagToken.LPAREN))
            i += 1
        elif q[i] == ")":
            tokens.append(_TagToken(_TagToken.RPAREN))
            i += 1
        else:
            j = i
            while j < len(q) and not q[j].isspace() and q[j] not in "()":
                j += 1
            word = q[i:j]
            upper = word.upper()
            if upper == "AND":
                tokens.append(_TagToken(_TagToken.AND))
            elif upper == "OR":
                tokens.append(_TagToken(_TagToken.OR))
            elif upper == "NOT":
                tokens.append(_TagToken(_TagToken.NOT))
            else:
                tokens.append(_TagToken(_TagToken.WORD, word.lower()))
            i = j
    tokens.append(_TagToken(_TagToken.EOF))
    return tokens


class _TagParser:
    """Gramatyka identyczna jak w backend/search_engine.py:
      expr   -> term (OR term)*
      term   -> factor (AND? factor)*   (spacja bez operatora = OR)
      factor -> NOT factor | atom
      atom   -> WORD | '(' expr ')'
    """
    def __init__(self, tokens: list[_TagToken]):
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> _TagToken:
        return self._tokens[self._pos]

    def _consume(self) -> _TagToken:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self):
        return self._parse_expr()

    def _parse_expr(self):
        left = self._parse_term()
        while self._peek().kind == _TagToken.OR:
            self._consume()
            left = ("OR", left, self._parse_term())
        return left

    def _parse_term(self):
        left = self._parse_factor()
        while self._peek().kind in (_TagToken.AND, _TagToken.WORD, _TagToken.LPAREN, _TagToken.NOT):
            if self._peek().kind == _TagToken.AND:
                self._consume()
                left = ("AND", left, self._parse_factor())
            else:
                left = ("OR", left, self._parse_factor())
        return left

    def _parse_factor(self):
        if self._peek().kind == _TagToken.NOT:
            self._consume()
            return ("NOT", self._parse_factor())
        return self._parse_atom()

    def _parse_atom(self):
        tok = self._peek()
        if tok.kind == _TagToken.WORD:
            self._consume()
            return ("TAG", tok.value)
        if tok.kind == _TagToken.LPAREN:
            self._consume()
            node = self._parse_expr()
            if self._peek().kind == _TagToken.RPAREN:
                self._consume()
            return node
        return ("ALL",)


def parse_tag_query(query: str):
    """query -> AST. Publiczne (bez podkreślnika), bo warto to testować
    osobno od ewaluacji — sam parser się nie zmienił, ewaluator tak."""
    return _TagParser(_tokenize_tags(query)).parse()


# ------------------------------------------------------------------ #
#  Dopasowanie tekstu (wierny port, bez zmian — to się i tak nie       #
#  przyspiesza, patrz docstring modułu)                                #
# ------------------------------------------------------------------ #

def match_text(query: str, note_text: str) -> bool:
    query = query.strip()
    if not query:
        return True
    text_lower = note_text.lower()
    if (query.startswith('"') and query.endswith('"') and len(query) > 1) or \
       (query.startswith("'") and query.endswith("'") and len(query) > 1):
        return query[1:-1].lower() in text_lower
    if "," in query:
        terms = [t.strip().lower() for t in query.split(",") if t.strip()]
        return all(term in text_lower for term in terms)
    return query.lower() in text_lower


# ------------------------------------------------------------------ #
#  SearchEngine — nowa część: AST ewaluowany na indeksie, nie na notatce #
# ------------------------------------------------------------------ #

class SearchEngine:
    def __init__(self, index: IndexFile, root_folder: str, note_loader: Callable[[str], str]):
        """
        note_loader(filepath) -> pełny tekst notatki. Wstrzyknięty, żeby
        SearchEngine nie wiedział NIC o haśle ani o cache'u — to sprawa
        wywołującego (przyszłe Api), jak dostarczyć tekst, np.:
            note_loader=lambda p: note_cache.get_content(p, password, os.path.getmtime(p)).text
        SearchEngine woła note_loader TYLKO gdy text_query jest niepuste —
        czyste tag_query nigdy nie dotyka note_loader w ogóle.
        """
        self.index = index
        self.root_folder = root_folder
        self.note_loader = note_loader

    def search(self, tag_query: str = "", text_query: str = "") -> list[dict]:
        candidates = self._match_tag_query(tag_query)  # zbiór nazw plików, zero I/O

        text_query = text_query.strip()
        results = []
        for filename in candidates:
            entry = self.index.notes.get(filename)
            if entry is None:
                continue  # rozjazd tag_index/notes - defensywnie pomijamy

            if text_query:
                path = os.path.join(self.root_folder, filename)
                try:
                    text = self.note_loader(path)
                except Exception:
                    continue  # jeden zepsuty/nieodczytywalny plik nie wywala reszty wyniku
                if not match_text(text_query, text):
                    continue

            results.append({
                "filepath": os.path.join(self.root_folder, filename),
                "title": entry["title"],
                "folder_path": entry["folder_path"],
            })
        return results

    # ---- ewaluacja AST na zbiorach nazw plików, nie na pojedynczej notatce -

    def _match_tag_query(self, tag_query: str) -> set[str]:
        tag_query = tag_query.strip()
        if not tag_query:
            return set(self.index.notes.keys())  # brak filtra = wszystkie notatki
        ast = parse_tag_query(tag_query)
        return self._eval_to_set(ast)

    def _eval_to_set(self, node) -> set[str]:
        kind = node[0]
        if kind == "TAG":
            term = node[1]
            # suma po wszystkich TAGACH (nie notatkach!) zawierających `term`
            # jako podciąg — dokładnie ta sama semantyka co stary match_tags,
            # ale liczona raz na unikalny tag, nie raz na notatkę
            matched: set[str] = set()
            for tag, filenames in self.index.tag_index.items():
                if term in tag.lower():
                    matched.update(filenames)
            return matched
        if kind == "AND":
            return self._eval_to_set(node[1]) & self._eval_to_set(node[2])
        if kind == "OR":
            return self._eval_to_set(node[1]) | self._eval_to_set(node[2])
        if kind == "NOT":
            universe = set(self.index.notes.keys())
            return universe - self._eval_to_set(node[1])
        if kind == "ALL":
            return set(self.index.notes.keys())
        return set()
