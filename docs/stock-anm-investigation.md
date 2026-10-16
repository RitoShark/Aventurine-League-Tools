# Unchanged-ANM investigation - 2026-10-16

Status: the requested lossless, unchanged-ANM shoulder edit for Seraphine is unresolved.
The previous root-companion fix was an ANM-rewrite experiment and does not meet that
requirement. Automatic root linking was removed from Add Offset Parent. The explicit
link operation remains available, labelled as requiring ANM export.

## Direct Maya control

Loaded original Seraphine SKN/SKL through unmodified lol_maya in Maya 2023. Added neutral
joints above L/R_Clavicle using Maya's own API, preserving the existing child local
transforms. Moved each control outward 18 cm and applied the untouched Riot run ANM.
No Blender files or Blender exporter were involved.

The regular hand joints moved by about 18 cm. L/R_Hand_Snap remained at their original
animated positions. Maximum difference between hand and snap displacement over frames
1-33: 17.999978 cm. The same hierarchy-only edit therefore reproduces the root cause in
Maya. This is separate from the earlier Blender exporter bugs and comparison-layout bug.

The saved diagnostic scene is `maya_native_offset_reproduction.ma` under
`Desktop/Aventurine-Stock-ANM-Investigation`. It is intentionally a failure reproduction,
not a corrected model. It evaluates the edit in Maya without exporting/rebinding skin;
it isolates which animation branches receive the new control displacement.

## Weight-only alternative

Measured transferring each palm vertex's R_Hand_Snap weight to R_Hand, retaining the
existing bindings and leaving the source ANM unchanged. Evaluated actual Maya skin
transforms against the original character, before adding any shoulder offset. The
L_Hand_Snap root had no skin influence in this model.

Probed 88 original animation files at bind, endpoints and up to eight evenly spaced
animation frames. This is sampled coverage, not all frames. The root and regular hand
are not interchangeable: their original deformation differs significantly.

| Clip | Largest sampled weighted-vertex change (cm) |
|---|---:|
| seraphin_run.anm | 3.203714 |
| seraphin_attack1.anm | 7.606823 |
| seraphin_idle2.anm | 0.529751 |
| seraphin_dance.anm | 1.027750 |
| seraphin_spell1_-90.anm | 10.689474 |

Reweighting is therefore a motion-changing asset edit, not an equivalent exporter fix.
No source weights or source ANMs were changed by this experiment.

## Why the previous correction needed animation

The desired independent-root pose is its original animated global transform plus a
translation that follows the animated chest. A fixed new root parent can add a constant
world offset; it cannot supply that varying chest-relative displacement. Parenting the
root under the chest instead also applies the chest's rotation to animation values that
were authored in root space. The previous Blender constraints supplied the missing
animated displacement, which the game/Maya then needed as new ANM tracks.

This does not prove that every possible rig or asset redesign is impossible. It explains
why ordinary static parenting, the previous live constraints, and direct weight transfer
do not provide a lossless answer for this asset. A different binding/hierarchy design
would need separate validation against original motion, not just Blender/Maya agreement.

## Source changes and verification

Default offset creation now leaves unrelated roots unparented and adds no live companion
constraints. It reports matching original Snap roots outside the selected subtree.
Optional explicit linking is labelled Link Root (Requires ANM Export). The existing
synthetic regression now verifies the default behavior before explicitly opting into the
rewrite experiment. Blender 4.0 and 4.5 each passed 39 tests, with one optional external
Lemon parser check skipped (40 total).

Do not use the previous hand-fix ZIP/review as evidence that unchanged ANMs are supported
for this shoulder edit. Existing source ANMs and open Maya sessions were left untouched.
