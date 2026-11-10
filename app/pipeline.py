"""Pipeline complet de traduction de PDF avec préservation des coordonnées."""

from __future__ import annotations

import logging
from collections.abc import Callable

from app.errors import PdfProcessingError, PdfServiceError
from app.extractor import extract_pdf_layout
from app.renderer import build_overlay_pdf, merge_overlay_with_original
from app.schemas import TargetLang, TextBlock
from app.translator import mock_translate

logger = logging.getLogger(__name__)

TranslateFunc = Callable[[str, TargetLang], str]


def translate_pdf_bytes(
    pdf_bytes: bytes,
    target_lang: TargetLang | str,
    translator_fn: TranslateFunc = mock_translate,
) -> bytes:
    """Exécute les 4 étapes du pipeline sur un PDF binaire et retourne le nouveau PDF :

    1. Extraction du texte et des Bounding Boxes via `pdfminer.six`.
    2. Traduction de chaque bloc via `translator_fn` (`mock_translate` par défaut).
    3. Si `target_lang == 'ar'`, formatage RTL via `arabic-reshaper` + `bidi.algorithm.get_display`
       et chargement de la police TTF `Amiri`.
    4. Génération du calque `reportlab` (fond blanc aux coordonnées exactes + texte traduit)
       puis fusion avec les pages originales via `pypdf`.

    Les erreurs connues sont remontées en `PdfServiceError` (statut HTTP explicite)
    et non en exception brute : plus de « HTTP 500 » sans explication côté client.
    """
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

    extracted_pages = extract_pdf_layout(pdf_bytes)
    if not extracted_pages:
        raise PdfProcessingError(
            "Aucune page exploitable n'a été trouvée dans le PDF.",
            status_code=400,
            code="empty_pdf",
        )

    translated_per_page: list[list[tuple[TextBlock, str]]] = []
    has_any_text = False

    for page in extracted_pages:
        page_pairs: list[tuple[TextBlock, str]] = []
        for block in page.blocks:
            translated = translator_fn(block.text, lang)
            page_pairs.append((block, translated))
            if translated:
                has_any_text = True
        translated_per_page.append(page_pairs)

    if not has_any_text:
        # PDF 100 % scanné (image) : rien à traduire, on renvoie l'original tel quel.
        logger.info("Aucun texte extractible : le PDF original est renvoyé tel quel.")
        return pdf_bytes

    try:
        overlay_bytes = build_overlay_pdf(
            pages=extracted_pages,
            translated_blocks_per_page=translated_per_page,
            target_lang=lang,
        )
        return merge_overlay_with_original(pdf_bytes, overlay_bytes)
    except PdfServiceError:
        raise
    except Exception as exc:
        # Objectif : ne jamais laisser filtrer une exception brute (500 opaque).
        logger.exception("Échec de la génération du calque ou de la fusion pypdf")
        raise PdfProcessingError(
            f"Échec du rendu du calque traduit : {type(exc).__name__}: {exc}"
        ) from exc
