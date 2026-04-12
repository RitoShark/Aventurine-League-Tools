"""
Auto Boobs Physics - Automated breast jiggle physics for LoL custom skins.
Uses the Wiggle 2 physics engine to apply jiggle to breast bones,
then bakes the result into animation keyframes for in-game use.
"""

import bpy
import os
from bpy.types import Panel, Operator, PropertyGroup, UIList
from bpy.props import (
    StringProperty, CollectionProperty, IntProperty,
    PointerProperty, FloatProperty, BoolProperty,
)
from ..ui import icons
from ..io import import_anm
from ..io import export_anm
from .physics_common import (
    lerp, lerp_exp,
    find_armature, get_animations_folder,
    ensure_object_mode, select_armature,
    configure_wiggle_bones, clear_wiggle_from_bones, strip_physics_keyframes,
    find_default_collision_bones, post_bake_collision_correct,
    precompute_collision_radii, hide_meshes_for_batch, restore_meshes_after_batch,
)
from .wiggle_bake_common import (
    _detect_animation_loops,
    _copy_loop_end_to_start,
    _restore_nonloop_start_to_tpose,
    _smooth_boundary_frames,
    _set_linear_interpolation,
    _clean_tpose_keyframes,
    _fix_quaternion_continuity,
)


# ---------------------------------------------------------------------------
#  Jiggle preset mapping: slider 1-20 → Wiggle 2 parameters
#  1  = barely any movement (very stiff, high damping)
#  10 = natural looking jiggle
#  20 = unrealistic, exaggerated bounce
# ---------------------------------------------------------------------------

def get_jiggle_params(intensity):
    """Map a 1-20 intensity slider to Wiggle 2 bone physics parameters.

    Returns dict with: stiff, damp, gravity, mass, stretch, chain.

    Uses exponential interpolation so every slider notch produces a
    perceptually distinct change. Floor values on stiff and damp are
    chosen to keep the spring physically stable at all intensities:

      stiff floor 55: spring always has enough restoring force to pull
        the bone back before velocity can accumulate infinitely.
      damp floor 0.25: at 24fps (dt=0.042), effective per-frame factor =
        1 - 0.25*0.042 = 0.9895 — still very bouncy but oscillation
        decays rather than growing. Below this the sim becomes chaotic.

    stretch is intentionally near-zero. Bone stretching looks distorted
    for breast bones; jiggle should come purely from rotation.
    """
    t = max(0.0, min(1.0, (intensity - 1) / 19.0))  # 0..1 linear
    t = t * t                                         # quadratic bias: slider 10 ≈ old slider 5

    return {
        'stiff':   lerp_exp(580.0, 55.0, t),   # high = snap-back, low = floaty
        'damp':    lerp_exp(10.0, 0.25, t),    # high = dies fast, low = sustained bounce
        'gravity': lerp(0.0, 0.03, t),         # near-zero — any more makes them droop
        'mass':    lerp_exp(0.3, 1.8, t),      # heavier at high intensity for momentum
        'stretch': lerp(0.0, 0.02, t),         # tiny — stretching looks distorted
        'chain':   True,
    }


def apply_wiggle_to_bones(context, armature_obj, bone_names, intensity):
    """Configure Wiggle 2 on breast bones with intensity-mapped parameters."""
    return configure_wiggle_bones(context, armature_obj, bone_names, get_jiggle_params(intensity))


# ---------------------------------------------------------------------------
#  Property group for animation list items
# ---------------------------------------------------------------------------

class BoobsAnimListItem(PropertyGroup):
    name:     StringProperty(name="Animation Name")
    filepath: StringProperty(name="File Path")


def update_search_filter(self, context):
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


# ---------------------------------------------------------------------------
#  Scene properties
# ---------------------------------------------------------------------------

class BoobsPhysicsProperties(PropertyGroup):
    breast_bone_L: StringProperty(
        name="Left Breast Bone",
        description="Name of the left breast bone in the armature",
        default=""
    )
    breast_bone_R: StringProperty(
        name="Right Breast Bone",
        description="Name of the right breast bone in the armature",
        default=""
    )

    jiggle_intensity: IntProperty(
        name="Jiggle Intensity",
        description="1 = barely any jiggle, 10 = natural, 20 = exaggerated bounce",
        default=10, min=1, max=20
    )

    # Animation folder browser
    animations:        CollectionProperty(type=BoobsAnimListItem)
    active_index:      IntProperty(default=0)
    animations_folder: StringProperty(name="Animations Folder", default="")
    custom_folder:     StringProperty(
        name="Custom Folder",
        description="Manually selected animations folder",
        default="", subtype='DIR_PATH'
    )
    search_filter: StringProperty(
        name="Search",
        description="Filter animations by name",
        default="",
        update=update_search_filter,
        options={'TEXTEDIT_UPDATE'}
    )
    status_text:    StringProperty(default="Ready")
    current_loaded: StringProperty(name="Currently Loaded", default="")

    # Export
    export_folder: StringProperty(
        name="Export Folder",
        description="Folder to export baked animations",
        default="", subtype='DIR_PATH'
    )

    is_processing:      BoolProperty(default=False)
    backup_action_name: StringProperty(default="")

    # Body collision
    collision_enabled: BoolProperty(
        name="Body Collision",
        description="Create invisible proxy spheres on arm/shoulder bones so jiggle "
                    "bones bounce off them instead of clipping through",
        default=False
    )
    collision_bones: StringProperty(
        name="Collision Bones",
        description="Comma-separated list of body bones to use as colliders "
                    "(auto-detect fills this for you)",
        default=""
    )
    collision_sphere_factor: FloatProperty(
        name="Radius Scale",
        description="Overall scale for all collision capsule radii. "
                    "1.0 = auto-sized per bone type.",
        default=1.0, min=0.05, max=3.0, step=5, precision=2,
    )

    # Breast self-collision
    boob_self_collision: BoolProperty(
        name="Boobs Collision",
        description="Prevent the breast bones from clipping through each other",
        default=False
    )
    boob_self_collision_scale: FloatProperty(
        name="Radius Scale",
        description="How far apart the breast bones are kept. "
                    "1.0 = default separation.",
        default=1.0, min=0.1, max=3.0, step=5, precision=2,
    )


# ---------------------------------------------------------------------------
#  Bake pipeline
# ---------------------------------------------------------------------------

def _ramp_physics_params(physics_pbs, target_params, t):
    """Set physics bone parameters interpolated between stiff/no-movement and target.

    t=0.0 → very stiff (no visible physics), t=1.0 → full target params.
    Uses the same lerp_exp for perceptually smooth ramp-up.
    """
    # "No physics" = very high stiffness + high damping (bone snaps to rest).
    STIFF_OFF = 2000.0
    DAMP_OFF  = 50.0
    MASS_OFF  = 0.1
    GRAV_OFF  = 0.0

    stiff = lerp_exp(STIFF_OFF, target_params['stiff'], t)
    damp  = lerp_exp(DAMP_OFF,  target_params['damp'],  t)
    mass  = lerp_exp(MASS_OFF,  target_params['mass'],  t)
    grav  = lerp(GRAV_OFF,      target_params['gravity'], t)

    for pb in physics_pbs:
        pb.wiggle_stiff   = stiff
        pb.wiggle_damp    = damp
        pb.wiggle_mass    = mass
        pb.wiggle_gravity = grav


def bake_wiggle_for_current_action(context, armature_obj, bone_names, intensity):
    """Bake wiggle physics into the current action.

    Loop animations use a ramp-up convergence approach:
      1) First preroll pass: physics gradually fades in from 0% to 100%
         over the animation length (bones start stiff, progressively loosen).
      2) 3 more preroll passes at full physics: each pass lets the physics
         state converge so frame_end naturally flows into frame_start.
      3) Final pass: bake the actual keyframes with fully converged physics.
    This produces smooth, seamless loops without settling artifacts.

    Non-loop animations: reset physics, bake from rest, ease-in at start.

    The caller is responsible for:
      - collision correction (if enabled)
      - re-snapping the loop seam after collision (_force_loop_perfect_match)
      - fixing quaternion signs as the LAST step (_fix_quaternion_continuity)
    """
    scene = context.scene
    print(f"[BOOBS BAKE] Starting bake for bones={bone_names}, intensity={intensity}")

    if not armature_obj.animation_data or not armature_obj.animation_data.action:
        return False, "No animation loaded on armature"

    action      = armature_obj.animation_data.action
    frame_start = max(1, int(action.frame_range[0]))
    frame_end   = int(action.frame_range[1])
    print(f"[BOOBS BAKE] frame_start={frame_start}, frame_end={frame_end}")

    if frame_end <= frame_start:
        return False, "Animation has no frames"

    scene.frame_start = 1
    scene.frame_end   = frame_end

    is_loop = _detect_animation_loops(action, frame_start, frame_end)
    print(f"[BOOBS BAKE] is_loop={is_loop}")

    # Cap intensity for non-loops (no preroll → less room for error).
    effective_intensity = intensity if is_loop else min(intensity, 12)
    if effective_intensity != intensity:
        apply_wiggle_to_bones(context, armature_obj, bone_names, effective_intensity)

    select_armature(context, armature_obj)
    bpy.ops.object.mode_set(mode='POSE')

    armature_obj.wiggle_freeze = False
    scene.wiggle.loop           = is_loop
    scene.wiggle.bake_overwrite = True

    scene.wiggle.loop = True
    bpy.ops.wiggle.reset()

    if is_loop:
        # --- RAMP-UP CONVERGENCE FOR PERFECT LOOPS ---
        #
        # The idea: instead of slamming full physics onto a T-pose and hoping
        # 5 blind passes converge, we ease the physics in gently:
        #
        # Pass 0 (ramp-up): physics starts at 0% (very stiff, no movement)
        #   and smoothly ramps to 100% over the animation length. This avoids
        #   the violent initial oscillation from T-pose mismatch and lets the
        #   spring system gradually find its natural motion.
        #
        # Passes 1-3 (convergence): full physics. Each pass feeds frame_end's
        #   physics state back into frame_start, converging the loop seam.
        #   After 3 passes the state at frame_end ≈ frame_start.
        #
        # Final pass (bake): one more full-physics cycle, this time recording
        #   the bone transforms for keyframe insertion.

        target_params = get_jiggle_params(effective_intensity)
        physics_pbs = [armature_obj.pose.bones[n] for n in bone_names
                       if n and n in armature_obj.pose.bones]
        total_frames = frame_end - frame_start

        CONVERGENCE_PASSES = 3   # full-physics preroll passes after ramp

        # --- Pass 0: ramp-up preroll ---
        bpy.ops.wiggle.reset()
        scene.wiggle.is_preroll = True

        for f in range(frame_start, frame_end + 1):
            # t goes from 0.0 (start) to 1.0 (end) over the animation
            t = (f - frame_start) / max(total_frames, 1)
            # Smooth-step for a more natural ramp (ease-in/ease-out)
            t = t * t * (3.0 - 2.0 * t)
            _ramp_physics_params(physics_pbs, target_params, t)
            scene.frame_set(f)

        # Restore full target params for convergence passes
        _ramp_physics_params(physics_pbs, target_params, 1.0)
        scene.wiggle.is_preroll = False
        print(f"[BOOBS BAKE] Loop ramp-up pass done")

        # --- Passes 1-3: full-physics convergence ---
        # Physics state carries over from previous pass's frame_end.
        # Each pass wraps the loop, converging the seam.
        for pass_num in range(CONVERGENCE_PASSES):
            cur_action = armature_obj.animation_data.action
            if cur_action:
                strip_physics_keyframes(cur_action, bone_names)

            scene.wiggle.is_preroll = True
            for f in range(frame_start, frame_end + 1):
                scene.frame_set(f)
            scene.wiggle.is_preroll = False

            print(f"[BOOBS BAKE] Convergence pass {pass_num + 1}/{CONVERGENCE_PASSES}")

        # --- Final pass: actual bake ---
        # Physics is now fully converged. One more cycle to record values.
        cur_action = armature_obj.animation_data.action
        if cur_action:
            strip_physics_keyframes(cur_action, bone_names)

        action = armature_obj.animation_data.action
        if not action:
            return False, "No action to bake into"

        # IMPORTANT: collect values first, insert keyframes AFTER the loop.
        # Inserting during the loop would make Blender apply the new keyframes
        # on subsequent frames, overriding wiggle_pre's identity reset and
        # breaking the physics (double-application).
        stored = {}  # (bone_name, frame) -> (quat, loc, scale)
        scene.wiggle.is_preroll = True

        # Main cycle: frame_start → frame_end
        for f in range(frame_start, frame_end + 1):
            scene.frame_set(f)
            for pb in physics_pbs:
                stored[(pb.name, f)] = (
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                    tuple(pb.scale),
                )

        # Settling pass: continue physics past frame_end, wrapping back to
        # frame_start for a few frames. This overwrites the first frames
        # with values that are continuous with frame_end's physics state.
        # Without this, _copy_loop_end_to_start would change frame_start
        # but frame_start+1 was baked from the ORIGINAL frame_start value,
        # creating a visible glitch at frame 2.
        SETTLE = 8
        settle_end = min(frame_start + SETTLE, frame_end)
        for f in range(frame_start, settle_end + 1):
            scene.frame_set(f)
            for pb in physics_pbs:
                stored[(pb.name, f)] = (
                    tuple(pb.rotation_quaternion),
                    tuple(pb.location),
                    tuple(pb.scale),
                )

        scene.wiggle.is_preroll = False

        # Phase 2: insert all keyframes at once
        for bname in bone_names:
            if not bname:
                continue
            for prop, count in [('rotation_quaternion', 4), ('location', 3), ('scale', 3)]:
                dp = f'pose.bones["{bname}"].{prop}'
                for i in range(count):
                    fc = action.fcurves.find(dp, index=i)
                    if fc is None:
                        fc = action.fcurves.new(dp, index=i, action_group=bname)
                    for f in range(frame_start, frame_end + 1):
                        vals = stored.get((bname, f))
                        if vals is None:
                            continue
                        if prop == 'rotation_quaternion':
                            val = vals[0][i]
                        elif prop == 'location':
                            val = vals[1][i]
                        else:
                            val = vals[2][i]
                        fc.keyframe_points.insert(f, val, options={'FAST', 'REPLACE'})
                    fc.update()
    else:
        # Non-loop: just reset and bake, physics starts from rest
        scene.wiggle.loop = False
        bpy.ops.wiggle.reset()
        bpy.ops.wiggle.select()
        try:
            bpy.ops.nla.bake(
                frame_start=frame_start, frame_end=frame_end,
                only_selected=True, visual_keying=True,
                use_current_action=True, bake_types={'POSE'}
            )
        except Exception as e:
            return False, f"NLA bake failed: {e}"

    # --- Post-bake cleanup ---
    action = armature_obj.animation_data.action
    if action:
        _set_linear_interpolation(action, bone_names)
        _clean_tpose_keyframes(action, bone_names)
        if is_loop:
            _copy_loop_end_to_start(action, frame_start, frame_end, bone_names)
            _fix_quaternion_continuity(action, bone_names)
        else:
            _fix_quaternion_continuity(action, bone_names)
            _restore_nonloop_start_to_tpose(action, frame_start, bone_names)
            _smooth_boundary_frames(action, frame_start, frame_end, bone_names, smooth_ends='start')

    armature_obj.wiggle_freeze = True
    return True, "Baked successfully"


# ---------------------------------------------------------------------------
#  Operators
# ---------------------------------------------------------------------------

class BOOBS_OT_AutoDetectBones(Operator):
    """Try to auto-detect breast bones from common naming conventions"""
    bl_idname    = "boobs_physics.auto_detect"
    bl_label     = "Auto-Detect Breast Bones"
    bl_description = "Scan the armature for common breast bone names"
    bl_options   = {'REGISTER'}

    PATTERNS_LEFT = [
        'L_Breast', 'Breast_L', 'breast_l', 'l_breast',
        'L_Boob', 'Boob_L', 'boob_l', 'l_boob',
        'Breast1_L', 'L_Breast1', 'Breast_01_L',
        'L_Bust', 'Bust_L', 'bust_l',
    ]
    PATTERNS_RIGHT = [
        'R_Breast', 'Breast_R', 'breast_r', 'r_breast',
        'R_Boob', 'Boob_R', 'boob_r', 'r_boob',
        'Breast1_R', 'R_Breast1', 'Breast_01_R',
        'R_Bust', 'Bust_R', 'bust_r',
    ]

    def execute(self, context):
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found in scene")
            return {'CANCELLED'}

        bone_names = [b.name for b in armature_obj.pose.bones]
        found_l = next((p for p in self.PATTERNS_LEFT  if p in bone_names), "")
        found_r = next((p for p in self.PATTERNS_RIGHT if p in bone_names), "")

        # Case-insensitive substring fallback (never match buffbones — VFX only)
        if not found_l:
            for bname in bone_names:
                bl = bname.lower()
                if 'buffbone' in bl:
                    continue
                if ('breast' in bl or 'boob' in bl or 'bust' in bl) and \
                   ('_l' in bl or 'l_' in bl or bl.endswith('_l') or
                    bl.startswith('l_') or '.l' in bl):
                    found_l = bname
                    break
        if not found_r:
            for bname in bone_names:
                bl = bname.lower()
                if 'buffbone' in bl:
                    continue
                if ('breast' in bl or 'boob' in bl or 'bust' in bl) and \
                   ('_r' in bl or 'r_' in bl or bl.endswith('_r') or
                    bl.startswith('r_') or '.r' in bl):
                    found_r = bname
                    break

        if found_l:
            props.breast_bone_L = found_l
        if found_r:
            props.breast_bone_R = found_r

        if found_l or found_r:
            self.report({'INFO'}, f"Found: {found_l or '(none)'} + {found_r or '(none)'}")
        else:
            self.report({'WARNING'}, "Could not auto-detect breast bones. Please select them manually.")
        return {'FINISHED'}


class BOOBS_OT_AutoDetectCollisionBones(Operator):
    """Scan the armature for common arm/shoulder bones and fill the collision list"""
    bl_idname  = "boobs_physics.auto_detect_collision"
    bl_label   = "Auto-Detect Collision Bones"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}
        found = find_default_collision_bones(armature_obj)
        if found:
            props.collision_bones        = ", ".join(found)
            props.collision_sphere_factor = 1.0
            self.report({'INFO'}, f"Found {len(found)} bones: {', '.join(found)}")
        else:
            self.report({'WARNING'}, "No common arm/clavicle bones found — enter names manually.")
        return {'FINISHED'}


class BOOBS_OT_PreviewPhysics(Operator):
    """Apply jiggle physics to the current animation for preview"""
    bl_idname    = "boobs_physics.preview"
    bl_label     = "Preview Jiggle"
    bl_description = "Apply jiggle physics to the current animation and bake it for preview"
    bl_options   = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)

        if not armature_obj:
            self.report({'ERROR'}, "No armature found in scene"); return {'CANCELLED'}

        bone_names = [b for b in [props.breast_bone_L, props.breast_bone_R] if b]
        if not bone_names:
            self.report({'ERROR'}, "No breast bones selected. Use Auto-Detect or pick them manually.")
            return {'CANCELLED'}

        if not armature_obj.animation_data or not armature_obj.animation_data.action:
            self.report({'ERROR'}, "No animation loaded. Load an animation first.")
            return {'CANCELLED'}

        current_action = armature_obj.animation_data.action

        # Snapshot current action so Undo Preview can restore it exactly.
        if props.backup_action_name:
            old = bpy.data.actions.get(props.backup_action_name)
            if old:
                bpy.data.actions.remove(old)
            props.backup_action_name = ""

        backup = current_action.copy()
        backup.name          = f"__boobs_bkp_{current_action.name}"
        backup.use_fake_user = True
        props.backup_action_name = backup.name

        # Strip existing physics keyframes so re-baking at a new intensity
        # starts clean rather than stacking on the old bake.
        strip_physics_keyframes(current_action, bone_names)

        armature_obj.wiggle_freeze = False
        configured = apply_wiggle_to_bones(context, armature_obj, bone_names, props.jiggle_intensity)
        if not configured:
            self.report({'ERROR'}, "Could not find the specified bones in the armature")
            return {'CANCELLED'}

        props.status_text = "Baking physics preview..."
        ok, message = bake_wiggle_for_current_action(
            context, armature_obj, bone_names, props.jiggle_intensity
        )

        if not ok:
            props.status_text = f"Preview failed: {message}"
            self.report({'ERROR'}, message); return {'CANCELLED'}

        if props.collision_enabled or props.boob_self_collision:
            coll_bones = []
            if props.collision_enabled:
                coll_bones = [n.strip() for n in props.collision_bones.split(',') if n.strip()]
            props.status_text = "Applying collision correction..."
            n_fixed = post_bake_collision_correct(
                context, armature_obj, bone_names, coll_bones,
                sphere_factor=props.collision_sphere_factor,
                self_coll_enabled=props.boob_self_collision,
                self_coll_scale=props.boob_self_collision_scale,
            )
            if n_fixed:
                self.report({'INFO'}, f"Collision: corrected {n_fixed} frames")

        # Fix quaternion signs after collision correction
        act = armature_obj.animation_data and armature_obj.animation_data.action
        if act:
            _fix_quaternion_continuity(act, bone_names)

        clear_wiggle_from_bones(context, armature_obj, bone_names)
        props.status_text = "Preview ready — play the animation!"
        self.report({'INFO'}, f"Jiggle physics baked on: {', '.join(configured)}")
        ensure_object_mode(context)
        return {'FINISHED'}


class BOOBS_OT_UndoPreview(Operator):
    """Restore the animation to the state it was in before Preview Jiggle"""
    bl_idname    = "boobs_physics.undo_preview"
    bl_label     = "Undo Preview"
    bl_description = "Restore the animation to exactly how it was before Preview Jiggle was clicked"
    bl_options   = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        armature_obj.wiggle_freeze = False
        clear_wiggle_from_bones(context, armature_obj,
                                [props.breast_bone_L, props.breast_bone_R])

        if props.backup_action_name:
            backup = bpy.data.actions.get(props.backup_action_name)
            if backup:
                baked = armature_obj.animation_data.action if armature_obj.animation_data else None
                armature_obj.animation_data.action = backup
                backup.name          = backup.name.replace("__boobs_bkp_", "", 1)
                backup.use_fake_user = False
                if baked and baked != backup:
                    bpy.data.actions.remove(baked)
                props.backup_action_name = ""

                context.scene.frame_start = 1
                context.scene.frame_end   = int(backup.frame_range[1])
                ensure_object_mode(context)
                context.scene.frame_set(1)
                props.status_text = "Ready"
                self.report({'INFO'}, "Restored to pre-preview state.")
                return {'FINISHED'}

        props.status_text = "Ready"
        self.report({'WARNING'}, "No preview backup found. Nothing to restore.")
        return {'FINISHED'}


class BOOBS_OT_BrowseFolder(Operator):
    """Browse for animations folder"""
    bl_idname    = "boobs_physics.browse_folder"
    bl_label     = "Browse Animations Folder"
    bl_description = "Choose a folder containing .anm animation files"
    bl_options   = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        if self.directory:
            context.scene.boobs_physics.custom_folder = self.directory.rstrip('/\\')
            bpy.ops.boobs_physics.refresh_anims()
        return {'FINISHED'}

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class BOOBS_OT_BrowseExportFolder(Operator):
    """Browse for export folder"""
    bl_idname    = "boobs_physics.browse_export_folder"
    bl_label     = "Browse Export Folder"
    bl_description = "Choose a folder to export baked animations"
    bl_options   = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        if self.directory:
            context.scene.boobs_physics.export_folder = self.directory.rstrip('/\\')
        return {'FINISHED'}

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class BOOBS_OT_ClearFolder(Operator):
    """Clear custom folder"""
    bl_idname  = "boobs_physics.clear_folder"
    bl_label   = "Clear Custom Folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        context.scene.boobs_physics.custom_folder = ""
        bpy.ops.boobs_physics.refresh_anims()
        return {'FINISHED'}


class BOOBS_OT_RefreshAnims(Operator):
    """Scan the animations folder and refresh the list"""
    bl_idname  = "boobs_physics.refresh_anims"
    bl_label   = "Refresh Animations"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.boobs_physics
        props.animations.clear()
        props.animations_folder = ""
        props.search_filter     = ""

        if props.custom_folder and os.path.isdir(props.custom_folder):
            anim_folder = props.custom_folder
        else:
            armature_obj = find_armature(context)
            if not armature_obj:
                self.report({'WARNING'}, "No armature found. Use folder button to select manually.")
                return {'CANCELLED'}
            anim_folder = get_animations_folder(armature_obj)
            if not anim_folder:
                self.report({'WARNING'}, "No 'animations' folder found. Use folder button.")
                return {'CANCELLED'}

        props.animations_folder = anim_folder
        anm_files = sorted(f for f in os.listdir(anim_folder) if f.lower().endswith('.anm'))

        for filename in anm_files:
            item          = props.animations.add()
            item.name     = os.path.splitext(filename)[0]
            item.filepath = os.path.join(anim_folder, filename)

        if anm_files:
            self.report({'INFO'}, f"Found {len(anm_files)} animations")
        else:
            self.report({'WARNING'}, f"No .anm files in: {anim_folder}")
        return {'FINISHED'}


class BOOBS_OT_LoadAnimation(Operator):
    """Load a single animation for preview"""
    bl_idname  = "boobs_physics.load_anim"
    bl_label   = "Load Animation"
    bl_options = {'REGISTER', 'UNDO'}

    filepath:   StringProperty()
    anim_name:  StringProperty()
    index:      IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.boobs_physics

        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, "Animation file not found"); return {'CANCELLED'}

        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        select_armature(context, armature_obj)
        armature_obj.wiggle_freeze = False

        # Reset breast bones to identity BEFORE loading so a mid-playback pose
        # doesn't contaminate the new action's T-pose frame.
        bpy.ops.object.mode_set(mode='POSE')
        for bname in [props.breast_bone_L, props.breast_bone_R]:
            if not bname:
                continue
            pb = armature_obj.pose.bones.get(bname)
            if pb:
                pb.location            = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler      = (0.0, 0.0, 0.0)
                pb.scale               = (1.0, 1.0, 1.0)
        bpy.ops.object.mode_set(mode='OBJECT')

        try:
            action_name = self.anim_name or os.path.splitext(os.path.basename(self.filepath))[0]
            anm         = import_anm.read_anm(self.filepath)

            if not armature_obj.animation_data:
                armature_obj.animation_data_create()

            new_action = bpy.data.actions.new(name=action_name)
            armature_obj.animation_data.action = new_action
            import_anm.apply_anm(anm, armature_obj, frame_offset=0)
            new_action["lol_anm_filepath"] = self.filepath
            new_action["lol_anm_filename"] = os.path.basename(self.filepath)

            frame_end = int(new_action.frame_range[1])
            context.scene.frame_start   = 1
            context.scene.frame_end     = frame_end
            context.scene.frame_current = 1

            props.current_loaded = action_name
            if self.index >= 0:
                props.active_index = self.index

            props.status_text = "Ready"
            self.report({'INFO'}, f"Loaded: {action_name}")
            return {'FINISHED'}

        except Exception as e:
            props.status_text = "Import failed"
            self.report({'ERROR'}, f"Failed: {e}")
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


class BOOBS_OT_ApplyToAll(Operator):
    """Apply jiggle physics to all animations in the folder, bake, and export"""
    bl_idname    = "boobs_physics.apply_all"
    bl_label     = "Apply to All Animations"
    bl_description = "Import each animation, apply jiggle, bake, and export to the output folder"
    bl_options   = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)

        if not armature_obj:
            self.report({'ERROR'}, "No armature found in scene"); return {'CANCELLED'}

        bone_names = [b for b in [props.breast_bone_L, props.breast_bone_R] if b]
        if not bone_names:
            self.report({'ERROR'}, "No breast bones selected"); return {'CANCELLED'}

        if len(props.animations) == 0:
            self.report({'ERROR'}, "No animations loaded. Click Refresh first.")
            return {'CANCELLED'}

        export_dir = props.export_folder or props.animations_folder
        if not export_dir or not os.path.isdir(export_dir):
            self.report({'ERROR'}, "No valid export folder. Set one or ensure animations folder exists.")
            return {'CANCELLED'}

        select_armature(context, armature_obj)
        if not armature_obj.animation_data:
            armature_obj.animation_data_create()

        # Disable global undo to prevent RAM throttling during batch processing.
        user_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False

        original_action = armature_obj.animation_data.action
        total   = len(props.animations)
        fps     = context.scene.render.fps
        success_count = 0
        fail_count    = 0

        # Pre-compute collision radii once — they're static (bone_len × factor).
        coll_bones_for_batch   = []
        precomputed_coll_radii = None
        if props.collision_enabled:
            coll_bones_for_batch = [n.strip() for n in props.collision_bones.split(',') if n.strip()]
            if coll_bones_for_batch:
                precomputed_coll_radii = precompute_collision_radii(armature_obj, coll_bones_for_batch)

        # Hide all meshes to stop Blender recalculating vertex deformations per frame.
        disabled_mods, hidden_objs = hide_meshes_for_batch(context)

        # Configure wiggle once after mesh hiding (mode switch is now safe).
        apply_wiggle_to_bones(context, armature_obj, bone_names, props.jiggle_intensity)

        for idx, anim_item in enumerate(props.animations):
            props.status_text = f"Processing {idx + 1}/{total}: {anim_item.name}..."
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

            try:
                # Hard-reset physics between animations so velocity/position
                # from the previous clip doesn't bleed into the next preroll.
                armature_obj.wiggle_freeze = False
                try:
                    bpy.ops.wiggle.reset()
                except Exception:
                    pass

                ensure_object_mode(context)
                select_armature(context, armature_obj)

                anm        = import_anm.read_anm(anim_item.filepath)
                new_action = bpy.data.actions.new(name=anim_item.name)
                armature_obj.animation_data.action = new_action
                import_anm.apply_anm(anm, armature_obj, frame_offset=0)
                new_action["lol_anm_filepath"] = anim_item.filepath

                # Re-apply wiggle each iteration: bake_wiggle_for_current_action may
                # change effective_intensity for non-loop clips, so the next iteration
                # (which could be a loop) needs the full intensity restored.
                apply_wiggle_to_bones(context, armature_obj, bone_names, props.jiggle_intensity)

                ok, msg = bake_wiggle_for_current_action(
                    context, armature_obj, bone_names, props.jiggle_intensity
                )
                if not ok:
                    print(f"Bake failed for {anim_item.name}: {msg}")
                    fail_count += 1
                    continue

                if coll_bones_for_batch or props.boob_self_collision:
                    post_bake_collision_correct(
                        context, armature_obj, bone_names, coll_bones_for_batch,
                        sphere_factor=props.collision_sphere_factor,
                        precomputed_radii=precomputed_coll_radii,
                        self_coll_enabled=props.boob_self_collision,
                        self_coll_scale=props.boob_self_collision_scale,
                    )
                # Fix quaternion signs after collision correction
                act = armature_obj.animation_data.action
                if act:
                    _fix_quaternion_continuity(act, bone_names)

                out_path = os.path.join(export_dir, f"{anim_item.name}.anm")
                export_anm.write_anm(out_path, armature_obj, fps)
                success_count += 1

            except Exception as e:
                print(f"Failed on {anim_item.name}: {e}")
                import traceback
                traceback.print_exc()
                fail_count += 1

        clear_wiggle_from_bones(context, armature_obj, bone_names)

        try:
            armature_obj.animation_data.action = original_action
        except Exception:
            pass

        armature_obj.wiggle_freeze = False
        ensure_object_mode(context)
        restore_meshes_after_batch(disabled_mods, hidden_objs)


        props.status_text = "Ready"
        context.preferences.edit.use_global_undo = user_undo

        if fail_count > 0:
            self.report({'WARNING'}, f"Done: {success_count}/{total} exported ({fail_count} failed) to {export_dir}")
        else:
            self.report({'INFO'}, f"Done: {success_count} animations exported to {export_dir}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
#  UI List
# ---------------------------------------------------------------------------

class BOOBS_UL_AnimList(UIList):
    """Scrollable animation list with search filtering"""

    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, index):
        props = context.scene.boobs_physics
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text="", icon='PLAY' if item.name == props.current_loaded else 'ACTION')
            op = row.operator("boobs_physics.load_anim", text=item.name, emboss=False)
            op.filepath  = item.filepath
            op.anim_name = item.name
            op.index     = index
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon='ACTION')

    def filter_items(self, context, data, propname):
        props       = context.scene.boobs_physics
        items       = getattr(data, propname)
        filter_name = props.search_filter.lower()
        flt_flags   = [self.bitflag_filter_item] * len(items)
        if filter_name:
            for i, item in enumerate(items):
                if filter_name not in item.name.lower():
                    flt_flags[i] = 0
        return flt_flags, []


# ---------------------------------------------------------------------------
#  Panel
# ---------------------------------------------------------------------------

class BOOBS_PT_BoobsPhysics(Panel):
    bl_label       = "Boobs Physics"
    bl_idname      = "VIEW3D_PT_boobs_physics"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'Misc LoL Tools'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(text="", icon_value=icons.get_icon("icon_52"))

    def draw(self, context):
        layout       = self.layout
        props        = context.scene.boobs_physics
        armature_obj = find_armature(context)

        # Status
        layout.box().label(text=props.status_text, icon='INFO')

        # --- Bone selection ---
        box = layout.box()
        box.label(text="Breast Bones", icon='BONE_DATA')
        if armature_obj:
            box.row(align=True).prop_search(props, "breast_bone_L", armature_obj.pose, "bones", text="Left")
            box.row(align=True).prop_search(props, "breast_bone_R", armature_obj.pose, "bones", text="Right")
            box.operator("boobs_physics.auto_detect", text="Auto-Detect", icon='VIEWZOOM')
        else:
            box.label(text="No armature in scene", icon='ERROR')

        # --- Self-collision ---
        box = layout.box()
        box.row().prop(props, "boob_self_collision", text="Boobs Collision", icon='MESH_UVSPHERE')
        if props.boob_self_collision:
            box.column().prop(props, "boob_self_collision_scale", slider=True,
                              text="Radius Scale (1.0 = auto)")

        # --- Intensity slider ---
        box = layout.box()
        box.label(text="Jiggle Settings", icon='MOD_WAVE')
        col = box.column(align=True)
        col.prop(props, "jiggle_intensity", slider=True)
        row = col.row(align=True)
        row.alignment = 'CENTER'
        i = props.jiggle_intensity
        label = "▸ Subtle" if i <= 3 else "▸ Mild" if i <= 7 else "▸ Natural" if i <= 13 \
            else "▸ Bouncy" if i <= 17 else "▸ Extreme!"
        row.label(text=label)

        # --- Body collision ---
        box = layout.box()
        box.row().prop(props, "collision_enabled", text="Body Collision", icon='MESH_UVSPHERE')
        if props.collision_enabled:
            col = box.column(align=True)
            col.label(text="Collision Bones (comma-separated):", icon='BONE_DATA')
            col.prop(props, "collision_bones", text="")
            col.operator("boobs_physics.auto_detect_collision",
                         text="Auto-Detect Arm Bones", icon='VIEWZOOM')
            col.separator()
            col.prop(props, "collision_sphere_factor", slider=True, text="Radius Scale  (1.0 = auto)")

        # --- Preview ---
        has_anim  = bool(armature_obj and armature_obj.animation_data and armature_obj.animation_data.action)
        has_bones = bool(props.breast_bone_L or props.breast_bone_R)

        box = layout.box()
        box.label(text="Preview", icon='PLAY')
        col = box.column(align=True)
        col.scale_y = 1.3
        row = col.row(align=True)
        row.enabled = has_anim and has_bones
        row.operator("boobs_physics.preview", text="Preview Jiggle", icon='MOD_WAVE')
        row = col.row(align=True)
        row.enabled = has_anim
        row.operator("boobs_physics.undo_preview", text="Undo Preview", icon='LOOP_BACK')
        if props.current_loaded:
            box.label(text=f"Animation: {props.current_loaded}", icon='ANIM')

        # --- Batch processing ---
        box = layout.box()
        box.label(text="Batch Processing", icon='FILE_FOLDER')

        row = box.row(align=True)
        row.operator("boobs_physics.refresh_anims", text="Refresh", icon='FILE_REFRESH')
        row.operator("boobs_physics.browse_folder", text="", icon='FILEBROWSER')

        row = box.row(align=True)
        row.scale_y = 0.7
        if props.animations_folder:
            folder_name = os.path.basename(props.animations_folder)
            parent_name = os.path.basename(os.path.dirname(props.animations_folder))
            row.label(text=f".../{parent_name}/{folder_name}", icon='FILE_FOLDER')
            if props.custom_folder:
                row.operator("boobs_physics.clear_folder", text="", icon='X')
        else:
            row.label(text="No folder selected", icon='FILE_FOLDER')

        if len(props.animations) > 0:
            filter_text = props.search_filter.lower()
            if filter_text:
                visible    = sum(1 for it in props.animations if filter_text in it.name.lower())
                label_text = f"Animations ({visible}/{len(props.animations)})"
            else:
                label_text = f"Animations ({len(props.animations)})"
            box.row().label(text=label_text, icon='ANIM')
            box.row().template_list("BOOBS_UL_AnimList", "",
                                    props, "animations", props, "active_index", rows=8)
            box.row(align=True).prop(props, "search_filter", text="", icon='VIEWZOOM')

        # --- Export ---
        layout.separator()
        box = layout.box()
        box.label(text="Export", icon='EXPORT')
        row = box.row(align=True)
        row.label(text="Output Folder:", icon='FILE_FOLDER')
        row.operator("boobs_physics.browse_export_folder", text="", icon='FILEBROWSER')
        row = box.row(align=True)
        row.scale_y = 0.7
        if props.export_folder:
            folder_name = os.path.basename(props.export_folder)
            parent_name = os.path.basename(os.path.dirname(props.export_folder))
            row.label(text=f".../{parent_name}/{folder_name}")
        else:
            row.label(text="(defaults to source folder)")

        layout.separator()
        col = layout.column(align=True)
        col.scale_y = 1.5
        col.enabled = has_bones and len(props.animations) > 0
        col.operator("boobs_physics.apply_all", text="⚡ Apply to All Animations", icon='MOD_WAVE')


# ---------------------------------------------------------------------------
#  Registration
# ---------------------------------------------------------------------------

classes = [
    BoobsAnimListItem,
    BoobsPhysicsProperties,
    BOOBS_OT_AutoDetectBones,
    BOOBS_OT_AutoDetectCollisionBones,
    BOOBS_OT_PreviewPhysics,
    BOOBS_OT_UndoPreview,
    BOOBS_OT_BrowseFolder,
    BOOBS_OT_BrowseExportFolder,
    BOOBS_OT_ClearFolder,
    BOOBS_OT_RefreshAnims,
    BOOBS_OT_LoadAnimation,
    BOOBS_OT_ApplyToAll,
    BOOBS_UL_AnimList,
    BOOBS_PT_BoobsPhysics,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.boobs_physics = PointerProperty(type=BoobsPhysicsProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'boobs_physics'):
        del bpy.types.Scene.boobs_physics
