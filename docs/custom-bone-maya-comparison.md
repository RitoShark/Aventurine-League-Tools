# Skeleton and animation audit: Maya comparison

2026-10-16. Aventurine commit `29dbe2f3556a3e51ff5c8fc16ba8d6e993d81e33`.

This is an investigation with an executable export prototype. No production importer,
exporter, UI, or Rust code was changed. It extends `custom-bone-investigation.md` with
Maya source comparisons, VISUAL orientation, animated duplicates, and real Seraphine data.

## Findings

The reproduced failures are in Blender-side bone identity and transform handling.
The existing Rust SKL/ANM writers can already express the successful prototype output.
A fresh leaf bone with a unique name works in the tested export path. Duplication,
reparenting, and importing animation onto custom bones fail for different reasons.

### 1. Export changes the user's hierarchy

`__init__.py:870` calls `export_skn.fix_custom_bone_parenting` before writing the mesh.
The helper at `io/export_skn.py:441` guesses offset controls from names and duplicated
native indices. In the ordinary Shift+D sibling case, it changes:

```
root -> child, child.001
```

to:

```
root -> child.001 -> child
```

This was reproduced with real Blender duplication in both import orientations.
It is a scene mutation, not merely an exported-file adjustment. An exporter must
not infer this change from a name. If offset controls remain supported, creation and
conversion should be explicit operations outside export.

### 2. A duplicate inherits a false native identity

Shift+D copies `native_bone_index`, original parent, stored bind TRS, and matrices.
Renaming the copy to `cape` does not clear those properties. The default SKL exporter
then treats its copied bind as authoritative instead of using its authored placement.

Keeping `.001` invokes a different path: `bone_utils.detect_offset_clones` classifies
it as an offset control, including when it is just a sibling. Both default and visual
ANM paths can skip that bone's track. An animated duplicate therefore loses its motion.

`export_skl.py` and `export_skn.py` also sort every copied index among native joints.
A duplicate of joint 1 produces `root, child, cape, tip` instead of appending `cape`.
Both files currently agree with each other, but original joint indices shift. Fixing
ordering must use one shared joint list and preserve the SKN influence-palette mapping.

### 3. Animation import deliberately removes channels

At `io/import_anm.py:246`, custom bones are prefilled with identity channels. At line
282, they are skipped even when their name hash has a matching ANM track. At lines
265 and 466, an original bone directly beneath a custom parent loses translation and
scale samples in normal import mode.

In the synthetic clip, the original child should reach scale `(1.12, 1.12, 1.12)` and
nonzero translation. Inserting a parent before import pins it to location zero and
scale one. The scene differs from the source clip by about 30 game units at probe
points. Inserting the same parent after import retains that motion in this fixture,
which helps isolate the importer as a separate failure.

Reimporting a matching animated `cape` track onto the edited rig loses roughly
76.59 game units of head motion. This is independent of Rust serialization.

Code inspection also found that the identity prepass uses frames `{0, frame_count}`
without `frame_offset`. This can write custom-bone keys outside an inserted clip's
range; that additional insertion case was not exercised by this audit.

### 4. SKL and ANM use different parent frames

The default animation exporter has separate original/custom/descendant branches
(`io/export_anm.py:202`, `:413`, `:478`). Some reconstruct a local transform against
the original native ancestor or pin native translation, while the SKL names the
current custom parent. Those locals cannot be composed through that exported hierarchy
and reproduce Blender's evaluated pose.

This is reproducible even when the animation was imported BEFORE the hierarchy edit,
so fixing import alone will not fix export.

### 5. Visual export is not a complete workaround

Visual SKL+ANM export matches the already-evaluated Blender scene for many fixtures,
but still skips animated `.001` duplicates. It also cannot restore motion discarded
by import. The mesh export UI's batch rewrite at `__init__.py:937` calls ordinary
`apply_anm`, without enabling its alternate full-TRS option, before visual export.
That path therefore inherits the import suppression.

The batch call also omits the mesh export's `model_scale` / animation scale and
`apply_object_transform` options. Those option mismatches are source findings,
not separately exercised here. The audit never invokes this folder-overwriting batch
on the user's assets.

### 6. Names are not resolved consistently

Default SKL export keeps a numeric suffix when its base name exists; default ANM
export strips it. Visual ANM preserves it but can skip the duplicate before reaching
name resolution. Collision handling can silently favor the unsuffixed track.
Import hashes the Blender name directly while Clean Names exports a sanitized name.
A single shared name/hash map is needed for SKL, ANM, and subsequent import matching;
collisions should produce a useful error instead of discarding tracks.

## What the Maya implementations do

Both reference checkouts were refreshed with `git pull --ff-only`; neither had newer
changes. They are under `Desktop/Maya-References`. No Maya code was copied into the
plugin and Maya itself was not run.

- [lol_maya SKL.dump](https://github.com/tarngaina/lol_maya/blob/362f3e219c28d91eee3aaf8b3082faf9256618b5/plug-ins/lol_maya.py#L1269)
  reads the current joint local transform and inverse inclusive world matrix.
  The original Riot skeleton is used for ordering, rather than overwriting new
  joints' transforms with imported values.
- [lol_maya ANM.dump](https://github.com/tarngaina/lol_maya/blob/362f3e219c28d91eee3aaf8b3082faf9256618b5/plug-ins/lol_maya.py#L2904)
  creates tracks for scene joints and samples local translation, scale, and rotation
  including rotate-axis and joint orientation.
- [Lemon SKL.scene_dump](https://github.com/tarngaina/LtMAO/blob/9c6ed6763c53f61dc43ce56893012f491f7dd63f/src/LtMAO/lemon3d/lemon_maya/plugins/translator/skin.py#L903)
  uses the same approach. Its `riotid` ordering block at lines 990-1033 treats a
  duplicated ID as an extra joint and appends it. Parent links are resolved from
  the current DAG after ordering. With a reference SKL it matches original names
  and appends extras.
- [Lemon ANM.scene_dump](https://github.com/tarngaina/LtMAO/blob/9c6ed6763c53f61dc43ce56893012f491f7dd63f/src/LtMAO/lemon3d/lemon_maya/plugins/translator/anm.py#L243)
  samples every scene joint's local TRS. Its importer matches joint-name hashes
  without requiring an Aventurine-style native index.

The useful model is that an ordinary added joint remains an ordinary joint. Joint
ordering, current hierarchy, and transforms are separate concerns. Maya joint
orientation has to be accounted for explicitly; Blender's visual bone orientation
needs a corresponding consistent conversion, not a literal copy of Maya API calls.

This does NOT prove that arbitrary reparenting remains compatible with every untouched
stock ANM. Animation values are parent-local. A changed hierarchy generally needs
compensated locals / rebaked clips, or a specifically designed compatibility control.
Adding a leaf without changing original parents is a different operation.

## Measured export results

`tests/audit_custom_bones.py` runs 30 scenarios per Blender version: 22 synthetic
and 8 Seraphine. Completed on Blender 4.0.0 and 4.5.14 LTS with no diagnostic errors.
Both NATIVE and VISUAL import orientations were tested. Seraphine uses
`seraphine_base.skl` and the first eight frames of `seraphin_run.anm`.

The script reads the exported files, composes joint locals through exported parents,
applies inverse bind transforms, and compares their deformation to Blender's evaluated
pose at each bone head and three points 20 game units away. The table reports the
maximum point distance, not a rendered mesh or in-game test. Values below are NATIVE
orientation on Blender 4.5.14; VISUAL produces the same failure pattern.

| Fixture | Default export error | Visual export error | Shared-frame prototype error |
|---|---:|---:|---:|
| Synthetic, unchanged | <0.001 | <0.001 | <0.001 |
| Fresh leaf, manually animated | <0.001 | <0.001 | <0.001 |
| Moved duplicate renamed `cape` | 60.314 | <0.001 | <0.001 |
| Animated `child.001` duplicate | 76.137 | 76.137 | <0.001 |
| Insert parent before importing clip | 146.235 | <0.001 | <0.001 |
| Insert parent after importing clip | 150.697 | <0.001 | <0.001 |
| Seraphine, unchanged | <0.001 | <0.001 | <0.001 |
| Seraphine, moved renamed duplicate | 53.852 | <0.001 | <0.001 |
| Seraphine, inserted parent at L_Clavicle | 244.321 | <0.001 | <0.001 |

Export-to-scene agreement is separate from source-animation preservation. The prototype
can faithfully export a scene whose importer already dropped channels. It does not fix
that loss. Likewise, the untouched Seraphine source comparison has a roughly 0.622-unit
residual around L_Top_Ribon; its source bind times inverse bind already has a roughly
0.566-unit residual at the probe points. These source/rounding/decomposition differences
are not counted as new custom-bone regressions and their exact origin is not resolved
here. Do not interpret the table as proof of exact stock-file fidelity.

## Concrete replacement design

1. Resolve original joint identity once. Keep a source joint registry on the armature
   (original ID/name, original parent, imported frames). Copied bone properties alone
   must not establish ownership. Existing ambiguous rigs need validation and an explicit
   conversion/mapping operation, not another name-based guess.
2. Build one export plan shared by SKL, SKN and ANM: original joints in original order,
   extras appended, current parents, unique final names/hashes, and the skin palette.
   Ordinary duplicates are extras. Offset controls are an explicit role if retained.
3. Use one coordinate frame per joint for bind and animation. The tested prototype uses:

   ```
   V = imported Blender rest global
   N = imported native rest global (in Blender coordinates)
   K = inverse(V) * N, for a genuine source joint; identity for an added joint

   exported bind global = current Blender rest global * K
   exported pose global = evaluated Blender pose global * K
   local = inverse(current exported parent global) * exported joint global
   inverse bind = inverse(exported bind global)
   ```

   Apply object transform, unit scale, and axis conversion consistently to both.
   The skinning transform is then pose * inverse(rest); K cancels. Use evaluated
   transforms so authored constraints can be baked, subject to separate verification.
4. Import all matching custom-joint tracks. For stock clips on changed hierarchies,
   reconstruct the source motion in the original hierarchy and solve/bake the new
   parent-local pose. Never implement that conversion by dropping translation or scale.
   Separate preserving original world motion from intentionally applying rig offsets.
5. Make export read-only with respect to the scene. Make batch rebaking use the same
   export plan and settings; stage outputs and report failures before replacement.

The prototype implements only the matrix part and complete track emission. It knows
original identities from the source fixture's names, so it is NOT a production identity
resolver. It does not implement importer repair, UI compatibility, SKN palette changes,
constraint-specific baking, deleted/renamed original joints, or offset-control migration.
Nonuniform scale plus rotation can introduce shear that the format's TRS records cannot
represent exactly; decomposition residuals need validation instead of silent acceptance.

## Reproduction

Run in a fresh background Blender process: this diagnostic clears its Blender scene.
It writes synthetic fixtures and exports into the supplied output directory. It reads
but does not modify optional real source assets.

```powershell
& '<Blender executable>' --background --factory-startup --disable-autoexec `
  --python-exit-code 1 --python tests/audit_custom_bones.py -- `
  --addon '<aventurine checkout>' --output '<new temporary output directory>' `
  --real-skl '<seraphine_base.skl>' --real-anm '<seraphin_run.anm>'
```

Omit the two real-asset arguments to run 22 synthetic scenarios. `results.json` contains
per-case errors, missing tracks, joint order, sampled channels, and export-button
reparenting. `provenance.json` records Blender version and case errors. A process failure
means the diagnostic could not run; known production regressions are recorded as
measurements rather than assertions. This is not part of the normal release test suite.

Before shipping a replacement, convert these reproductions into regression assertions
and add weighted mesh/palette checks, nonuniform scale, constraints/physics baking,
object transforms, renamed/deleted originals, animation insertion offsets, old offset
rig migration, full real clips, and in-game playback. No new build is needed for this
investigation, because runtime files were not changed.