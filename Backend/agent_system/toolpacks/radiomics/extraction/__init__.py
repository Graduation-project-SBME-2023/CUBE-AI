"""Radiomics extraction, as ``radiomics_extract_features`` runs it:

- ``config`` — the PyRadiomics settings and the extraction's identity (its
  configuration document and digest);
- ``pairs`` — the reviewed image/mask pairs to measure;
- ``masks`` — one pair's masks, prepared for the extractor (label plan,
  binary mask files, content digests, cache lookup);
- ``worker`` — PyRadiomics itself, in a reusable worker process;
- ``summary`` — the per-organ summary of the recorded rows.
"""
