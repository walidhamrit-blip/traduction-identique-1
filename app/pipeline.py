"""Pipeline complet de traduction de PDF avec préservation des coordonnées."""

from __future__ import annotations

from collections.abc import Callable

from app.extractor import extract_pdf_layout
from app.renderer import build_overlay_pdf, merge_overlay_with_original
from app.schemas import TargetLang, TextBlock
from app.translator import mock_translate

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
    """
    lang = TargetLang(target_lang)

    if not pdf_bytes or not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise ValueError("Le fichier fourni n'est pas un document PDF valide.")

    extracted_pages = extract_pdf_layout(pdf_bytes)
    if not extracted_pages:
        return pdf_bytes

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
        return pdf_bytes

    overlay_bytes = build_overlay_pdf(
        pages=extracted_pages,
        translated_blocks_per_page=translated_per_page,
        target_lang=lang,
    )
    return merge_overlay_with_original(pdf_bytes, overlay_bytes)
