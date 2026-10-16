"""
Large-file DfT source ingestion (Phase 12).

Upload -> Identify -> Validate -> Preview -> Save to staging.

Uploads are streamed to disk, parsed by a bounded background worker in a separate
process, and accepted versions are stored immutably in the source registry.
Nothing in this package writes to production inputs (ev/), production outputs
(project/output_processed/) or the C1 release state. See README.md.
"""
