# Microservice Python de Traduction PDF (`FastAPI` & `Vercel Serverless`)

Microservice Python prêt pour la production permettant de traduire un document PDF vers l'anglais (`en`), le français (`fr`) ou l'arabe (`ar`) tout en **préservant la mise en page vectorielle originale** (logos, tableaux, bordures, graphiques).

## Architecture & Pipeline en 4 étapes

1. **Extraction (`app/extractor.py` — `pdfminer.six`)** :
   - Parcours récursif de l'arbre `LTPage` / `LTTextContainer` / `LTTextLine` / `LTChar`.
   - Récupération des coordonnées exactes `bbox = (x0, y0, x1, y1)` (origine PDF `(0, 0)` en bas à gauche) ainsi que de la taille médiane de police (`LTChar.size`).
   - Distinction intelligente entre paragraphes continus et lignes indépendantes (titres, paires `Clé : Valeur`, cellules de tableaux).

2. **Traduction Mock (`app/translator.py`)** :
   - Fonction `mock_translate(text, target_lang)` déterministe, facilement remplaçable par un appel LLM / DeepL / Google Cloud Translation.

3. **Traitement Arabe RTL (`app/arabic_utils.py` — `arabic-reshaper` + `python-bidi`)** :
   - Si `target_lang == 'ar'`, découpage multi-lignes dans l'ordre **logique**, puis application ligne par ligne de `arabic_reshaper.reshape()` (liaison cursive Presentation Forms) et `bidi.algorithm.get_display()` (réordonnancement visuel RTL UAX #9 préservant les nombres et acronymes latins).
   - Enregistrement et embarquement de la police TTF Unicode **`Amiri-Regular.ttf`** (`assets/fonts/`).

4. **Calque & Fusion (`app/renderer.py` — `reportlab` + `pypdf`)** :
   - Génération d'un calque `reportlab` dessinant un rectangle blanc opaque (`fill=1, stroke=0`) aux coordonnées exactes `(x0, y0, x1, y1)` puis le texte traduit ajusté dynamiquement par-dessus (`drawRightString` sur `x1` pour `ar`, `drawString` sur `x0` pour `en`/`fr`).
   - Fusion page par page sur le document original avec `pypdf` (`PdfReader` / `PdfWriter.add_page()` + `merge_page()`).

---

## Arborescence du projet

```text
.
├── api/
│   └── index.py               # Point d'entrée FastAPI & Vercel Serverless Function
├── app/
│   ├── schemas.py             # Modèles TargetLang (en, fr, ar), TextBlock, ExtractedPage
│   ├── extractor.py           # Étape 1 : Extraction texte + Bounding Boxes (pdfminer.six)
│   ├── translator.py          # Étape 2 : Fonction mock de traduction
│   ├── arabic_utils.py        # Étape 3 : arabic-reshaper + bidi.algorithm.get_display
│   ├── renderer.py            # Étape 4 : Calque ReportLab (fond blanc + TTF) & fusion pypdf
│   ├── pipeline.py            # Orchestration de bout en bout
│   └── sample_generator.py    # Générateur d'un PDF de démonstration
├── assets/fonts/
│   ├── Amiri-Regular.ttf      # Police TTF arabe & latine
│   └── DejaVuSans.ttf         # Police TTF latine Unicode
├── samples/                   # Exemples générés (original + ar, fr, en)
├── tests/
│   └── test_service.py        # Suite de tests pytest (unitaires & API)
├── main.py                    # Lancement local Uvicorn
├── requirements.txt           # Dépendances Python
└── vercel.json                # Configuration Vercel Serverless (inclut assets/fonts/**)
```

---

## Exécution locale

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

- **Console web interactive** : `http://localhost:8000/`
- **Documentation Swagger OpenAPI** : `http://localhost:8000/docs`

### Exemple d'appel `cURL`

```bash
curl -X POST "http://localhost:8000/translate" \
  -F "file=@samples/sample_invoice.pdf;type=application/pdf" \
  -F "target_lang=ar" \
  --output translated_ar.pdf
```

---

## Déploiement Vercel Serverless

Le fichier `vercel.json` route les requêtes vers `api/index.py` et embarque explicitement les polices TTF via `"includeFiles": "{app/**,assets/fonts/**}"` :

```bash
vercel deploy --prod
```
