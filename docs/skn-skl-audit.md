# SKN/SKL binary audit — 2026-10-16

The current Rust exporter writes SKN **4.1**, including real geometry bounds,
and modern SKL **version 0**. Bounds are regenerated from exported positions,
not zeroed or copied from an obsolete mesh. No ANMs were changed by this audit.

## References and checks

Compared the local lol_maya checkout at `362f3e219c28d91eee3aaf8b3082faf9256618b5`
and LtMAO/Lemon at `9c6ed6763c53f61dc43ce56893012f491f7dd63f` against the Rust
writer and fresh Blender exports of the original Seraphine base SKN/SKL.

The inspected lol_maya SKN writer emits **1.1**, whose header has no bounding-box
or sphere fields. It is useful for comparison of basic mesh and skeleton data,
but copying that writer would lose modern SKN fields. Its SKL writer also writes
identity influence slots and defaults several metadata fields; those conventions
are not a reason to discard data from an imported Riot file.

| Data | Result |
| --- | --- |
| SKN header and layout | Actual bytes declare 4.1; counts, vertex stride/type, indices and material ranges are consistent. |
| Bounding box | Exact minima/maxima of the exported vertex positions. Nonzero and updates after geometry edits or model scaling. |
| Bounding sphere | Encloses exported positions within float32 rounding tolerance. Its center/radius can differ from Riot's sphere while remaining valid. |
| Positions, UVs, weights, triangles | Real Seraphine geometry/material triangle coverage preserved. UVs unchanged; maximum coordinate difference 0.00000763 game units and weight difference 0.000000119. |
| Normals | Written from Blender's current corner normals. Maximum measured round-trip direction change approximately 0.36 degrees, already present immediately after Blender import. Not a bit-exact normal round trip. |
| Optional color/tangent data | Existing integration tests preserve BGRA colors, float4 tangents and tangent handedness; Lemon independently reads the exported 72-byte vertex layout. |
| SKN flags and extra bytes | Existing tests cover flags, relative indices, prefix bytes and trailing bytes. Unknown bytes are retained opaquely. |
| SKL joint records | Names, IDs, parents, flags, radii and hierarchy match the real source. Local TRS and inverse-bind TRS are written; inverse binds are regenerated from the exported bind pose. |
| SKL lookup/offsets | Raw-byte checks confirm file size, section offsets, relative name pointers, ELF name hashes and sorted hash-to-ID entries. |
| SKL bind consistency | Maximum component error in global-bind × inverse-bind is 0.0000563, or 0.0000687 at 1.25× export scale. |
| SKL metadata | Name, asset, skeleton flags, per-joint flags and radius survive synthetic round trips. |

The original has 13,036 vertices, 59,934 indices, six materials, 125 joints and
98 skinning-palette entries. Fresh Blender export has 13,042 vertices after
corner-normal splitting; triangle/material coverage is unchanged. The exported
AABB is approximately `(-115.432, -29.922, -63.427)` to
`(115.432, 198.771, 48.231)`, with sphere radius `136.859`.

## Confirmed bug fixed

Standalone SKL export previously replaced the source influence palette with
`[0, 1, 2, ...]`. Seraphine's original palette contains 98 entries and skips some
of its 125 joints, so this redirected an unchanged SKN's vertex weights to the
wrong bones. Paired SKN/SKL export already produced a matching compact palette.

- Imported rigs now store their palette using original bone identities.
- Standalone SKL export retains its slots while resolving current joint IDs.
  Native bone renames and inserted/unweighted bones do not reset the mapping.
- Successful SKL exports remember the emitted palette for later separate exports.
- SKN-only export uses the existing palette instead of silently compacting it.
  Newly weighted bones outside that palette require exporting SKN and SKL together.
- Older .blend files can recover the palette from their recorded source SKL only
  if its joint identities still match. Missing/changed sources or deleted referenced
  bones produce an actionable error instead of guessing a mapping.
- Completely new rigs without imported metadata retain the identity fallback.

The corrected standalone Seraphine SKL has the original 98-entry palette.
These fixes change Blender-side bookkeeping; the bundled Rust extension did
not require rebuilding. Animation files and animation-export code were untouched.

## Validation and limits

45 integration tests pass in **Blender 4.0 and 4.5.14**, with both the real
Seraphine fixture and Lemon reader enabled. Five new regressions cover separate
exports, palette recovery/remapping, missing weighted bones and raw bounds after
editing/scaling. lol_maya's reader under Maya 2023 also successfully reads both
the normal and 1.25× fresh SKN/SKL pairs and the corrected standalone SKL.

The reusable `tests/audit_skin_files.py` performs the raw binary checks and a
fresh real-asset round trip. Run it in background Blender with `--factory-startup`
and `AVENTURINE_SERAPHINE_SKN` set to the original source. It writes artifacts to
an isolated temporary directory unless `AVENTURINE_AUDIT_OUTPUT` is provided.
Numerical results from this audit are in [skn-skl-audit-results.json](skn-skl-audit-results.json).

This validates the supported fields and this real asset, not every Riot layout
or in-game rendering. Bounds cover the exported mesh, not an animation-swept
volume. The established limitations on visual-pose tangent export, opaque extra
bytes and Blender's mesh/submesh limits still apply. See the earlier
[rust format audit](rust-format-audit.md) for those details.
