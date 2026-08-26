"""
image_note_file.py — format pliku .imgnote (binarny, AES-GCM/Scrypt).

Port 1:1 z v2 (imgnotev2/image_note_file.py) — brak zależności od Qt.
Format pliku pozostaje kompatybilny wstecz (VERSION = 2 bez zmian).
"""
import struct
import json
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os


class ImageNoteFile:
    MAGIC = b'IMNG'
    VERSION = 2
    HEADER_FORMAT = '<4sBBBBBI'  # magic, version, flags, iv_len, salt_len, tag_len, enc_len
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self, text: str = "", image_data: bytes = b"", tags=None, encrypted=False):
        self.text = text
        self.image_data = image_data
        self.tags = tags or []
        self.encrypted = encrypted

    def _derive_key(self, password: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
        return kdf.derive(password.encode("utf-8"))

    def _serialize_payload(self):
        return json.dumps({
            "text": self.text,
            "tags": self.tags,
            "image": self.image_data.hex()
        }).encode("utf-8")

    @staticmethod
    def _deserialize_payload(data: bytes):
        j = json.loads(data.decode("utf-8"))
        return j["text"], bytes.fromhex(j["image"]), j["tags"]

    def save_to_file(self, path: str, password: str | None = None):
        payload = self._serialize_payload()

        if password:
            salt = os.urandom(16)
            iv = os.urandom(12)
            key = self._derive_key(password, salt)
            aes = AESGCM(key)
            # aes.encrypt() zwraca: zaszyfrowane_dane + 16B_tag (łącznie)
            encrypted_with_tag = aes.encrypt(iv, payload, None)
            ciphertext = encrypted_with_tag[:-16]   # dane bez taga
            gcm_tag    = encrypted_with_tag[-16:]   # 16B tag

            flags = 0x01
            # enc_len = długość samego ciphertextu (BEZ taga)
            header = struct.pack(
                self.HEADER_FORMAT,
                self.MAGIC, self.VERSION, flags,
                len(iv), len(salt), len(gcm_tag), len(ciphertext)
            )
            with open(path, "wb") as f:
                f.write(header)
                f.write(salt)       # 16 B
                f.write(iv)         # 12 B
                f.write(gcm_tag)    # 16 B
                f.write(ciphertext) # N B
        else:
            flags = 0x00
            header = struct.pack(
                self.HEADER_FORMAT,
                self.MAGIC, self.VERSION, flags, 0, 0, 0, len(payload)
            )
            with open(path, "wb") as f:
                f.write(header)
                f.write(payload)

    @classmethod
    def load_from_file(cls, path: str, password: str | None = None):
        with open(path, "rb") as f:
            header_data = f.read(cls.HEADER_SIZE)
            (magic, version, flags,
             iv_len, salt_len, tag_len, enc_len) = struct.unpack(cls.HEADER_FORMAT, header_data)

            if magic != cls.MAGIC:
                raise ValueError("Nieprawidłowy magic number.")
            if version != cls.VERSION:
                raise ValueError("Nieobsługiwana wersja.")

            is_encrypted = bool(flags & 0x01)

            if is_encrypted:
                salt       = f.read(salt_len)   # 16 B
                iv         = f.read(iv_len)      # 12 B
                gcm_tag    = f.read(tag_len)     # 16 B
                ciphertext = f.read(enc_len)     # N B

                if len(ciphertext) != enc_len:
                    raise ValueError(
                        f"Plik uszkodzony: oczekiwano {enc_len} B danych, "
                        f"odczytano {len(ciphertext)} B."
                    )

                if not password:
                    raise ValueError("Ten plik wymaga hasła.")

                key = cls()._derive_key(password, salt)
                aes = AESGCM(key)
                # decrypt wymaga: ciphertext + tag (w tej kolejności)
                decrypted = aes.decrypt(iv, ciphertext + gcm_tag, None)
                text, image_data, tags = cls._deserialize_payload(decrypted)
                return cls(text, image_data, tags, encrypted=True)
            else:
                payload = f.read(enc_len)
                text, image_data, tags = cls._deserialize_payload(payload)
                return cls(text, image_data, tags, encrypted=False)
