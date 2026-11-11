"""Suppression du texte d'origine SANS repeindre le fond (rédaction ciblée).

Pourquoi ce module
------------------
La première version masquait le texte par des rectangles. Même colorés, ces
rectangles ne peuvent pas reproduire fidèlement un fond complexe : dégradés,
bandes de largeur variable, images de fond, cellules où le texte déborde de la
partie colorée. Il restait toujours une cellule « à problème ».

La rédaction PDF (`Page.apply_redactions`) supprime le **texte** en laissant le
fond graphique intact : c'est exactement ce que l'on veut. Les paramètres sont
choisis pour ne jamais toucher au décor :

- ``images=PDF_REDACT_IMAGE_NONE`` : les images (logos, trames) sont conservées ;
- ``graphics=PDF_REDACT_LINE_ART_NONE`` : les rectangles et traits (fonds de
  cellules, bordures de tableau) sont conservés — vérifié : la bande grise reste
  grise après rédaction ;
- ``text=PDF_REDACT_TEXT_REMOVE`` : seul le texte est retiré.

Si PyMuPDF est absent ou si la rédaction échoue, l'appelant retombe sur les
masques colorés mesurés (voir `app/page_colors.py`) : la fonction ne lève jamais.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Marge (points) autour du bloc : suffisante pour englober les glyphes qui
# dépassent légèrement la boîte mesurée par pdfminer (jambages, accents).
REDACTION_MARGIN = 1.2


def strip_text(
    pdf_bytes: bytes,
    rects_per_page: list[list[tuple[float, float, float, float]]],
) -> bytes | None:
    """Retourne le PDF d'origine privé du texte des blocs indiqués.

    ``rects_per_page`` contient, pour chaque page, les rectangles (repère PDF,
    origine en bas à gauche) du texte à supprimer. Retourne ``None`` si
    l'opération n'est pas possible (PyMuPDF absent, PDF protégé…).
    """
    if not rects_per_page:
        return pdf_bytes
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - déploiement sans PyMuPDF
        logger.info("PyMuPDF absent : suppression du texte impossible.")
        return None

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # pragma: no cover - PDF exotique
        logger.info("Rédaction impossible (PDF illisible)", exc_info=True)
        return None

    try:
        for index, rects in enumerate(rects_per_page):
            if index >= document.page_count or not rects:
                continue
            page = document[index]
            height = page.rect.height
            touched = False
            for x0, y0, x1, y1 in rects:
                # Repère PDF (origine en bas) -> repère PyMuPDF (origine en haut).
                rect = pymupdf.Rect(
                    x0 - REDACTION_MARGIN,
                    height - y1 - REDACTION_MARGIN,
                    x1 + REDACTION_MARGIN,
                    height - y0 + REDACTION_MARGIN,
                )
                if rect.is_empty:
                    continue
                page.add_redact_annot(rect, fill=None)
                touched = True
            if not touched:
                continue
            # 0 = ne pas toucher aux images, 0 = ne pas toucher aux traits/fonds.
            page.apply_redactions(
                images=pymupdf.PDF_REDACT_IMAGE_NONE,
                graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                text=pymupdf.PDF_REDACT_TEXT_REMOVE,
            )
        result = document.tobytes(garbage=3, deflate=True)
    except Exception:  # pragma: no cover - sécurité
        logger.info("Rédaction du texte impossible", exc_info=True)
        return None
    finally:
        document.close()

    if not result.lstrip().startswith(b"%PDF-"):
        return None
    return result
