"""
search_engine.py – parser i ewaluator zapytań wyszukiwania dla ImgNote.

Port 1:1 z v2 (imgnotev2/search_engine.py) — brak zależności od Qt.

=== TAGI ===
Składnia boolowska (case-insensitive):
  projekt AND ważne          – obie muszą być obecne
  projekt OR notatka         – którakolwiek
  projekt NOT archiwum       – projekt ale NIE archiwum
  (projekt OR szkic) AND ważne  – nawiasy grupujące
  projekt ważne              – spacja bez operatora = OR (kompatybilność wsteczna)

Dopasowanie tagu: częściowe (tag "projekt2024" pasuje na zapytanie "projekt").

=== TEKST ===
  "pełny ciąg"               – exact match całej frazy (case-insensitive)
  słowo1, słowo2, słowo3     – lista AND: wszystkie słowa muszą wystąpić
  pojedyncze słowo           – proste wyszukiwanie podciągu
"""

from __future__ import annotations
import re
import shlex


# ------------------------------------------------------------------ #
#  Parser tagów                                                        #
# ------------------------------------------------------------------ #

class _TagToken:
    WORD = "WORD"
    AND  = "AND"
    OR   = "OR"
    NOT  = "NOT"
    LPAREN = "("
    RPAREN = ")"
    EOF  = "EOF"

    def __init__(self, kind: str, value: str = ""):
        self.kind = kind
        self.value = value

    def __repr__(self):
        return f"Token({self.kind}, {self.value!r})"


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
            # Zbierz słowo
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
    """
    Gramatyka:
      expr   → term (OR term)*
      term   → factor (AND? factor)*    ← AND opcjonalne; spacja = OR między wyrazami
      factor → NOT factor | atom
      atom   → WORD | '(' expr ')'
    
    Uwaga: spacja między dwoma słowami bez operatora = OR (wsteczna kompatybilność).
    Jawne AND wymaga słowa kluczowego AND.
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
        node = self._parse_expr()
        if self._peek().kind != _TagToken.EOF:
            # Ignorujemy nadmiarowe tokeny zamiast rzucać wyjątek
            pass
        return node

    def _parse_expr(self):
        """expr → term (OR term)*"""
        left = self._parse_term()
        while self._peek().kind == _TagToken.OR:
            self._consume()
            right = self._parse_term()
            left = ("OR", left, right)
        return left

    def _parse_term(self):
        """term → factor (AND? factor)* — spacja między atomami = OR"""
        left = self._parse_factor()
        while self._peek().kind in (_TagToken.AND, _TagToken.WORD, _TagToken.LPAREN, _TagToken.NOT):
            explicit_and = self._peek().kind == _TagToken.AND
            if explicit_and:
                self._consume()
                right = self._parse_factor()
                left = ("AND", left, right)
            else:
                # spacja bez operatora → OR (wsteczna kompatybilność)
                right = self._parse_factor()
                left = ("OR", left, right)
        return left

    def _parse_factor(self):
        """factor → NOT factor | atom"""
        if self._peek().kind == _TagToken.NOT:
            self._consume()
            operand = self._parse_factor()
            return ("NOT", operand)
        return self._parse_atom()

    def _parse_atom(self):
        """atom → WORD | '(' expr ')'"""
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
        # Błąd składni – zwróć None (dopasuje wszystko → ignorujemy)
        return ("ALL",)


def _eval_tag_tree(node, note_tags_lower: list[str]) -> bool:
    """Rekurencyjnie ewaluuje drzewo AST na zbiorze tagów notatki."""
    kind = node[0]
    if kind == "TAG":
        term = node[1]
        return any(term in nt for nt in note_tags_lower)
    if kind == "AND":
        return _eval_tag_tree(node[1], note_tags_lower) and _eval_tag_tree(node[2], note_tags_lower)
    if kind == "OR":
        return _eval_tag_tree(node[1], note_tags_lower) or _eval_tag_tree(node[2], note_tags_lower)
    if kind == "NOT":
        return not _eval_tag_tree(node[1], note_tags_lower)
    if kind == "ALL":
        return True
    return True


def match_tags(query: str, note_tags: list[str]) -> bool:
    """Zwraca True jeśli notatka z podanymi tagami pasuje do zapytania."""
    query = query.strip()
    if not query:
        return True
    tokens = _tokenize_tags(query)
    ast = _TagParser(tokens).parse()
    note_tags_lower = [t.lower() for t in note_tags]
    return _eval_tag_tree(ast, note_tags_lower)


# ------------------------------------------------------------------ #
#  Parser tekstu                                                       #
# ------------------------------------------------------------------ #

def match_text(query: str, note_text: str) -> bool:
    """
    Zwraca True jeśli tekst notatki pasuje do zapytania.

    Tryby:
      "fraza w cudzysłowie"   – exact match całej frazy (case-insensitive)
      słowo1, słowo2, słowo3  – AND: wszystkie muszą wystąpić (case-insensitive)
      pojedyncze słowo        – podciąg (case-insensitive)
    """
    query = query.strip()
    if not query:
        return True

    text_lower = note_text.lower()

    # Tryb 1: cudzysłów → exact match frazy
    if (query.startswith('"') and query.endswith('"') and len(query) > 1) or \
       (query.startswith("'") and query.endswith("'") and len(query) > 1):
        phrase = query[1:-1].lower()
        return phrase in text_lower

    # Tryb 2: lista po przecinkach → AND wszystkich
    if "," in query:
        terms = [t.strip().lower() for t in query.split(",") if t.strip()]
        return all(term in text_lower for term in terms)

    # Tryb 3: pojedyncze słowo/fraza → podciąg
    return query.lower() in text_lower


# ------------------------------------------------------------------ #
#  Pomocnicze: podpowiedź składni do wyświetlenia w UI               #
# ------------------------------------------------------------------ #

TAG_HINT = (
    'Składnia tagów: spacja=OR, AND, OR, NOT, nawiasy\n'
    'Przykład: (projekt OR szkic) AND ważne NOT archiwum'
)

TEXT_HINT = (
    'Tekst: "fraza" = exact | słowo1, słowo2 = oba muszą być | słowo = zawiera'
)
