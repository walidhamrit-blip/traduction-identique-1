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

Le fichier `vercel.json` déclare le preset `fastapi` et exclut les fichiers inutiles du bundle ;
le point d'entrée est `api/index.py` (objet `app`), et les polices TTF de `assets/fonts/`
sont incluses automatiquement dans le bundle de la fonction (Vercel embarque tous les fichiers
du projet pour une app Python ; rien n'est « tree-shaké »).

```bash
vercel deploy --prod
```

Configuration associée :

- `.python-version` → `3.12` (versions Vercel : 3.12 / 3.13 / 3.14).
- `requirements.txt` → versions **épinglées** (une montée de version majeure de `python-bidi`,
  `pypdf` ou `reportlab` peut casser l'import → 500 au runtime).
- Limite d'upload par défaut : **4 Mo** (Vercel rejette les corps > 4,5 Mo avant la fonction).

### Diagnostic des erreurs HTTP 500

`GET /api/diag` renvoie l'environnement d'exécution (chemins, présence des polices TTF,
versions réellement installées). C'est le premier appel à faire en cas d'erreur.

Toutes les erreurs sont désormais renvoyées en JSON structuré — plus aucun 500 muet :

| Statut | `detail.code` | Cause |
| --- | --- | --- |
| 400 | `invalid_pdf` | Fichier non-PDF, tronqué ou corrompu |
| 400 | `encrypted_pdf` | PDF protégé par mot de passe |
| 400 | `empty_pdf` | Fichier vide / aucune page |
| 413 | `payload_too_large` | Fichier > `PDF_MAX_UPLOAD_MB` (ou > 4,5 Mo sur Vercel) |
| 413 | `too_many_pages` | Plus de `PDF_MAX_PAGES` pages |
| 422 | `pdf_processing_failed` | Rendu/fusion impossible (PDF exotique) |
| 503 | `font_unavailable` | `assets/fonts/*.ttf` absent du bundle déployé |

```json
{
  "detail": {
    "code": "encrypted_pdf",
    "message": "Le PDF est chiffré ou protégé par un mot de passe...",
    "hint": "Retirez la protection par mot de passe du PDF puis réessayez."
  }
}
```

### Variables d'environnement (optionnelles)

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `PDF_MAX_UPLOAD_MB` | `4` | Taille maximale du PDF (Mo) |
| `PDF_MAX_PAGES` | `40` | Nombre maximal de pages par requête |
| `PDF_MAX_BLOCKS_PER_PAGE` | `4000` | Garde-fou sur les PDF pathologiques |
| `TRANSLATOR_PROVIDER` | `auto` | `auto`, `google_web`, `mymemory`, `libretranslate` ou `offline` |
| `PDF_SOURCE_LANG` | `auto` | Langue source (`tr`, `en`, `fr`… ou `auto`) |
| `PDF_TRANSLATE` | `1` | `0` = pas d'appel réseau (PDF nettoyé, sans traduction) |
| `PDF_TRANSLATE_BUDGET_S` | `35` | Budget temps réseau avant assemblage |
| `LIBRETRANSLATE_URL` | `https://libretranslate.com` | Instance LibreTranslate (utile en auto-hébergé) |

---

## Traduction **gratuite**, sans clé API

Trois fournisseurs gratuits sont essayés dans l'ordre, sans carte bancaire :

1. **`google_web`** — point d'entrée public de Google Traduction (`client=gtx`), gratuit et sans clé.
2. **`mymemory`** — API gratuite sans clé (quota quotidien par IP).
3. **`libretranslate`** — instance publique ou **auto-hébergée** (gratuit et sans limite si vous l'hébergez).
4. **`offline`** — glossaire local, dernier recours.

Garanties du pipeline :

- **Aucune fausse traduction** : si aucun fournisseur ne répond, le texte d'origine est
  conservé tel quel (fin du texte de remplissage « نص مترجم »).
- **Codes et références intacts** : conteneurs (`CAAU2973244`), références (`I000503614`),
  e-mails, téléphones, montants, coordonnées et artefacts CSS/JS ne sont **jamais** envoyés
  au moteur ni masqués.
- **Masquage sélectif** : seuls les blocs réellement traduits sont recouverts ; les autres
  gardent leur texte et leur position d'origine (mise en page préservée).
- **Cache de libellés** : un en-tête de tableau répété 50 fois n'est traduit qu'une fois.
- **Traçabilité** : chaque réponse expose `X-Translation-Provider`, `X-Translation-Degraded`,
  `X-Blocks-Translated`, `X-Blocks-Kept` ; `/api/diag` détaille l'ordre des fournisseurs.

### Limites à connaître

Les points d'entrée gratuits ne sont pas contractuels : Google peut limiter l'usage intensif,
et MyMemory plafonne à quelques milliers de caractères par jour. Pour un usage régulier ou
professionnel, **auto-hébergez LibreTranslate** (gratuit, illimité, qualité correcte) et
pointez `LIBRETRANSLATE_URL` dessus.

---

## Éditeur avant export (police, taille, position, corrections)

Ouvrez **`/editor`** (lien « ✎ Éditeur avant export » depuis la page d'accueil).

1. Importez un fichier (PDF, DOCX, TXT/Markdown, image PNG/JPG/WEBP).
2. Cliquez sur **« Préparer l'aperçu »** : la page est rendue en image et chaque
   bloc de texte est listé avec sa traduction.
3. Réglez ce que vous voulez :
   - **Police** : Amiri, Noto Naskh Arabic, Noto Sans Arabic, IBM Plex Sans Arabic,
     DejaVu Sans (multi-script), Roboto / Open Sans / Lato (latin) ;
   - **Taille** : automatique (taille d'origine), imposée en points, ou échelle en % ;
   - **Alignement** : horizontal (droite/centre/gauche) et vertical (haut/milieu/bas) ;
   - **Couleur**, **interligne**, **décalage global** X/Y en points ;
   - **Correction bloc par bloc** : nouveau texte, taille, déplacement, masquage.
4. Choisissez le **format de sortie** puis cliquez sur **« Exporter le document »**.

L'aperçu et l'export passent par le **même code** (`/preview` et `/translate`
partagent le rendu) : ce que vous voyez est ce que vous obtenez.

### Formats

| Entrée | Sortie |
| --- | --- |
| PDF (vectoriel) | PDF (mise en page préservée) |
| Word `.docx` | Word `.docx` |
| TXT / Markdown / CSV | Texte `.txt` |
| Image PNG / JPG / WEBP (OCR si `pytesseract` installé) | Image `.png` |

### API correspondante

```bash
# 1) Préparer l'édition (blocs + aperçu PNG base64)
curl -X POST http://localhost:8000/preview \
  -F "file=@facture.pdf" -F "target_lang=ar" -F "page=1"

# 2) Exporter avec le style et les corrections
curl -X POST http://localhost:8000/translate \
  -F "file=@facture.pdf" -F "target_lang=ar" \
  -F 'style={"font_id":"noto-naskh","font_size":12,"align":"center","color":"#B3121B","offset_x":4}' \
  -F 'edits=[{"block_id":"p0-b3-100.0_700.0_200.0_720.0","text":"Texte corrigé","x0":110,"font_size":13}]' \
  -F "output_format=docx" --output facture_ar.docx
```

Polices proposées par `GET /api/fonts`, formats supportés par `GET /api/formats`.
