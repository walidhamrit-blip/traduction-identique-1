"""Génération du calque de traduction via `reportlab` et fusion avec l'original via `pypdf`."""

from __future__ import annotations

import io
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.arabic_utils import format_arabic_text, reshape_arabic
from app.schemas import ExtractedPage, TargetLang, TextBlock

ASSETS_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
AMIRI_FONT_PATH = ASSETS_FONTS_DIR / "Amiri-Regular.ttf"
LATIN_FONT_PATH = ASSETS_FONTS_DIR / "DejaVuSans.ttf"

ARABIC_FONT_NAME = "Amiri"
LATIN_FONT_NAME = "DejaVuSans"

_FONTS_REGISTERED = False


def ensure_fonts_registered() -> None:
    """Enregistre les polices TTF Unicode (`Amiri` pour l'arabe, `DejaVuSans` pour en/fr)."""
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    if not AMIRI_FONT_PATH.is_file():
        raise FileNotFoundError(
            f"Police TTF arabe introuvable : {AMIRI_FONT_PATH}. "
            "Assurez-vous que Amiri-Regular.ttf est présente dans assets/fonts/."
        )
    pdfmetrics.registerFont(TTFont(ARABIC_FONT_NAME, str(AMIRI_FONT_PATH)))

    if LATIN_FONT_PATH.is_file():
        pdfmetrics.registerFont(TTFont(LATIN_FONT_NAME, str(LATIN_FONT_PATH)))
    elif Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").is_file():
        pdfmetrics.registerFont(
            TTFont(LATIN_FONT_NAME, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        )

    _FONTS_REGISTERED = True


def _select_font_name(target_lang: TargetLang) -> str:
    """Retourne le nom de police ReportLab adapté à la langue cible."""
    ensure_fonts_registered()
    if target_lang == TargetLang.AR:
        return ARABIC_FONT_NAME
    if LATIN_FONT_NAME in pdfmetrics.getRegisteredFontNames():
        return LATIN_FONT_NAME
    return "Helvetica"


def _measure_logical_line_width(
    text: str, font_name: str, font_size: float, is_arabic: bool
) -> float:
    """Mesure la largeur en points PDF d'une ligne logique (avec reshaping préalable pour l'arabe)."""
    sample = reshape_arabic(text) if is_arabic else text
    return float(pdfmetrics.stringWidth(sample, font_name, font_size))


def _wrap_logical_text(
    text: str,
    max_width: float,
    font_name: str,
    font_size: float,
    is_arabic: bool,
) -> list[str]:
    """Découpe le texte dans son ordre logique en lignes tenant dans `max_width`.

    Important pour l'arabe (RTL) : le découpage doit impérativement s'effectuer
    AVANT `bidi.algorithm.get_display`, sinon la dernière ligne de la phrase se
    retrouverait visuellement en haut du paragraphe.
    """
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current_line = words[0]

    for word in words[1:]:
        candidate = f"{current_line} {word}"
        if _measure_logical_line_width(candidate, font_name, font_size, is_arabic) <= max_width:
            current_line = candidate
        else:
            lines.append(current_line)
            current_line = word

    lines.append(current_line)
    return lines


def _fit_text_in_bbox(
    text: str,
    block: TextBlock,
    font_name: str,
    is_arabic: bool,
    min_font_size: float = 5.5,
) -> tuple[list[str], float, float]:
    """Ajuste dynamiquement la taille de police et le retour à la ligne dans la Bounding Box."""
    box_width = max(block.width, 8.0)
    box_height = max(block.height, 6.0)
    line_spacing_factor = 1.26 if is_arabic else 1.18

    initial_size = min(max(block.font_size, min_font_size), 42.0)
    # Légère tolérance horizontale/verticale pour absorber la différence de métriques de police
    usable_width = box_width + 2.0
    usable_height = box_height + 3.0

    size = initial_size
    best_lines = [text]
    best_size = min_font_size

    while size >= min_font_size - 1e-6:
        lines = _wrap_logical_text(text, usable_width, font_name, size, is_arabic)
        max_w = max(
            (_measure_logical_line_width(ln, font_name, size, is_arabic) for ln in lines),
            default=0.0,
        )
        # Hauteur optique occupée par N lignes (cap-height ~0.76*size + interlignes + descente ~0.22*size)
        needed_h = size * 0.96 + max(0, len(lines) - 1) * (size * line_spacing_factor)

        # Si le bloc source était sur 1 ligne, on privilégie 1 seule ligne tant que size >= 68% de l'original
        single_line_ok = block.line_count > 1 or len(lines) == 1 or size <= initial_size * 0.68

        if max_w <= usable_width + 1.5 and needed_h <= usable_height and single_line_ok:
            best_lines = lines
            best_size = size
            break

        best_lines = lines
        best_size = size
        size = round(size - 0.5, 2)

    line_height = best_size * line_spacing_factor
    return best_lines, best_size, line_height


def build_overlay_pdf(
    pages: list[ExtractedPage],
    translated_blocks_per_page: list[list[tuple[TextBlock, str]]],
    target_lang: TargetLang,
    mask_padding: float = 0.8,
) -> bytes:
    """Construit avec `reportlab` un PDF calque contenant :
    1. Un rectangle blanc opaque aux coordonnées exactes `(x0, y0, x1, y1)` de chaque bloc.
    2. Le texte traduit positionné par-dessus (aligné à droite en RTL avec `Amiri` si `ar`).
    """
    font_name = _select_font_name(target_lang)
    is_arabic = target_lang == TargetLang.AR

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)

    for page, page_items in zip(pages, translated_blocks_per_page, strict=True):
        c.setPageSize((page.width, page.height))

        # Passe 1 : Masques blancs opaques aux coordonnées exactes de toutes les Bounding Boxes
        c.saveState()
        c.setFillColorRGB(1.0, 1.0, 1.0)
        c.setStrokeColorRGB(1.0, 1.0, 1.0)
        for block, translated_text in page_items:
            if not translated_text:
                continue
            rx0 = max(0.0, block.x0 - mask_padding)
            ry0 = max(0.0, block.y0 - mask_padding)
            rx1 = min(page.width, block.x1 + mask_padding)
            ry1 = min(page.height, block.y1 + mask_padding)
            c.rect(rx0, ry0, max(0.5, rx1 - rx0), max(0.5, ry1 - ry0), stroke=0, fill=1)
        c.restoreState()

        # Passe 2 : Écriture du texte traduit par-dessus les fonds blancs
        c.saveState()
        c.setFillColorRGB(0.08, 0.09, 0.12)
        for block, translated_text in page_items:
            if not translated_text:
                continue

            logical_lines, font_size, line_height = _fit_text_in_bbox(
                translated_text, block, font_name, is_arabic
            )
            c.setFont(font_name, font_size)

            if len(logical_lines) == 1:
                # Centrage vertical précis de la ligne de base dans [y0, y1]
                # Cap-height visuel moyen ~ 0.72 * font_size, descente ~ 0.20 * font_size
                baseline_y = block.y0 + max(
                    font_size * 0.18, (block.height - font_size * 0.54) / 2.0
                )
                line_text = (
                    format_arabic_text(logical_lines[0])
                    if is_arabic
                    else logical_lines[0]
                )
                if is_arabic:
                    c.drawRightString(block.x1, baseline_y, line_text)
                else:
                    c.drawString(block.x0, baseline_y, line_text)
            else:
                total_text_height = font_size * 0.74 + (len(logical_lines) - 1) * line_height
                vertical_slack = max(0.0, block.height - total_text_height - font_size * 0.18)
                first_baseline_y = block.y1 - font_size * 0.76 - (vertical_slack * 0.3)

                for idx, logical_line in enumerate(logical_lines):
                    baseline_y = first_baseline_y - idx * line_height
                    line_text = (
                        format_arabic_text(logical_line)
                        if is_arabic
                        else logical_line
                    )
                    if is_arabic:
                        c.drawRightString(block.x1, baseline_y, line_text)
                    else:
                        c.drawString(block.x0, baseline_y, line_text)
        c.restoreState()

        c.showPage()

    c.save()
    return buffer.getvalue()


def merge_overlay_with_original(original_pdf_bytes: bytes, overlay_pdf_bytes: bytes) -> bytes:
    """Fusionne page par page le calque ReportLab par-dessus le PDF original via `pypdf`."""
    original_reader = PdfReader(io.BytesIO(original_pdf_bytes))
    overlay_reader = PdfReader(io.BytesIO(overlay_pdf_bytes))
    writer = PdfWriter()

    for idx, original_page in enumerate(original_reader.pages):
        # Important (pypdf >= 7.0) : rattacher d'abord la page au PdfWriter avant merge_page()
        writer_page = writer.add_page(original_page)

        if idx < len(overlay_reader.pages):
            overlay_page = overlay_reader.pages[idx]
            mb_left = float(writer_page.mediabox.left)
            mb_bottom = float(writer_page.mediabox.bottom)

            if abs(mb_left) > 1e-3 or abs(mb_bottom) > 1e-3:
                writer_page.merge_translated_page(
                    overlay_page, tx=mb_left, ty=mb_bottom
                )
            else:
                writer_page.merge_page(overlay_page)

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
