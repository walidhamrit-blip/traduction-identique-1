"""Formats d'entrée autres que le PDF : DOCX, TXT, Markdown et images.

Chaque convertisseur produit un **PDF intermédiaire** (mis en page proprement),
qui traverse ensuite le pipeline habituel (extraction → traduction → calque →
export). L'utilisateur conserve donc les mêmes options d'édition.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path

from app.errors import InvalidPdfError, PdfServiceError

logger = logging.getLogger(__name__)


class UnsupportedFormatError(PdfServiceError):
    """Format d'entrée non pris en charge."""

    status_code = 415
    code = "unsupported_input_format"


SUPPORTED_INPUT_FORMATS = {
    "pdf": "PDF (vectoriel, mise en page préservée)",
    "docx": "Word .docx",
    "txt": "Texte brut / Markdown",
    "png": "Image (PNG, JPG, WEBP) — OCR si disponible",
    "jpg": "Image (PNG, JPG, WEBP) — OCR si disponible",
    "jpeg": "Image (PNG, JPG, WEBP) — OCR si disponible",
    "webp": "Image (PNG, JPG, WEBP) — OCR si disponible",
}

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}
TEXT_EXTENSIONS = {"txt", "md", "markdown", "csv", "log"}


def detect_format(filename: str | None, content: bytes) -> str:
    """Détecte le format réel d'après la signature binaire, puis l'extension."""
    head = content[:16]

    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        # ZIP : .docx (mais aussi .xlsx/.pptx) — on tranche avec l'extension
        suffix = Path(filename or "").suffix.lower().lstrip(".")
        if suffix in ("docx", "docm", "dotx"):
            return "docx"
        if suffix:
            return suffix
        return "zip"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")):
        return "image"
    if head[:4] == b"RIFF" and b"WEBP" in content[:16]:
        return "image"

    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in ("docx",):
        return "docx"
    # Le texte brut ne passe que si l'extension le confirme : sinon un fichier
    # corrompu présenté comme ".pdf" serait silencieusement converti en texte.
    if suffix in TEXT_EXTENSIONS:
        return "text"
    if not suffix:
        # Pas d'extension : on accepte un contenu UTF-8 manifestement textuel
        if b"\x00" not in content[:1024]:
            try:
                content.decode("utf-8")
                return "text"
            except UnicodeDecodeError:
                pass
    # Un .pdf dont la signature binaire ne correspond pas est un fichier corrompu :
    # on garde le code d'erreur historique 'invalid_pdf' (400) pour la compatibilité.
    if suffix == "pdf":
        raise InvalidPdfError(
            "Le fichier porte l'extension .pdf mais son contenu n'est pas un PDF valide.",
        )

    raise UnsupportedFormatError(
        "Format de fichier non reconnu ou fichier corrompu.",
        hint="Formats acceptés : PDF, Word (.docx), TXT/Markdown, images PNG/JPG/WEBP.",
    )


# ---------------------------------------------------------------------------
# Texte brut / Markdown / CSV -> PDF
# ---------------------------------------------------------------------------


def text_to_pdf(content: bytes) -> bytes:
    """Met en page un texte brut (A4, marges, police Unicode) et retourne un PDF."""
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from app.fonts import ensure_family_registered, get_font_family

    raw = content.decode("utf-8", "replace")
    # Nettoyage minimal : on conserve les paragraphes, on élimine les BOM
    raw = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")

    family = get_font_family("dejavu", is_arabic=False)
    font_name = ensure_family_registered(family)

    style = ParagraphStyle(
        "body",
        fontName=font_name,
        fontSize=10.5,
        leading=15,
        alignment=TA_LEFT,
        spaceAfter=6,
    )

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="Document",
    )

    story = []
    for block in re.split(r"\n\s*\n", raw):
        block = block.strip()
        if not block:
            continue
        # Échappement du balisage minimal et conservation des sauts simples
        safe = (
            block.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        safe = safe.replace("\n", "<br/>")
        story.append(Paragraph(safe, style))
    if not story:
        story.append(Spacer(1, 12))

    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Word .docx -> PDF
# ---------------------------------------------------------------------------


def _docx_paragraph_text(paragraph) -> str:
    return "".join(run.text for run in paragraph.runs)


def docx_to_pdf(content: bytes) -> bytes:
    """Convertit un .docx en PDF (titres, paragraphes, tableaux, listes)."""
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover - dépendance optionnelle
        raise UnsupportedFormatError(
            "Le support .docx n'est pas installé sur ce déploiement.",
            hint="Ajoutez 'python-docx' dans requirements.txt et redéployez.",
            status_code=503,
            code="docx_support_missing",
        ) from exc

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    from app.fonts import ensure_family_registered, get_font_family

    document = docx.Document(io.BytesIO(content))

    family = get_font_family("dejavu", is_arabic=False)
    regular = ensure_family_registered(family)
    try:
        bold_font = ensure_family_registered(family, bold=True)
    except Exception:  # pragma: no cover
        bold_font = regular

    base = ParagraphStyle("docx-body", fontName=regular, fontSize=10.5, leading=15, spaceAfter=6)
    heading = ParagraphStyle(
        "docx-heading", parent=base, fontName=bold_font, fontSize=14, leading=19,
        spaceBefore=10, spaceAfter=8,
    )

    # Table des styles du document source (nom -> Taille/poids)
    def _style_for(paragraph) -> ParagraphStyle:
        name = (paragraph.style.name or "").lower() if paragraph.style else ""
        if name.startswith("title"):
            return ParagraphStyle("t", parent=heading, fontSize=18, leading=23)
        if name.startswith("heading"):
            level = "".join(ch for ch in name if ch.isdigit()) or "1"
            size = max(11.0, 17.0 - 2 * int(level))
            return ParagraphStyle(f"h{level}", parent=heading, fontSize=size, leading=size * 1.35)
        return base

    story: list = []
    body = document.element.body
    # Parcours dans l'ordre réel du document (paragraphes et tableaux entremêlés)
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            paragraph = docx.text.paragraph.Paragraph(child, document)
            text = _docx_paragraph_text(paragraph).strip()
            if not text:
                story.append(Spacer(1, 6))
                continue
            safe = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            style = _style_for(paragraph)
            if paragraph.style is not None and "list" in (paragraph.style.name or "").lower():
                story.append(ListFlowable([ListItem(Paragraph(safe, style))], bulletType="bullet"))
            else:
                story.append(Paragraph(safe, style))
        elif tag == "tbl":
            table = docx.table.Table(child, document)
            rows = []
            for row in table.rows:
                cells = []
                for cell in row.cells:
                    cell_text = " ".join(p.text for p in cell.paragraphs).strip()
                    safe = cell_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    cells.append(Paragraph(safe or " ", base))
                if cells:
                    rows.append(cells)
            if rows:
                flowable = Table(rows, repeatRows=1, hAlign="LEFT")
                flowable.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9AA5B1")),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF2F7")),
                            ("LEFTPADDING", (0, 0), (-1, -1), 4),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story.append(Spacer(1, 4))
                story.append(flowable)
                story.append(Spacer(1, 8))

    if not story:
        story.append(Spacer(1, 12))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )
    doc.build(story)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Image -> PDF (avec OCR si pytesseract est disponible)
# ---------------------------------------------------------------------------


def image_to_pdf(content: bytes) -> tuple[bytes, str | None]:
    """Insère l'image dans un PDF A4 ; tente un OCR pour rendre le texte traduisible.

    Retourne (pdf_bytes, message d'avertissement éventuel).
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    warning: str | None = None

    # 1) Couche invisible contenant le texte OCR (traduisible, non visible)
    ocr_layer: bytes | None = None
    try:
        import pytesseract
        from PIL import Image

        image = Image.open(io.BytesIO(content))
        data = pytesseract.image_to_pdf_or_hocr(image, extension="pdf")
        ocr_layer = data if isinstance(data, bytes) else bytes(data)
    except Exception as exc:  # OCR absent ou en échec : on continue sans OCR
        logger.info("OCR indisponible (%s) : l'image sera exportée sans texte.", type(exc).__name__)
        warning = (
            "OCR indisponible sur ce déploiement : l'image est exportée telle quelle, "
            "sans traduction du texte qu'elle contient."
        )

    # 2) PDF de mise en page contenant l'image
    image_reader = ImageReader(io.BytesIO(content))
    width, height = image_reader.getSize()
    page_w, page_h = A4
    scale = min((page_w - 40) / width, (page_h - 40) / height, 1.0)
    draw_w, draw_h = width * scale, height * scale

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    c.drawImage(
        image_reader,
        (page_w - draw_w) / 2,
        (page_h - draw_h) / 2,
        width=draw_w,
        height=draw_h,
        preserveAspectRatio=True,
        mask="auto",
    )
    c.showPage()
    c.save()
    return buffer.getvalue(), warning


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------


def to_pdf(filename: str | None, content: bytes) -> tuple[bytes, str, list[str]]:
    """Convertit n'importe quel format supporté en PDF.

    Retourne ``(pdf_bytes, format_detecte, avertissements)``.
    """
    fmt = detect_format(filename, content)
    warnings: list[str] = []

    if fmt == "pdf":
        return content, "pdf", warnings
    if fmt == "docx":
        return docx_to_pdf(content), "docx", warnings
    if fmt == "text":
        return text_to_pdf(content), "text", warnings
    if fmt == "image":
        pdf, warning = image_to_pdf(content)
        if warning:
            warnings.append(warning)
        return pdf, "image", warnings

    raise UnsupportedFormatError(
        f"Format '.{fmt}' non pris en charge.",
        hint="Formats acceptés : PDF, Word (.docx), TXT/Markdown, images PNG/JPG/WEBP.",
    )
