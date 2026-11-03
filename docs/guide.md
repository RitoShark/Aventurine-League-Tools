# Aventurine user guide

[Back to README](../README.md) · [Development guide](development.md)

## Installation and updates

Use the packaged **Aventurine-VERSION.zip**, rather than GitHub's source-code ZIP. The bundled native extension supports Windows x64; Blender 4.0 or newer is required.

1. Download the ZIP from the [releases page](https://github.com/RitoShark/Aventurine-League-Tools/releases).
2. Open **Edit > Preferences > Add-ons**, use **Install...** or **Install from Disk...** depending on your Blender version, and select the ZIP.
3. Enable **Aventurine: League Tools**.
4. Press **N** in the 3D Viewport to open the sidebar.

The main import/export tools are in **Aventurine LoL**. Enable individual extras under **Misc LoL Tools** in the add-on preferences. MAPGEO has a separate optional **Aventurine MapGeo** tab, and UV helpers appear in the UV Editor's **UV Tools** tab.

Use **Check for Updates** in the add-on preferences to install updates, then restart Blender. The updater validates the packaged Windows ZIP before swapping folders. If Windows locks the installation, close Blender and install the ZIP manually; the loaded Rust DLL is never overwritten in place.

## Import and export

Import an SKN to load a mesh, with options to load its matching SKL, separate it by material and load textures. Keep the extracted skin files and BIN paths available for texture lookup. **Reload Textures** retries loading after files change.

**Connected Bones (Classic)** points displayed bones towards their children. **Exact Joints** retains the source joint orientations for cross-tool work. Both use the imported joint metadata during export.

Select the armature to export its meshes, or select the meshes you intend to export. Export a matching SKN and SKL together after changing the skeleton or skinning palette. The exporter writes SKN **4.1** with bounds calculated from the exported geometry.

SKN export supports up to **31 nonempty submesh/material sections**. Exports with 32 or
more are blocked because they can render invisible in game. Merge sections before
exporting; unused Blender material slots do not count. Larger SKNs can still be imported.

ANM import targets the active armature. Select the skeleton itself, rather than its mesh, when several rigs are present. With no active armature, regular ANM import falls back to the first scene armature.

Drag-and-drop is available on Blender versions supporting file handlers. The **Direct Import (Drag & Drop)** preference controls whether dropping files skips the settings dialog. Recent SKN/ANM entries are also available from the main panel.

## Animations and retargeting

### Importing clips

- **New Action** creates one action per imported ANM; the last imported action becomes active.
- **Insert at Current Frame** inserts into the active action, replacing matching frame keys while preserving keys outside the inserted clip.
- Frame 0 is the committed rest pose for a fresh import; clip samples begin at frame 1.
- The scene timeline and FPS are shared by every rig. Importing another clip can change those settings.

The **Load Animations** panel finds an `animations` folder beside the imported skin/skeleton, or uses a chosen custom folder. Its list is shared across the scene: select the intended imported armature and click **Refresh** after switching characters. Clear a custom folder override when returning to automatic detection.

Use the NLA import/export buttons for multiple clips, or the ANM export dialog's batch action option. Preserve actions you want to keep with Fake User or an NLA strip when replacing an armature's active action.

### Retargeting onto another skeleton

1. Import an animation onto its source armature.
2. In **Misc LoL Tools > LoL Retarget**, choose the source and target armatures.
3. Generate the bone mapping and review it. Regenerate when changing rigs.
4. Choose **World Bake** for matching skeletons, or **Rotations Only** for different proportions. **Raw Curve Copy** is the legacy option for matching rest poses.
5. Apply Retarget and preserve the resulting target action before transferring another clip.

Two source rigs can hold separate actions and retarget onto a third rig in successive operations. The second result becomes the target's active action; use Blender's NLA to sequence or blend saved results. Retargeting reuses an existing result with the same source-action-derived name, so rename or duplicate a result before rebaking if you need both versions.

## Skinning and physics

**Skin Tools** includes bone detection, Auto Skin, weight transfer, nearest-bone binding, weight inspection and vertex-group cleanup. Use Blender's weight painting tools to review the result on your own model.

**Wiggle 2**, **Hair Physics** and **Boobs Physics** are optional Misc panels. The dedicated physics panels support preview/undo, collision controls and batch processing of animation folders. Choose an export folder for the baked clips. These are explicit animation-authoring tools; ordinary model export does not run them.

## SKN Material Order

Select an armature or one of its bound meshes, then open **Misc LoL Tools > SKN Material Order**.
Use the up/down arrows to set the top-to-bottom SKN export order. **Match SKN...** copies the
order of matching material names from a reference SKN, with unmatched materials at the end.
**Reset** restores the imported order. Settings are stored on the armature in the `.blend`.
Toggle the panel in **Preferences > Add-ons > Aventurine > Misc LoL Tools > SKN Material Order**.
The master Misc toggle controls it too; when enabled, the panel stays visible even with no selection.

New imports remember their source order, including after separating by material. Existing
scenes can use **Match SKN...** to recover it from the original SKN. Selected-only exports
follow the same order for the materials included. This controls the exported submesh list;
it does not change material assignments, shader settings, BIN files, or guarantee a particular
transparency result in-game. See [material ordering](material-order.md).

## Adjusting a skeleton to a mesh

Import the replacement mesh without binding it yet. Pose the existing armature to
fit it: all meshes already bound to that armature follow the bones, including the
original body and any props. Then use **Pose > Apply > Apply Pose as Rest Pose
(Keep Bound Meshes)**. Aventurine puts this command in Blender's normal Apply menu
for imported League rigs; it is also searchable by its full name with F3.

This commits the skeleton and the posed shape of its bound meshes together, including
hidden objects and meshes in excluded collections. It preserves weights, UVs, flat
viewport shading, export normals and armature modifiers. Unbound replacement meshes
stay untouched. You can then delete the old body, retain the props, and bind the
replacement body. Preserve any prop geometry that shares the old body's material.

Export SKN with its matching SKL, leaving **Bake Frame 0 Pose** off. Imported animations
use the current rest pose; frame 0 is the committed rest and animation begins at frame 1.
Model export does not rewrite adjacent ANMs. Explicit animation export remains available
for authored animation. Save/Restore Pose Snapshot only stores controls; it does not
commit the rest pose.

Use the **Keep Bound Meshes** command for this workflow. Blender's raw
`pose.armature_apply` operator, still available through scripting/search, changes bones
only. The menu's **Apply Selected as Rest Pose (Bones Only)** retains Blender's partial
operation. No global operator replacement or background scene handler is installed.

Fit a static rig with its active animation unlinked and bone constraints disabled.
The command stops before baking if shape keys, linked data, multiple active armatures,
or modifiers ahead of the armature require a different bake order. Downstream modifiers
remain in place. Shared mesh data is copied so unrelated objects are not changed. Singular
or sheared bind transforms are rejected and the transaction restored. Undo/redo includes
both the meshes and skeleton.

SKN and SKL export dialogs include **Bypass Shear Check**, off by default. If export
reports shear, this lets you export an approximation of the bone transforms. Mesh
deformation can change, so check the result in game. Invalid/zero-scale transforms
and all other export limits remain enforced. This option applies to export only;
Apply Pose as Rest Pose keeps its validation.

Existing visual-axis approximations for nonuniform animated scale and compressed-animation
interpolation differences remain separate limitations. See the
[bound-mesh rest-pose checks](bound-mesh-rest-pose.md) and earlier
[rest-pose test results](rest-pose-test-results.md).

## SKN shading

New SKN imports use the legacy flat shading in Blender. Original SKN normals are
stored separately in the mesh's `SKNNormal` corner attribute and used for export;
flat viewport shading does not replace them. The stored data survives saving a
`.blend` and the importer's Split by Material option. Posed exports and the Apply
Offset to Bind Pose tool evaluate those normals with the armature deformation.

Changing mesh positions/topology invalidates the stored normal basis. For imported
SKN meshes, export then recalculates smooth normals on a temporary copy instead of
using the flat preview's face normals. Explicit sharp edges and authored custom
normals remain effective. This avoids unnecessary per-triangle vertex duplication;
UV seams and real normal discontinuities can still require extra SKN vertices.
The same normal preparation is used when committing a rest pose. Ordinary Blender
meshes retain their authored shading. Existing scenes retain their viewport shading;
reimport an SKN to get the new flat preview behavior.

## Custom bones and offset controls

Add and parent new bones normally, then weight and animate their geometry. Stock clips without tracks for those new bones do not supply their animation. See [bone workflows](bone-workflow.md) for duplicate metadata, offset parents and independently animated roots.

**Save/Restore Pose Snapshot** stores pose controls, not a new rest pose. **Link Root (Requires ANM Export)** is an optional authored-animation workflow and requires exporting its resulting tracks; it is not part of the unchanged-ANM fitting workflow.

## Static objects and maps

SCB import/export is available in the main panel. Legacy SCO remains under **File > Import/Export**. Enable **MapGeo Tools** in the preferences for the separate map panel.

MAPGEO export defaults to **Preserve original** spatial data. Keep the original source accessible and export its matching version. Missing, truncated or mismatched sources stop export. **Disabled spatial data** is experimental and does not generate new bucket grids or guarantee game compatibility.

## Format details and validation

See the [SKN/SKL audit](skn-skl-audit.md), [Rust format audit](rust-format-audit.md) and [rest-pose test results](rest-pose-test-results.md) for preserved fields, verification and known limits.
