"""Tool packs (merged plan, Plan 2).

Every sub-package is one pack (``population``, ``db_reads``, ``segmentation``,
``reporting``, ``bookkeeping``, ``literature``, ``modeling``, ``radiomics``);
``common`` holds the helpers they share and registers no tools.
``catalog.build()`` walks this package and collects every ``@tool``, so there
is no central import list: a new module here is picked up as it is.

Packs import the toolkit through ``toolpacks._toolkit``, never by an absolute
name (see that module for why).
"""
