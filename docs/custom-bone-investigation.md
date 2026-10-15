# Custom-bone investigation — 2026-10-12

Investigation only: no custom-bone classification, skeleton-export, pose-conversion,
or animation-transform rules were changed. The general animation-insertion fix only
changes how samples are written into existing curves.

## Scope and method

Reproduced in Blender 4.0.0 with factory startup and synthetic assets. The starting
rig has `root` and `child`, with the child's game-space local translation `(0, 100, 0)`.
Import orientation is NATIVE. The three-frame clip moves the child's X translation
through `0, 10, 20` and X scale through `1, 1.2, 1.4`. Export uses the default
non-visual mode and Clean Names. Tested with both the original bundled binding
and the refreshed binding built from library commit
`c78cf7077638ab8e5546810a09b6f0037c1f0d1d`.

These are controlled reproductions, not a complete real-character or Maya comparison.
VISUAL orientation, retargeted rigs, physics baking, constraints, and game playback
need separate validation before redesigning the workflow.

## Reproduced results

| Operation | Result |
|---|---|
| Unchanged rig | Translation and scale round-trip correctly. |
| Add a fresh, uniquely named leaf `cape` under `child` | Existing child animation still round-trips correctly. |
| Insert a fresh `helper` between `root` and `child` | Child animation loses translation and scale: all exported samples become `(0,100,0)` and `(1,1,1)`. |
| Shift+D `child`, keep `child.001`, move it sideways | Classified as an offset clone even though it is only a sibling. Convert to New Bone returns CANCELLED and incorrectly describes it as an original skeleton joint. |
| Shift+D `child`, rename to `cape`, move it sideways | The copied native metadata remains. Its exported bind position collapses onto `child` instead of the authored position. The converter does recognize this renamed case. |
| Add a fresh `cape` and import an ANM containing a matching cape track | The track is ignored because the bone lacks native metadata. Re-export yields constant rest translation instead of the input motion. |

The inserted-parent case also demonstrates a skeleton/animation mismatch, not merely
lost channels. At animation frame 1, the child's Blender position converted to game
coordinates is `(0,100,0)`. Composing the exported SKL hierarchy with the exported
ANM local transforms places it at `(-40,50,100)` in the same units.

## Why it happens

1. **Copied identity is treated as authoritative.** Shift+D copies `native_bone_index`,
   stored bind TRS, original parent, and stored matrices. The skeleton exporter takes
   its native-bind branch for an independently renamed duplicate and ignores its
   authored rest placement. See `io/export_skl.py`, `calc_league_matrix`.

2. **Names are used to guess bone roles.** `detect_offset_clones` treats an original
   plus a `.001` duplicate as an offset-control pair without requiring the duplicate
   to be inserted above the original. `find_duplicate_native_index_bones` excludes
   those clones, so `LOL_OT_ConvertToNewBone` refuses the most ordinary duplicate.
   See `io/bone_utils.py` and `tools/offset_bones.py`.

3. **Custom-bone animation is intentionally suppressed.** `apply_anm` selects one
   native owner per stored index, pins non-native bones to identity pose channels,
   and skips their matching animation tracks. Native children of custom parents
   normally get rotation only; translation/scale are pinned to rest. These rules
   are assumptions about offset controls, not general added joints.

4. **SKL and ANM use different parent frames.** Skeleton export rebases a native
   child's bind transform relative to the inserted parent. Normal animation export
   reconstructs it against the old native ancestor and pins translation to the
   original bind translation. Those local transforms cannot be composed interchangeably
   with the new skeleton hierarchy. See `io/export_anm.py`, `_native_anim_parent`,
   and `io/export_skl.py`, custom-chain adjustment.

5. **Other inconsistencies remain to cover.** The animation importer uses the
   presence of any dot to break native-identity ties, while shared helpers specifically
   detect numeric suffixes. Skeleton and animation export have different suffix/hash
   handling. Custom-bone identity pinning also uses frames `0` and `frame_count`
   regardless of the insertion offset. These are code findings, not separately
   quantified reproductions in this investigation.

## Proposed direction, pending a decision

- Separate ordinary new joints from intentional offset controls explicitly; a name
  or copied native index should not determine the user's intent.
- Keep original-joint identity distinct from editable rest transforms and current
  parent relationships. Duplication should not confer original-joint identity.
- Share one naming/hash policy and one parent-relative transform model between
  SKL and ANM export. Honor tracks on added joints when those tracks exist.
- Define what insertion into a hierarchy should preserve: current global poses,
  authored offsets, or unchanged stock animation locals. Changing a parent generally
  requires rebasing/baking local animation to preserve global motion; those are
  different workflows and should not be selected by suffix heuristics.
- Before implementing, create a real-asset comparison set covering a new leaf,
  renamed duplicate, inserted parent, animated custom joint, and changed root,
  with translated/rotated/scaled ancestors. Compare viewport poses with composed
  exported SKL+ANM transforms and finally game playback.

The evidence locates the reproduced failures in the Blender adapter's identity and
transform rules. Merely rebuilding Rust does not fix them. No Maya execution was
available, so parity with Maya has not been independently verified.
