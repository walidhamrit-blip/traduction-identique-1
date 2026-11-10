"""Chaîne de traduction : sélection du fournisseur, cache, lots et mode dégradé."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

from app.schemas import TargetLang
from app.translation.providers import (
    GoogleWebProvider,
    LibreTranslateProvider,
    MyMemoryProvider,
    OfflineGlossaryProvider,
    ProviderUnavailable,
    TranslationProvider,
)
from app.translation.sanitizer import (
    is_untranslatable,
    normalize_for_detection,
    split_long_segment,
)

logger = logging.getLogger(__name__)

# Cache mémoire par instance de fonction : les libellés de tableaux se répètent
# des dizaines de fois dans un même PDF (et souvent d'un PDF à l'autre).
_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_MAX_ENTRIES = 5000

# Nombre maximal de segments envoyés en une passe (protège la durée d'exécution)
MAX_BATCH_SEGMENTS = 60


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def provider_order() -> list[str]:
    """Ordre des fournisseurs à essayer, piloté par TRANSLATOR_PROVIDER.

    ``auto`` (défaut) : google_web -> mymemory -> libretranslate -> offline
    Une valeur explicite (ex: ``mymemory``) force ce fournisseur en premier.
    """
    configured = _env("TRANSLATOR_PROVIDER", "auto").lower()
    chain = ["google_web", "mymemory", "libretranslate", "offline"]
    if configured and configured not in ("auto", "chain"):
        if configured not in chain:
            logger.warning("TRANSLATOR_PROVIDER=%s inconnu, utilisation de 'auto'", configured)
        else:
            chain.remove(configured)
            chain.insert(0, configured)
    return chain


def build_provider(name: str) -> TranslationProvider | None:
    """Instancie un fournisseur ; retourne None s'il n'est pas configuré."""
    if name == "google_web":
        return GoogleWebProvider()
    if name == "mymemory":
        return MyMemoryProvider()
    if name == "libretranslate":
        base = _env("LIBRETRANSLATE_URL", "https://libretranslate.com")
        return LibreTranslateProvider(base_url=base, api_key=_env("LIBRETRANSLATE_API_KEY"))
    if name == "offline":
        return OfflineGlossaryProvider()
    return None


class TranslationResult:
    """Résultat d'une traduction de page : texte par bloc + statistiques."""

    __slots__ = ("translations", "provider", "degraded", "translated_count", "skipped_count", "errors")

    def __init__(self) -> None:
        self.translations: list[str] = []
        self.provider: str = "offline"
        self.degraded: bool = False
        self.translated_count: int = 0
        self.skipped_count: int = 0
        self.errors: list[str] = []


class TranslationChain:
    """Traduit des segments en essayant les fournisseurs gratuits dans l'ordre."""

    def __init__(self, providers: Iterable[TranslationProvider] | None = None) -> None:
        if providers is None:
            self.providers = [
                p for p in (build_provider(n) for n in provider_order()) if p is not None
            ]
        else:
            self.providers = list(providers)
        self.used_provider: str | None = None
        self.errors: list[str] = []

    # -- API publique ------------------------------------------------------
    def translate_segments(
        self, texts: list[str], source: str, target: str
    ) -> TranslationResult:
        """Traduit une liste de segments PDF.

        - Les codes, références et artefacts web sont conservés tels quels.
        - Les segments identiques sont traduits une seule fois (cache).
        - En cas d'échec de tous les fournisseurs réseau, repli sur le glossaire
          local : le PDF reste lisible, aucune fausse traduction n'est inventée.
        """
        result = TranslationResult()
        result.translations = [""] * len(texts)

        to_translate: list[tuple[int, str]] = []  # (index source, texte normalisé)
        for index, raw in enumerate(texts):
            cleaned = " ".join(raw.split())
            if not cleaned:
                result.translations[index] = ""
                result.skipped_count += 1
                continue
            if is_untranslatable(normalize_for_detection(cleaned)):
                # Conservé à l'identique : sera ré-affiché sans masquage
                result.translations[index] = cleaned
                result.skipped_count += 1
                continue
            to_translate.append((index, cleaned))

        if not to_translate:
            result.provider = "skipped"
            return result

        # Déduplication via cache (les libellés répétés ne coûtent rien)
        unique_needed: list[str] = []
        seen: set[str] = set()
        for _, cleaned in to_translate:
            key = (cleaned, source, target)
            if key in _CACHE or cleaned in seen:
                continue
            seen.add(cleaned)
            unique_needed.append(cleaned)

        fresh = self._translate_with_chain(unique_needed, source, target)

        result.provider = self.used_provider or "offline"
        result.degraded = result.provider == "offline" and bool(unique_needed)
        result.errors = self.errors[-3:]

        for index, cleaned in to_translate:
            key = (cleaned, source, target)
            translated = _CACHE.get(key) or fresh.get(cleaned) or cleaned
            if translated.strip() and translated.strip() != cleaned:
                result.translated_count += 1
            else:
                result.skipped_count += 1
            result.translations[index] = translated
        return result

    # -- Interne -----------------------------------------------------------
    def _translate_with_chain(
        self, texts: list[str], source: str, target: str
    ) -> dict[str, str]:
        """Essaie chaque fournisseur ; retourne le mapping texte -> traduction."""
        if not texts:
            return {}

        network_providers = [p for p in self.providers if p.name != "offline"]
        offline = next((p for p in self.providers if p.name == "offline"), None)

        for provider in network_providers:
            try:
                mapping = self._translate_all(provider, texts, source, target)
                self.used_provider = provider.name
                self._store(mapping, source, target)
                return mapping
            except ProviderUnavailable as exc:
                logger.warning("Fournisseur %s indisponible : %s", provider.name, exc)
                self.errors.append(f"{provider.name}: {exc}")
            except Exception as exc:  # défense en profondeur : jamais de 500 ici
                logger.exception("Erreur inattendue du fournisseur %s", provider.name)
                self.errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")

        # Repli local honnête : glossaire connu, sinon texte d'origine
        self.used_provider = "offline"
        if offline is None:
            offline = OfflineGlossaryProvider()
        try:
            mapping = dict(zip(texts, offline.translate_batch(texts, source, target), strict=False))
        except Exception:
            mapping = {t: t for t in texts}
        self._store(mapping, source, target)
        return mapping

    def _translate_all(
        self, provider: TranslationProvider, texts: list[str], source: str, target: str
    ) -> dict[str, str]:
        """Découpe en lots et en morceaux traduisibles, puis réassemble."""
        mapping: dict[str, str] = {}

        # 1) Segments trop longs découpés en morceaux
        expanded: list[tuple[str, list[str]]] = [
            (text, split_long_segment(text)) for text in texts
        ]

        # 2) Lots bornés pour rester sous la durée d'exécution
        flat: list[tuple[str, int, str]] = []  # (texte d'origine, position, morceau)
        for original, pieces in expanded:
            for position, piece in enumerate(pieces):
                flat.append((original, position, piece))

        for start in range(0, len(flat), MAX_BATCH_SEGMENTS):
            batch = flat[start : start + MAX_BATCH_SEGMENTS]
            translated = provider.translate_batch(
                [piece for _, _, piece in batch], source=source, target=target
            )
            if len(translated) != len(batch):
                raise ProviderUnavailable(
                    f"{provider.name} a renvoyé {len(translated)} traductions pour {len(batch)} segments"
                )
            joined: dict[str, dict[int, str]] = {}
            for (original, position, _), piece_translation in zip(batch, translated, strict=True):
                joined.setdefault(original, {})[position] = piece_translation
            for original, pieces_map in joined.items():
                mapping[original] = " ".join(
                    pieces_map[i] for i in sorted(pieces_map)
                ).strip()
        return mapping

    @staticmethod
    def _store(mapping: dict[str, str], source: str, target: str) -> None:
        if len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.clear()
        for original, translated in mapping.items():
            _CACHE[(original, source, target)] = translated


def clear_cache() -> None:
    """Vide le cache (utile pour les tests)."""
    _CACHE.clear()


def chain_diagnostics() -> dict[str, object]:
    """État de la chaîne pour /api/diag (sans révéler de secret)."""
    return {
        "provider_env": _env("TRANSLATOR_PROVIDER", "auto") or "auto",
        "order": provider_order(),
        "cache_entries": len(_CACHE),
        "libretranslate_url": _env("LIBRETRANSLATE_URL", "https://libretranslate.com"),
        "requires_api_key": False,
    }
