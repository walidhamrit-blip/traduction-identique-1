"""Base de référence d'articles : traductions validées, prioritaires sur tout moteur.

Principe
--------
L'entreprise tient une liste d'articles (référence, désignation anglaise,
désignation arabe, famille, unité). Pour ces articles, la traduction officielle
existe déjà : il serait absurde — et faux — de laisser un traducteur automatique
réinventer « Spare Part, Concealed Cistern, Drainage Group ».

La base est donc consultée **avant** les fournisseurs réseau :

1. la **référence article** (ex. ``16000000090``) est la clé de référence : si elle
   apparaît dans un segment, la traduction officielle de cet article est utilisée ;
2. à défaut, la **désignation** est comparée au segment (comparaison normalisée,
   insensible à la casse, aux accents et à la ponctuation).

Le fichier ``assets/reference/articles.json`` est produit par
``tools/build_reference.py`` à partir de l'export « Liste article par Tarif ».
Si le fichier est absent, tout fonctionne normalement (base vide) : la base est
un enrichissement, jamais une dépendance.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

ASSETS_REFERENCE_DIR = Path(__file__).resolve().parent.parent / "assets" / "reference"
ARTICLES_FILE = ASSETS_REFERENCE_DIR / "articles.json"
TERMINOLOGY_FILE = ASSETS_REFERENCE_DIR / "terminologie_fr.json"

# Une référence d'article : 8 chiffres ou plus, ou un code alphanumérique long.
_CODE_RE = re.compile(r"(?<![A-Z0-9\-])([A-Z0-9][A-Z0-9\-/.]{5,})(?![A-Z0-9\-])", re.IGNORECASE)

# Longueur minimale d'une désignation pour être comparée (évite « AC », « Stand »).
MIN_DESIGNATION_LENGTH = 8


def normalize(value: str) -> str:
    """Normalise un libellé pour la comparaison (casse, accents, ponctuation, espaces)."""
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("\u2019", "'").replace("\u2014", "-").replace("\u2013", "-")
    text = re.sub(r"[^\w\s\u0600-\u06FF-]", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _sorted_tokens(value: str) -> str:
    """Signature insensible à l'ordre des mots (« Stand Double » == « double stand »)."""
    return " ".join(sorted(normalize(value).split()))


@dataclass
class Terminology:
    """Terminologie métier anglais -> français (phrases puis mots)."""

    phrases: dict[str, str] = field(default_factory=dict)
    words: dict[str, str] = field(default_factory=dict)
    invariables: set[str] = field(default_factory=set)

    def __bool__(self) -> bool:
        return bool(self.words or self.phrases)

    def translate(self, text: str) -> str:
        """Traduit une désignation anglaise avec la terminologie métier.

        - les expressions complètes (« wall hang ») passent avant les mots isolés ;
        - la ponctuation d'origine (virgules, tirets) est conservée ;
        - les mots inconnus sont gardés tels quels, avec leur casse d'origine
          (marques et gammes : « Ibiza », « Xanthi ») ;
        - un mot traduit reprend la casse du mot source.
        """
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return ""

        # 1) Expressions complètes, insensibles à la casse, ponctuation préservée.
        result = cleaned
        for phrase, translation in self.phrases.items():
            pattern = re.compile(
                r"(?<![A-Za-z])" + re.escape(phrase).replace(r"\ ", r"[\s,]+") + r"(?![A-Za-z])",
                re.IGNORECASE,
            )
            result = pattern.sub(translation, result)

        # 2) Mots isolés : on ne touche ni aux chiffres, ni à la ponctuation.
        def replace_word(match: re.Match[str]) -> str:
            word = match.group(0)
            key = normalize(word)
            if key in self.invariables or key not in self.words:
                return word  # conservé tel quel (marque, gamme, unité)
            translation = self.words[key]
            if word.isupper() and len(word) > 2:
                return translation.upper()
            # En français, une désignation d'article s'écrit en minuscules
            # (« Présentoir double ») : seul le premier mot garde sa majuscule.
            return translation

        result = re.sub(r"[A-Za-zÀ-ÿ]+", replace_word, result)
        # 3) Nettoyage de la ponctuation (virgule collée, espaces multiples).
        result = re.sub(r",(?=[A-Za-zÀ-ÿ])", ", ", result)
        result = re.sub(r"\s+([,;:])", r"\1", result)
        result = re.sub(r"\s{2,}", " ", result).strip()
        if cleaned[:1].isupper() and result:
            result = result[0].upper() + result[1:]
        return result


@dataclass
class ReferenceArticle:
    """Un article de la base, avec ses traductions validées par l'entreprise."""

    code: str
    designation_en: str = ""
    designation_ar: str = ""
    designation_fr: str = ""
    family: str = ""
    unit: str = ""

    def translation_for(self, target: str) -> str:
        """Traduction officielle pour une langue cible ('' si non renseignée)."""
        return {
            "fr": self.designation_fr,
            "en": self.designation_en,
            "ar": self.designation_ar,
        }.get(target, "")


@dataclass
class ReferenceIndex:
    """Index de recherche : par référence, par désignation et par signature de mots."""

    articles: dict[str, ReferenceArticle] = field(default_factory=dict)
    terminology: Terminology = field(default_factory=Terminology)
    _by_designation: dict[str, ReferenceArticle] = field(default_factory=dict)
    _by_sorted: dict[str, ReferenceArticle] = field(default_factory=dict)
    _by_code_upper: dict[str, str] = field(default_factory=dict)

    # -- construction ------------------------------------------------------
    def add(self, article: ReferenceArticle) -> None:
        if not article.code:
            return
        self.articles[article.code] = article
        self._by_code_upper[article.code.upper()] = article.code
        for value in (article.designation_en, article.designation_fr, article.designation_ar):
            key = normalize(value)
            if len(key) >= MIN_DESIGNATION_LENGTH:
                self._by_designation.setdefault(key, article)
                self._by_sorted.setdefault(_sorted_tokens(value), article)

    # -- interrogation -----------------------------------------------------
    def __len__(self) -> int:
        return len(self.articles)

    def find_code(self, code: str) -> ReferenceArticle | None:
        """Article correspondant à une référence (comparaison insensible à la casse)."""
        canonical = self._by_code_upper.get((code or "").strip().upper())
        return self.articles.get(canonical) if canonical else None

    def codes_in(self, text: str) -> list[ReferenceArticle]:
        """Articles dont la référence apparaît dans le texte, dans l'ordre d'apparition."""
        found: list[ReferenceArticle] = []
        for match in _CODE_RE.finditer(text or ""):
            article = self.find_code(match.group(1))
            if article is not None and all(a.code != article.code for a in found):
                found.append(article)
        return found

    def french_for(self, article: ReferenceArticle) -> str:
        """Désignation française : validée si elle existe, sinon construite par terminologie."""
        if article.designation_fr:
            return article.designation_fr
        if self.terminology and article.designation_en:
            return self.terminology.translate(article.designation_en)
        return ""

    def find_designation(self, text: str) -> ReferenceArticle | None:
        """Article dont la désignation correspond exactement (à la normalisation près)."""
        cleaned = normalize(text)
        if len(cleaned) < MIN_DESIGNATION_LENGTH:
            return None
        return self._by_designation.get(cleaned) or self._by_sorted.get(
            " ".join(sorted(cleaned.split()))
        )

    def translation_for(self, article: ReferenceArticle, target: str) -> str:
        """Traduction officielle d'un article pour la langue cible."""
        if target == "fr":
            return self.french_for(article)
        return article.translation_for(target)

    def lookup(self, text: str, target: str) -> tuple[ReferenceArticle, str] | None:
        """Meilleur article pour un segment : la référence prime sur la désignation."""
        for article in self.codes_in(text):
            translation = self.translation_for(article, target)
            if translation:
                return article, translation
        article = self.find_designation(text)
        if article is not None:
            translation = self.translation_for(article, target)
            if translation:
                return article, translation
        return None

    def stats(self) -> dict[str, object]:
        """État de la base, exposé par /api/diag."""
        return {
            "articles": len(self.articles),
            "with_french": sum(1 for a in self.articles.values() if a.designation_fr),
            "french_via_terminology": sum(
                1
                for a in self.articles.values()
                if not a.designation_fr and self.terminology and a.designation_en
            ),
            "with_arabic": sum(1 for a in self.articles.values() if a.designation_ar),
            "with_english": sum(1 for a in self.articles.values() if a.designation_en),
            "terminology_entries": len(self.terminology.words) + len(self.terminology.phrases),
        }


def load_terminology(path: Path | None = None) -> Terminology:
    """Charge la terminologie métier anglais -> français (facultative)."""
    target = path or TERMINOLOGY_FILE
    if not target.is_file():
        return Terminology()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Terminologie illisible (%s) : %s", target, exc)
        return Terminology()
    return Terminology(
        phrases={normalize(k): v for k, v in (payload.get("phrases") or {}).items()},
        words={normalize(k): v for k, v in (payload.get("mots") or {}).items()},
        invariables={normalize(v) for v in (payload.get("invariables") or [])},
    )


def load_index(path: Path | None = None) -> ReferenceIndex:
    """Charge la base depuis le JSON ; retourne un index vide si le fichier manque."""
    index = ReferenceIndex(terminology=load_terminology())
    target = path or ARTICLES_FILE
    if not target.is_file():
        logger.info("Aucune base d'articles (%s) : traduction automatique seule.", target)
        return index
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Base d'articles illisible (%s) : %s", target, exc)
        return index

    for record in payload.get("articles", []):
        index.add(
            ReferenceArticle(
                code=str(record.get("code", "")).strip(),
                designation_en=(record.get("designation_en") or "").strip(),
                designation_ar=(record.get("designation_ar") or "").strip(),
                designation_fr=(record.get("designation_fr") or "").strip(),
                family=(record.get("family") or "").strip(),
                unit=(record.get("unit") or "").strip(),
            )
        )
    logger.info("Base d'articles chargée : %s articles.", len(index))
    return index


@lru_cache(maxsize=1)
def get_index() -> ReferenceIndex:
    """Index partagé (chargé une seule fois par instance de fonction)."""
    return load_index()


def clear_cache() -> None:
    """Vide le cache d'index (tests et rechargement à chaud)."""
    get_index.cache_clear()
