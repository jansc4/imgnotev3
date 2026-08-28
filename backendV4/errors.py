"""
errors.py — wspólne typy wyjątków dla formatów backendV4.

To NIE jest naruszenie decyzji o duplikacji koperty kryptograficznej —
image_note_file.py i index_file.py mają każdy swoją kopię logiki
nagłówka/Scrypt/AES-GCM, celowo, dla izolacji. Ten moduł nie zawiera
żadnej logiki, tylko jeden pusty typ wyjątku, żeby wywołujący (IndexManager,
przyszłe Api) mógł złapać "złe hasło" jednym `except`, niezależnie który
z dwóch formatów je zgłosił. Współdzielone słownictwo błędu, nie
współdzielona implementacja — to inny rodzaj sprzężenia niż to, którego
świadomie unikamy przy kopercie krypto.
"""


class WrongPasswordError(ValueError):
    """Hasło nie pasuje do pliku — AES-GCM nie zweryfikował taga
    integralności. Odróżnione od innych ValueError (zły magic number,
    nieobsługiwana wersja, uszkodzony plik), żeby dało się to pokazać
    jako konkretny komunikat "złe hasło", nie ogólne "coś poszło nie tak"."""
    pass
