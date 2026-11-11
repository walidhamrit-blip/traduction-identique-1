"""Suite de tests unitaires et d'intégration du microservice PDF Translator."""

from __future__ import annotations

import io
import json

from fastapi.testclient import TestClient
from pypdf import PdfReader

from api.index import app
from app.arabic_utils import format_arabic_text
from app.extractor import extract_pdf_layout
from app.pipeline import translate_pdf_bytes
from app.sample_generator import generate_sample_pdf
from app.schemas import TargetLang
from app.translator import mock_translate

client = TestClient(app)


def test_pdfminer_extracts_text_and_bboxes() -> None:
    """Vérifie l'extraction du texte et des Bounding Boxes (x0, y0, x1, y1) via pdfminer.six."""
    sample_pdf = generate_sample_pdf()
    pages = extract_pdf_layout(sample_pdf)

    assert len(pages) == 1
    page = pages[0]
    assert page.width > 500 and page.height > 800
    assert len(page.blocks) >= 15

    extracted_texts = [b.text for b in page.blocks]
    assert any("Commercial Invoice" in t for t in extracted_texts)
    assert any("Executive Summary" in t for t in extracted_texts)

    for block in page.blocks:
        assert 0 <= block.x0 < block.x1 <= page.width
        assert 0 <= block.y0 < block.y1 <= page.height
        assert block.font_size > 0


def test_arabic_reshaper_and_bidi_formatting() -> None:
    """Vérifie l'utilisation conjointe de arabic-reshaper et bidi.algorithm.get_display."""
    logical_arabic = "فاتورة تجارية 2026"
    visual_arabic = format_arabic_text(logical_arabic)

    assert visual_arabic != logical_arabic
    # Les nombres doivent conserver leur ordre gauche-à-droite grâce à l'algorithme BiDi
    assert "2026" in visual_arabic
    # Présence de glyphes Arabic Presentation Forms-B (U+FE70..U+FEFF) produits par arabic-reshaper
    assert any(0xFE70 <= ord(ch) <= 0xFEFF or 0xFB50 <= ord(ch) <= 0xFDFF for ch in visual_arabic)


def test_mock_translator_languages() -> None:
    """Vérifie la fonction mock pour en, fr et ar."""
    assert mock_translate("Commercial Invoice", TargetLang.FR) == "Facture Commerciale"
    assert mock_translate("Commercial Invoice", TargetLang.EN) == "Commercial Invoice"
    ar_result = mock_translate("Commercial Invoice", TargetLang.AR)
    assert ar_result == "فاتورة تجارية"


def test_pipeline_embeds_amiri_font_for_arabic() -> None:
    """Vérifie que le calque ReportLab embarque bien la police TTF Amiri et fusionne via pypdf."""
    sample_pdf = generate_sample_pdf()
    out_ar = translate_pdf_bytes(sample_pdf, target_lang=TargetLang.AR)

    assert out_ar.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(out_ar))
    assert len(reader.pages) == 1

    # Inspection des polices embarquées sur la page fusionnée
    fonts_dict = reader.pages[0].get("/Resources", {}).get("/Font", {})
    font_names = [
        str(font_obj.get_object().get("/BaseFont", ""))
        for font_obj in fonts_dict.values()
    ]
    assert any("Amiri" in name for name in font_names), f"Amiri absente de {font_names}"


def test_fastapi_translate_endpoints_all_languages() -> None:
    """Teste l'endpoint HTTP POST /translate pour en, fr et ar."""
    sample_pdf = generate_sample_pdf()

    for lang in ("en", "fr", "ar"):
        response = client.post(
            "/translate",
            files={"file": ("invoice.pdf", sample_pdf, "application/pdf")},
            data={"target_lang": lang},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.headers["x-target-language"] == lang
        assert response.content.startswith(b"%PDF-")

        merged_reader = PdfReader(io.BytesIO(response.content))
        assert len(merged_reader.pages) == 1


def test_fastapi_rejects_invalid_inputs() -> None:
    """Vérifie la validation stricte des entrées (fichier non-PDF et langue non supportée)."""
    sample_pdf = generate_sample_pdf()

    bad_lang = client.post(
        "/translate",
        files={"file": ("invoice.pdf", sample_pdf, "application/pdf")},
        data={"target_lang": "es"},
    )
    assert bad_lang.status_code == 422

    bad_file = client.post(
        "/translate",
        files={"file": ("fake.pdf", b"not a pdf content", "application/pdf")},
        data={"target_lang": "fr"},
    )
    assert bad_file.status_code == 400


# ---------------------------------------------------------------------------
# Non-régression « erreur HTTP 500 » : chaque cas doit produire une réponse
# JSON explicite (code + message + hint), jamais un 500 muet.
# ---------------------------------------------------------------------------


def _make_encrypted_pdf() -> bytes:
    """PDF valide mais protégé par mot de passe (cause classique de 500 côté pdfminer)."""
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(generate_sample_pdf())))
    writer.encrypt("s3cret")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_encrypted_pdf_returns_400_not_500() -> None:
    response = client.post(
        "/translate",
        files={"file": ("secret.pdf", _make_encrypted_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "encrypted_pdf"
    assert detail["hint"]


def test_truncated_pdf_returns_400_not_500() -> None:
    truncated = generate_sample_pdf()[:400]
    response = client.post(
        "/translate",
        files={"file": ("broken.pdf", truncated, "application/pdf")},
        data={"target_lang": "fr"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_pdf"


def test_extract_on_encrypted_pdf_returns_400_not_500() -> None:
    response = client.post(
        "/extract",
        files={"file": ("secret.pdf", _make_encrypted_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "encrypted_pdf"


def test_upload_limit_uses_configured_value(monkeypatch) -> None:
    import api.index as api_module

    monkeypatch.setattr(api_module, "MAX_UPLOAD_BYTES", 10)
    monkeypatch.setattr(api_module, "MAX_UPLOAD_MB", 1)
    response = client.post(
        "/translate",
        files={"file": ("big.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "en"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"


def test_too_many_pages_returns_explicit_error(monkeypatch) -> None:
    import app.extractor as extractor_module

    monkeypatch.setattr(extractor_module, "MAX_PAGES", 1)
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(generate_sample_pdf())))
    writer.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    writer.write(buf)

    response = client.post(
        "/translate",
        files={"file": ("two_pages.pdf", buf.getvalue(), "application/pdf")},
        data={"target_lang": "en"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "too_many_pages"


def test_diag_endpoint_reports_fonts_and_versions() -> None:
    response = client.get("/diag")
    assert response.status_code == 200
    payload = response.json()
    assert payload["fonts"]["amiri"]["ok"] is True
    assert payload["status"] == "ok"
    assert "pypdf" in payload["dependencies"]
    assert "ERREUR" not in payload["dependencies"]["pypdf"]


def test_health_still_ok() -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# Non-régression : noms de fichiers non-ASCII (arabe, cyrillique, accents, %)
# provoquaient « UnicodeEncodeError: 'latin-1' codec can't encode » -> HTTP 500
# car les en-têtes HTTP ne transportent pas d'Unicode brut (RFC 5987).
# ---------------------------------------------------------------------------


import pytest  # noqa: E402
from urllib.parse import unquote  # noqa: E402

from app.filenames import build_content_disposition, safe_ascii_stem  # noqa: E402


@pytest.mark.parametrize(
    "filename",
    [
        "فاتورة تجارية.pdf",
        "Facture été 2026.pdf",
        "договор № 12.pdf",
        "KONSIMANTO DRAFT ASSADAKA.pdf",
        "invoice 100%.pdf",
        "機密文書.pdf",
    ],
)
def test_non_ascii_filename_does_not_break_response(filename: str) -> None:
    """Le PDF doit être renvoyé (200) et l'en-tête rester 100 % ASCII."""
    response = client.post(
        "/translate",
        files={"file": (filename, generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")

    disposition = response.headers["content-disposition"]
    assert disposition.isascii(), disposition
    assert "filename*=UTF-8''" in disposition
    # Le nom d'origine reste accessible au navigateur via filename*
    decoded = unquote(disposition.split("filename*=UTF-8''")[1])
    assert decoded.endswith("_ar.pdf")


def test_content_disposition_header_injection_is_sanitized() -> None:
    """Guillemets, points-virgules et sauts de ligne ne doivent pas casser l'en-tête."""
    header = build_content_disposition('evil"; rm -rf.pdf\r\nX-Injected: 1', "en")
    assert "\r" not in header and "\n" not in header
    assert header.isascii()
    assert "X-Injected" not in header or "%" in header


def test_safe_ascii_stem_fallbacks() -> None:
    assert safe_ascii_stem("فاتورة.pdf") == "document"
    assert safe_ascii_stem("Facture été.pdf") == "Facture_ete"
    assert safe_ascii_stem(None) == "document"
    assert safe_ascii_stem("../../etc/passwd.pdf") == "passwd"
    assert len(safe_ascii_stem("a" * 500 + ".pdf")) <= 80


# ---------------------------------------------------------------------------
# Chaîne de traduction gratuite : fournisseurs factices (aucun réseau en test).
# ---------------------------------------------------------------------------


from app.translation.chain import TranslationChain, clear_cache  # noqa: E402
from app.translation.providers import (  # noqa: E402
    ProviderUnavailable,
    TranslationProvider,
)
from app.translation.sanitizer import is_untranslatable, split_long_segment  # noqa: E402


class FakeProvider(TranslationProvider):
    name = "fake"

    def __init__(self, mapping: dict[str, str] | None = None, fail: bool = False) -> None:
        self.mapping = mapping or {}
        self.fail = fail
        self.seen: list[str] = []

    def translate_batch(self, texts, source="auto", target="ar"):
        if self.fail:
            raise ProviderUnavailable("service coupé")
        self.seen.extend(texts)
        return [self.mapping.get(t, f"AR:{t}") for t in texts]


def test_codes_and_references_are_never_translated() -> None:
    """Les codes conteneurs, références et artefacts web restent intacts."""
    for segment in (
        "I000503614",
        "CAAU2973244 20DC",
        "info@ais.ly",
        "+218 91 616 2121",
        "3/4/1/2",
        "4960,000",
        "www.ais.ly",
        "function(e){return e}",
        "4922,20,00,00,19",
    ):
        assert is_untranslatable(segment), segment

    # Le vrai texte doit au contraire être traduit
    for segment in ("Port of Loading", "Gross Weight", "Standard Conditions (1997)"):
        assert not is_untranslatable(segment), segment


def test_chain_translates_only_translatable_segments() -> None:
    clear_cache()
    provider = FakeProvider({"Port of Loading": "ميناء الشحن"})
    chain = TranslationChain(providers=[provider])

    texts = ["Port of Loading", "CAAU2973244 20DC", "Gross Weight"]
    result = chain.translate_segments(texts, source="en", target="ar")

    assert result.translations == ["ميناء الشحن", "CAAU2973244 20DC", "AR:Gross Weight"]
    assert provider.seen == ["Port of Loading", "Gross Weight"]
    assert result.skipped_count == 1
    assert result.provider == "fake"


def test_chain_deduplicates_repeated_labels() -> None:
    """Un libellé répété 30 fois ne doit être envoyé qu'une seule fois au moteur."""
    clear_cache()
    provider = FakeProvider()
    chain = TranslationChain(providers=[provider])

    texts = ["Gross Weight"] * 30 + ["Port of Loading"] * 5
    chain.translate_segments(texts, source="en", target="ar")

    assert provider.seen.count("Gross Weight") == 1
    assert provider.seen.count("Port of Loading") == 1


def test_chain_falls_back_honestly_when_all_providers_fail() -> None:
    """Aucun fournisseur joignable : le texte d'origine est conservé, rien n'est inventé."""
    clear_cache()
    chain = TranslationChain(providers=[FakeProvider(fail=True)])
    result = chain.translate_segments(["Port of Loading"], source="en", target="ar")

    assert result.provider == "offline"
    assert result.translations == ["Port of Loading"]  # pas de faux texte arabe
    assert result.degraded is True


def test_long_segments_are_split_without_losing_content() -> None:
    paragraph = ("The Freight Forwarder shall be liable. " * 120).strip()
    pieces = split_long_segment(paragraph, max_chars=500)
    assert len(pieces) > 1
    assert all(len(piece) <= 500 for piece in pieces)
    assert "Freight Forwarder" in " ".join(pieces)


def test_real_pdf_translation_keeps_layout_masks_only_translated_blocks(monkeypatch) -> None:
    """Le PDF traduit ne doit jamais contenir de texte de remplissage inventé."""
    clear_cache()
    from app import pipeline as pipeline_module

    class StubChain:
        def translate_segments(self, texts, source, target):
            from app.translation.chain import TranslationResult

            result = TranslationResult()
            result.provider = "stub"
            result.translations = [f"ترجمة {t}" for t in texts]
            result.translated_count = len(texts)
            return result

    monkeypatch.setattr(pipeline_module, "TranslationChain", StubChain)
    output = pipeline_module.translate_pdf_bytes(generate_sample_pdf(), "ar")
    text = PdfReader(io.BytesIO(output)).pages[0].extract_text()
    # Le texte est reshapé en formes de présentation arabes (U+FB50..U+FEFF)
    has_arabic = any("\uFB50" <= ch <= "\uFEFF" or "\u0600" <= ch <= "\u06FF" for ch in text)
    assert has_arabic
    # Aucun texte de remplissage du faux traducteur
    fillers = ("نص مترجم", "\uFEE1\uFEE8\uFE8E \uFEA9\uFEF2\uFEB3\uFEE2")
    assert not any(filler in text for filler in fillers)


# ---------------------------------------------------------------------------
# Éditeur avant export : police, taille, alignement, position, corrections
# et formats d'entrée/sortie.
# ---------------------------------------------------------------------------


from app.fonts import font_catalog, get_font_family  # noqa: E402
from app.schemas import BlockEdit, StyleOptions, TextAlign, VerticalAlign  # noqa: E402


def test_font_catalog_only_lists_verified_fonts() -> None:
    """Les polices Google cassant les ligatures arabes ne doivent pas être proposées."""
    ids = {font["id"] for font in font_catalog()}
    assert {"amiri", "noto-naskh", "noto-sans-arabic", "ibm-plex-arabic", "roboto"} <= ids
    # Vérifiées comme défectueuses sur les Presentation Forms arabes :
    assert not ({"cairo", "tajawal", "almarai", "readex-pro"} & ids)


def test_latin_font_is_replaced_by_arabic_font_for_arabic() -> None:
    assert get_font_family("roboto", is_arabic=True).id == "amiri"
    assert get_font_family("roboto", is_arabic=False).id == "roboto"
    assert get_font_family("inconnue", is_arabic=True).id == "amiri"


def test_style_changes_embedded_font() -> None:
    """Choisir une autre police doit réellement changer la police embarquée."""
    pdf = generate_sample_pdf()
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", pdf, "application/pdf")},
        data={"target_lang": "ar", "style": '{"font_id": "noto-sans-arabic"}'},
    )
    assert out.status_code == 200
    reader = PdfReader(io.BytesIO(out.content))
    fonts = reader.pages[0].get("/Resources", {}).get("/Font", {})
    names = [str(f.get_object().get("/BaseFont", "")) for f in fonts.values()]
    assert any("NotoSansArabic" in name for name in names), names


def test_style_size_and_color_are_applied() -> None:
    pdf = generate_sample_pdf()
    style = (
        '{"font_size": 16, "align": "center", "vertical_align": "top",'
        ' "color": "#B3121B", "offset_x": 4, "offset_y": 3, "line_spacing": 1.3}'
    )
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", pdf, "application/pdf")},
        data={"target_lang": "ar", "style": style},
    )
    assert out.status_code == 200
    assert out.content.startswith(b"%PDF-")


def test_invalid_style_json_returns_400() -> None:
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "style": "{pas du json"},
    )
    assert out.status_code == 400
    assert out.json()["detail"]["code"] == "invalid_style"


def test_preview_returns_editable_blocks_and_fonts() -> None:
    out = client.post(
        "/preview",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "page": "1"},
    )
    assert out.status_code == 200
    payload = out.json()
    assert payload["status"] == "ok"
    assert payload["page"] == 1 and payload["page_count"] >= 1
    assert payload["blocks"], "aucun bloc éditable renvoyé"
    first = payload["blocks"][0]
    assert {"block_id", "text", "translation", "bbox", "font_size"} <= set(first)
    assert payload["fonts"] and payload["output_formats"]
    assert payload["preview_png_base64"]


def test_block_edit_replaces_text_and_position() -> None:
    preview = client.post(
        "/preview",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    ).json()
    block = preview["blocks"][0]
    edits = json.dumps(
        [
            {
                "block_id": block["block_id"],
                "text": "CORRECTION MANUELLE",
                "x0": block["bbox"][0] + 12,
                "font_size": 14,
            }
        ]
    )
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "edits": edits},
    )
    assert out.status_code == 200
    text = PdfReader(io.BytesIO(out.content)).pages[0].extract_text()
    assert "CORRECTION MANUELLE" in text


def test_hidden_block_is_removed_for_ar() -> None:
    preview = client.post(
        "/preview",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    ).json()
    block = preview["blocks"][0]
    edits = json.dumps([{"block_id": block["block_id"], "hidden": True}])
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "edits": edits},
    )
    assert out.status_code == 200


@pytest.mark.parametrize("fmt", ["pdf", "png", "txt", "docx"])
def test_output_formats(fmt: str) -> None:
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "output_format": fmt},
    )
    assert out.status_code == 200
    assert out.headers["x-output-format"] == fmt
    assert len(out.content) > 20
    if fmt == "pdf":
        assert out.content.startswith(b"%PDF-")
    if fmt == "png":
        assert out.content.startswith(b"\x89PNG")
    if fmt == "txt":
        assert "Page 1" in out.content.decode("utf-8")


def test_unsupported_output_format_returns_415() -> None:
    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar", "output_format": "xlsx"},
    )
    assert out.status_code == 415
    assert out.json()["detail"]["code"] == "unsupported_output_format"


def test_txt_input_and_docx_input_are_converted() -> None:
    txt = "Port of Loading\n\nGross Weight : 4960,000 kgs\n".encode()
    out = client.post("/translate", files={"file": ("notes.txt", txt, "text/plain")}, data={"target_lang": "ar"})
    assert out.status_code == 200
    assert out.content.startswith(b"%PDF-")

    import docx

    document = docx.Document()
    document.add_heading("Commercial Invoice", 1)
    document.add_paragraph("Bill To: ASSADAKA GROUP")
    buffer = io.BytesIO()
    document.save(buffer)

    out = client.post(
        "/translate",
        files={
            "file": (
                "facture.docx",
                buffer.getvalue(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
        data={"target_lang": "ar"},
    )
    assert out.status_code == 200
    assert out.content.startswith(b"%PDF-")


def test_editor_page_and_script_are_served() -> None:
    page = client.get("/editor")
    assert page.status_code == 200
    assert "Éditeur" in page.text
    script = client.get("/editor.js")
    assert script.status_code == 200
    assert "exportBtn" in script.text


def test_formats_endpoint_lists_supported_types() -> None:
    payload = client.get("/formats").json()
    assert "pdf" in payload["input"] and "docx" in payload["input"]
    assert set(payload["output"]) == {"pdf", "png", "txt", "docx"}


def test_style_options_helpers() -> None:
    style = StyleOptions(font_size=12, font_size_scale=1.5, align=TextAlign.CENTER)
    assert style.resolved_font_size(8) == 18  # taille imposée × échelle
    assert StyleOptions().resolved_font_size(10) == 10  # taille détectée
    assert BlockEdit(block_id="x", text="y").hidden is False
    assert VerticalAlign.MIDDLE.value == "middle"


# ---------------------------------------------------------------------------
# Spécificités Vercel : limites de corps (entrée ET sortie) et durée.
# ---------------------------------------------------------------------------


def test_vercel_body_limit_is_enforced_on_input() -> None:
    """La limite d'entrée ne doit jamais dépasser celle de Vercel (4,5 Mo)."""
    import app.config as config_module

    assert config_module.MAX_UPLOAD_BYTES < config_module.VERCEL_BODY_LIMIT_BYTES
    assert config_module.MAX_RESPONSE_BYTES < config_module.VERCEL_BODY_LIMIT_BYTES


def test_oversized_translated_pdf_returns_explicit_error(monkeypatch) -> None:
    """Un PDF traduit qui dépasserait 4,5 Mo doit produire un 413 explicite, pas un 413 opaque."""
    import api.index as api_module

    monkeypatch.setattr(api_module, "MAX_RESPONSE_BYTES", 1024)  # 1 Ko pour le test
    monkeypatch.setattr(api_module, "compress_pdf", lambda data: data)

    out = client.post(
        "/translate",
        files={"file": ("a.pdf", generate_sample_pdf(), "application/pdf")},
        data={"target_lang": "ar"},
    )
    assert out.status_code == 413
    detail = out.json()["detail"]
    assert detail["code"] == "payload_too_large"
    assert "4,5 Mo" in detail["message"]


def test_pdf_compression_never_makes_file_bigger() -> None:
    from app.exporters import compress_pdf

    original = generate_sample_pdf()
    compressed = compress_pdf(original)
    assert len(compressed) <= len(original)
    assert compressed.startswith(b"%PDF-")


def test_vercel_config_is_valid_and_uses_hobby_max_duration() -> None:
    import json
    from pathlib import Path

    config = json.loads(Path("vercel.json").read_text())
    assert config["framework"] == "fastapi"
    entry = config["functions"]["api/index.py"]
    assert entry["maxDuration"] <= 300  # maximum du plan Hobby
    assert "excludeFiles" in entry


def test_platform_defaults_differ_on_vercel(monkeypatch) -> None:
    """Le budget temps doit être plus généreux sur Vercel (300 s autorisées)."""
    import importlib

    import app.config as config_module

    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("PDF_TRANSLATE_BUDGET_S", raising=False)
    reloaded = importlib.reload(config_module)
    assert reloaded.TRANSLATE_TIME_BUDGET_S >= 60
    monkeypatch.delenv("VERCEL", raising=False)
    importlib.reload(config_module)


# ---------------------------------------------------------------------------
# Robustesse du rendu : un PDF ne doit plus jamais échouer à cause des `assert`
# internes de ReportLab (erreur `pdf_processing_failed` / AssertionError en prod).
# ---------------------------------------------------------------------------


def test_pdf_writer_produces_valid_pdf_with_embedded_font(tmp_path) -> None:
    """Le moteur PDF interne écrit un fichier lisible, avec police embarquée et texte."""
    import pymupdf

    from app.pdf_writer import GlyphRun, PageContent, PdfWriter

    from pathlib import Path as _Path

    writer = PdfWriter()
    writer.register_font("f", _Path("assets/fonts/Amiri-Regular.ttf"))
    page = PageContent(width=595, height=842)
    page.masks.append((40.0, 700.0, 400.0, 730.0))
    page.runs.append(GlyphRun(text="فاتورة", x=200.0, y=712.0, font_size=14.0, font_key="f"))
    writer.add_page(page)
    data = writer.build()

    assert data.startswith(b"%PDF-")
    path = tmp_path / "out.pdf"
    path.write_bytes(data)
    document = pymupdf.open(path)
    try:
        assert document.page_count == 1
        # Le texte est restitué dans l'ordre logique (RTL) grâce à /ToUnicode.
        assert document[0].get_text().replace("\n", "") != ""
        assert "فاتورة" in document[0].get_text() or "ﺓﺭﻭﺕﺍﻑ" in document[0].get_text()
        assert any("Amiri" in font[3] for font in document[0].get_fonts())
    finally:
        document.close()


def test_pdf_writer_handles_degenerate_page_size() -> None:
    """Une page minuscule ou nulle ne doit pas produire un PDF vide/cassé."""
    from app.pdf_writer import PageContent, PdfWriter

    writer = PdfWriter()
    writer.add_page(PageContent(width=0.0, height=0.0))
    data = writer.build()
    assert data.startswith(b"%PDF-")
    assert len(data) > 200
    assert b"%%EOF" in data[-2048:]


def test_render_falls_back_when_reportlab_fails(monkeypatch) -> None:
    """Si ReportLab lève une AssertionError, le PDF est quand même produit."""
    import app.pipeline as pipeline
    from app.schemas import TargetLang

    def boom(**_: object) -> bytes:
        raise AssertionError()

    monkeypatch.setattr(pipeline, "build_overlay_pdf", boom)
    report = pipeline.PipelineReport()
    output = pipeline.translate_pdf_bytes(
        generate_sample_pdf(), TargetLang.FR, report=report
    )

    assert output.startswith(b"%PDF-")
    assert b"%%EOF" in output[-2048:]
    assert report.warnings
    assert any("secours" in warning for warning in report.warnings)


def test_render_falls_back_to_pymupdf_merge(monkeypatch) -> None:
    """Si la fusion pypdf échoue aussi, PyMuPDF prend le relais."""
    import app.pipeline as pipeline
    from app.schemas import TargetLang

    monkeypatch.setattr(
        pipeline, "merge_overlay_with_original", lambda *a, **k: (_ for _ in ()).throw(AssertionError())
    )
    output = pipeline.translate_pdf_bytes(generate_sample_pdf(), TargetLang.FR)

    assert output.startswith(b"%PDF-")
    assert b"%%EOF" in output[-2048:]
    import pymupdf

    document = pymupdf.open(stream=output, filetype="pdf")
    try:
        assert document.page_count >= 1
    finally:
        document.close()


def test_render_failure_raises_clear_error_when_every_engine_fails(monkeypatch) -> None:
    """En dernier recours, le message d'erreur explique quoi faire (export TXT/DOCX)."""
    import app.pipeline as pipeline
    from app.errors import PdfProcessingError
    from app.schemas import TargetLang

    def boom(**_: object) -> bytes:
        raise AssertionError()

    monkeypatch.setattr(pipeline, "build_overlay_pdf", boom)
    monkeypatch.setattr(pipeline, "build_overlay_pdf_fallback", boom)

    with pytest.raises(PdfProcessingError) as excinfo:
        pipeline.translate_pdf_bytes(generate_sample_pdf(), TargetLang.FR)
    assert "TXT" in (excinfo.value.hint or "")


def test_compress_pdf_never_returns_empty_or_truncated_pdf() -> None:
    from app.exporters import compress_pdf

    assert compress_pdf(b"") == b""
    broken = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    assert compress_pdf(broken) == broken  # illisible -> renvoyé tel quel, pas vidé


def test_diag_exposes_render_selftest() -> None:
    """`/api/diag` indique quel moteur de rendu fonctionne réellement en production."""
    payload = client.get("/api/diag").json()
    selftest = payload["render"]
    assert selftest["reportlab"]["ok"] is True
    assert selftest["interne"]["ok"] is True
