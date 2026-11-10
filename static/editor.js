/* Éditeur de traduction : réglages visuels + corrections par bloc.
 * Tout est appliqué côté serveur (/preview et /translate) : l'aperçu et le
 * fichier exporté passent par le même code, donc « ce que vous voyez est ce
 * que vous obtenez ».
 */
"use strict";

const API = "./api";
const state = {
  file: null,
  filename: "document.pdf",
  page: 1,
  pageCount: 1,
  zoom: 100,
  blocks: [],
  edits: new Map(), // block_id -> {text, x0, y0, font_size, hidden}
  selected: null,
  fonts: [],
  style: {
    font_id: "",
    sizeMode: "auto",
    font_size: null,
    font_size_scale: 1,
    align: "right",
    vertical_align: "middle",
    color: "#14171e",
    line_spacing: 1.22,
    offset_x: 0,
    offset_y: 0,
  },
  busy: false,
};

const $ = (id) => document.getElementById(id);
const statusBox = $("status");

function showStatus(message, kind = "info", sticky = false) {
  statusBox.textContent = message;
  statusBox.className = "status show " + kind;
  statusBox.dataset.sticky = sticky ? "1" : "0";
}

function clearStatus() {
  statusBox.className = "status";
  statusBox.textContent = "";
}

function describeError(payload, httpStatus) {
  const detail = payload && payload.detail;
  if (detail && typeof detail === "object") {
    const parts = [detail.message || detail.code || "Erreur"];
    if (detail.code) parts.push("(code : " + detail.code + ")");
    if (detail.hint) parts.push("— " + detail.hint);
    return parts.join(" ");
  }
  if (typeof detail === "string") return detail;
  return "Erreur HTTP " + httpStatus;
}

/* ------------------------------------------------------------------ style */
function stylePayload() {
  const s = state.style;
  const payload = {
    font_id: s.font_id || null,
    align: s.align,
    vertical_align: s.vertical_align,
    color: s.color,
    line_spacing: s.line_spacing,
    offset_x: s.offset_x,
    offset_y: s.offset_y,
    font_size_scale: 1,
    font_size: null,
  };
  if (s.sizeMode === "fixed" && s.font_size) payload.font_size = s.font_size;
  if (s.sizeMode === "scale") payload.font_size_scale = s.font_size_scale;
  return payload;
}

function editsPayload() {
  const list = [];
  state.edits.forEach((edit, blockId) => {
    list.push({
      block_id: blockId,
      text: edit.text ?? null,
      x0: edit.x0 ?? null,
      y0: edit.y0 ?? null,
      font_size: edit.font_size ?? null,
      hidden: !!edit.hidden,
    });
  });
  return list;
}

function buildForm() {
  const form = new FormData();
  form.append("file", state.file, state.filename);
  form.append("target_lang", $("langSelect").value);
  form.append("style", JSON.stringify(stylePayload()));
  const edits = editsPayload();
  if (edits.length) form.append("edits", JSON.stringify(edits));
  return form;
}

/* -------------------------------------------------------------- aperçu */
async function refreshPreview() {
  if (!state.file || state.busy) return;
  state.busy = true;
  showStatus("Génération de l'aperçu…", "info");
  try {
    const form = buildForm();
    form.append("page", String(state.page));
    const response = await fetch(`${API}/preview`, { method: "POST", body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(describeError(payload, response.status));
    }
    const data = await response.json();
    state.pageCount = data.page_count || 1;
    state.blocks = data.blocks || [];
    if (state.fonts.length === 0) renderFonts(data.fonts || []);

    if (data.preview_png_base64) {
      $("previewImg").src = "data:image/png;base64," + data.preview_png_base64;
      $("previewImg").style.display = "block";
      $("placeholder").style.display = "none";
      applyZoom();
    }
    $("exportBtn").disabled = false;
    $("pageLabel").textContent = `page ${state.page} / ${state.pageCount}`;
    renderBlocks();

    const translation = data.translation || {};
    $("providerBadge").textContent = "moteur : " + (translation.provider || "—");
    if (translation.degraded) {
      showStatus(
        "Aucun service de traduction joignable : le texte d'origine est conservé. " +
          "Vérifiez /api/diag.",
        "warn",
        true
      );
    } else {
      clearStatus();
    }
  } catch (error) {
    showStatus("Erreur : " + error.message, "err", true);
  } finally {
    state.busy = false;
  }
}

function applyZoom() {
  $("previewImg").style.width = state.zoom + "%";
  $("previewImg").style.maxWidth = "none";
  $("zoomReset").textContent = state.zoom + " %";
}

/* -------------------------------------------------------------- polices */
function renderFonts(fonts) {
  state.fonts = fonts;
  const select = $("fontSelect");
  select.innerHTML = "";
  const isArabic = $("langSelect").value === "ar";

  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = isArabic ? "Automatique (Amiri)" : "Automatique (DejaVu Sans)";
  select.appendChild(auto);

  fonts.forEach((font) => {
    const option = document.createElement("option");
    option.value = font.id;
    option.textContent = font.label + (isArabic && !font.arabic ? " — incompatible arabe" : "");
    option.disabled = isArabic && !font.arabic;
    select.appendChild(option);
  });
}

/* --------------------------------------------------------------- blocs */
function renderBlocks() {
  const list = $("blockList");
  list.innerHTML = "";
  if (state.blocks.length === 0) {
    list.innerHTML = '<div class="hint">Aucun bloc de texte sur cette page.</div>';
    return;
  }
  state.blocks.forEach((block) => {
    const edit = state.edits.get(block.block_id) || {};
    const div = document.createElement("div");
    div.className = "block" + (state.selected === block.block_id ? " active" : "");
    div.dataset.id = block.block_id;

    const src = document.createElement("div");
    src.className = "src";
    src.textContent = block.text;

    const tr = document.createElement("div");
    tr.className = "tr";
    tr.dir = $("langSelect").value === "ar" ? "rtl" : "ltr";
    tr.textContent = edit.hidden
      ? "— (bloc masqué) —"
      : edit.text ?? block.translation ?? block.text;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent =
      `taille ${(edit.font_size || block.font_size).toFixed(1)} pt` +
      (edit.x0 || edit.y0 ? ` · déplacé (${edit.x0 || 0}, ${edit.y0 || 0})` : "") +
      (edit.text !== undefined && edit.text !== null ? " · texte corrigé" : "");

    div.append(src, tr, meta);
    div.addEventListener("click", () => selectBlock(block));
    list.appendChild(div);
  });

  const count = state.edits.size;
  $("editCount").textContent = count
    ? `${count} bloc(s) modifié(s) — l'export en tiendra compte.`
    : "Aucune correction pour l'instant.";
}

function selectBlock(block) {
  state.selected = block.block_id;
  const edit = state.edits.get(block.block_id) || {};
  $("editorPane").style.display = "block";
  $("blockHint").style.display = "none";
  $("selectedId").textContent = block.block_id;
  $("blockText").value = edit.text ?? block.translation ?? block.text;
  $("blockText").dir = $("langSelect").value === "ar" ? "rtl" : "ltr";
  $("blockFontSize").value = edit.font_size || block.font_size;
  $("blockX").value = edit.x0 || 0;
  $("blockY").value = edit.y0 || 0;
  document.querySelectorAll(".block").forEach((el) => {
    el.classList.toggle("active", el.dataset.id === block.block_id);
  });
}

function currentBlock() {
  return state.blocks.find((b) => b.block_id === state.selected) || null;
}

$("applyBlock").addEventListener("click", () => {
  const block = currentBlock();
  if (!block) return;
  const edit = state.edits.get(block.block_id) || {};
  const text = $("blockText").value;
  if (text.trim() && text !== (block.translation ?? block.text)) edit.text = text;
  else delete edit.text;

  const size = parseFloat($("blockFontSize").value);
  if (!Number.isNaN(size) && Math.abs(size - block.font_size) > 0.01) edit.font_size = size;
  else delete edit.font_size;

  const dx = parseFloat($("blockX").value) || 0;
  const dy = parseFloat($("blockY").value) || 0;
  if (dx) edit.x0 = block.bbox[0] + dx;
  else delete edit.x0;
  if (dy) edit.y0 = block.bbox[1] - dy; // Y écran inversé par rapport aux points PDF
  else delete edit.y0;

  if (Object.keys(edit).length) state.edits.set(block.block_id, edit);
  else state.edits.delete(block.block_id);
  renderBlocks();
  if ($("autoPreview").checked) refreshPreview();
});

$("hideBlock").addEventListener("click", () => {
  const block = currentBlock();
  if (!block) return;
  const edit = state.edits.get(block.block_id) || {};
  edit.hidden = true;
  state.edits.set(block.block_id, edit);
  renderBlocks();
  if ($("autoPreview").checked) refreshPreview();
});

$("resetBlock").addEventListener("click", () => {
  const block = currentBlock();
  if (!block) return;
  state.edits.delete(block.block_id);
  selectBlock(block);
  renderBlocks();
  if ($("autoPreview").checked) refreshPreview();
});

/* --------------------------------------------------------- import fichier */
$("fileInput").addEventListener("change", (event) => {
  const file = event.target.files[0];
  if (!file) return;
  state.file = file;
  state.filename = file.name;
  state.edits.clear();
  state.blocks = [];
  state.selected = null;
  $("editorPane").style.display = "none";
  $("blockHint").style.display = "block";
  showStatus(`Fichier chargé : ${file.name}. Cliquez sur « Préparer l'aperçu ».`, "info");
});

$("loadBtn").addEventListener("click", () => {
  if (!state.file) {
    showStatus("Choisissez d'abord un fichier ou chargez la démo.", "err", true);
    return;
  }
  state.page = 1;
  refreshPreview();
});

$("sampleBtn").addEventListener("click", async () => {
  try {
    showStatus("Chargement du PDF de démonstration…", "info");
    const response = await fetch(`${API}/sample`);
    if (!response.ok) throw new Error("Démo indisponible (" + response.status + ")");
    const blob = await response.blob();
    state.file = new File([blob], "sample_invoice.pdf", { type: "application/pdf" });
    state.filename = "sample_invoice.pdf";
    state.edits.clear();
    state.page = 1;
    await refreshPreview();
  } catch (error) {
    showStatus("Erreur : " + error.message, "err", true);
  }
});

/* ------------------------------------------------------------ réglages UI */
$("fontSelect").addEventListener("change", (event) => {
  state.style.font_id = event.target.value;
  maybeRefresh();
});

$("sizeMode").addEventListener("change", (event) => {
  state.style.sizeMode = event.target.value;
  $("sizeFixedBox").style.display = event.target.value === "fixed" ? "block" : "none";
  $("sizeScaleBox").style.display = event.target.value === "scale" ? "block" : "none";
  maybeRefresh();
});

$("sizeInput").addEventListener("input", (event) => {
  state.style.font_size = parseFloat(event.target.value) || null;
  maybeRefresh(true);
});

$("scaleInput").addEventListener("input", (event) => {
  state.style.font_size_scale = parseFloat(event.target.value) / 100;
  $("scaleVal").textContent = event.target.value;
  maybeRefresh(true);
});

$("alignSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.style.align = button.dataset.align;
  setActive($("alignSeg"), button);
  maybeRefresh();
});

$("valignSeg").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  state.style.vertical_align = button.dataset.valign;
  setActive($("valignSeg"), button);
  maybeRefresh();
});

function setActive(group, button) {
  group.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === button));
}

$("colorInput").addEventListener("input", (event) => {
  state.style.color = event.target.value;
  maybeRefresh(true);
});

$("spacingInput").addEventListener("input", (event) => {
  const value = parseFloat(event.target.value);
  if (value >= 1 && value <= 2) {
    state.style.line_spacing = value;
    maybeRefresh(true);
  }
});

$("offsetX").addEventListener("input", (event) => {
  state.style.offset_x = parseFloat(event.target.value) || 0;
  $("offsetXVal").textContent = event.target.value;
  maybeRefresh(true);
});

$("offsetY").addEventListener("input", (event) => {
  state.style.offset_y = parseFloat(event.target.value) || 0;
  $("offsetYVal").textContent = event.target.value;
  maybeRefresh(true);
});

let refreshTimer = null;
function maybeRefresh(debounce = false) {
  if (!$("autoPreview").checked || !state.file) return;
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshPreview, debounce ? 450 : 120);
}

$("refreshBtn").addEventListener("click", refreshPreview);

$("langSelect").addEventListener("change", () => {
  renderFonts(state.fonts);
  maybeRefresh();
});

/* --------------------------------------------------------------- pages */
$("prevPage").addEventListener("click", () => {
  if (state.page > 1) {
    state.page -= 1;
    refreshPreview();
  }
});
$("nextPage").addEventListener("click", () => {
  if (state.page < state.pageCount) {
    state.page += 1;
    refreshPreview();
  }
});

/* ---------------------------------------------------------------- zoom */
$("zoomIn").addEventListener("click", () => {
  state.zoom = Math.min(250, state.zoom + 20);
  applyZoom();
});
$("zoomOut").addEventListener("click", () => {
  state.zoom = Math.max(40, state.zoom - 20);
  applyZoom();
});
$("zoomReset").addEventListener("click", () => {
  state.zoom = 100;
  applyZoom();
});

/* -------------------------------------------------------------- export */
$("exportBtn").addEventListener("click", async () => {
  if (!state.file || state.busy) return;
  const format = $("formatSelect").value;
  state.busy = true;
  $("exportBtn").disabled = true;
  showStatus("Préparation du fichier " + format.toUpperCase() + "…", "info");
  try {
    const form = buildForm();
    form.append("output_format", format);
    const response = await fetch(`${API}/translate`, { method: "POST", body: form });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(describeError(payload, response.status));
    }
    const blob = await response.blob();
    const disposition = response.headers.get("content-disposition") || "";
    let name = `document_traduit.${format}`;
    const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    const simple = /filename="([^"]+)"/i.exec(disposition);
    if (utf8) name = decodeURIComponent(utf8[1]);
    else if (simple) name = simple[1];

    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 4000);

    const provider = response.headers.get("x-translation-provider") || "—";
    const translated = response.headers.get("x-blocks-translated") || "0";
    const kept = response.headers.get("x-blocks-kept") || "0";
    showStatus(
      `Export réussi : ${name} (moteur « ${provider} », ${translated} blocs traduits, ${kept} conservés).`,
      "ok",
      true
    );
  } catch (error) {
    showStatus("Erreur d'export : " + error.message, "err", true);
  } finally {
    state.busy = false;
    $("exportBtn").disabled = false;
  }
});

/* -------------------------------------------------------------- initial */
(function init() {
  const ui = state.style;
  $("sizeInput").value = 11;
  $("scaleInput").value = 100;
  $("spacingInput").value = ui.line_spacing;
  $("colorInput").value = ui.color;
  renderFonts([]);
})();
