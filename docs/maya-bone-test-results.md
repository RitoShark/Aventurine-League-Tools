> Status correction: this is the **ANM-rewrite experiment**, not a solution to the
> requested unchanged-ANM workflow. Automatic root linking has been removed from Add
> Offset Parent. See `stock-anm-investigation.md` for the direct Maya control and the
> measured drawbacks of weight reassignment.

# Corrected hand and offset tests - 2026-10-16

The earlier shoulder test passed Blender-to-Maya agreement while producing the same
broken palm in both applications. This report supersedes its claim that Seraphine's
wider shoulders work with an unchanged Riot ANM.

## Cause and correction

Seraphine's palm is weighted to the separately animated root `R_Hand_Snap`. Her fingers
follow the clavicle subtree. The first offset changed only that subtree; palm vertices
could remain about 18 cm behind the fingers. The new companion controls apply the same
animated displacement to the separate roots while retaining their original motion.
They use native Blender constraints, so normal ANM export and batch conversion both
bake the correction. Mesh weights are unchanged. Translation offsets are supported;
linked source-control rotation/scale edits are rejected at bind application.

Root companions are now explicit opt-in via Link Root (Requires ANM Export).
The test driver explicitly creates them to reproduce this experiment.
**Linked root corrections require updated ANMs together with SKN/SKL.** A static SKL
parent cannot encode a displacement that changes direction with animated chest motion.

## Executed checks

Used Blender 4.5.14 and Maya 2023 with unmodified lol_maya. The corrected scenes are in
`Desktop/Aventurine-Bone-Tests-Hand-Fix`; the old review remains available separately.

- Cases 00 and 01 retain the untouched Riot run clip.
- Case 02 uses the corrected shoulder rig and a Blender-exported full ANM.
- Case 03 retains the animated cape and exported ANM.
- All four cases compare every joint and 924-1035 mesh probes over frames 0-33.
- The assembled comparison scene is checked too, with mesh transform inheritance
  disabled to avoid double layout translation and skeleton display layers visible.
- Corrected shoulder transfer: maximum joint-head error 0.000512 cm, sampled mesh
  error 0.000397 cm. All cases remain inside the 0.01 cm tolerance.

The hand-shape test is independent of transfer agreement: compare 376 vertices on each
hand against the unedited character after subtracting the intended shoulder displacement.
It checks bind plus animation frames, preserving the palm's independent animation.

| Clip | Frames including bind | Max hand-shape error in Maya (cm) |
|---|---:|---:|
| seraphin_run.anm | 34 | 0.000164058 |
| seraphin_attack1.anm | 72 | 0.000175664 |
| seraphin_passive_attack1.anm | 24 | 0.000190494 |
| seraphin_idle2.anm | 176 | 0.000146124 |
| seraphin_spell4.anm | 17 | 0.000187497 |
| seraphin_dance.anm | 201 | 0.000229622 |

That is 752 hand vertices across 524 frames. Blender's corresponding maximum error was
below 0.000159 cm; Maya's below 0.000230 cm. The Maya six-clip comparisons use independently
imported Blender-exported reference and edited animations, with fresh SKN/SKL imports
for each clip. They test preservation of hand shape, not the Rust decoder's fidelity to
Riot's compressed interpolation.

Batch conversion was checked on copies of those six source clips. Its output matched
direct export; original backups remained identical, and the scene action/frame were
restored. Both Blender 4.0 and 4.5 passed 39 of 40 suite tests; the optional external Lemon
parser test was skipped. A synthetic regression checks an independently moving/rotating
root, stock clip reimport, and full exported clip reimport without double offsets.

## Limits and earlier findings

The separate compressed-animation interpolation discrepancy remains unresolved. For the
four-case controlled run comparison, Blender's base motion still uses the Maya-sampled
reference. These checks do not claim all champions, arbitrary disconnected dependencies,
rotational offsets, physics, or in-game playback are universally solved.

The initial review also accidentally hid the skeleton display layers and applied the
comparison translation twice to skinned meshes. The current generator corrects that
layout and verifies the assembled scene rather than only the individual imports.

## Reproduce

Use `tests/manual_blender_bone_scenes.py` and `tests/manual_maya_bone_scenes.py` as described
in the bone workflow. The Desktop review folder also contains the exact six-clip Blender
and Maya hand-shape validation drivers and JSON results. No Riot assets are included in
the repository or plugin ZIP.
