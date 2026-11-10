"""Construction sûre des noms de fichiers et de l'en-tête `Content-Disposition`.

Les en-têtes HTTP (HTTP/1.1) ne transportent que de l'ASCII/latin-1 : un nom de
fichier contenant de l'arabe, du cyrillique, des accents ou un emoji faisait
planter Starlette (`UnicodeEncodeError: 'latin-1' codec can't encode...`) et
produisait une erreur HTTP 500 alors que le PDF, lui, était correctement généré.

Solution (RFC 6266 / RFC 5987) : un nom ASCII de repli dans `filename=` et le nom
réel, correctement percent-encodé, dans `filename*=UTF-8''...`.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

DEFAULT_STEM = "document"
MAX_STEM_LENGTH = 80

# Caractères interdits / dangereux dans un nom de fichier ou un en-tête
_UNSAFE_CHARS = re.compile(r'[\x00-\x1f\x7f"\\/;:*?<>|]')
_MULTIPLE_UNDERSCORES = re.compile(r"_+")


def _strip_control_and_quotes(name: str) -> str:
    """Supprime les caractères de contrôle et les guillemets (anti-injection d'en-tête)."""
    return _UNSAFE_CHARS.sub("_", name)


def safe_ascii_stem(filename: str | None, max_length: int = MAX_STEM_LENGTH) -> str:
    """Retourne un nom de base 100 % ASCII, sans accent ni espace.

    Exemples :
    - ``"فاتورة تجارية.pdf"``      -> ``"document"``
    - ``"Facture été 2026.pdf"``  -> ``"Facture_ete_2026"``
    - ``"договор № 12.pdf"``       -> ``"12"``
    """
    if not filename:
        return DEFAULT_STEM

    # Retire un éventuel chemin (nom envoyé par certains clients)
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    stem = _strip_control_and_quotes(stem)

    # Décompose les accents puis ne garde que l'ASCII (é -> e, ø -> o...)
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")

    cleaned = "".join(
        ch if (ch.isalnum() or ch in "-_") else "_" for ch in ascii_only if ch.isascii()
    )
    cleaned = _MULTIPLE_UNDERSCORES.sub("_", cleaned).strip("_-")

    return (cleaned or DEFAULT_STEM)[:max_length]


def build_content_disposition(
    original_filename: str | None,
    target_lang: str,
    *,
    disposition: str = "inline",
) -> str:
    """Construit un en-tête `Content-Disposition` compatible RFC 5987 (100 % ASCII).

    - ``filename="..."`` : nom de repli ASCII, toujours lisible même si le client
      ignore ``filename*``.
    - ``filename*=UTF-8''<percent-encoded>`` : nom d'origine préservé (arabe,
      cyrillique, accents…), décodé par les navigateurs modernes.
    """
    safe_lang = re.sub(r"[^a-z-]", "", target_lang.lower()) or "fr"
    ascii_stem = safe_ascii_stem(original_filename)
    fallback_name = f"{ascii_stem}_{safe_lang}.pdf"

    # Nom « joli » : on repart du nom d'origine assaini, sans chemin ni caractères
    # de contrôle, pour que le client retrouve son nom de fichier d'origine.
    raw_name = (original_filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    raw_name = _strip_control_and_quotes(raw_name).strip() or f"{DEFAULT_STEM}.pdf"
    stem = raw_name[:-4] if raw_name.lower().endswith(".pdf") else raw_name
    pretty_name = f"{stem.strip()[:MAX_STEM_LENGTH]}_{safe_lang}.pdf"

    encoded = quote(pretty_name, safe="")
    return f'{disposition}; filename="{fallback_name}"; filename*=UTF-8\'\'{encoded}'
