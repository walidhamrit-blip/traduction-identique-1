"""Point d'entrée FastAPI & Vercel Serverless Function (`api/index.py`).

Compatible simultanément avec :
- Un déploiement Vercel Serverless Python (`api/index.py` + `vercel.json`)
- Une exécution microservice standard via Uvicorn / Docker (`uvicorn main:app`)
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.arabic_utils import format_arabic_text
from app.config import (
    IMAGE_OPTIMIZE_ENABLED,
    IMAGE_OPTIMIZE_TARGET_BYTES,
    MAX_PAGES,
    MAX_UPLOAD_BYTES,
    MAX_RESPONSE_BYTES,
    MAX_UPLOAD_MB,
    PROJECT_ROOT,
    SERVICE_VERSION,
    SOURCE_LANG,
    TRANSLATE_ENABLED,
)
from app.errors import (
    EmptyPdfError,
    InvalidPdfError,
    PayloadTooLargeError,
    PdfServiceError,
)
from app.extractor import extract_pdf_layout
from app.filenames import build_content_disposition
from app.pipeline import PipelineReport, translate_pdf_bytes
from app.translation.chain import chain_diagnostics, provider_order
from app.renderer import ensure_fonts_registered, fonts_diagnostics
from app.sample_generator import generate_sample_pdf
from app.schemas import BlockEdit, StyleOptions, TargetLang, TextAlign, VerticalAlign
from app.converters import SUPPORTED_INPUT_FORMATS, to_pdf
from app.exporters import (
    SUPPORTED_OUTPUT_FORMATS,
    compress_pdf,
    shrink_pdf,
    export_docx,
    export_pdf_to_png,
    export_text,
)
from app.fonts import font_catalog
from app.translator import mock_translate

logger = logging.getLogger(__name__)

app = FastAPI(
    title="PDF Layout-Preserving Translator Microservice",
    description=(
        "Microservice Python (FastAPI / Vercel Serverless) de traduction de fichiers PDF "
        "vers `en`, `fr` ou `ar` avec extraction des Bounding Boxes (`pdfminer.six`), "
        "reshaping + BiDi RTL (`arabic-reshaper` & `python-bidi`), calque `reportlab` "
        "et fusion `pypdf`."
    ),
    version=SERVICE_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PdfServiceError)
async def _pdf_service_error_handler(request: Request, exc: PdfServiceError) -> JSONResponse:
    """Transforme toute erreur métier en réponse JSON explicite (code + message + conseil)."""
    logger.warning(
        "Erreur métier %s sur %s : %s", exc.code, request.url.path, exc.message
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.to_dict()},
        headers={"X-Error-Code": exc.code},
    )


@app.exception_handler(Exception)
async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Filet de sécurité : plus de 500 muet, on remonte le type et le message d'erreur."""
    logger.exception("Exception non gérée sur %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"{type(exc).__name__}: {exc}",
                "hint": "Consultez /api/diag et les logs de la fonction (Vercel > Deployments > Logs).",
            }
        },
        headers={"X-Error-Code": "internal_error"},
    )


async def _read_upload(file: UploadFile, *, optimize: bool = True) -> tuple[bytes, str]:
    """Lit, valide puis convertit le fichier envoyé en PDF si nécessaire.

    `optimize=True` : un PDF trop lourd (document numérisé contenant de grosses
    images) est allégé avant traitement, plutôt que d'être refusé (erreur 413).
    """
    content = await file.read()
    if not content:
        raise EmptyPdfError("Le fichier envoyé est vide.")

    if len(content) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(
            f"Le fichier ({len(content) / (1024 * 1024):.1f} Mo) dépasse la taille "
            f"maximale autorisée ({MAX_UPLOAD_MB} Mo).",
            hint=(
                "Vercel refuse les requêtes de plus de 4,5 Mo (limite de la plateforme), "
                "quelle que soit la configuration du service. Pour un document numérisé : "
                "scannez en 150 dpi en noir et blanc, ou découpez le PDF en plusieurs "
                "fichiers de moins de 4 Mo."
            ),
        )

    pdf_bytes, detected_format, _warnings = to_pdf(file.filename, content)

    if optimize and IMAGE_OPTIMIZE_ENABLED and len(pdf_bytes) > MAX_UPLOAD_BYTES:
        from app.exporters import shrink_pdf

        shrunk = shrink_pdf(pdf_bytes, target_bytes=IMAGE_OPTIMIZE_TARGET_BYTES)
        if shrunk and len(shrunk) < len(pdf_bytes):
            logger.info(
                "PDF allégé pour rester sous les limites : %.1f Mo -> %.1f Mo",
                len(pdf_bytes) / (1024 * 1024),
                len(shrunk) / (1024 * 1024),
            )
            pdf_bytes = shrunk

    if len(pdf_bytes) > MAX_UPLOAD_BYTES:
        raise PayloadTooLargeError(
            f"Le document reste trop volumineux après compression "
            f"({len(pdf_bytes) / (1024 * 1024):.1f} Mo, limite {MAX_UPLOAD_MB} Mo).",
            hint=(
                "Découpez le PDF en plusieurs fichiers, ou réduisez la qualité des "
                "images du scan (150 dpi, noir et blanc) avant de l'envoyer."
            ),
        )

    return pdf_bytes, detected_format


def _parse_style(style_json: str | None) -> StyleOptions:
    """Désérialise les options de mise en forme envoyées par l'éditeur."""
    if not style_json:
        return StyleOptions()
    try:
        payload = json.loads(style_json)
    except ValueError as exc:
        raise InvalidPdfError(
            "Les options de mise en forme ne sont pas un JSON valide.",
            status_code=400,
            code="invalid_style",
        ) from exc
    if not isinstance(payload, dict):
        return StyleOptions()

    def _num(key: str, default: float | None):
        value = payload.get(key)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    align_raw = str(payload.get("align", "right")).lower()
    valign_raw = str(payload.get("vertical_align", "middle")).lower()

    return StyleOptions(
        font_id=(payload.get("font_id") or None),
        font_size=_num("font_size", None),
        font_size_scale=_num("font_size_scale", 1.0) or 1.0,
        min_font_size=_num("min_font_size", 5.0) or 5.0,
        align=(
            TextAlign(align_raw)
            if align_raw in TextAlign._value2member_map_
            else TextAlign.RIGHT
        ),
        vertical_align=(
            VerticalAlign(valign_raw)
            if valign_raw in VerticalAlign._value2member_map_
            else VerticalAlign.MIDDLE
        ),
        color=str(payload.get("color") or "#14171E"),
        line_spacing=_num("line_spacing", 1.22) or 1.22,
        offset_x=_num("offset_x", 0.0) or 0.0,
        offset_y=_num("offset_y", 0.0) or 0.0,
        letter_spacing=_num("letter_spacing", 0.0) or 0.0,
        mask_padding=_num("mask_padding", 0.8) or 0.8,
    )


def _parse_edits(edits_json: str | None) -> dict[str, BlockEdit]:
    """Désérialise les corrections bloc par bloc (texte, position, taille, masquage)."""
    if not edits_json:
        return {}
    try:
        payload = json.loads(edits_json)
    except ValueError as exc:
        raise InvalidPdfError(
            "Les corrections de blocs ne sont pas un JSON valide.",
            status_code=400,
            code="invalid_edits",
        ) from exc
    if not isinstance(payload, list):
        return {}

    edits: dict[str, BlockEdit] = {}
    for item in payload:
        if not isinstance(item, dict) or not item.get("block_id"):
            continue
        try:
            edits[str(item["block_id"])] = BlockEdit(
                block_id=str(item["block_id"]),
                text=item.get("text"),
                x0=float(item["x0"]) if item.get("x0") is not None else None,
                y0=float(item["y0"]) if item.get("y0") is not None else None,
                font_size=float(item["font_size"]) if item.get("font_size") else None,
                hidden=bool(item.get("hidden", False)),
            )
        except (TypeError, ValueError):
            continue
    return edits


def _translated_file_response(
    content: bytes,
    filename: str | None,
    output_format: str,
    target_lang: TargetLang,
    report: PipelineReport,
) -> Response:
    """Réponse de téléchargement : en-tête ASCII (RFC 5987) + format demandé."""
    from app.exporters import export as export_name

    media_types = {
        "pdf": "application/pdf",
        "png": "image/png",
        "txt": "text/plain; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return Response(
        content=content,
        media_type=media_types.get(output_format, "application/octet-stream"),
        headers={
            "Content-Disposition": build_content_disposition(
                export_name(filename, output_format), target_lang.value
            ),
            "X-Target-Language": target_lang.value,
            "X-Output-Format": output_format,
            **report.headers(),
        },
    )


async def _handle_translate_request(
    file: UploadFile,
    target_lang: TargetLang,
    style_json: str | None = None,
    edits_json: str | None = None,
    output_format: str = "pdf",
) -> Response:
    """Valide l'entrée, applique les options d'édition puis retourne le fichier final."""
    fmt = (output_format or "pdf").lower()
    if fmt not in SUPPORTED_OUTPUT_FORMATS:
        raise InvalidPdfError(
            f"Format de sortie inconnu : {fmt}.",
            status_code=415,
            code="unsupported_output_format",
            hint="Formats disponibles : " + ", ".join(SUPPORTED_OUTPUT_FORMATS),
        )

    pdf_bytes, _detected = await _read_upload(file)
    style = _parse_style(style_json)
    edits = _parse_edits(edits_json)

    report = PipelineReport()
    translated_pdf = translate_pdf_bytes(
        pdf_bytes, target_lang=target_lang, report=report, style=style, edits=edits
    )

    if fmt == "pdf":
        content = translated_pdf
        # La réponse Vercel est plafonnée à 4,5 Mo : on allège le PDF si l'on s'en
        # approche, sinon la plateforme renvoie un 413 opaque.
        if len(content) > MAX_RESPONSE_BYTES:
            compressed = compress_pdf(content)
            if len(compressed) > MAX_RESPONSE_BYTES:
                compressed = shrink_pdf(compressed, target_bytes=MAX_RESPONSE_BYTES)
            if len(compressed) > MAX_RESPONSE_BYTES:
                raise PayloadTooLargeError(
                    f"Le PDF traduit pèse {len(compressed) / (1024 * 1024):.1f} Mo, au-delà "
                    "de la limite de 4,5 Mo d'une réponse Vercel, même après compression.",
                    hint=(
                        "Découpez le PDF en plusieurs fichiers de quelques pages, ou "
                        "choisissez l'export TXT/DOCX (bien plus léger)."
                    ),
                )
            content = compressed
    elif fmt == "png":
        content = export_pdf_to_png(translated_pdf)
    else:
        # Le texte exporté provient de la même source que l'export PDF : extraction
        # + traduction + corrections, donc TXT/DOCX sont cohérents avec l'aperçu.
        from app.extractor import extract_pdf_layout
        from app.pipeline import _apply_edits
        from app.translation.chain import TranslationChain as _Chain

        _pages = extract_pdf_layout(pdf_bytes)
        _chain = _Chain()
        _texts = [b.text for page in _pages for b in page.blocks]
        _res = _chain.translate_segments(
            _texts, source=SOURCE_LANG, target=target_lang.value
        )
        _cursor = 0
        _per_page: list[list[tuple[object, str]]] = []
        for page in _pages:
            items: list[tuple[object, str]] = []
            for block in page.blocks:
                items.append((block, _res.translations[_cursor]))
                _cursor += 1
            _per_page.append(items)
        _per_page = _apply_edits(_per_page, edits)
        pages_text = [
            [(block, text) for block, text in page if text] for page in _per_page
        ]
        content = (
            export_text(pages_text)
            if fmt == "txt"
            else export_docx(pages_text, target_lang=target_lang.value, style=style)
        )

    return _translated_file_response(content, file.filename, fmt, target_lang, report)


def _pdf_text_by_page(pdf_bytes: bytes) -> list[list[tuple[object, str]]]:
    """Extrait le texte du PDF produit, page par page, pour les exports TXT/DOCX."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes), strict=False)
        pages: list[list[tuple[object, str]]] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            blocks = [
                (_SimpleBlock(), line.strip())
                for line in text.splitlines()
                if line.strip()
            ]
            pages.append(blocks)
        return pages
    except Exception:
        return []


class _SimpleBlock:
    """Bloc minimal pour les exports texte."""

    text = ""
    font_size = 11.0


@app.post(
    "/translate",
    summary="Traduire un PDF (multipart/form-data)",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Nouveau fichier PDF avec calque traduit fusionné.",
        }
    },
)
@app.post("/api/translate", include_in_schema=False)
async def translate_pdf_endpoint(
    file: Annotated[UploadFile, File(description="Fichier source : PDF, DOCX, TXT, image")],
    target_lang: Annotated[
        TargetLang | None,
        Form(description="Langue cible ('en', 'fr' ou 'ar')"),
    ] = None,
    target_lang_query: Annotated[
        TargetLang | None,
        Query(alias="target_lang", description="Langue cible en query param optionnel"),
    ] = None,
    style: Annotated[
        str | None,
        Form(description="Options d'édition JSON (police, taille, alignement, couleur, décalages)"),
    ] = None,
    edits: Annotated[
        str | None,
        Form(description="Corrections par bloc JSON : [{block_id, text, x0, y0, font_size, hidden}]"),
    ] = None,
    output_format: Annotated[
        str,
        Form(description="Format de sortie : pdf (défaut), png, txt, docx"),
    ] = "pdf",
) -> Response:
    resolved_lang = target_lang or target_lang_query
    if resolved_lang is None:
        raise HTTPException(
            status_code=422,
            detail="Le paramètre 'target_lang' ('en', 'fr' ou 'ar') est requis.",
        )
    return await _handle_translate_request(
        file=file,
        target_lang=resolved_lang,
        style_json=style,
        edits_json=edits,
        output_format=output_format,
    )


@app.post(
    "/preview",
    summary="Préparer l'édition : blocs traduits + aperçu image (sans exporter)",
)
@app.post("/api/preview", include_in_schema=False)
async def preview_endpoint(
    file: Annotated[UploadFile, File(description="Fichier source : PDF, DOCX, TXT, image")],
    target_lang: Annotated[TargetLang, Form(description="Langue cible")] = TargetLang.AR,
    page: Annotated[int, Form(description="Page à prévisualiser (1 = première)")] = 1,
    max_blocks: Annotated[int, Form(description="Nombre maximal de blocs renvoyés")] = 300,
    style: Annotated[
        str | None, Form(description="Options d'édition JSON (aperçu fidèle au rendu final)")
    ] = None,
    edits: Annotated[
        str | None, Form(description="Corrections par bloc JSON")
    ] = None,
    dpi: Annotated[
        int, Form(description="Résolution de l'aperçu (80 = léger, 110 = net, 150 = haute)")
    ] = 100,
) -> dict:
    """Retourne tout ce dont l'éditeur a besoin **avant** l'export :

    - l'aperçu de la page demandée (PNG base64) ;
    - les blocs traduits avec un identifiant stable, leur texte source, leur
      traduction, leur position et leur taille de police ;
    - la liste des polices disponibles et les limites du service.

    Aucun fichier n'est écrit : le client applique ses réglages puis appelle
    `/translate` avec `style` + `edits` pour obtenir l'export final.
    """
    import base64

    pdf_bytes, detected_format = await _read_upload(file)
    style_options = _parse_style(style)
    block_edits = _parse_edits(edits)

    report = PipelineReport()
    translated_pdf = translate_pdf_bytes(
        pdf_bytes,
        target_lang=target_lang,
        report=report,
        style=style_options,
        edits=block_edits,
    )

    # Rendu de la page demandée pour l'aperçu visuel
    preview_png: str | None = None
    try:
        import pymupdf

        document = pymupdf.open(stream=translated_pdf, filetype="pdf")
        index = max(0, min(page - 1, document.page_count - 1))
        # DPI borné : un aperçu 150 dpi sur un plan gratuit peut dépasser la mémoire
        pixmap = document[index].get_pixmap(dpi=max(60, min(dpi, 150)))
        preview_png = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        page_count = document.page_count
        page_sizes = [
            {"width": round(p.rect.width, 2), "height": round(p.rect.height, 2)}
            for p in document
        ]
    except Exception:  # pragma: no cover - aperçu optionnel
        page_count = report.pages
        page_sizes = []

    # Blocs extraits (source + traduction) pour l'édition fine
    from app.extractor import extract_pdf_layout
    from app.pipeline import _apply_edits  # noqa: PLC0415
    from app.schemas import block_id_of
    from app.translation.chain import TranslationChain

    pages = extract_pdf_layout(pdf_bytes)
    chain = TranslationChain()
    blocks_payload: list[dict] = []
    per_page: list[list[tuple]] = []

    for page_index, extracted in enumerate(pages):
        texts = [block.text for block in extracted.blocks]
        result = chain.translate_segments(texts, source=SOURCE_LANG, target=target_lang.value)
        items = list(zip(extracted.blocks, result.translations, strict=False))
        per_page.append(items)
        if page_index != page - 1:  # `page` est numérotée à partir de 1
            continue
        for position, (block, translated) in enumerate(items):
            source_text = block.text.strip()
            if not source_text:
                continue
            # Tous les blocs sont éditables (texte, position, taille) : l'utilisateur
            # peut corriger une traduction ou repositionner un code conservé.
            unchanged = source_text == (translated or "").strip()
            blocks_payload.append(
                {
                    "block_id": block_id_of(block, page_index, position),
                    "text": block.text,
                    "translation": translated,
                    "unchanged": unchanged,
                    "bbox": [round(v, 2) for v in block.bbox],
                    "font_size": block.font_size,
                    "line_count": block.line_count,
                }
            )
            if len(blocks_payload) >= max_blocks:
                break

    return {
        "status": "ok",
        "input_format": detected_format,
        "target_lang": target_lang.value,
        "page": page,
        "page_count": page_count,
        "page_sizes": page_sizes,
        "preview_png_base64": preview_png,
        "blocks": blocks_payload,
        "block_total": sum(len(b) for b in per_page) if per_page else 0,
        "fonts": font_catalog(),
        "output_formats": SUPPORTED_OUTPUT_FORMATS,
        "input_formats": SUPPORTED_INPUT_FORMATS,
        "translation": {
            "provider": report.provider,
            "degraded": report.degraded,
            "warnings": report.warnings,
        },
        "limits": {"max_upload_mb": MAX_UPLOAD_MB, "max_pages": MAX_PAGES},
    }


@app.get("/fonts", summary="Catalogue des polices disponibles pour l'éditeur")
@app.get("/api/fonts", include_in_schema=False)
def fonts_endpoint() -> dict:
    return {"fonts": font_catalog()}


@app.get("/formats", summary="Formats d'entrée et de sortie pris en charge")
@app.get("/api/formats", include_in_schema=False)
def formats_endpoint() -> dict:
    return {
        "input": SUPPORTED_INPUT_FORMATS,
        "output": SUPPORTED_OUTPUT_FORMATS,
    }


@app.post(
    "/extract",
    summary="Inspecter les Bounding Boxes extraites via pdfminer.six et leur traduction",
)
@app.post("/api/extract", include_in_schema=False)
async def inspect_extraction_endpoint(
    file: Annotated[UploadFile, File(description="Fichier PDF à analyser")],
    target_lang: Annotated[TargetLang, Form(description="Langue cible")] = TargetLang.AR,
) -> dict:
    pdf_bytes, _detected = await _read_upload(file)

    pages = extract_pdf_layout(pdf_bytes)
    return {
        "target_lang": target_lang.value,
        "page_count": len(pages),
        "pages": [
            {
                "page_index": p.page_index,
                "width": round(p.width, 2),
                "height": round(p.height, 2),
                "blocks": [
                    {
                        "text": b.text,
                        "translated": (tr := mock_translate(b.text, target_lang)),
                        "visual_display": (
                            format_arabic_text(tr)
                            if target_lang == TargetLang.AR
                            else tr
                        ),
                        "bbox": [round(v, 2) for v in b.bbox],
                        "font_size": b.font_size,
                        "line_count": b.line_count,
                    }
                    for b in p.blocks
                ],
            }
            for p in pages
        ],
    }


@app.get("/sample", summary="Télécharger un PDF de démonstration")
@app.get("/api/sample", include_in_schema=False)
def get_sample_pdf() -> Response:
    sample_bytes = generate_sample_pdf()
    return Response(
        content=sample_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(
                "sample_invoice.pdf", "source"
            )
        },
    )


@app.get("/health", summary="Vérification de l'état du service et des polices TTF")
@app.get("/api/health", include_in_schema=False)
def healthcheck() -> dict:
    ensure_fonts_registered()
    return {
        "status": "ok",
        "supported_languages": [lang.value for lang in TargetLang],
        "arabic_font": "Amiri (TTF)",
        "engine": {
            "extraction": "pdfminer.six",
            "rtl_shaping": ["arabic-reshaper", "python-bidi"],
            "overlay": "reportlab",
            "merge": "pypdf",
        },
    }


@app.get("/diag", summary="Diagnostic de l'environnement de déploiement (polices, chemins, limites)")
@app.get("/api/diag", include_in_schema=False)
def diagnostics() -> JSONResponse:
    """Diagnostic non destructif : répond 200 même si les polices sont absentes.

    C'est le premier appel à faire quand une requête renvoie une erreur 500 :
    il montre si `assets/fonts/*.ttf` a bien été embarqué dans le bundle Vercel.
    """
    fonts = fonts_diagnostics()
    payload = {
        "service": {
            "version": SERVICE_VERSION,
            "project_root": str(PROJECT_ROOT),
            "cwd": str(Path.cwd()),
            "python": __import__("sys").version.split()[0],
        },
        "limits": {
            "max_upload_mb": MAX_UPLOAD_MB,
            "max_pages": MAX_PAGES,
        },
        "translation": {**chain_diagnostics(), "enabled": TRANSLATE_ENABLED, "source_lang": SOURCE_LANG},
        "fonts": fonts,
        "status": "ok" if fonts["amiri"]["ok"] else "degraded",
        "hint": (
            None
            if fonts["amiri"]["ok"]
            else (
                "Police arabe absente du bundle. Ajoutez 'assets/fonts/**' dans "
                "includeFiles de vercel.json puis redéployez."
            )
        ),
        "dependencies": _dependency_versions(),
        "render": _render_selftest(),
    }
    return JSONResponse(payload)


def _minimal_pdf() -> bytes:
    """Construit un PDF d'une page blanche valide (base de l'auto-test de rendu)."""
    from app.pdf_writer import PageContent, PdfWriter

    writer = PdfWriter()
    writer.add_page(PageContent(width=595, height=842))
    return writer.build()


def _render_selftest() -> dict[str, object]:
    """Auto-test de rendu : reproduit une mini-page PDF et indique le moteur qui répond.

    Permet de vérifier depuis le navigateur (`/api/diag`) si ReportLab fonctionne
    dans l'environnement déployé, sans avoir à lire les logs Vercel.
    """
    from app.renderer import (
        _select_font_name,
        build_overlay_pdf,
        build_overlay_pdf_fallback,
        merge_overlay_with_original,
        merge_overlay_with_pymupdf,
    )
    from app.schemas import ExtractedPage, TargetLang, TextBlock

    block = TextBlock(
        text="Invoice 2026", x0=40, y0=700, x1=300, y1=730, font_size=12, line_count=1
    )
    page = ExtractedPage(page_index=0, width=595, height=842, blocks=[block])
    kwargs = {
        "pages": [page],
        "translated_blocks_per_page": [[(block, "Facture 2026")]],
        "target_lang": TargetLang.FR,
    }
    result: dict[str, object] = {}
    for name, render in (
        ("reportlab", build_overlay_pdf),
        ("interne", build_overlay_pdf_fallback),
    ):
        try:
            overlay = render(**kwargs)
            sample = merge_overlay_with_original(_minimal_pdf(), overlay)
            result[name] = {"ok": True, "overlay_bytes": len(overlay), "merged_bytes": len(sample)}
        except Exception as exc:  # noqa: BLE001 - diagnostic : on veut l'erreur brute
            result[name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": __import__("traceback").format_exc(limit=4).splitlines()[-6:],
            }
    try:
        merge_overlay_with_pymupdf(_minimal_pdf(), build_overlay_pdf_fallback(**kwargs))
        result["merge_pymupdf"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        result["merge_pymupdf"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    result["font_used"] = _select_font_name(TargetLang.FR)
    return result


def _dependency_versions() -> dict[str, str]:
    """Versions effectives des dépendances critiques (les 500 viennent souvent d'un décalage)."""
    versions: dict[str, str] = {}
    for module_name, package_name in (
        ("fastapi", "fastapi"),
        ("starlette", "starlette"),
        ("pydantic", "pydantic"),
        ("pdfminer", "pdfminer.six"),
        ("pypdf", "pypdf"),
        ("reportlab", "reportlab"),
        ("arabic_reshaper", "arabic-reshaper"),
        ("bidi", "python-bidi"),
    ):
        try:
            module = __import__(module_name)
            versions[package_name] = str(getattr(module, "__version__", "unknown"))
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            versions[package_name] = f"ERREUR D'IMPORT: {type(exc).__name__}: {exc}"
    return versions


@app.get("/editor", response_class=HTMLResponse, include_in_schema=False)
@app.get("/api/editor", response_class=HTMLResponse, include_in_schema=False)
def editor_page() -> HTMLResponse:
    """Éditeur : réglage police / taille / position / corrections avant export."""
    path = PROJECT_ROOT / "static" / "editor.html"
    if not path.is_file():
        raise InvalidPdfError(
            "L'éditeur n'est pas présent dans ce déploiement (static/editor.html manquant).",
            status_code=503,
            code="editor_missing",
        )
    return HTMLResponse(content=path.read_text(encoding="utf-8"))


@app.get("/editor.js", include_in_schema=False)
@app.get("/api/editor.js", include_in_schema=False)
def editor_script() -> Response:
    path = PROJECT_ROOT / "static" / "editor.js"
    if not path.is_file():
        raise InvalidPdfError(
            "Script de l'éditeur manquant dans ce déploiement.",
            status_code=503,
            code="editor_missing",
        )
    return Response(
        content=path.read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def interactive_console() -> HTMLResponse:
    """Interface web légère permettant de tester immédiatement le microservice."""
    html = """<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>PDF Translator Microservice — FastAPI & Vercel</title>
  <style>
    :root {
      --bg: #f8fafc;
      --surface: #ffffff;
      --border: #e2e8f0;
      --text: #0f172a;
      --muted: #475569;
      --primary: #1d4ed8;
      --primary-hover: #1e40af;
      --accent-bg: #eff6ff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding: 24px;
    }
    .container { max-width: 1280px; margin: 0 auto; }
    header {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 24px;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
    }
    h1 { font-size: 1.35rem; font-weight: 700; letter-spacing: -0.02em; }
    .subtitle { color: var(--muted); font-size: 0.9rem; margin-top: 2px; }
    .badges { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .badge {
      font-size: 0.75rem;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--accent-bg);
      color: var(--primary);
      border: 1px solid #bfdbfe;
      text-decoration: none;
    }
    .controls {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px;
      display: flex;
      flex-wrap: wrap;
      gap: 16px;
      align-items: flex-end;
      margin-bottom: 20px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .field { display: flex; flex-direction: column; gap: 6px; flex: 1; min-width: 210px; }
    label { font-size: 0.82rem; font-weight: 600; color: var(--muted); }
    input[type="file"], select {
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 9px 12px;
      font-size: 0.9rem;
      background: #fff;
      color: var(--text);
    }
    button {
      border: none;
      border-radius: 8px;
      padding: 10px 18px;
      font-size: 0.9rem;
      font-weight: 600;
      cursor: pointer;
      transition: background 0.15s ease;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-secondary {
      background: #f1f5f9;
      color: var(--text);
      border: 1px solid var(--border);
    }
    .btn-secondary:hover { background: #e2e8f0; }
    button:disabled { opacity: 0.55; cursor: not-allowed; }
    .status {
      font-size: 0.86rem;
      margin-bottom: 16px;
      padding: 10px 14px;
      border-radius: 8px;
      display: none;
    }
    .status.info { display: block; background: #eff6ff; color: #1e40af; border: 1px solid #bfdbfe; }
    .status.error { display: block; background: #fef2f2; color: #b91c1c; border: 1px solid #fecaca; }
    .status.ok { display: block; background: #f0fdf4; color: #15803d; border: 1px solid #bbf7d0; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }
    @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
    .panel {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      min-height: 580px;
    }
    .panel-header {
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 0.88rem;
      font-weight: 600;
      background: #f8fafc;
    }
    .panel-header a {
      font-size: 0.8rem;
      color: var(--primary);
      text-decoration: none;
      font-weight: 600;
    }
    iframe { width: 100%; flex: 1; border: none; background: #cbd5e1; }
    .empty {
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--muted);
      font-size: 0.9rem;
      padding: 32px;
      text-align: center;
    }
    details {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 18px;
    }
    summary { cursor: pointer; font-weight: 600; font-size: 0.9rem; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.82rem; }
    th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }
    th { color: var(--muted); font-weight: 600; background: #f8fafc; }
    code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>PDF Layout-Preserving Translator</h1>
        <p class="subtitle">pdfminer.six &rarr; mock_translate &rarr; arabic-reshaper + python-bidi (Amiri TTF) &rarr; ReportLab + pypdf</p>
      </div>
      <div class="badges">
        <span class="badge">EN / FR / AR (RTL)</span>
        <a class="badge" href="./editor">✎ Éditeur avant export</a>
        <a class="badge" href="./docs" target="_blank">OpenAPI /docs &nearr;</a>
      </div>
    </header>

    <form class="controls" id="translateForm">
      <div class="field">
        <label for="pdfFile">Fichier PDF source</label>
        <input type="file" id="pdfFile" accept="application/pdf,.pdf" />
      </div>
      <div class="field" style="max-width: 260px;">
        <label for="targetLang">Langue cible (target_lang)</label>
        <select id="targetLang">
          <option value="ar" selected>Arabe (ar) — RTL + Police Amiri</option>
          <option value="fr">Français (fr)</option>
          <option value="en">Anglais (en)</option>
        </select>
      </div>
      <button type="button" class="btn-secondary" id="loadSampleBtn">Charger le PDF de démo</button>
      <button type="submit" class="btn-primary" id="submitBtn">Traduire le PDF</button>
    </form>

    <div id="statusBox" class="status"></div>

    <div class="grid">
      <div class="panel">
        <div class="panel-header">
          <span>Document PDF Original</span>
          <a id="origDownload" href="#" download="original.pdf" style="display:none;">Télécharger</a>
        </div>
        <div class="empty" id="origEmpty">Chargez un fichier PDF ou cliquez sur « Charger le PDF de démo ».</div>
        <iframe id="origFrame" style="display:none;"></iframe>
      </div>

      <div class="panel">
        <div class="panel-header">
          <span id="transTitle">Nouveau PDF Traduit (calque ReportLab + pypdf)</span>
          <a id="transDownload" href="#" download="translated.pdf" style="display:none;">Télécharger le PDF</a>
        </div>
        <div class="empty" id="transEmpty">Le PDF traduit s'affichera ici.</div>
        <iframe id="transFrame" style="display:none;"></iframe>
      </div>
    </div>

    <details id="bboxDetails">
      <summary>Inspecter les Bounding Boxes extraites par pdfminer.six (<span id="bboxCount">0</span> blocs)</summary>
      <div style="overflow-x:auto;">
        <table>
          <thead>
            <tr>
              <th>Page</th>
              <th>Bounding Box (x0, y0, x1, y1)</th>
              <th>Taille (pt)</th>
              <th>Texte extrait</th>
              <th>Traduction (logique)</th>
            </tr>
          </thead>
          <tbody id="bboxTableBody"></tbody>
        </table>
      </div>
    </details>
  </div>

  <script>
    let currentPdfBlob = null;
    let currentFilename = "sample_invoice.pdf";

    const pdfInput = document.getElementById("pdfFile");
    const targetLangSelect = document.getElementById("targetLang");
    const loadSampleBtn = document.getElementById("loadSampleBtn");
    const submitBtn = document.getElementById("submitBtn");
    const statusBox = document.getElementById("statusBox");
    const origFrame = document.getElementById("origFrame");
    const origEmpty = document.getElementById("origEmpty");
    const origDownload = document.getElementById("origDownload");
    const transFrame = document.getElementById("transFrame");
    const transEmpty = document.getElementById("transEmpty");
    const transDownload = document.getElementById("transDownload");
    const bboxCount = document.getElementById("bboxCount");
    const bboxTableBody = document.getElementById("bboxTableBody");

    function setStatus(msg, kind) {
      statusBox.textContent = msg;
      statusBox.className = "status " + kind;
    }

    // Transforme une réponse d'erreur JSON (detail string ou objet structuré) en message lisible
    function describeError(errJson, httpStatus) {
      const detail = errJson && errJson.detail;
      if (detail && typeof detail === "object") {
        const parts = [detail.message || detail.code || "Erreur inconnue"];
        if (detail.code) parts.push("(code: " + detail.code + ")");
        if (detail.hint) parts.push("— " + detail.hint);
        return parts.join(" ");
      }
      if (typeof detail === "string") return detail;
      if (httpStatus === 413) {
        return "Fichier trop volumineux : la limite d'envoi est de 4,5 Mo. " +
          "Réduisez le poids du document (scan 150 dpi, noir et blanc) ou découpez-le.";
      }
      return "Erreur HTTP " + httpStatus;
    }

    function showOriginalBlob(blob, filename) {
      currentPdfBlob = blob;
      currentFilename = filename;
      const url = URL.createObjectURL(blob);
      origFrame.src = url;
      origFrame.style.display = "block";
      origEmpty.style.display = "none";
      origDownload.href = url;
      origDownload.download = filename;
      origDownload.style.display = "inline";
    }

    pdfInput.addEventListener("change", (e) => {
      const f = e.target.files[0];
      if (f && f.size > 4.5 * 1024 * 1024) {
        e.target.value = "";
        currentPdfBlob = null;
        setStatus(
          "Fichier trop volumineux : " + (f.size / (1024 * 1024)).toFixed(1) +
          " Mo pour une limite de 4,5 Mo. Scannez en 150 dpi noir et blanc, ou " +
          "découpez le PDF en plusieurs fichiers.",
          "error"
        );
        return;
      }
      if (f) {
        showOriginalBlob(f, f.name);
        setStatus("Fichier chargé : " + f.name + ". Cliquez sur « Traduire le PDF ».", "info");
      }
    });

    async function loadDemoAndTranslate() {
      try {
        setStatus("Chargement du PDF de démonstration...", "info");
        const res = await fetch("./api/sample");
        if (!res.ok) {
          const errJson = await res.json().catch(() => ({}));
          throw new Error(describeError(errJson, res.status));
        }
        const blob = await res.blob();
        showOriginalBlob(blob, "sample_invoice.pdf");
        await runTranslation();
      } catch (err) {
        setStatus(err.message, "error");
      }
    }

    async function runTranslation() {
      if (!currentPdfBlob) {
        setStatus("Veuillez d'abord sélectionner un fichier PDF ou charger la démo.", "error");
        return;
      }
      // Le nom du fichier est envoyé via le multipart. Certains navigateurs/clients
      // transmettent des noms non-ASCII mal encodés : on les assainit côté client
      // (le serveur gère de toute façon les noms Unicode via RFC 5987).
      const lang = targetLangSelect.value;
      submitBtn.disabled = true;
      setStatus("Extraction pdfminer.six, traduction (" + lang + ") et fusion ReportLab/pypdf en cours...", "info");

      try {
        const buildForm = () => {
          const fd = new FormData();
          fd.append("file", currentPdfBlob, currentFilename);
          fd.append("target_lang", lang);
          return fd;
        };

        // Deux FormData distincts : un corps multipart ne peut pas être rejoué deux fois
        const [pdfRes, extRes] = await Promise.all([
          fetch("./api/translate", { method: "POST", body: buildForm() }),
          fetch("./api/extract", { method: "POST", body: buildForm() })
        ]);

        if (!pdfRes.ok) {
          const errJson = await pdfRes.json().catch(() => ({}));
          throw new Error(describeError(errJson, pdfRes.status));
        }

        const outBlob = await pdfRes.blob();
        const outUrl = URL.createObjectURL(outBlob);
        const outName = currentFilename.replace(/\\.pdf$/i, "") + "_" + lang + ".pdf";
        transFrame.src = outUrl;
        transFrame.style.display = "block";
        transEmpty.style.display = "none";
        transDownload.href = outUrl;
        transDownload.download = outName;
        transDownload.style.display = "inline";

        if (extRes.ok) {
          const data = await extRes.json();
          bboxTableBody.innerHTML = "";
          let count = 0;
          for (const p of data.pages) {
            for (const b of p.blocks) {
              count++;
              const tr = document.createElement("tr");
              const dir = lang === "ar" ? "rtl" : "ltr";
              tr.innerHTML =
                "<td>" + (p.page_index + 1) + "</td>" +
                "<td><code>[" + b.bbox.join(", ") + "]</code></td>" +
                "<td>" + b.font_size + "</td>" +
                "<td></td>" +
                "<td dir='" + dir + "'></td>";
              tr.children[3].textContent = b.text;
              tr.children[4].textContent = b.translated;
              bboxTableBody.appendChild(tr);
            }
          }
          bboxCount.textContent = String(count);
        }

        setStatus("Traduction vers '" + lang + "' terminée avec succès (" + outName + ").", "ok");
      } catch (err) {
        setStatus("Erreur : " + err.message, "error");
      } finally {
        submitBtn.disabled = false;
      }
    }

    loadSampleBtn.addEventListener("click", loadDemoAndTranslate);
    document.getElementById("translateForm").addEventListener("submit", (e) => {
      e.preventDefault();
      runTranslation();
    });

    // Charge automatiquement la démo au premier affichage
    window.addEventListener("DOMContentLoaded", loadDemoAndTranslate);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)
