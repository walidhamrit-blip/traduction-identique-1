"""Formats de sortie : PDF, DOCX, TXT et images (PNG).

Les conversions s'appuient sur les mêmes données que l'éditeur : le texte
corrigé/positionné par l'utilisateur est la source unique de vérité.
"""

from __future__ import annotations

import io
import logging

from app.errors import PdfServiceError

logger = logging.getLogger(__name__)


class ExportFormatError(PdfServiceError):
    """Format de sortie non pris en charge."""

    status_code = 415
    code = "unsupported_output_format"


SUPPORTED_OUTPUT_FORMATS = {
    "pdf": "PDF (mise en page préservée)",
    "png": "Images PNG (une par page)",
    "txt": "Texte brut",
    "docx": "Word .docx",
}


def export_pdf_to_png(pdf_bytes: bytes, dpi: int = 130, page: int = 1) -> bytes:
    """Rasterise une page en PNG (aperçu de l'éditeur, export image)."""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise ExportFormatError(
            "Le rendu PNG n'est pas disponible sur ce déploiement.",
            hint="Ajoutez 'pymupdf' dans requirements.txt pour activer l'aperçu image.",
            status_code=503,
            code="png_export_missing",
        ) from exc

    try:
        document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        if document.page_count == 0:
            raise ExportFormatError("Le PDF exporté ne contient aucune page.")
        index = max(0, min(page - 1, document.page_count - 1))
        pixmap = document[index].get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    except PdfServiceError:
        raise
    except Exception as exc:
        raise ExportFormatError(f"Échec de la conversion PNG : {exc}") from exc


def export_pdf_to_png_pages(pdf_bytes: bytes, dpi: int = 110, max_pages: int = 10) -> list[bytes]:
    """Rasterise plusieurs pages (borné pour ne pas saturer la mémoire d'un plan gratuit)."""
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover
        raise ExportFormatError(
            "Le rendu PNG n'est pas disponible sur ce déploiement.",
            hint="Ajoutez 'pymupdf' dans requirements.txt pour activer l'export image.",
            status_code=503,
            code="png_export_missing",
        ) from exc

    images: list[bytes] = []
    document = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    for index in range(min(document.page_count, max_pages)):
        pixmap = document[index].get_pixmap(dpi=dpi)
        images.append(pixmap.tobytes("png"))
    return images


def compress_pdf(pdf_bytes: bytes, *, compress_streams: bool = True) -> bytes:
    """Réduit le poids d'un PDF sans perte visuelle visible.

    Utile sur Vercel, où la **réponse** est aussi plafonnée à 4,5 Mo : un PDF
    traduit peut dépasser cette limite même si l'entrée était petite.
    """
    if not compress_streams:
        return pdf_bytes
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:  # pragma: no cover
        return pdf_bytes

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        writer = PdfWriter(clone_from=reader)
        for page in writer.pages:
            try:
                page.compress_content_streams()
            except Exception:
                continue
        buffer = io.BytesIO()
        writer.write(buffer)
        compressed = buffer.getvalue()
        # On ne garde la version compressée que si elle est réellement plus légère
        return compressed if len(compressed) < len(pdf_bytes) else pdf_bytes
    except Exception:
        return pdf_bytes


def export_text(pages: list[list[tuple[object, str]]], *, include_original: bool = False) -> bytes:
    """Exporte le texte traduit (aligné sur les blocs extraits).

    `pages` contient, pour chaque page, des tuples ``(bloc, texte_traduit)``.
    """
    lines: list[str] = []
    for page_index, page_items in enumerate(pages):
        lines.append(f"===== Page {page_index + 1} =====")
        for block, translated in page_items:
            text = (translated or "").strip()
            if not text:
                continue
            if include_original:
                original = getattr(block, "text", "").strip()
                lines.append(f"[source] {original}")
            lines.append(text)
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def export_docx(
    pages: list[list[tuple[object, str]]],
    *,
    target_lang: str = "ar",
    title: str = "Document traduit",
    style: object | None = None,
) -> bytes:
    """Génère un .docx propre (titres conservés par heuristique de taille)."""
    try:
        import docx
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt
    except ImportError as exc:  # pragma: no cover
        raise ExportFormatError(
            "L'export Word n'est pas disponible sur ce déploiement.",
            hint="Ajoutez 'python-docx' dans requirements.txt puis redéployez.",
            status_code=503,
            code="docx_export_missing",
        ) from exc

    document = docx.Document()
    document.core_properties.title = title

    align_map = {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }
    default_align = align_map.get(getattr(style, "align", "right").value
                                  if hasattr(getattr(style, "align", None), "value")
                                  else "right", WD_ALIGN_PARAGRAPH.RIGHT)

    base_size = getattr(style, "font_size", None) or 11

    for page_index, page_items in enumerate(pages):
        if page_index > 0:
            document.add_page_break()
        for block, translated in page_items:
            text = (translated or "").strip()
            if not text:
                continue
            paragraph = document.add_paragraph()
            paragraph.alignment = default_align
            run = paragraph.add_run(text)
            run.font.size = Pt(max(8, min(28, base_size)))
            # Heuristique : les blocs courts en grande police deviennent des titres
            detected = getattr(block, "font_size", 11) or 11
            if len(text) < 70 and detected >= 15:
                run.bold = True

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def export(filename: str | None, fmt: str) -> str:
    """Nom de fichier de sortie cohérent avec le format demandé."""
    from app.filenames import safe_ascii_stem

    stem = safe_ascii_stem(filename)
    extension = {"pdf": "pdf", "png": "png", "txt": "txt", "docx": "docx"}.get(fmt, "pdf")
    return f"{stem}_traduit.{extension}"
