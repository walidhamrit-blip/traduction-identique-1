"""Détection des segments qui ne doivent PAS être envoyés à un moteur de traduction.

Sur un PDF imprimé depuis une page web (cas réel : connaissement « KONŞİMANTO »),
le flux texte contient des références, des codes conteneurs, du CSS et du JS.
Les traduire produit du bruit (« the », « نص مترجم ») et casse la mise en page.
Ils sont donc conservés strictement à l'identique : le bloc n'est ni masqué ni réécrit.
"""

from __future__ import annotations

import re
import unicodedata

# --- Motifs « à ne jamais traduire » ---------------------------------------
_URL_RE = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)
_EMAIL_RE = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
# Code conteneur ISO 6346 : 4 lettres + 7 chiffres (ex: CAAU2973244)
_CONTAINER_RE = re.compile(r"^[A-Z]{4}\d{7}$")
# Référence technique : I000503614, INV-2026-089, FWB/12345
_REFERENCE_RE = re.compile(r"^(?=[A-Z0-9][A-Z0-9\-/._#]*$)(?=.*\d)[A-Z0-9\-/._#]{4,}$")
# Identification de colis / conteneur avec suffixe de type : "CAAU2973244 20DC", "MSKU1234567 40HC"
_CONTAINER_LINE_RE = re.compile(
    r"^[A-Z]{4}\s?\d{6,7}(?:\s?\d{0,2}[A-Z]{2,3})?$"
)
# Immatriculation / code véhicule : "AB-123-CD", "12-345-67"
_PLATE_RE = re.compile(r"^[A-Z]{1,3}[-\s]?\d{2,4}[-\s]?[A-Z]{0,3}$", re.IGNORECASE)
# Numéros, montants, dates, coordonnées, IBAN, téléphones
_NUMERIC_RE = re.compile(
    r"^[\d\s.,:/+()\[\]%#°€$£¥–—-]+(?:[A-Z]{3})?$", re.IGNORECASE
)
_PHONE_RE = re.compile(r"^\+?[\d\s().-]{7,}$")
# Séquence de nombres séparés (coordonnées GPS, plages, listes de codes)
_NUMBERS_LIST_RE = re.compile(r"^[\d\s.,:;/\-–—]+$")

# --- Motifs de code web (CSS / JS / HTML) ----------------------------------
_WEB_ARTIFACT_MARKERS = (
    "function(",
    "function (",
    "=>",
    "var ",
    "let ",
    "const ",
    "document.",
    "window.",
    "@media",
    "</",
    "/>",
    "::before",
    "::after",
    "!important",
    "url(data:",
    "base64,",
    "{\"",
    "\":\"",
    "};",
)

# Caractères typiques d'une feuille de style ou d'un script
_CODE_CHARS = set("{}<>;=[]|\\")


def _is_mostly_code(text: str) -> bool:
    """Vrai si le segment ressemble à du CSS/JS/HTML plutôt qu'à du texte humain."""
    lowered = text.lower()
    if any(marker in lowered for marker in _WEB_ARTIFACT_MARKERS):
        return True
    if not text:
        return False
    code_chars = sum(1 for ch in text if ch in _CODE_CHARS)
    if code_chars / len(text) > 0.12:
        return True
    # Jetons très longs sans espace = base64 / hash / identifiant technique
    longest = max((len(tok) for tok in text.split()), default=0)
    return longest >= 60 and " " not in text.strip()


def is_untranslatable(text: str) -> bool:
    """Décide si un segment doit être conservé tel quel (sans traduction ni masquage)."""
    cleaned = " ".join(text.split())
    if len(cleaned) < 2:
        return True

    # Aucune lettre (chiffres, ponctuation, symboles) -> rien à traduire
    if not any(ch.isalpha() for ch in cleaned):
        return True

    if (
        _URL_RE.match(cleaned)
        or _EMAIL_RE.match(cleaned)
        or _CONTAINER_RE.match(cleaned)
        or _CONTAINER_LINE_RE.match(cleaned)
        or _REFERENCE_RE.match(cleaned)
        or _NUMERIC_RE.match(cleaned)
        or _PHONE_RE.match(cleaned)
        or _NUMBERS_LIST_RE.match(cleaned)
    ):
        return True

    if _is_mostly_code(cleaned):
        return True

    # Ligne composée uniquement de codes et de nombres (ex: "CAAU2973244 20DC 322")
    tokens = cleaned.split()
    if len(tokens) > 1 and all(
        not any(ch.isalpha() for ch in token)
        or _CONTAINER_LINE_RE.match(token)
        or _REFERENCE_RE.match(token)
        or _CONTAINER_RE.match(token)
        for token in tokens
    ):
        return True

    # Acronyme / sigle court en majuscules sans voyelle prononçable (ex: "FWB", "B/L")
    letters = [ch for ch in cleaned if ch.isalpha()]
    if (
        len(cleaned) <= 5
        and all(ch.isupper() for ch in letters)
        and not any(ch in "AEIOUYÀÂÉÈÊËÎÏÔÖÙÛÜ" for ch in letters)
    ):
        return True

    # Texte déjà dans la langue cible (arabe) : inutile de le retraduire
    if _is_arabic(cleaned):
        return True

    return False


def _is_arabic(text: str) -> bool:
    """Vrai si la majorité des lettres du segment sont arabes."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return False
    arabic = sum(1 for ch in letters if "\u0600" <= ch <= "\u06FF" or "\uFB50" <= ch <= "\uFEFF")
    return arabic / len(letters) > 0.6


def split_long_segment(text: str, max_chars: int = 1800) -> list[str]:
    """Découpe un paragraphe trop long en morceaux traduisibles séparément.

    Le découpage privilégie la ponctuation forte pour ne pas casser le sens.
    """
    if len(text) <= max_chars:
        return [text]

    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(current) + len(sentence) + 1 <= max_chars:
            current = f"{current} {sentence}".strip()
        else:
            if current:
                chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)

    # Filet de sécurité : un morceau encore trop long est coupé sur les espaces
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars)
            cut = cut if cut > max_chars * 0.5 else max_chars
            result.append(chunk[:cut])
            chunk = chunk[cut:].lstrip()
        if chunk:
            result.append(chunk)
    return result or [text]


def normalize_for_detection(text: str) -> str:
    """Normalise (NFKC) pour fiabiliser la détection des codes et références."""
    return unicodedata.normalize("NFKC", text)
