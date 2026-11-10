"""Pipeline complet de traduction de PDF avec préservation des coordonnées."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass

from app.config import SOURCE_LANG, TRANSLATE_ENABLED, TRANSLATE_TIME_BUDGET_S
from app.errors import PdfProcessingError, PdfServiceError
from app.extractor import extract_pdf_layout
from app.renderer import build_overlay_pdf, merge_overlay_with_original
from app.schemas import ExtractedPage, TargetLang, TextBlock
from app.translation.chain import TranslationChain, TranslationResult

logger = logging.getLogger(__name__)


@dataclass
class PipelineReport:
    """Statistiques renvoyées au client (en-têtes X-*) pour rendre le résultat vérifiable."""

    provider: str = "offline"
    degraded: bool = False
    translated_blocks: int = 0
    kept_blocks: int = 0
    total_blocks: int = 0
    pages: int = 0
    seconds: float = 0.0
    warnings: list[str] | None = None

    @staticmethod
    def _ascii(value: str) -> str:
        """Les en-têtes HTTP n'acceptent que latin-1 : on translittère en ASCII."""
        decomposed = unicodedata.normalize("NFKD", value)
        ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
        return re.sub(r"\s+", " ", ascii_text).strip()

    def headers(self) -> dict[str, str]:
        headers = {
            "X-Translation-Provider": self._ascii(self.provider),
            "X-Translation-Degraded": "1" if self.degraded else "0",
            "X-Blocks-Translated": str(self.translated_blocks),
            "X-Blocks-Kept": str(self.kept_blocks),
            "X-Pages": str(self.pages),
        }
        if self.warnings:
            headers["X-Translation-Warnings"] = self._ascii(" | ".join(self.warnings))[:400]
        return headers


def _translate_pages(
    pages: list[ExtractedPage],
    target_lang: TargetLang,
    chain: TranslationChain,
    time_budget_s: float,
) -> tuple[list[list[tuple[TextBlock, str]]], TranslationResult, bool]:
    """Traduit toutes les pages d'un coup (cache + lots) avec budget temps respecté."""
    started = time.monotonic()
    all_texts = [block.text for page in pages for block in page.blocks]

    result = chain.translate_segments(all_texts, source=SOURCE_LANG, target=target_lang.value)

    elapsed = time.monotonic() - started
    timed_out = elapsed > time_budget_s

    per_page: list[list[tuple[TextBlock, str]]] = []
    cursor = 0
    for page in pages:
        page_items: list[tuple[TextBlock, str]] = []
        for block in page.blocks:
            translated = result.translations[cursor] if cursor < len(result.translations) else ""
            page_items.append((block, translated))
            cursor += 1
        per_page.append(page_items)
    return per_page, result, timed_out


def translate_pdf_bytes(
    pdf_bytes: bytes,
    target_lang: TargetLang | str,
    *,
    report: PipelineReport | None = None,
) -> bytes:
    """Exécute le pipeline complet et retourne le PDF traduit.

    1. Extraction du texte et des Bounding Boxes via `pdfminer.six`.
    2. Traduction **gratuite** par la chaîne de fournisseurs (`app/translation/`),
       avec cache, filtrage des codes/références et mode dégradé honnête.
    3. Pour `ar`, reshaping + BiDi RTL (police TTF Amiri).
    4. Calque `reportlab` (masque uniquement les blocs réellement traduits) + fusion `pypdf`.

    Les erreurs connues remontent en `PdfServiceError` (statut HTTP explicite).
    """
    started = time.monotonic()
    try:
        lang = TargetLang(target_lang)
    except ValueError as exc:
        raise PdfProcessingError(
            f"Langue cible non supportée : {target_lang!r}.",
            hint="Utilisez 'en', 'fr' ou 'ar'.",
            status_code=400,
            code="unsupported_language",
        ) from exc

    if not pdf_bytes or not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise PdfProcessingError(
            "Le fichier fourni n'est pas un document PDF valide.",
            status_code=400,
            code="invalid_pdf",
            hint="Le contenu doit commencer par la signature binaire '%PDF-'.",
        )

    pages = extract_pdf_layout(pdf_bytes)
    if not pages:
        raise PdfProcessingError(
            "Aucune page exploitable n'a été trouvée dans le PDF.",
            status_code=400,
            code="empty_pdf",
        )

    total_blocks = sum(len(page.blocks) for page in pages)
    if total_blocks == 0:
        logger.info("PDF sans texte extractible (scan/image) : original renvoyé tel quel.")
        if report is not None:
            report.pages = len(pages)
            report.warnings = ["PDF sans texte extractible (probablement un scan) : rien à traduire."]
        return pdf_bytes

    chain = TranslationChain()
    if TRANSLATE_ENABLED:
        per_page, result, timed_out = _translate_pages(
            pages, lang, chain, TRANSLATE_TIME_BUDGET_S
        )
    else:
        per_page = [[(block, block.text) for block in page.blocks] for page in pages]
        result = TranslationResult()
        result.provider = "disabled"
        timed_out = False

    if report is not None:
        report.provider = result.provider
        report.degraded = result.degraded
        report.translated_blocks = result.translated_count
        report.kept_blocks = result.skipped_count + (
            total_blocks - result.translated_count - result.skipped_count
        )
        report.total_blocks = total_blocks
        report.pages = len(pages)
        report.warnings = list(result.errors)
        if timed_out:
            report.warnings.append(
                "Budget de temps dépassé : certains segments n'ont pas été traduits."
            )
        if result.degraded:
            report.warnings.append(
                "Aucun fournisseur de traduction joignable : texte original conservé "
                "(aucune fausse traduction n'a été insérée)."
            )

    try:
        overlay_bytes = build_overlay_pdf(
            pages=pages,
            translated_blocks_per_page=per_page,
            target_lang=lang,
        )
        output = merge_overlay_with_original(pdf_bytes, overlay_bytes)
    except PdfServiceError:
        raise
    except Exception as exc:
        logger.exception("Échec de la génération du calque ou de la fusion pypdf")
        raise PdfProcessingError(
            f"Échec du rendu du calque traduit : {type(exc).__name__}: {exc}"
        ) from exc

    if report is not None:
        report.seconds = round(time.monotonic() - started, 2)
    return output
