"""
medical_vocabulary.py
─────────────────────
Single source of truth for all medical label synonyms, structural aliases,
and fuzzy-matching utilities used across the segmentation pipeline.

Files that consume this module:
  - segmentation/services.py   (_class_key_candidates)
  - segmentation/planner.py    (builds the AI prompt synonym section dynamically)
  - segmentation/utils.py      (normalize_label, extract_organ_label, matches_label)
  - apps/radiology/lib/infers/voxtell.py  (FALLBACK_PROMPT_MAP)

To add a new synonym or alias, edit ONLY this file.

IMPORTANT RULE for any mapping added here:
  The TARGET (value) of every mapping MUST be a label that actually exists
  in at least one segmentation model catalog (automatic or VoxTell).
  Never map to a label that no model can produce.
"""

# ═══════════════════════════════════════════════════════════════════
# 1. ANATOMICAL SYNONYMS
#    Maps non-standard / adjectival terms → canonical organ name(s).
#    Used for fuzzy label matching in _class_key_candidates and
#    for semantic concept expansion in the AI planner prompt.
#    All target values must exist as labels in the model catalog.
# ═══════════════════════════════════════════════════════════════════
ANATOMICAL_SYNONYMS: dict[str, list[str]] = {
    # ── Adjectival → canonical organ name ────────────────────────────────────
    # Liver
    "hepatic":         ["liver"],
    "hepatocellular":  ["liver"],
    # Kidney / Renal
    "renal":           ["kidney"],
    "nephrotic":       ["kidney"],   # from lit-review: nephrotic syndrome papers
    "nephric":         ["kidney"],
    # Spleen
    "splenic":         ["spleen"],
    # Lung
    "pulmonary":       ["lung"],
    "bronchial":       ["lung"],     # from lit-review: _ORGAN_TERMS
    "pulmonological":  ["lung"],
    # Heart
    "cardiac":         ["heart"],
    "myocardial":      ["heart"],
    "cardiovascular":  ["heart"],
    # Pancreas
    "pancreatic":      ["pancreas"],
    # Colon / GI
    "colonic":         ["colon", "large intestine"],
    "colorectal":      ["colon", "rectum"],  # from lit-review: colorectal cancer papers
    # Stomach
    "gastric":         ["stomach"],
    # Brain
    "cerebral":        ["brain"],
    "cranial":         ["brain"],
    "intracranial":    ["brain"],    # from lit-review: _ORGAN_TERMS
    "neurological":    ["brain"],
    # Breast
    "mammary":         ["breast"],
    "mammographic":    ["breast"],   # from lit-review: mammographic studies
    # Prostate
    "prostatic":       ["prostate"],
    # Bone
    "osseous":         ["bone", "bones"],
    "skeletal":        ["bone", "bones"],
    # Adrenal
    "adrenal":         ["adrenal gland"],
    # Bladder
    "vesical":         ["urinary bladder"],
    "urothelial":      ["urinary bladder"],  # from labels.json: urothelial carcinoma
    # Small intestine / bowel
    "bowel":           ["small intestine"],
    "intestinal":      ["small intestine"],
    # Rectum
    "rectal":          ["rectum"],
    # Esophagus
    "oesophageal":     ["esophagus"],
    "esophageal":      ["esophagus"],
    # Ovary (from lit-review: ovarian cancer papers)
    "ovarian":         ["ovary"],
    # Thyroid (from lit-review: _ORGAN_TERMS)
    "thyroid":         ["thyroid"],
    # Uterus (from labels.json: uterine fibroid, uterine leiomyoma)
    "uterine":         ["uterus"],
    # Lymph nodes (from lit-review: lymph node metastasis papers)
    "lymphatic":       ["lymph node"],
    "nodal":           ["lymph node"],
}

# ═══════════════════════════════════════════════════════════════════
# 2. PATHOLOGY SYNONYMS
#    Maps synonymous pathology terms to equivalent catalog terms.
#    IMPORTANT: These are ONLY applied to compound (organ-specific) labels.
#    They must NEVER be used to match a generic whole-body label alone.
#    All target values must exist as labels in the model catalog.
# ═══════════════════════════════════════════════════════════════════
PATHOLOGY_SYNONYMS: dict[str, list[str]] = {
    # ── Clinical pathology terms → catalog-safe equivalents ──────────────────
    # Core
    "cancer":        ["tumor", "carcinoma"],
    "malignancy":    ["tumor", "carcinoma"],
    "malignant":     ["tumor", "carcinoma"],
    "neoplasm":      ["tumor", "carcinoma"],
    "mass":          ["lesion", "tumor"],
    "growth":        ["lesion", "tumor"],
    "nodule":        ["lesion"],
    # From lit-review _PATHOLOGY_TERMS
    "metastasis":    ["lesion", "tumor"],   # metastatic deposits → lesion
    "metastatic":    ["lesion", "tumor"],
    "metastases":    ["lesion", "tumor"],
    "adenocarcinoma":["carcinoma"],
    "sarcoma":       ["tumor"],
    "hemangioma":    ["lesion"],            # benign vascular lesion
    "haemangioma":   ["lesion"],
    "abscess":       ["lesion"],
    "infarct":       ["lesion"],
    "benign":        ["lesion"],
    "cyst":          ["lesion"],
    "fibroid":       ["lesion"],            # from labels.json: uterine fibroid
    "leiomyoma":     ["lesion"],            # from labels.json: uterine leiomyoma
    "myoma":         ["lesion"],
    "schwannoma":    ["lesion"],            # from labels.json: vestibular schwannoma
    "hamartoma":     ["lesion"],
}

# ═══════════════════════════════════════════════════════════════════
# 3. STRUCTURAL ALIASES
#    Maps non-standard label names to their exact catalog equivalents.
#    All target values must be real catalog keys.
# ═══════════════════════════════════════════════════════════════════
STRUCTURAL_ALIASES: dict[str, str | list[str]] = {
    # Digestive
    "large_intestine":    "colon",
    "colon":              "large_intestine",
    "small_intestine":    "small_bowel",
    "small_bowel":        "small_intestine",
    # Vascular
    "portal_vessels":     "portal_vein_and_splenic_vein",
    "portal_vein":        "portal_vein_and_splenic_vein",
    "splenic_vein":       "portal_vein_and_splenic_vein",
    "inferior_vena_cava": "inferior_vena_cava",
    "superior_vena_cava": "superior_vena_cava",
    # Heart chambers
    "heart":              "heart_myocardium",
    "left_atrium":        "heart_atrium_left",
    "right_atrium":       "heart_atrium_right",
    "left_ventricle":     "heart_ventricle_left",
    "right_ventricle":    "heart_ventricle_right",
    # Kidneys (laterality normalisation)
    "left_kidney":        "kidney_left",
    "right_kidney":       "kidney_right",
    # Bones
    "left_hipbone":       "hip_left",
    "right_hipbone":      "hip_right",
    "left_hip":           "hip_left",
    "right_hip":          "hip_right",
    "left_femur":         "femur_left",
    "right_femur":        "femur_right",
    "left_humerus":       "humerus_left",
    "right_humerus":      "humerus_right",
    # Lungs (general → lobes)
    "left_lung":          ["lung_upper_lobe_left", "lung_lower_lobe_left"],
    "right_lung":         ["lung_upper_lobe_right", "lung_lower_lobe_right"],
    # Urinary
    "urinary_bladder":    "urinary_bladder",
}

# ═══════════════════════════════════════════════════════════════════
# 4. FLAT MEDICAL SYNONYMS
#    Used by get_semantic_concepts() in both utils.py and voxtell.py
#    for set-equality semantic matching.
#
#    Maps a SINGLE WORD to its canonical concept word so that
#    "splenic lesions" and "spleen lesion" yield the same concept set.
#
#    Built automatically from ANATOMICAL_SYNONYMS (single-target entries)
#    and PATHOLOGY_SYNONYMS (collapsed to canonical "lesion").
#    To add a new synonym, edit ANATOMICAL_SYNONYMS or PATHOLOGY_SYNONYMS
#    above — do NOT add entries here.
#
#    PLURAL HANDLING: all singular pathology/anatomy words are also
#    mapped with their common plural form so "lesions" == "lesion",
#    "tumors" == "tumor", "kidneys" == "kidney", etc.
#    This is the fix for the plural gap identified in 48ff95a7.
# ═══════════════════════════════════════════════════════════════════

# Anatomical: use the first (and usually only) canonical replacement word.
FLAT_MEDICAL_SYNONYMS: dict[str, str] = {
    key: vals[0]
    for key, vals in ANATOMICAL_SYNONYMS.items()
    if vals
}

# Pathology: collapse all clinical terms to the canonical concept "lesion".
_PATHOLOGY_TO_LESION = {
    "tumor":        "lesion",
    "tumour":       "lesion",
    "cancer":       "lesion",
    "carcinoma":    "lesion",
    "mass":         "lesion",
    "nodule":       "lesion",
    "growth":       "lesion",
    "malignancy":   "lesion",
    "malignant":    "lesion",
    "neoplasm":     "lesion",
    "metastasis":   "lesion",
    "metastatic":   "lesion",
    "abscess":      "lesion",
    "infarct":      "lesion",
    "cyst":         "lesion",
    "fibroid":      "lesion",
    "leiomyoma":    "lesion",
    "myoma":        "lesion",
    "schwannoma":   "lesion",
    "hamartoma":    "lesion",
    "adenocarcinoma": "lesion",
    "sarcoma":      "lesion",
    "hemangioma":   "lesion",
    "haemangioma":  "lesion",
    "benign":       "lesion",
}
FLAT_MEDICAL_SYNONYMS.update(_PATHOLOGY_TO_LESION)

# ── Plural forms ─────────────────────────────────────────────────────────────
# Map each plural to the same canonical concept as its singular.
# This closes the gap where "lesions" != "lesion", "tumors" != "tumor",
# "kidneys" != "kidney" in get_semantic_concepts() set comparison.
_PLURAL_FORMS: dict[str, str] = {}

# Anatomy plurals: map "livers" → "liver", "kidneys" → "kidney", etc.
for _word, _canonical in list(FLAT_MEDICAL_SYNONYMS.items()):
    if not _word.endswith("s"):            # not already plural
        _plural = _word + "s"
        if _plural not in FLAT_MEDICAL_SYNONYMS:
            _PLURAL_FORMS[_plural] = _canonical

# Pathology plurals: explicit forms that need a canonical target
_PLURAL_FORMS.update({
    "lesions":          "lesion",
    "tumors":           "lesion",
    "tumours":          "lesion",
    "cancers":          "lesion",
    "carcinomas":       "lesion",
    "masses":           "lesion",
    "nodules":          "lesion",
    "growths":          "lesion",
    "malignancies":     "lesion",
    "neoplasms":        "lesion",
    "metastases":       "lesion",
    "abscesses":        "lesion",
    "infarcts":         "lesion",
    "cysts":            "lesion",
    "fibroids":         "lesion",
    "schwannomas":      "lesion",
    "sarcomas":         "lesion",
    "hemangiomas":      "lesion",
    "haemangiomas":     "lesion",
    # Common anatomy plurals that need explicit mapping
    "kidneys":          "kidney",
    "lungs":            "lung",
    "livers":           "liver",
    "spleens":          "spleen",
    "pancreases":       "pancreas",
    "stomachs":         "stomach",
    "bones":            "bone",
    "ovaries":          "ovary",
    "glands":           "gland",
    "lymph nodes":      "lymph node",
    "vessels":          "vessel",
})

FLAT_MEDICAL_SYNONYMS.update(_PLURAL_FORMS)

# ═══════════════════════════════════════════════════════════════════
# 5. VOXTELL PROMPT ALIASES
#    Maps clinical phrases the user might send to the EXACT label name
#    that exists in VOXTELL_SUPPORTED_LABELS.
#
#    Purpose: when the planner selects a VoxTell label, or when the
#    VoxTell infer task receives a text prompt, these ensure the prompt
#    string matches a real, supported VoxTell label.
#
#    RULE: Every VALUE here MUST exist verbatim in VOXTELL_SUPPORTED_LABELS.
#    If VoxTell cannot produce a label for the concept, do NOT add it —
#    the planner will correctly return UNSUPPORTED instead.
# ═══════════════════════════════════════════════════════════════════
VOXTELL_PROMPT_ALIASES: dict[str, str] = {
    # ── Liver pathology → "hepatocellular carcinoma" (EXISTS in labels.json) ──
    "liver cancer":              "hepatocellular carcinoma",
    "liver tumor":               "hepatocellular carcinoma",
    "liver lesion":              "hepatocellular carcinoma",
    "hepatic lesion":            "hepatocellular carcinoma",
    "hepatic tumor":             "hepatocellular carcinoma",
    "focal liver lesion":        "hepatocellular carcinoma",
    "liver metastasis":          "hepatocellular carcinoma",
    "liver metastases":          "hepatocellular carcinoma",   # plural form
    "hcc":                       "hepatocellular carcinoma",
    "hepatic carcinoma":         "hepatocellular carcinoma",
    "liver adenocarcinoma":      "hepatocellular carcinoma",   # from lit-review pathology

    # ── Lung pathology → "lung nodule" / "lung lesion" (EXISTS in labels.json) ─
    "pulmonary nodule":          "lung nodule",
    "pulmonary nodules":         "lung nodule",   # plural
    "pulmonary lesion":          "lung lesion",
    "pulmonary lesions":         "lung lesion",   # plural
    "pulmonary mass":            "lung nodule",
    "lung cancer":               "lung lesion",
    "lung mass":                 "lung nodule",
    "lung tumor":                "lung lesion",
    "bronchial lesion":          "lung lesion",   # from lit-review: bronchial = lung
    "lung metastasis":           "lung lesion",
    "lung metastases":           "lung lesion",   # plural

    # ── Pancreas pathology → "pancreas tumor" (EXISTS in labels.json) ──────────
    "pancreatic cancer":         "pancreas tumor",
    "pancreatic tumor":          "pancreas tumor",
    "pancreatic lesion":         "pancreas tumor",
    "pancreatic mass":           "pancreas tumor",
    "pancreatic carcinoma":      "pancreas tumor",
    "pancreatic adenocarcinoma": "pancreas tumor",  # from lit-review: adenocarcinoma

    # ── Esophagus pathology → "esophageal cancer" (EXISTS in labels.json) ──────
    "esophageal tumor":          "esophageal cancer",
    "oesophageal cancer":        "esophageal cancer",
    "oesophageal tumor":         "esophageal cancer",
    "esophagus cancer":          "esophageal cancer",
    "esophagus tumor":           "esophageal cancer",
    "esophageal carcinoma":      "esophageal cancer",

    # ── Soft tissue → "soft tissue lesion" (EXISTS in labels.json) ─────────────
    "soft tissue tumor":         "soft tissue lesion",
    "soft tissue mass":          "soft tissue lesion",
    "soft tissue sarcoma":       "soft tissue lesion",  # from lit-review: sarcoma

    # ── Bladder → "urinary bladder cancer" (EXISTS in labels.json) ─────────────
    # from labels.json: "urinary bladder cancer", "urothelial bladder cancer",
    # "urothelial carcinoma", "vesical cancer", "vesical carcinoma"
    "bladder cancer":            "urinary bladder cancer",
    "bladder tumor":             "urinary bladder cancer",
    "bladder lesion":            "urinary bladder cancer",
    "urothelial cancer":         "urinary bladder cancer",
    "vesical tumor":             "urinary bladder cancer",
    "bladder carcinoma":         "urinary bladder cancer",

    # ── Uterus → "uterine fibroid" / "uterine leiomyoma" (EXISTS in labels.json) 
    # from lit-review: uterine pathology papers
    "uterine tumor":             "uterine fibroid",
    "uterine lesion":            "uterine fibroid",
    "uterine mass":              "uterine fibroid",
    "uterine myoma":             "uterine fibroid",

    # ── Brain → "tumor" (EXISTS in labels.json) — no brain-specific tumor label ─
    # from lit-review: glioma, glioblastoma are key brain pathology terms
    "brain tumor":               "tumor",
    "brain lesion":              "tumor",
    "brain tumors":              "tumor",   # plural
    "glioma":                    "tumor",
    "glioblastoma":              "tumor",
    "glioblastoma multiforme":   "tumor",   # from lit-review: GBM
    "intracranial tumor":        "tumor",   # from lit-review: intracranial = brain
    "intracranial lesion":       "tumor",
    "cerebral tumor":            "tumor",
    "cerebral lesion":           "tumor",
    "whole brain tumor":         "tumor",   # from labels.json: "whole brain tumor"

    # ── Breast → "fdg-avid breast cancer" (EXISTS in labels.json) ──────────────
    "breast cancer":             "fdg-avid breast cancer",
    "breast tumor":              "fdg-avid breast cancer",
    "breast lesion":             "fdg-avid breast cancer",
    "breast carcinoma":          "fdg-avid breast cancer",
    "mammary tumor":             "fdg-avid breast cancer",  # from anatomical synonyms
    "mammary lesion":            "fdg-avid breast cancer",

    # ── FDG-avid / PET → "fdg-avid tumor" (EXISTS in labels.json) ──────────────
    "fdg avid tumor":            "fdg-avid tumor",
    "metabolic tumor":           "fdg-avid tumor",
    "pet positive tumor":        "fdg-avid tumor",
    "uptaking tumor":            "fdg-avid tumor",  # from labels.json: "uptaking tumor"
    "uptaking malignancy":       "fdg-avid tumor",  # from labels.json: "uptaking malignancy"
    "uptaking neoplasm":         "fdg-avid tumor",  # from labels.json: "uptaking neoplasm"

    # ── MS / white matter → "ms lesion" (EXISTS in labels.json) ────────────────
    "multiple sclerosis lesion": "ms lesion",
    "demyelinating lesion":      "ms lesion",
    "white matter lesion":       "ms lesion",
    "white matter lesions":      "ms lesion",  # plural — from labels.json
    "wmh":                       "ms lesion",  # from labels.json: "wmh"
    "wmh lesions":               "ms lesion",  # from labels.json: "wmh lesions"

    # ── Vestibular schwannoma → "vestibular schwannoma" (EXISTS in labels.json) ─
    # from labels.json: "vestibular schwannoma lesion", "vestibular nerve schwannoma"
    "acoustic neuroma":          "vestibular schwannoma",
    "vestibular nerve tumor":    "vestibular schwannoma",  # from labels.json
    "acoustic neurinoma":        "vestibular schwannoma",
}


# ═══════════════════════════════════════════════════════════════════
# 6. PROMPT SYNONYM TEXT (auto-generated — do NOT edit manually)
#    Injected into the AI planner's prompt so the LLM uses exactly
#    the same synonyms as the code does.
# ═══════════════════════════════════════════════════════════════════
def build_prompt_synonym_section() -> str:
    """
    Build the synonym section of the planner prompt from the canonical dicts.
    This ensures the AI prompt and the code always use the same vocabulary.
    """
    anatomical_lines = "\n".join(
        f"          - {key} = {', '.join(vals)}"
        for key, vals in ANATOMICAL_SYNONYMS.items()
    )
    pathology_lines = "\n".join(
        f"          - {key} = {', '.join(vals)}"
        for key, vals in PATHOLOGY_SYNONYMS.items()
    )
    return (
        "        Anatomical synonyms (adjectival → canonical organ name):\n"
        f"{anatomical_lines}\n\n"
        "        Pathology synonyms (organ-specific context ONLY — never apply to whole-body labels):\n"
        f"{pathology_lines}"
    )
