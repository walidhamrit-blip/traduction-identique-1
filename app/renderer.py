"""Génération du calque de traduction via `reportlab` et fusion avec l'original via `pypdf`."""

from __future__ import annotations

import io
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.arabic_utils import format_arabic_text, reshape_arabic
from app.config import PROJECT_ROOT
from app.errors import FontUnavailableError
from app.fonts import LatinOrArabic, ensure_family_registered, get_font_family
from app.schemas import (
    BlockEdit,
    ExtractedPage,
    StyleOptions,
    TargetLang,
    TextAlign,
    TextBlock,
    VerticalAlign,
    block_id_of,
)

ASSETS_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
AMIRI_FONT_PATH = ASSETS_FONTS_DIR / "Amiri-Regular.ttf"
LATIN_FONT_PATH = ASSETS_FONTS_DIR / "DejaVuSans.ttf"

# Chemins de repli : selon le mode de bundling serverless, le répertoire de
# travail peut être la racine du projet ou /var/task.
_FALLBACK_FONT_DIRS = (
    PROJECT_ROOT / "assets" / "fonts",
    Path.cwd() / "assets" / "fonts",
    Path("/var/task/assets/fonts"),
    Path("/var/task/user/assets/fonts"),
)

_FALLBACK_LATIN_FONTS = (
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/dejavu/DejaVuSans.ttf"),
)

ARABIC_FONT_NAME = "Amiri"
LATIN_FONT_NAME = "DejaVuSans"

_FONTS_REGISTERED = False


def _resolve_font_path(filename: str) -> Path | None:
    """Cherche un TTF dans assets/fonts puis dans les emplacements de repli serverless."""
    candidates = [ASSETS_FONTS_DIR / filename, *(d / filename for d in _FALLBACK_FONT_DIRS)]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def fonts_diagnostics() -> dict[str, object]:
    """État des polices TTF : utile pour diagnostiquer un 500 en production (`/api/diag`)."""
    amiri = _resolve_font_path(AMIRI_FONT_PATH.name)
    latin = _resolve_font_path(LATIN_FONT_PATH.name) or next(
        (p for p in _FALLBACK_LATIN_FONTS if p.is_file()), None
    )
    return {
        "project_root": str(PROJECT_ROOT),
        "cwd": str(Path.cwd()),
        "assets_fonts_dir": str(ASSETS_FONTS_DIR),
        "assets_fonts_dir_exists": ASSETS_FONTS_DIR.is_dir(),
        "amiri": {
            "expected": str(AMIRI_FONT_PATH),
            "found": str(amiri) if amiri else None,
            "ok": amiri is not None,
        },
        "latin": {
            "expected": str(LATIN_FONT_PATH),
            "found": str(latin) if latin else None,
            "ok": latin is not None,
        },
        "registered": _FONTS_REGISTERED,
    }


def ensure_fonts_registered() -> None:
    """Enregistre les polices TTF Unicode (`Amiri` pour l'arabe, `DejaVuSans` pour en/fr).

    Lève `FontUnavailableError` (HTTP 503 + message explicite) si le bundle déployé
    ne contient pas les polices, au lieu d'une `FileNotFoundError` transformée en 500.
    """
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return

    registered = set(pdfmetrics.getRegisteredFontNames())

    amiri_path = _resolve_font_path(AMIRI_FONT_PATH.name)
    if amiri_path is None:
        diag = fonts_diagnostics()
        raise FontUnavailableError(
            "Police TTF arabe 'Amiri-Regular.ttf' introuvable dans le bundle déployé.",
            hint=(
                "Chemin attendu : "
                f"{diag['amiri']['expected']}. Cause la plus fréquente sur Vercel : "
                "les polices ne sont pas incluses dans le bundle de la fonction. "
                "Ajoutez la section 'includeFiles' de vercel.json (assets/**) "
                "ou placez les polices dans un dossier importé par l'application."
            ),
        )
    if ARABIC_FONT_NAME not in registered:
        pdfmetrics.registerFont(TTFont(ARABIC_FONT_NAME, str(amiri_path)))

    latin_path = _resolve_font_path(LATIN_FONT_PATH.name) or next(
        (p for p in _FALLBACK_LATIN_FONTS if p.is_file()), None
    )
    if latin_path is not None and LATIN_FONT_NAME not in registered:
        try:
            pdfmetrics.registerFont(TTFont(LATIN_FONT_NAME, str(latin_path)))
        except Exception:  # pragma: no cover - police de repli optionnelle
            pass

    _FONTS_REGISTERED = True


def _select_font_name(
    target_lang: TargetLang,
    style: StyleOptions | None = None,
    *,
    bold: bool = False,
) -> str:
    """Retourne le nom ReportLab de la police choisie (ou celle par défaut du script)."""
    ensure_fonts_registered()
    is_arabic = target_lang == TargetLang.AR
    family = get_font_family(style.font_id if style else None, is_arabic=is_arabic)
    try:
        return ensure_family_registered(family, bold=bold)
    except Exception:  # pragma: no cover - police de repli
        if is_arabic and ARABIC_FONT_NAME in pdfmetrics.getRegisteredFontNames():
            return ARABIC_FONT_NAME
        return LATIN_FONT_NAME if LATIN_FONT_NAME in pdfmetrics.getRegisteredFontNames() else "Helvetica"


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


def _fit_text_with_style(
    text: str,
    block: TextBlock,
    font_name: str,
    is_arabic: bool,
    style: StyleOptions | None = None,
    *,
    allow_shrink: bool = True,
) -> tuple[list[str], float, float]:
    """Ajuste la taille et le retour à la ligne en respectant les options utilisateur.

    - `style.font_size` (taille imposée) : aucune réduction automatique agressive,
      l'utilisateur garde la main.
    - `allow_shrink=False` (mode éditeur) : la taille demandée est écrite telle quelle
      et seul le retour à la ligne s'adapte : ce que l'utilisateur voit est ce qu'il obtient.
    """
    style = style or StyleOptions()
    box_width = max(block.width, 8.0)
    line_spacing_factor = style.line_spacing if is_arabic else min(style.line_spacing, 1.18)

    target = style.resolved_font_size(block.font_size)
    max_size = 42.0 if style.font_size else target

    if not allow_shrink:
        lines = _wrap_logical_text(text, box_width + 2.0, font_name, target, is_arabic)
        return lines, target, target * line_spacing_factor

    usable_width = box_width + 2.0
    usable_height = max(block.height, 6.0) + 3.0

    size = min(target, max_size)
    best_lines = [text]
    best_size = max(style.min_font_size, min(size, 8.0))

    while size >= style.min_font_size - 1e-6:
        lines = _wrap_logical_text(text, usable_width, font_name, size, is_arabic)
        max_w = max(
            (_measure_logical_line_width(ln, font_name, size, is_arabic) for ln in lines),
            default=0.0,
        )
        needed_h = size * 0.96 + max(0, len(lines) - 1) * (size * line_spacing_factor)
        single_line_ok = block.line_count > 1 or len(lines) == 1 or size <= target * 0.68

        if max_w <= usable_width + 1.5 and needed_h <= usable_height and single_line_ok:
            best_lines = lines
            best_size = size
            break

        best_lines = lines
        best_size = size
        size = round(size - 0.5, 2)

    return best_lines, best_size, best_size * line_spacing_factor


def _hex_to_rgb(color: str, default: tuple[float, float, float] = (0.08, 0.09, 0.12)) -> tuple[float, float, float]:
    """Convertit '#RRGGBB' en triplet ReportLab, avec repli silencieux."""
    value = (color or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        return default
    try:
        return (
            int(value[0:2], 16) / 255.0,
            int(value[2:4], 16) / 255.0,
            int(value[4:6], 16) / 255.0,
        )
    except ValueError:
        return default


def _horizontal_anchor(
    block: TextBlock,
    line_width: float,
    align: TextAlign,
    is_arabic: bool,
    offset_x: float,
) -> float:
    """Calcule la position X de départ de la ligne (alignement gauche/centre/droite)."""
    x0 = block.x0 + offset_x
    x1 = block.x1 + offset_x
    if align == TextAlign.CENTER:
        return (x0 + x1) / 2.0 - line_width / 2.0
    if align == TextAlign.RIGHT:
        return x1 - line_width
    if align == TextAlign.JUSTIFY:
        # Pas de justification native en PDF : on aligne selon le sens de lecture
        return x1 - line_width if is_arabic else x0
    return x0


def _vertical_start(
    block: TextBlock, font_size: float, line_count: int, line_height: float,
    align: VerticalAlign, offset_y: float,
) -> float:
    """Retourne l'ordonnée de la première ligne de base selon l'alignement vertical."""
    box_bottom = block.y0 + offset_y
    box_top = block.y1 + offset_y
    block_height = max(box_top - box_bottom, font_size)
    total_height = font_size * 0.74 + (line_count - 1) * line_height

    if align == VerticalAlign.TOP:
        top_baseline = box_top - font_size * 0.76
    elif align == VerticalAlign.BOTTOM:
        top_baseline = box_bottom + font_size * 0.22 + (line_count - 1) * line_height
    else:  # MIDDLE
        slack = max(0.0, block_height - total_height - font_size * 0.18)
        top_baseline = box_top - font_size * 0.76 - slack * 0.4
    return max(top_baseline, box_bottom + font_size * 0.12)


def build_overlay_pdf(
    pages: list[ExtractedPage],
    translated_blocks_per_page: list[list[tuple[TextBlock, str]]],
    target_lang: TargetLang,
    mask_padding: float = 0.8,
    style: StyleOptions | None = None,
    edits: dict[str, BlockEdit] | None = None,
    *,
    page_boxes: list[tuple[float, float]] | None = None,
    page_colors: list[list[object]] | None = None,
    with_masks: bool = True,
) -> bytes:
    """Construit le calque ReportLab à appliquer sur le PDF original.

    Applique les options choisies dans l'éditeur avant export :
    police, taille, alignement horizontal/vertical, couleur, interligne, décalages
    et corrections bloc par bloc (`edits`).

    Seuls les blocs réellement traduits (ou déplacés / corrigés) sont masqués et
    réécrits : les codes, références et textes conservés gardent leur place exacte.
    """
    style = style or StyleOptions()
    edits = edits or {}
    is_arabic = target_lang == TargetLang.AR
    font_name = _select_font_name(target_lang, style)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    text_color = _hex_to_rgb(style.color)

    def _resolve(
        bid: str, block: TextBlock, translated_text: str
    ) -> tuple[TextBlock, str, BlockEdit | None]:
        """Applique les corrections d'un bloc (texte, position, taille)."""
        edit = edits.get(bid)
        if edit is not None:
            if edit.hidden:
                return block, "", edit
            text = edit.text if edit.text is not None else translated_text
            if edit.x0 is not None or edit.y0 is not None:
                dx = (edit.x0 - block.x0) if edit.x0 is not None else 0.0
                dy = (edit.y0 - block.y0) if edit.y0 is not None else 0.0
                shifted = TextBlock(
                    text=block.text,
                    x0=block.x0 + dx,
                    y0=block.y0 + dy,
                    x1=block.x1 + dx,
                    y1=block.y1 + dy,
                    font_size=edit.font_size or block.font_size,
                    line_count=block.line_count,
                    baseline_y=(block.baseline_y + dy) if block.baseline_y is not None else None,
                )
                return shifted, text, edit
            if edit.font_size:
                resized = TextBlock(
                    text=block.text,
                    x0=block.x0, y0=block.y0, x1=block.x1, y1=block.y1,
                    font_size=edit.font_size, line_count=block.line_count,
                    baseline_y=block.baseline_y,
                )
                return resized, text, edit
            return block, text, edit
        return block, translated_text, None

    for page_index, (page, page_items) in enumerate(
        zip(pages, translated_blocks_per_page, strict=True)
    ):
        # Taille réelle de la page dans le PDF d'origine (cropbox/rotation prises en compte)
        if page_boxes and page_index < len(page_boxes):
            real_width, real_height = page_boxes[page_index]
            width = real_width or page.width
            height = real_height or page.height
        else:
            width, height = page.width, page.height
        c.setPageSize((width, height))

        prepared: list[tuple[TextBlock, str, bool]] = []
        for position, (block, translated_text) in enumerate(page_items):
            bid = block_id_of(block, page_index, position)
            resolved_block, resolved_text, edit = _resolve(bid, block, translated_text)
            if edit is not None and edit.hidden:
                continue
            original = " ".join(block.text.split())
            candidate = " ".join(resolved_text.split())
            is_edited = edit is not None and (edit.text is not None or edit.x0 is not None
                                              or edit.y0 is not None or edit.font_size)
            # Masqué + réécrit uniquement si le texte change réellement
            needs_replacement = bool(candidate) and (
                is_edited or candidate.casefold() != original.casefold()
            )
            prepared.append((resolved_block, candidate, needs_replacement))

        # Couleurs réelles observées sur la page (fond de cellule, encre d'origine).
        observed = page_colors[page_index] if page_colors and page_index < len(page_colors) else []

        # --- Passe 1 : masques de la couleur EXACTE du fond original ---
        # Peindre en blanc créait des trous visibles sur les tableaux gris : on
        # reprend la couleur mesurée dans le rendu de la page.
        c.saveState()
        for position, (block, _, needs_replacement) in enumerate(prepared):
            if not needs_replacement or not with_masks:
                continue
            colors = observed[position] if position < len(observed) else None
            background = _background_for(colors, style)
            if background is None:
                continue  # fond non uniforme : on ne masque pas, l'original reste lisible
            c.setFillColorRGB(*background)
            rx0 = max(0.0, block.x0 + style.offset_x - mask_padding)
            ry0 = max(0.0, block.y0 + style.offset_y - mask_padding)
            rx1 = min(width, block.x1 + style.offset_x + mask_padding)
            ry1 = min(height, block.y1 + style.offset_y + mask_padding)
            c.rect(rx0, ry0, max(0.5, rx1 - rx0), max(0.5, ry1 - ry0), stroke=0, fill=1)
        c.restoreState()

        # --- Passe 2 : écriture du texte traduit stylé ---
        c.saveState()
        for position, (block, text, needs_replacement) in enumerate(prepared):
            if not needs_replacement or not text:
                continue
            colors = observed[position] if position < len(observed) else None
            c.setFillColorRGB(*_text_color_for(colors, style))

            lines, font_size, line_height = _fit_text_with_style(
                text, block, font_name, is_arabic, style
            )
            c.setFont(font_name, font_size)
            first_baseline = _vertical_start(
                block, font_size, len(lines), line_height, style.vertical_align, style.offset_y
            )

            for index, logical_line in enumerate(lines):
                baseline_y = first_baseline - index * line_height
                line_text = format_arabic_text(logical_line) if is_arabic else logical_line
                line_width = pdfmetrics.stringWidth(
                    reshape_arabic(logical_line) if is_arabic else logical_line,
                    font_name,
                    font_size,
                )
                start_x = _horizontal_anchor(block, line_width, style.align, is_arabic, style.offset_x)
                if style.letter_spacing:
                    # Letter-spacing manuel : un caractère à la fois
                    cursor = start_x
                    for char in line_text:
                        c.drawString(cursor, baseline_y, char)
                        cursor += pdfmetrics.stringWidth(char, font_name, font_size) + style.letter_spacing
                else:
                    c.drawString(start_x, baseline_y, line_text)
        c.restoreState()

        c.showPage()

    c.save()
    return buffer.getvalue()


def merge_overlay_with_pymupdf(original_pdf_bytes: bytes, overlay_pdf_bytes: bytes) -> bytes:
    """Fusionne le calque sur l'original via PyMuPDF (voie de secours de `merge_overlay_with_original`).

    `pypdf` reste la voie principale ; cette implémentation indépendante évite qu'un
    document particulier (structure de page exotique, flux abîmé...) bloque tout
    l'export. Si PyMuPDF n'est pas disponible, `RuntimeError` est levée.
    """
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - dépend du déploiement
        raise RuntimeError("PyMuPDF indisponible pour la fusion de secours.") from exc

    document = pymupdf.open(stream=original_pdf_bytes, filetype="pdf")
    overlay = pymupdf.open(stream=overlay_pdf_bytes, filetype="pdf")
    try:
        for index in range(document.page_count):
            if index >= overlay.page_count:
                break
            page = document[index]
            page.show_pdf_page(page.rect, overlay, index, overlay=True)
        return document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()
        overlay.close()


def _background_for(colors: object, style: StyleOptions) -> tuple[float, float, float] | None:
    """Couleur du masque : celle du fond réel, sinon le blanc configuré.

    Retourne ``None`` quand le fond n'est pas uniforme (photo, dégradé) et que
    l'utilisateur n'a pas imposé de couleur : mieux vaut ne rien masquer que
    peindre un rectangle visible.
    """
    background = getattr(colors, "background", None) if colors else None
    if background is not None:
        return tuple(background)  # type: ignore[return-value]
    if colors is not None and style.color:
        # Page analysée mais sans fond uniforme : on n'invente pas de couleur.
        return None
    return (1.0, 1.0, 1.0)


def _text_color_for(colors: object, style: StyleOptions) -> tuple[float, float, float]:
    """Couleur du texte traduit : celle d'origine, sauf choix explicite de l'utilisateur.

    Un texte blanc sur fond gris foncé doit le rester, sinon il devient illisible.
    La préférence de l'utilisateur (éditeur) reste prioritaire si elle a été changée.
    """
    chosen = _hex_to_rgb(style.color)
    if chosen != _hex_to_rgb("#14171E"):
        return chosen  # l'utilisateur a choisi une couleur : on la respecte
    ink = getattr(colors, "ink", None) if colors else None
    if ink is not None:
        return tuple(ink)  # type: ignore[return-value]
    return chosen


def _resolve_font_file(font_name: str, target_lang: TargetLang) -> Path | None:
    """Retrouve le fichier TTF réellement utilisé par une police ReportLab enregistrée."""
    try:
        font = pdfmetrics.getFont(font_name)
        filename = getattr(getattr(font, "face", None), "filename", None)
        if filename and Path(filename).is_file():
            return Path(filename)
    except Exception:  # pragma: no cover - police exotique
        pass
    if target_lang == TargetLang.AR:
        return _resolve_font_path(AMIRI_FONT_PATH.name)
    return _resolve_font_path(LATIN_FONT_PATH.name) or next(
        (p for p in _FALLBACK_LATIN_FONTS if p.is_file()), None
    )


def _fallback_lines(
    text: str,
    block: TextBlock,
    font_key: str,
    writer: "PdfWriter",
    is_arabic: bool,
    style: StyleOptions,
) -> tuple[list[str], float, float]:
    """Découpe et dimensionne le texte en s'appuyant sur les métriques du PDF de secours."""
    box_width = max(block.width, 8.0)
    line_spacing_factor = style.line_spacing if is_arabic else min(style.line_spacing, 1.18)
    target = style.resolved_font_size(block.font_size)

    def width_of(sample: str, size: float) -> float:
        shaped = reshape_arabic(sample) if is_arabic else sample
        return writer.font_width(font_key, shaped, size)

    lines = [text]
    size = target
    while size >= style.min_font_size - 1e-6:
        lines = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if current and width_of(candidate, size) > box_width + 2.0:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        widest = max((width_of(line, size) for line in lines), default=0.0)
        needed_h = size * 0.96 + max(0, len(lines) - 1) * size * line_spacing_factor
        fits = widest <= box_width + 2.0 and needed_h <= max(block.height, 6.0) + 3.0
        single_line_ok = block.line_count > 1 or len(lines) == 1 or size <= target * 0.68
        if fits and single_line_ok:
            break
        size = round(size - 0.5, 2)
    size = max(size, style.min_font_size)
    return lines, size, size * line_spacing_factor


def build_overlay_pdf_fallback(
    pages: list[ExtractedPage],
    translated_blocks_per_page: list[list[tuple[TextBlock, str]]],
    target_lang: TargetLang,
    mask_padding: float = 0.8,
    style: StyleOptions | None = None,
    edits: dict[str, BlockEdit] | None = None,
    *,
    page_boxes: list[tuple[float, float]] | None = None,
    page_colors: list[list[object]] | None = None,
    with_masks: bool = True,
) -> bytes:
    """Calque de secours, écrit sans ReportLab (voir `app.modules pdf_writer`).

    Même contrat que `build_overlay_pdf` (mêmes paramètres, même résultat attendu :
    un PDF d'une page par page du document original), mais aucune dépendance aux
    `assert` internes de ReportLab : un PDF exotique ne peut donc plus faire échouer
    tout l'export. Le rendu est volontairement simple : masques blancs + texte aux
    coordonnées d'origine.
    """
    from app.pdf_writer import GlyphRun, PageContent, PdfWriter

    style = style or StyleOptions()
    edits = edits or {}
    is_arabic = target_lang == TargetLang.AR
    font_name = _select_font_name(target_lang, style)
    font_path = _resolve_font_file(font_name, target_lang)
    if font_path is None:
        raise FontUnavailableError(
            "Aucune police TTF exploitable pour le rendu de secours.",
            hint="Vérifiez que assets/fonts contient Amiri-Regular.ttf / DejaVuSans.ttf.",
        )

    writer = PdfWriter()
    font_key = "main"
    writer.register_font(font_key, font_path)
    text_color = _hex_to_rgb(style.color)

    for page_index, (page, page_items) in enumerate(
        zip(pages, translated_blocks_per_page, strict=True)
    ):
        if page_boxes and page_index < len(page_boxes):
            real_width, real_height = page_boxes[page_index]
            width = real_width or page.width
            height = real_height or page.height
        else:
            width, height = page.width, page.height

        content = PageContent(width=max(1.0, float(width)), height=max(1.0, float(height)))
        observed = page_colors[page_index] if page_colors and page_index < len(page_colors) else []

        for position, (block, translated_text) in enumerate(page_items):
            bid = block_id_of(block, page_index, position)
            edit = edits.get(bid)
            if edit is not None and edit.hidden:
                continue
            target_block = block
            text = translated_text
            if edit is not None:
                text = edit.text if edit.text is not None else translated_text
                dx = (edit.x0 - block.x0) if edit.x0 is not None else 0.0
                dy = (edit.y0 - block.y0) if edit.y0 is not None else 0.0
                if dx or dy or edit.font_size:
                    target_block = TextBlock(
                        text=block.text,
                        x0=block.x0 + dx,
                        y0=block.y0 + dy,
                        x1=block.x1 + dx,
                        y1=block.y1 + dy,
                        font_size=edit.font_size or block.font_size,
                        line_count=block.line_count,
                        baseline_y=(block.baseline_y + dy) if block.baseline_y is not None else None,
                    )

            candidate = " ".join(text.split())
            original = " ".join(block.text.split())
            is_edited = edit is not None and (
                edit.text is not None or edit.x0 is not None
                or edit.y0 is not None or edit.font_size
            )
            if not candidate or not (is_edited or candidate.casefold() != original.casefold()):
                continue

            x0 = max(0.0, target_block.x0 + style.offset_x - mask_padding)
            y0 = max(0.0, target_block.y0 + style.offset_y - mask_padding)
            x1 = min(width, target_block.x1 + style.offset_x + mask_padding)
            y1 = min(height, target_block.y1 + style.offset_y + mask_padding)
            colors = observed[position] if position < len(observed) else None
            if with_masks:
                content.masks.append((x0, y0, x1, y1, _background_for(colors, style)))

            lines, font_size, line_height = _fallback_lines(
                candidate, target_block, font_key, writer, is_arabic, style
            )
            first_baseline = _vertical_start(
                target_block, font_size, len(lines), line_height,
                style.vertical_align, style.offset_y,
            )
            for index, logical_line in enumerate(lines):
                baseline_y = first_baseline - index * line_height
                line_text = format_arabic_text(logical_line) if is_arabic else logical_line
                line_width = writer.font_width(font_key, line_text, font_size)
                start_x = _horizontal_anchor(
                    target_block, line_width, style.align, is_arabic, style.offset_x
                )
                content.runs.append(
                    GlyphRun(
                        text=line_text,
                        x=max(0.0, start_x),
                        y=baseline_y,
                        font_size=font_size,
                        font_key=font_key,
                        color=_text_color_for(colors, style),
                    )
                )

        if not content.runs:
            # Page sans texte à réécrire : conserver un contenu vide mais valide.
            content.runs = []
        writer.add_page(content)

    return writer.build()


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
