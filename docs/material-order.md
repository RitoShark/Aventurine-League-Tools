# SKN material ordering

Implemented and tested locally on 2026-10-26. Python-only change; no Rust rebuild required.

Select an armature or a bound mesh and expand **SKN Material Order** in the Misc LoL Tools
sidebar. Rows show material ordering groups; the top row is exported first. Up/down arrows
save changes immediately on the armature, with Blender undo support. Save the .blend to retain
those settings. Reset discards the custom ordering and uses the recorded import order.

Match SKN... reads a reference SKN without modifying it. Matching is case-insensitive. Materials
absent from the scene are skipped; unmatched scene materials follow the matched names. A
reference with no matching names is rejected without changing settings. This mirrors the
reference-name ordering found in LtMAO's sborf.py and Lemon's Maya skin exporter. The inspected
LtMAO checkout was commit 9c6ed6763c53f61dc43ce56893012f491f7dd63f.

The ordering is per armature and spans separate mesh objects as well as multiple material
slots on one object. The same material name, including case-insensitive duplicates, forms a
single ordering group; its separate submeshes retain their relative order. Unused slots and
removed materials are not displayed. Materials newly added to the rig follow saved entries.
The exporter uses only the selected/exported subset and never adds an unselected mesh.

New SKN imports retain their original material order on the rig and mesh. Older scenes do not
have this metadata; use Match SKN... with their original source file or order the list manually.
Raw Blender slot reordering does not override a remembered/custom SKN order.

Export sorts material groups and repacks their vertex and index ranges together. It preserves
vertex attributes, skin weights, palette references and triangle assignments. The existing
name-cleaning export option continues to apply. Material slots, source material names and
BIN/shader definitions are not edited. This is control over the file's submesh order, not an
automatic transparency sorter; the appropriate visual order must still be checked in-game.

Validation:

- All 68 integration tests pass in Blender 4.0 and 4.5.15 with local Seraphine/Lemon fixtures.
- New coverage includes remembered order after splitting/reversed object input, custom
  ordering, reference matching and reset operators, unmatched references, subset exports,
  duplicate names, new materials, .blend serialization, corrupt-order rejection, and geometry,
  normal, UV, tangent, color and skinning-palette preservation with relative SKN indices.
- Independent real-model check: reversed Seraphine's six materials and read both outputs
  with LtMAO pyRitoFile. All 13,036 vertices retained exactly the same per-material positions,
  normals, UVs, influences and weights, with identical local triangles and bounding boxes.
- No in-game transparency test was performed. The ZIP also includes the preceding rest-pose
  preservation and flat-shading changes.

The panel header uses the Currency Wars **Climbing the Ranks** augment icon from the
Honkai: Star Rail Wiki, matching the custom-icon headers and boxed sections of Skin Tools.


Panel visibility is controlled by **Preferences > Add-ons > Aventurine > Misc LoL Tools >
SKN Material Order**, enabled by default. The master Misc switch also controls it. Saved
settings are reapplied when the add-on starts. When enabled, the panel stays visible with
no selection and shows a selection hint; disabling it only hides the panel and keeps saved
export ordering and operators available.
