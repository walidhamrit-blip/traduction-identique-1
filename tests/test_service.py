"""Suite de tests unitaires et d'intégration du microservice PDF Translator."""

from __future__ import annotations

import io
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
