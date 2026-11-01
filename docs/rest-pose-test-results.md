# Rest-pose workflow verification

Historical bones-only validation. The newer [Keep Bound Meshes command](bound-mesh-rest-pose.md)
commits bound geometry too. Its normal-menu workflow supersedes steps 1-2 below; raw
Blender operator behavior tested here is unchanged.

Test build: 4.0.1, 2026-10-26. These are local validation results, not a claim of universal in-game compatibility.

## User workflow

1. Fit bones to the mesh in Blender Pose Mode.
2. Use Pose > Apply > Apply Pose as Rest Pose.
3. Export SKN with its matching SKL. Leave Bake Frame 0 Pose off.
4. Import an original animation to preview. Frame 0 is the committed rest pose; animation begins at frame 1 and replaces the tracked joint locals.

The plugin uses Blender's resulting rest matrices, including its normal Apply Pose as Rest semantics. It does not recover scale that Blender has discarded when committing the rest pose. Mesh rest coordinates are not silently baked or adjusted. Save/Restore Pose Snapshot only stores pose controls and does not change the rest pose.

## Changes

Animation import now calculates Blender pose channels against the current rest pose and current hierarchy. Imported native joint-axis corrections are retained. Old source rest offsets are no longer added to animation after a rest edit. This requires no special fitting mode.

Removed the Unmodified League Armature option and its automatic adjacent-ANM rewriting routine. Model export never rewrites neighboring animations. Explicit animation export remains available for authored animation.

Bind transforms are checked before writing models. Singular transforms or shear that cannot be represented by the format's TRS fields produce an error instead of silently approximating the binding or partially overwriting the model pair. Uniform scaling or baking stretch into the mesh avoids that unsupported case.

The test ZIP also includes flat viewport shading with preserved imported skin normals for export.

## Validation

- All 53 format/integration tests pass in Blender 4.0 and 4.5.15, including the actual Blender Apply Pose as Rest operator, native and visual bone modes, rotation/translation/uniform-scale combinations, frame-zero rest, later-frame export, skin deformation, and unsupported-bind rejection.
- The actual model-export operator leaves a neighboring ANM's bytes and modification time unchanged and creates no animation backup directory.
- A synthetic inserted-parent hierarchy was independently loaded through lol_maya. Blender's transforms matched Maya over frames 0-3 (maximum matrix component difference approximately 0.000006). An arbitrary inserted parent must affect its descendants as it does in Maya; animations are not compensated to hide that effect.
- Real Seraphine: rotate, translate, scale, combined, and native-mode combined shoulder edits were committed with Blender's Apply Pose as Rest Pose, exported, and animated using the original run clip. All 125 joint positions and every mesh vertex were checked at frames 1, 9, 17, 25 and 33 against the decoded source animation and serialized bind transforms; errors stayed below 0.001 cm.
- The five exported model pairs were loaded into isolated Maya 2023 sessions using lol_maya and the original run animation. All joint matrices and mesh vertices sampled every 53 vertices matched the independent reference within 0.001 cm at frames 1 and 33.
- All 88 original Seraphine clips imported in full into an unchanged rig and a rig with a combined shoulder rest edit. At the first animation frame, the fitted rig returned to the baseline rig's animated joint transforms within 0.001 matrix-component tolerance. SHA-256 checks confirmed all source animations were unchanged.
- The 88-clip sweep checks import coverage and first-frame agreement with the unchanged Blender rig. It does not establish every-frame Maya or game parity.

Source run ANM SHA-256: `221442578be1d4e6a48d838bd86974cce1aa6fa846e729505ee000f594152ff3`.

Reference readers: lol_maya commit `362f3e219c28d91eee3aaf8b3082faf9256618b5`; LtMAO/Lemon commit `9c6ed6763c53f61dc43ce56893012f491f7dd63f`.

## Remaining limits

Visual-axis conversion of some nonuniform animated scales requires an approximation in Blender pose channels. This exists on the original unedited rig too; the rest-pose fix preserves that baseline behavior and does not reject otherwise valid animations. Seven clips in the sweep exceeded 0.001 matrix-component error against an independent full-TRS calculation, with respawn especially affected. This is separate work, not resolved here.

Compressed-animation decoding/interpolation differs between the current backend and lol_maya at some interior frames, including frames 9 and 25 of the run clip, even without rest edits. Maya parity above is deliberately limited to the checked frames 1 and 33. The Blender full-mesh checks use the backend-decoded animation as their reference.

Changing the hierarchy can intentionally change stock animation results, as it does in Maya. This workflow does not retarget animations, compensate arbitrary inserted parents, or bake linked-root constraints into untouched clips.
