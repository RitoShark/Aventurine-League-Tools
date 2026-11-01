# Apply rest pose while keeping bound meshes

Local test build, 2026-10-26, version 4.0.1.

## Workflow

1. Import Yasuo (or another source rig) with its bound meshes.
2. Bring in the replacement mesh, such as Yone, without binding it yet.
3. Adjust bones in Pose Mode. The original bound body and props follow the bones.
4. Choose **Pose > Apply > Apply Pose as Rest Pose (Keep Bound Meshes)**.
5. Delete unwanted original body geometry while retaining any props that share its material.
6. Bind the replacement mesh to the fitted rig. Export SKN with matching SKL, with Bake Frame 0 Pose off.

The Keep Bound Meshes entry replaces the full-rest entry in Blender's normal Pose Apply menu
for imported League rigs. It is also available under its full name in F3 search. Generic rigs
keep their normal menu. Blender's original bones-only operator remains accessible through
scripting/search; Apply Selected as Rest Pose (Bones Only) still performs Blender's partial
operation. There is no background handler and no global native-operator replacement.

Blender's native operation resets skinned geometry relative to the new rest, as described in
[Blender's Apply documentation](https://docs.blender.org/manual/en/latest/animation/armatures/posing/editing/apply.html).
Aventurine's command evaluates and commits the bound geometry first, together with the new rest.

## Preserved data

All scene-used meshes whose Armature modifier targets this armature are included, including
hidden objects, excluded collections and meshes in other scenes sharing that armature object.
Unbound replacement meshes are untouched. The command keeps skin weights, vertex groups,
UVs, materials, geometry attributes, armature modifiers, flat viewport shading, and transformed
export normals. Modifiers following the armature remain downstream and are not baked twice.
Shared mesh data is copied, so unrelated users retain their geometry.

The operation is undoable. Preparation and validation errors restore the meshes and skeleton.
It requires a static, local, single-user armature, with active action/NLA and drivers unlinked,
and enabled bone constraints disabled or baked first. Shape keys, bone-parented skinned objects,
multiple active armature modifiers, and enabled modifiers preceding the armature are rejected
with a message before baking. Unsupported singular/sheared output bindings also stop and roll
back. This is a full-armature rest commit, not a selected-bone bake or animation retargeter.

No ANMs are rewritten. After fitting, import original animations afresh against the new rest.
The existing visual-axis/nonuniform-animation-scale and compressed interpolation limitations
noted in the earlier rest-pose audit remain separate issues.

## Validation

- 61 tests pass in Blender 4.0 and 4.5.15 with the real Seraphine and Lemon reference fixtures enabled.
- Added tests cover live pose deformation, visible/hidden meshes, excluded collections,
  unbound replacement/shared data, weights/UVs/modifiers, scaled geometry and transformed
  source normals, repeated neutral commits, unsupported-input rejection, failed-commit rollback,
  actual operator execution, menu callback restoration, and joint mesh/skeleton undo/redo.
- Real Yasuo was imported and split into its original three material meshes. Shoulder and hand
  rotations plus a hand translation were posed. The body was hidden, the props retained, and an
  unbound mesh kept alongside as the replacement stand-in. The actual operator preserved all
  three meshes' posed vertex coordinates exactly at commit. The unbound mesh was unchanged.
- The fitted Yasuo was exported as a paired SKN/SKL. Original flute and blade vertices were
  compared in Blender against the unchanged rig at first, middle and last frames of all 44
  original animations. Maximum difference: 0.000278 cm. Vertex correspondence uses UVs,
  weights and original positions, accounting for coincident UV seams.
- SHA-256 checks confirmed all 44 source animation files remained unchanged.
- These are Blender/API and sampled animation tests, not an in-game verification of the new build.
  No user mod files were replaced.

The test ZIP contains the current native Rust extension. These changes are Python-only and
require no new Rust build. The earlier flat-shading and current-rest animation-import fixes
are included as well.
