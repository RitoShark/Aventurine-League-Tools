# Added bones and offset parents

Ordinary new bones, including Shift+D duplicates, export as real joints. New joints
are appended after the original joints. Skeleton, skin palette, and animation share
the same ordering and name/hash mapping. Clean Names replaces illegal characters but
keeps numeric suffixes distinct (`Cape.001` becomes `Cape_001`). A naming/hash collision
stops export instead of merging animation tracks.

## Cape / accessory bones

Add and parent the bones normally. Weight geometry to them and animate them normally.
Importing an ANM with matching tracks now animates custom bones too. Stock animations
without those tracks leave the controls alone; a fresh unanimated leaf follows its
parent. No duplicate-conversion step is required. Convert to New Bone remains available
as optional cleanup of copied native metadata.

## Widening shoulders

1. Select the existing clavicle in Pose Mode.
2. Under Offset Bones, use **Add Offset Parent**. It inserts a fresh control above the
   selected joint and records an explicit neutral export frame.
3. Move that control in Pose Mode to offset the shoulder. Repeat on the other side.
4. Use **Apply Offset to Bind Pose**. This bakes the frame-zero mesh positions and the
   skeleton rest pose together. Existing motion remains in the original joint axes.
5. Export the matching SKN and SKL. **If linked root companions are present, also export
   the ANMs or use batch animation conversion.** The panel and exporter report this
   requirement. Using the unchanged clip leaves the new animated root controls static.

Some Riot rigs skin parts of a limb to separate animated roots. Seraphine's palm uses
`R_Hand_Snap`, while the fingers follow the clavicle hierarchy. Moving only the clavicle
therefore separates the palm from its fingers, even if Blender and Maya agree exactly.
Add Offset Parent reports original `_Snap` roots whose matching original joint is in
the affected subtree. It does not reparent these roots or create ANM-dependent links.
Automatic linking was removed because it violated the unchanged-ANM requirement. Their existing
animation remains independent; no skin weights are reassigned.

For other separately animated roots, select those roots in Pose Mode and make the source
offset parent the active bone, then use **Link Selected Root to Offset** before applying
the bind pose. This also supports adding companions to a previously bound offset rig.
This link is an explicit ANM-rewrite option, not a solution for unchanged clips.
Linked companions currently support translation edits, not rotation/scale edits. The
bind operation rejects rotated/scaled source controls instead of silently damaging them.
A linked root receives the difference between the moved offset frame and an unmoved
reference under the same animated parent. Normal Blender constraints evaluate that
movement, and ANM export bakes it. Maya/game playback needs the resulting ANM tracks.
This requirement also applies to new animation clips; batch conversion handles it.

For a subtree with no independent animated root dependencies, the static inserted parent
can still work with the original Riot clip. Do not assume this for every champion.

The bind bake is essential for vertices weighted partly to the chest and partly to the
arm. Baking only the exported mesh would use different inverse binds than Blender and
produce different deformation. Export refuses an explicit control with an unapplied
frame-zero offset and points to the bind-bake operation. The operation is undoable.
Frame zero must otherwise be the bind pose. Shared mesh data, shape keys, and additional
enabled modifiers must be resolved before this operation.

This explicit operation replaces the old Shift+D/name-based offset guessing. Export
never reparents the user's bones. Moving an ordinary bone in Edit Mode alone is a rest
edit, not an instruction to turn it into an offset controller.

## Animation and bind conventions

One export plan supplies the current parent links, joint names, indices, and coordinate
frames to SKL, SKN and ANM. Imported visual bone orientations are converted back with
one per-joint correction for both bind and animation. Every exported joint gets a track
in an exported ANM, including duplicates and custom joints. Full translation, rotation,
and scale are retained below inserted parents.

Visual-pose export still uses frame zero as the mesh/skeleton bind pose; it uses the same
joint axes as ordinary animation export. Batch conversion uses the same importer and
exporter, forwards scale/transform/name options, stages clip outputs before replacement,
and restores the scene's action and pose afterwards.

Arbitrary reparenting is not the same as adding a leaf or using the explicit offset
control. Re-export/bake animations for a changed general hierarchy. The three Maya tests
cover added leaves, translated shoulder controls, and an animated cape. They do not
establish compatibility for every old offset rig, nonuniform scale/shear, or physics rig.
Older .blend files with several renamed copies of one native ID can be ambiguous;
reimport their source skeleton or explicitly clear copied native metadata on added bones.

## Regression coverage

See `maya-bone-test-results.md` for the real-character transfer and the separate
compressed-animation interpolation finding. `tests/test_format_sweep.py` includes
regressions for duplicate ordering/tracks, inserted-parent translation/scale, explicit
offset bind validation, blended-weight skinning, custom-track insertion, and name
collisions. No Rust rebuild is required for these Python-side changes.