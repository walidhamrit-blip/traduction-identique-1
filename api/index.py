"""Point d'entrée FastAPI & Vercel Serverless Function (`api/index.py`).

Compatible simultanément avec :
- Un déploiement Vercel Serverless Python (`api/index.py` + `vercel.json`)
- Une exécution microservice standard via Uvicorn / Docker (`uvicorn main:app`)
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response

from app.arabic_utils import format_arabic_text
from app.extractor import extract_pdf_layout
from app.pipeline import translate_pdf_bytes
from app.renderer import ensure_fonts_registered
from app.sample_generator import generate_sample_pdf
from app.schemas import TargetLang
from app.translator import mock_translate

MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024  # 20 Mo

app = FastAPI(
    title="PDF Layout-Preserving Translator Microservice",
    description=(
        "Microservice Python (FastAPI / Vercel Serverless) de traduction de fichiers PDF "
        "vers `en`, `fr` ou `ar` avec extraction des Bounding Boxes (`pdfminer.six`), "
        "reshaping + BiDi RTL (`arabic-reshaper` & `python-bidi`), calque `reportlab` "
        "et fusion `pypdf`."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def _handle_translate_request(
    file: UploadFile,
    target_lang: TargetLang,
) -> Response:
    """Valide l'entrée, exécute le pipeline de traduction PDF et retourne le binaire PDF."""
    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Le fichier envoyé est vide.")

    if len(pdf_bytes) > MAX_PDF_SIZE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="Le fichier dépasse la taille maximale autorisée (20 Mo).",
        )

    if not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise HTTPException(
            status_code=400,
            detail="Format invalide : seuls les fichiers PDF valides (%PDF-) sont acceptés.",
        )

    try:
        translated_pdf = translate_pdf_bytes(pdf_bytes, target_lang=target_lang)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Erreur lors du traitement du PDF : {exc}",
        ) from exc

    original_stem = Path(file.filename or "document.pdf").stem
    safe_stem = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in original_stem)
    out_filename = f"{safe_stem or 'document'}_{target_lang.value}.pdf"

    return Response(
        content=translated_pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="{out_filename}"',
            "X-Target-Language": target_lang.value,
        },
    )


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
    file: Annotated[UploadFile, File(description="Fichier PDF source à traduire")],
    target_lang: Annotated[
        TargetLang | None,
        Form(description="Langue cible ('en', 'fr' ou 'ar')"),
    ] = None,
    target_lang_query: Annotated[
        TargetLang | None,
        Query(alias="target_lang", description="Langue cible en query param optionnel"),
    ] = None,
) -> Response:
    resolved_lang = target_lang or target_lang_query
    if resolved_lang is None:
        raise HTTPException(
            status_code=422,
            detail="Le paramètre 'target_lang' ('en', 'fr' ou 'ar') est requis.",
        )
    return await _handle_translate_request(file=file, target_lang=resolved_lang)


@app.post(
    "/extract",
    summary="Inspecter les Bounding Boxes extraites via pdfminer.six et leur traduction",
)
@app.post("/api/extract", include_in_schema=False)
async def inspect_extraction_endpoint(
    file: Annotated[UploadFile, File(description="Fichier PDF à analyser")],
    target_lang: Annotated[TargetLang, Form(description="Langue cible")] = TargetLang.AR,
) -> dict:
    pdf_bytes = await file.read()
    if not pdf_bytes or not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="Fichier PDF invalide.")

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
        headers={"Content-Disposition": 'inline; filename="sample_invoice.pdf"'},
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
      if (f) {
        showOriginalBlob(f, f.name);
        setStatus("Fichier chargé : " + f.name + ". Cliquez sur « Traduire le PDF ».", "info");
      }
    });

    async function loadDemoAndTranslate() {
      try {
        setStatus("Chargement du PDF de démonstration...", "info");
        const res = await fetch("./api/sample");
        if (!res.ok) throw new Error("Impossible de récupérer le PDF de démonstration.");
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
      const lang = targetLangSelect.value;
      submitBtn.disabled = true;
      setStatus("Extraction pdfminer.six, traduction (" + lang + ") et fusion ReportLab/pypdf en cours...", "info");

      try {
        const formData = new FormData();
        formData.append("file", currentPdfBlob, currentFilename);
        formData.append("target_lang", lang);

        const [pdfRes, extRes] = await Promise.all([
          fetch("./api/translate", { method: "POST", body: formData }),
          fetch("./api/extract", { method: "POST", body: formData })
        ]);

        if (!pdfRes.ok) {
          const errJson = await pdfRes.json().catch(() => ({}));
          throw new Error(errJson.detail || ("Erreur HTTP " + pdfRes.status));
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
