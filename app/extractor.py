"""Extraction du texte et des Bounding Boxes via `pdfminer.six`."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator

from pdfminer.high_level import extract_pages
from pdfminer.layout import (
    LAParams,
    LTChar,
    LTComponent,
    LTFigure,
    LTPage,
    LTTextContainer,
    LTTextLine,
)
from pdfminer.pdfdocument import PDFPasswordIncorrect
from pdfminer.pdfparser import PDFSyntaxError
from pypdf.errors import DependencyError, PdfReadError, PdfStreamError

from app.config import MAX_BLOCKS_PER_PAGE, MAX_PAGES
from app.errors import EncryptedPdfError, InvalidPdfError, TooManyPagesError
from app.schemas import ExtractedPage, TextBlock

logger = logging.getLogger(__name__)

# Signatures d'erreur indiquant un PDF chiffré, quel que soit le lecteur
_ENCRYPTED_MARKERS = (
    "password",
    "encrypted",
    "crypt",
    "decrypt",
    "aes",
)


def _looks_encrypted(exc: BaseException) -> bool:
    """Heuristique : pypdf/pdfminer signalent un chiffrement de façons très variées."""
    if isinstance(exc, (PDFPasswordIncorrect,)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in _ENCRYPTED_MARKERS)


def _probe_pdf(pdf_bytes: bytes) -> None:
    """Ouvre le PDF avec pypdf pour détecter tôt chiffrement et corruption.

    pdfminer, lui, échoue souvent avec un message interne peu lisible
    (`PDFSyntaxError`, `KeyError`…) qui produisait un HTTP 500 générique.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        if reader.is_encrypted:
            try:
                unlocked = reader.decrypt("")
            except Exception:  # pragma: no cover - dépend de la version pypdf
                unlocked = 0
            if not unlocked:
                raise EncryptedPdfError(
                    "Le PDF est chiffré ou protégé par un mot de passe : "
                    "son contenu texte ne peut pas être extrait."
                )
        page_count = len(reader.pages)
    except (EncryptedPdfError, TooManyPagesError):
        raise
    except (PdfReadError, PdfStreamError, DependencyError, ValueError, KeyError) as exc:
        if _looks_encrypted(exc):
            raise EncryptedPdfError(
                f"Le PDF semble chiffré et ne peut pas être ouvert ({exc})."
            ) from exc
        raise InvalidPdfError(f"Le PDF n'a pas pu être ouvert : {exc}") from exc
    except Exception as exc:  # défense en profondeur : jamais de 500 opaque
        if _looks_encrypted(exc):
            raise EncryptedPdfError(
                f"Le PDF semble chiffré et ne peut pas être ouvert ({exc})."
            ) from exc
        raise InvalidPdfError(f"Le PDF n'a pas pu être ouvert : {exc}") from exc

    if page_count == 0:
        raise InvalidPdfError("Le PDF ne contient aucune page.")
    if page_count > MAX_PAGES:
        raise TooManyPagesError(
            f"Le PDF contient {page_count} pages, au-delà de la limite de "
            f"{MAX_PAGES} pages par requête."
        )


def _extract_median_font_size(element: LTTextContainer | LTTextLine) -> float:
    """Calcule la taille médiane de police (en pt) à partir des glyphes `LTChar`."""
    sizes: list[float] = []
    if isinstance(element, LTTextLine):
        sizes = [ch.size for ch in element if isinstance(ch, LTChar) and ch.size > 0]
    else:
        for child in element:
            if isinstance(child, LTTextLine):
                sizes.extend(
                    ch.size for ch in child if isinstance(ch, LTChar) and ch.size > 0
                )
            elif isinstance(child, LTChar) and child.size > 0:
                sizes.append(child.size)

    if sizes:
        sizes.sort()
        return round(float(sizes[len(sizes) // 2]), 2)
    return round(max(6.0, min(24.0, element.height * 0.75)), 2)


def _baseline_y(line: LTTextLine) -> float | None:
    """Estime la ligne de base d'une ligne pdfminer (y0 + profondeur de descente).

    pdfminer ne fournit pas la ligne de base directement ; le bas de la boîte de la
    ligne correspond au point le plus bas des glyphes (descendantes incluses), donc
    légèrement sous la ligne de base. On ajoute une fraction de la hauteur des
    glyphes pour retrouver un alignement visuel correct.
    """
    sizes = [ch.size for ch in line if isinstance(ch, LTChar) and ch.size > 0]
    if not sizes:
        return None
    sizes.sort()
    median = float(sizes[len(sizes) // 2])
    return float(line.y0) + median * 0.20


def _iter_text_containers(component: LTComponent) -> Iterator[LTTextContainer]:
    """Parcourt récursivement l'arbre `pdfminer.six` (y compris `LTFigure`) pour trouver les blocs texte."""
    if isinstance(component, LTTextContainer):
        yield component
        return
    if isinstance(component, (LTPage, LTFigure)) or hasattr(component, "__iter__"):
        for child in component:
            if isinstance(child, LTComponent):
                yield from _iter_text_containers(child)


def _is_flowing_paragraph(lines: list[LTTextLine], container_width: float) -> bool:
    """Détermine si plusieurs `LTTextLine` forment un paragraphe continu ou des lignes distinctes."""
    if len(lines) <= 1 or container_width <= 0:
        return False

    # Un titre suivi d'un sous-titre a des tailles de police différentes -> lignes distinctes
    line_sizes = [_extract_median_font_size(ln) for ln in lines]
    if max(line_sizes) - min(line_sizes) > 1.2:
        return False

    non_last_lines = lines[:-1]
    for line in non_last_lines:
        txt = line.get_text().strip()
        # Les paires "Clé : Valeur" ou puces courtes sont des lignes indépendantes
        if ":" in txt and len(txt) < 48:
            return False
        if line.width < container_width * 0.72:
            return False
    return True


def extract_pdf_layout(pdf_bytes: bytes) -> list[ExtractedPage]:
    """Extrait page par page les blocs de texte et leurs coordonnées exactes `(x0, y0, x1, y1)`.

    Utilise `pdfminer.six` (`extract_pages` + `LAParams`) conformément à la spécification.
    Lève `EncryptedPdfError` / `InvalidPdfError` / `TooManyPagesError` (erreurs HTTP
    explicites) plutôt que de laisser remonter une exception brute.
    """
    _probe_pdf(pdf_bytes)

    laparams = LAParams(
        line_margin=0.55,
        char_margin=1.5,
        word_margin=0.1,
        boxes_flow=0.5,
        all_texts=True,
    )

    extracted_pages: list[ExtractedPage] = []

    def _pages() -> Iterator[object]:
        try:
            yield from extract_pages(io.BytesIO(pdf_bytes), laparams=laparams)
        except Exception as exc:  # remonte l'erreur typée au code appelant
            if _looks_encrypted(exc):
                raise EncryptedPdfError(
                    f"Le PDF est chiffré ou corrompu et ne peut pas être analysé ({exc})."
                ) from exc
            if isinstance(exc, (PDFSyntaxError, PdfReadError, PdfStreamError, KeyError, ValueError)):
                raise InvalidPdfError(
                    f"Le PDF n'a pas pu être analysé par pdfminer.six : {exc}"
                ) from exc
            raise

    for page_index, lt_page in enumerate(_pages()):
        if page_index >= MAX_PAGES:
            raise TooManyPagesError(
                f"Le PDF dépasse la limite de {MAX_PAGES} pages par requête."
            )
        if not isinstance(lt_page, LTPage):  # pragma: no cover - garde-fou
            continue

        blocks: list[TextBlock] = []

        for container in _iter_text_containers(lt_page):
            if len(blocks) >= MAX_BLOCKS_PER_PAGE:
                logger.warning(
                    "Limite de %s blocs atteinte sur la page %s : le reste est ignoré.",
                    MAX_BLOCKS_PER_PAGE,
                    page_index + 1,
                )
                break

            raw_text = container.get_text()
            if not raw_text or not raw_text.strip():
                continue

            text_lines = [
                child
                for child in container
                if isinstance(child, LTTextLine) and child.get_text().strip()
            ]

            if len(text_lines) > 1 and not _is_flowing_paragraph(
                text_lines, container.width
            ):
                # Conserve la Bounding Box individuelle de chaque ligne (tableaux, métadonnées)
                for line in text_lines:
                    if len(blocks) >= MAX_BLOCKS_PER_PAGE:
                        break
                    cleaned = " ".join(line.get_text().split())
                    if not cleaned:
                        continue
                    x0, y0, x1, y1 = line.bbox
                    blocks.append(
                        TextBlock(
                            text=cleaned,
                            x0=float(x0),
                            y0=float(y0),
                            x1=float(x1),
                            y1=float(y1),
                            font_size=_extract_median_font_size(line),
                            line_count=1,
                            baseline_y=_baseline_y(line),
                        )
                    )
            else:
                cleaned = " ".join(raw_text.split())
                if not cleaned:
                    continue
                x0, y0, x1, y1 = container.bbox
                blocks.append(
                    TextBlock(
                        text=cleaned,
                        x0=float(x0),
                        y0=float(y0),
                        x1=float(x1),
                        y1=float(y1),
                        font_size=_extract_median_font_size(container),
                        line_count=max(1, len(text_lines)),
                        baseline_y=_baseline_y(text_lines[0]) if text_lines else None,
                    )
                )

        extracted_pages.append(
            ExtractedPage(
                page_index=page_index,
                width=float(lt_page.width),
                height=float(lt_page.height),
                blocks=blocks,
            )
        )

    return extracted_pages
