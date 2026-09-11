"""Extract-side transforms: external API payloads -> `target` table rows.

This is the mirror image of :mod:`laddel_migration.payload`. Where that module
shapes ``laddel`` rows into target-system API payloads (the *write* direction),
this package shapes external API payloads into rows of a `target` snapshot
table (the *read* direction).

Everything here is pure and side-effect free, so it can be unit-tested without
a database or a live API.
"""
