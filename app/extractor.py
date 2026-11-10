"""Extraction du texte et des Bounding Boxes via `pdfminer.six`."""

from __future__ import annotations

import io
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

from app.schemas import ExtractedPage, TextBlock


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
    """
    laparams = LAParams(
        line_margin=0.55,
        char_margin=1.5,
        word_margin=0.1,
        boxes_flow=0.5,
        all_texts=True,
    )

    extracted_pages: list[ExtractedPage] = []

    for page_index, lt_page in enumerate(
        extract_pages(io.BytesIO(pdf_bytes), laparams=laparams)
    ):
        blocks: list[TextBlock] = []

        for container in _iter_text_containers(lt_page):
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
