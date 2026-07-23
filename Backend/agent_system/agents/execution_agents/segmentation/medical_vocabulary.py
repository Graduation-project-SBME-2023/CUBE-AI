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
    "hepatic":        ["liver"],
    "hepatocellular": ["liver"],
    "renal":          ["kidney"],
    "splenic":        ["spleen"],
    "pulmonary":      ["lung"],
    "cardiac":        ["heart"],
    "myocardial":     ["heart"],
    "pancreatic":     ["pancreas"],
    "colonic":        ["colon", "large intestine"],
    "gastric":        ["stomach"],
    "cerebral":       ["brain"],
    "cranial":        ["brain"],
    "osseous":        ["bone", "bones"],
    "skeletal":       ["bone", "bones"],
    "adrenal":        ["adrenal gland"],
    "vesical":        ["urinary bladder"],
    "bowel":          ["small intestine"],
    "mammary":        ["breast"],
    "prostatic":      ["prostate"],
    "rectal":         ["rectum"],
    "oesophageal":    ["esophagus"],
    "esophageal":     ["esophagus"],
}

# ═══════════════════════════════════════════════════════════════════
# 2. PATHOLOGY SYNONYMS
#    Maps synonymous pathology terms to equivalent catalog terms.
#    IMPORTANT: These are ONLY applied to compound (organ-specific) labels.
#    They must NEVER be used to match a generic whole-body label alone.
#    All target values must exist as labels in the model catalog.
# ═══════════════════════════════════════════════════════════════════
PATHOLOGY_SYNONYMS: dict[str, list[str]] = {
    "cancer":     ["tumor", "carcinoma"],
    "malignancy": ["tumor", "carcinoma"],
    "neoplasm":   ["tumor", "carcinoma"],
    "mass":       ["lesion", "tumor"],
    "growth":     ["lesion", "tumor"],
    "nodule":     ["lesion"],
}

# ═══════════════════════════════════════════════════════════════════
# 3. STRUCTURAL ALIASES
#    Maps non-standard label names to their exact catalog equivalents.
#    Used for the VISTA class-index lookup and other catalog lookups.
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
# 4. FLAT MEDICAL SYNONYMS  (auto-generated — do NOT edit manually)
#    Used by check_voxtell_label_availability for set-equality matching.
#    Flat one-to-one dict: maps a word to its canonical concept so that
#    "left kidney" and "kidney left" produce the same concept set.
#    Targets are canonical concept words, not necessarily catalog labels.
#
#    Built automatically from ANATOMICAL_SYNONYMS (single-target entries)
#    and PATHOLOGY_SYNONYMS (all mapped to the canonical concept "lesion").
#    To add a new synonym, edit ANATOMICAL_SYNONYMS or PATHOLOGY_SYNONYMS
#    above — do NOT add entries here.
# ═══════════════════════════════════════════════════════════════════
# Anatomical: use the first (and usually only) canonical replacement word.
FLAT_MEDICAL_SYNONYMS: dict[str, str] = {
    key: vals[0]
    for key, vals in ANATOMICAL_SYNONYMS.items()
    if vals
}
# Pathology: collapse all clinical terms to the canonical concept "lesion".
FLAT_MEDICAL_SYNONYMS.update({
    "tumor":      "lesion",
    "tumour":     "lesion",
    "cancer":     "lesion",
    "carcinoma":  "lesion",
    "mass":       "lesion",
    "nodule":     "lesion",
    "growth":     "lesion",
    "malignancy": "lesion",
    "neoplasm":   "lesion",
})

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
    # Liver pathology → "hepatocellular carcinoma" (EXISTS in VoxTell: 73.24)
    "liver cancer":             "hepatocellular carcinoma",
    "liver tumor":              "hepatocellular carcinoma",
    "liver lesion":             "hepatocellular carcinoma",
    "hepatic lesion":           "hepatocellular carcinoma",
    "hepatic tumor":            "hepatocellular carcinoma",
    "focal liver lesion":       "hepatocellular carcinoma",
    "liver metastasis":         "hepatocellular carcinoma",
    "hcc":                      "hepatocellular carcinoma",

    # Lung pathology → "lung nodule" (EXISTS: 45.32) or "lung lesion" (EXISTS: 45.32)
    "pulmonary nodule":         "lung nodule",
    "pulmonary lesion":         "lung lesion",
    "lung cancer":              "lung lesion",
    "lung mass":                "lung nodule",

    # Pancreas pathology → "pancreas tumor" (EXISTS: 35.66)
    "pancreatic cancer":        "pancreas tumor",
    "pancreatic tumor":         "pancreas tumor",
    "pancreatic lesion":        "pancreas tumor",
    "pancreatic mass":          "pancreas tumor",

    # Esophagus pathology → "esophageal cancer" (EXISTS: 69.07)
    "esophageal tumor":         "esophageal cancer",
    "oesophageal cancer":       "esophageal cancer",
    "esophagus cancer":         "esophageal cancer",
    "esophagus tumor":          "esophageal cancer",

    # Soft tissue → "soft tissue lesion" (EXISTS: 40.34)
    "soft tissue tumor":        "soft tissue lesion",
    "soft tissue mass":         "soft tissue lesion",

    # Brain → "tumor" (EXISTS: 77.23)  — VoxTell has no brain-specific tumor label
    # so we use the generic tumor label which is better than nothing.
    "brain tumor":              "tumor",
    "brain lesion":             "tumor",
    "glioma":                   "tumor",
    "glioblastoma":             "tumor",

    # Breast → "fdg-avid breast cancer" (EXISTS: 72.27)
    "breast cancer":            "fdg-avid breast cancer",
    "breast tumor":             "fdg-avid breast cancer",

    # FDG-avid / metabolic → "fdg-avid tumor" (EXISTS: 83.24)
    "fdg avid tumor":           "fdg-avid tumor",
    "metabolic tumor":          "fdg-avid tumor",
    "pet positive tumor":       "fdg-avid tumor",

    # MS → "ms lesion" (EXISTS: 72.71)
    "multiple sclerosis lesion": "ms lesion",
    "demyelinating lesion":     "ms lesion",
    "white matter lesion":      "ms lesion",
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
