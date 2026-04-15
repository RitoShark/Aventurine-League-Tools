"""
Hair Physics - Automated hair jiggle physics for LoL custom skins.
Same Wiggle 2 engine as boobs_physics but tuned for hair chains:
higher gravity, lighter mass, stronger chain coupling.

Real-time collision: creates temporary icosphere meshes bone-parented to
body bones and wires them into Wiggle 2's built-in collision system so the
simulation itself prevents hair from clipping through the body.
"""

import bpy
import os
import math
import bmesh
from bpy.types import Panel, Operator, PropertyGroup, UIList
from bpy.props import (
    StringProperty, CollectionProperty, IntProperty,
    PointerProperty, FloatProperty, BoolProperty, EnumProperty,
)
from ..ui import icons
from ..io import import_anm
from ..io import export_anm
from .physics_common import (
    find_armature, get_animations_folder,
    ensure_object_mode, select_armature,
    configure_wiggle_bones, clear_wiggle_from_bones, strip_physics_keyframes,
    find_default_hair_collision_bones, post_bake_collision_correct,
    precompute_collision_radii, hide_meshes_for_batch, restore_meshes_after_batch,
    smooth_physics_spikes,
    lerp, lerp_exp,
    _bone_capsule_radius,
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
#  Hair physics parameter mapping (intensity 1-20)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
#  Hair type presets
#
#  A single parameter curve doesn't fit every hair shape. A short ponytail
#  has 2-4 bones that mostly need to swing; long loose hair has 6-10 bones
#  that need to flow and hang under gravity; twin-tails sit on the sides
#  of the head and need gentle gravity plus tighter chain coupling so they
#  don't cross each other. Each preset tweaks stiffness, gravity, mass,
#  and damping-floor independently.
#
#  ponytail    — short/medium single chain off the back of the head
#  long_loose  — long free-flowing hair (6+ bones, or multiple side/back
#                chains) that needs to hang more and move more softly
#  short_bob   — short hair with barely perceptible motion
#  twin_tails  — side tails / pigtails
#  custom      — no preset, pure intensity-based defaults (legacy behavior)
# ---------------------------------------------------------------------------

_HAIR_TYPE_PRESETS = {
    'ponytail': {
        'stiff_hi':   400.0,   # slider=1
        'stiff_lo':    40.0,   # slider=20
        'damp_hi':      8.0,
        'damp_lo':      0.35,
        'grav_lo':      0.05,
        'grav_hi':      0.35,
        'mass_hi':      0.15,
        'mass_lo':      1.2,
        'stretch_hi':   0.0,
        'stretch_lo':   0.04,
    },
    'long_loose': {
        # Long hair: much softer stiffness, more gravity so it hangs,
        # slightly heavier mass so bones at the tip don't whip.
        'stiff_hi':   260.0,
        'stiff_lo':    18.0,
        'damp_hi':      6.0,
        'damp_lo':      0.30,
        'grav_lo':      0.15,
        'grav_hi':      0.65,
        'mass_hi':      0.25,
        'mass_lo':      1.8,
        'stretch_hi':   0.0,
        'stretch_lo':   0.06,
    },
    'short_bob': {
        # Very subtle: stiff, light, barely any gravity.
        'stiff_hi':   600.0,
        'stiff_lo':    90.0,
        'damp_hi':     10.0,
        'damp_lo':      0.60,
        'grav_lo':      0.02,
        'grav_hi':      0.15,
        'mass_hi':      0.10,
        'mass_lo':      0.80,
        'stretch_hi':   0.0,
        'stretch_lo':   0.02,
    },
    'twin_tails': {
        # Side tails: slightly stiffer than ponytail so they don't cross,
        # lower gravity so they stay to the sides, firmer damping floor.
        'stiff_hi':   480.0,
        'stiff_lo':    55.0,
        'damp_hi':      8.0,
        'damp_lo':      0.45,
        'grav_lo':      0.03,
        'grav_hi':      0.25,
        'mass_hi':      0.15,
        'mass_lo':      1.0,
        'stretch_hi':   0.0,
        'stretch_lo':   0.03,
    },
}


def get_hair_params(intensity, hair_type='ponytail'):
    """Map (1-20 intensity, hair_type) to Wiggle 2 params tuned for hair bones.

    Hair differences vs breast bones:
      - gravity is noticeable (hair hangs and swings visibly)
      - stiffness is lower overall (hair is more flexible)
      - chain coupling is critical (bones in a chain pull each other)
      - mass is lighter (hair strands aren't heavy)
      - damping floor is slightly higher to prevent wild fly-away
    """
    t = max(0.0, min(1.0, (intensity - 1) / 19.0))
    t = t * t  # quadratic bias: natural feel in the lower half

    p = _HAIR_TYPE_PRESETS.get(hair_type, _HAIR_TYPE_PRESETS['ponytail'])

    return {
        'stiff':   lerp_exp(p['stiff_hi'], p['stiff_lo'], t),
        'damp':    lerp_exp(p['damp_hi'],  p['damp_lo'],  t),
        'gravity': lerp(p['grav_lo'], p['grav_hi'], t),
        'mass':    lerp_exp(p['mass_hi'],  p['mass_lo'],  t),
        'stretch': lerp(p['stretch_hi'], p['stretch_lo'], t),
        'chain':   True,
    }


def _apply_wiggle(context, arm, bone_names, intensity, hair_type='ponytail'):
    """Configure Wiggle 2 on hair bones with intensity-mapped parameters."""
    return configure_wiggle_bones(
        context, arm, bone_names, get_hair_params(intensity, hair_type)
    )


# ---------------------------------------------------------------------------
#  Real-time collision helpers
# ---------------------------------------------------------------------------

_TEMP_COLL_NAME = "__hair_coll_temp"


def _classify_bone(bone_name):
    """Classify a bone by name for collision shape selection."""
    bl = bone_name.lower()
    if 'head' in bl and 'upper' not in bl:
        return 'head'
    if 'neck' in bl:
        return 'neck'
    return 'default'


# Hair-specific collision radius overrides for bones whose shape differs
# greatly from a thin capsule.
#   Head: roughly spherical, much wider than its bone length.
#         Generic factor 0.55 gives ~5.5cm for a 10cm bone — too small,
#         hair wraps around the whole skull which is ~9-10cm radius.
#   Neck: wider than generic 0.30 factor suggests — hair drapes over it
#         and needs a fatter collision cylinder to prevent clipping.
_HAIR_COLL_RADIUS_OVERRIDES = {
    'head': 0.90,   # nearly as wide as the bone is long
    'neck': 0.50,   # significantly fatter than generic
}


def _create_collision_meshes(context, armature_obj, coll_bone_names, sphere_factor=1.0):
    """Create temporary icosphere colliders that follow body bones.

    For each collision bone, creates icospheres along the bone's length to
    approximate a capsule. Head and neck bones get special treatment: larger
    radii and more coverage since hair wraps around them fully.

    CRITICAL — uses Copy Location + Copy Rotation constraints instead of
    bone-parenting. Bone-parenting inherits the bone's scale, and many
    imported .anm files have bones with scale=0 on some axis for squash
    poses. A sphere that inherits scale=0 has a SINGULAR world matrix,
    and Wiggle 2's collide() calls cmw.inverted() on it — which raises
    ValueError: matrix does not have an inverse. The frame_change_post
    handler aborts, all subsequent frames re-raise, hair physics freezes
    from that frame to the end. This was the "frame 157 → T-pose" bug.

    Copy Location + Copy Rotation copy only the bone's position and
    orientation, NOT its scale. The sphere keeps its own clean (r,r,r)
    scale so its world matrix is always invertible regardless of what
    the bone does.

    Returns (collection, [objects]) for later cleanup via _cleanup_collision_meshes().
    Returns (None, []) if no valid collision bones exist.
    """
    # Clean up leftover temp collection from a previous crashed run.
    old_coll = bpy.data.collections.get(_TEMP_COLL_NAME)
    if old_coll:
        for obj in list(old_coll.objects):
            mesh_data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh_data and mesh_data.users == 0:
                bpy.data.meshes.remove(mesh_data)
        bpy.data.collections.remove(old_coll)
    # Also clean up orphaned template mesh from a previous crash.
    old_template = bpy.data.meshes.get("__hc_unit_sphere")
    if old_template and old_template.users == 0:
        bpy.data.meshes.remove(old_template)

    arm_mw = armature_obj.matrix_world
    created = []

    collection = bpy.data.collections.new(_TEMP_COLL_NAME)
    context.scene.collection.children.link(collection)

    # Single unit icosphere shared by all collision objects.
    # Each object uses obj.scale to set its effective radius.
    # closest_point_on_mesh works in local space via matrix_world,
    # so uniform scale is handled correctly by the physics engine.
    template_mesh = bpy.data.meshes.new("__hc_unit_sphere")
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
    bm.to_mesh(template_mesh)
    bm.free()

    for bone_name in coll_bone_names:
        pb = armature_obj.pose.bones.get(bone_name)
        if not pb:
            continue
        bone_len = (arm_mw @ pb.tail - arm_mw @ pb.head).length
        if bone_len < 1e-6:
            continue

        bone_type = _classify_bone(bone_name)
        override  = _HAIR_COLL_RADIUS_OVERRIDES.get(bone_type)

        if override:
            radius = max(0.01, bone_len * override * sphere_factor)
        else:
            radius = _bone_capsule_radius(bone_name, bone_len, sphere_factor)

        if bone_type == 'head':
            # Head is roughly a sphere, not a capsule. Place one large sphere
            # at the midpoint plus smaller ones at the ends for full coverage.
            # Also add one above the head tip to catch hair draped over the top.
            placements = [
                (0.5,  radius),              # center: full-size sphere
                (0.0,  radius * 0.75),       # base of head (near neck junction)
                (1.0,  radius * 0.75),       # top of head bone
                (1.3,  radius * 0.55),       # above head: catches hair draped over the crown
            ]
        elif bone_type == 'neck':
            # Neck: 3 spheres along its length, fatter than default.
            placements = [
                (0.0,  radius),
                (0.5,  radius),
                (1.0,  radius),
            ]
        else:
            # Generic capsule: evenly-spaced spheres along the bone.
            num = max(2, min(4, int(bone_len / max(radius, 0.01)) + 1))
            placements = [
                (i / max(1, num - 1), radius) for i in range(num)
            ]

        for i, (t, r) in enumerate(placements):
            obj_name = f"__hc_{bone_name}_{i}"
            obj = bpy.data.objects.new(obj_name, template_mesh)
            obj.location = (0.0, 0.0, 0.0)
            obj.scale = (r, r, r)
            obj.hide_viewport = True
            obj.hide_render = True
            collection.objects.link(obj)

            # Follow the bone via constraints, NOT bone-parenting. Copy
            # Location + Copy Rotation never propagate the bone's scale,
            # so the sphere's world matrix stays invertible even when the
            # animated bone has a zero-scale axis.
            cl = obj.constraints.new(type='COPY_LOCATION')
            cl.target      = armature_obj
            cl.subtarget   = bone_name
            # head_tail: 0 = bone head, 1 = bone tail. For t > 1 (extra
            # sphere above the head), we approximate with use_bbone_shape
            # not applicable here — clamp to 1.0 and rely on the sphere
            # radius to still cover hair draped over the crown.
            cl.head_tail   = min(1.0, max(0.0, t))
            cl.use_offset  = False

            cr = obj.constraints.new(type='COPY_ROTATION')
            cr.target      = armature_obj
            cr.subtarget   = bone_name
            cr.use_offset  = False

            created.append(obj)

    if not created:
        bpy.data.collections.remove(collection)
        if template_mesh.users == 0:
            bpy.data.meshes.remove(template_mesh)
        return None, []

    return collection, created


def _setup_hair_collision_props(armature_obj, bone_names, coll_collection):
    """Wire Wiggle 2's collision system on each hair bone.

    After this, every call to Wiggle 2's move() → collide() will check the
    hair bone's tail position against the icosphere meshes in coll_collection
    and push it out if it penetrates. This is real-time collision during the
    simulation — not a post-bake approximation.
    """
    # Deselect all bones so the update_prop() callback doesn't try to
    # copy PointerProperty values to other selected bones (the copy
    # silently fails for PointerProperty, leaving the selected bone with
    # collider_type='Collection' but collider_collection=None).
    for bone in armature_obj.data.bones:
        bone.select = False

    arm_mw = armature_obj.matrix_world
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        bone_len = (arm_mw @ pb.tail - arm_mw @ pb.head).length
        pb.wiggle_collider_type       = 'Collection'
        pb.wiggle_collider_collection = coll_collection
        # Hair strand collision radius: keeps the bone this far from surfaces.
        # Too small → hair visually clips; too large → hair floats above body.
        pb.wiggle_radius   = max(0.008, bone_len * 0.12)
        # Friction must stay LOW. In physics.collide() the friction value
        # drives a per-frame lerp of the hair position toward the cached
        # contact point. On long clips this compounds: after ~100 frames of
        # on-and-off contact with head/shoulders the velocity is damped to
        # zero and the bone is effectively welded to the surface — the
        # "hair stops moving mid-animation" bug.
        pb.wiggle_friction = 0.05
        pb.wiggle_bounce   = 0.1
        # Sticky margin MUST be 0. Any positive value keeps `collision_ob`
        # truthy across frames, which keeps the friction-drag active even
        # after the bone has moved past the surface → progressive stiffening.
        pb.wiggle_sticky   = 0.0


def _clear_hair_collision_props(armature_obj, bone_names):
    """Reset Wiggle 2 collision properties on hair bones to defaults."""
    # Deselect all bones so the update callback in update_prop() doesn't
    # try to copy None PointerProperty values to other selected bones.
    for bone in armature_obj.data.bones:
        bone.select = False
    for bname in bone_names:
        if not bname:
            continue
        pb = armature_obj.pose.bones.get(bname)
        if not pb:
            continue
        pb.wiggle_collider_type       = 'Object'
        pb.wiggle_collider_collection = None
        pb.wiggle_collider            = None
        pb.wiggle_radius              = 0
        pb.wiggle_friction            = 0.5
        pb.wiggle_bounce              = 0.5
        pb.wiggle_sticky              = 0


def _cleanup_collision_meshes(collection, objects):
    """Remove temporary collision icospheres and their collection."""
    for obj in objects:
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh_data and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)
    if collection:
        bpy.data.collections.remove(collection)


# ---------------------------------------------------------------------------
#  Properties
# ---------------------------------------------------------------------------

class HairBoneItem(PropertyGroup):
    bone_name: StringProperty(name="Bone", default="")
    # Which branch this bone belongs to. The physics doesn't care — Wiggle 2
    # chains bones via their parent relationship in the armature — but
    # grouping bones by branch in the UI makes it much easier to handle
    # multi-chain hair (back / left / right tails, twin tails, etc).
    branch: IntProperty(name="Branch", default=0, min=0, max=7)


class HairAnimListItem(PropertyGroup):
    name:     StringProperty()
    filepath: StringProperty()


def _update_hair_search_filter(self, context):
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


class HairPhysicsProperties(PropertyGroup):
    bones:             CollectionProperty(type=HairBoneItem)
    active_bone_index: IntProperty(default=0)

    # How many hair branches the character has. 1 = single chain (ponytail),
    # 2 = left+right (twin tails / split back), 3 = center+left+right, etc.
    # Bones are grouped by branch in the UI. Physics doesn't change.
    num_branches: IntProperty(
        name="Branches",
        description="How many separate hair chains this character has. "
                    "Use 1 for a ponytail, 2 for twin tails, 3+ for back + "
                    "side tails or multi-branch long hair",
        default=1, min=1, max=6,
    )

    jiggle_intensity: IntProperty(
        name="Hair Intensity",
        description="1 = barely moves, 10 = natural sway, 20 = exaggerated",
        default=8, min=1, max=20
    )

    # Preset tuning curve. Different hair shapes need different stiffness,
    # gravity, and mass floors; one curve can't cover ponytail AND long loose
    # hair AND short bob. See _HAIR_TYPE_PRESETS for the actual values.
    hair_type: EnumProperty(
        name="Hair Type",
        description="Pick the preset closest to this character's hairstyle. "
                    "Each preset tunes stiffness, gravity, and mass floors "
                    "differently — one curve doesn't fit every hair shape",
        items=[
            ('ponytail',   "Ponytail",    "Short/medium single chain — default"),
            ('long_loose', "Long / Loose","Long free-flowing hair (6+ bones or multi-branch). Softer, more gravity"),
            ('short_bob',  "Short / Bob", "Very subtle motion — stiff, low gravity"),
            ('twin_tails', "Twin Tails",  "Side tails / pigtails. Firmer, low gravity, stay on the sides"),
        ],
        default='ponytail',
    )
    status_text:        StringProperty(default="Ready")
    backup_action_name: StringProperty(default="")

    # Animation folder browser
    animations:        CollectionProperty(type=HairAnimListItem)
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
        update=_update_hair_search_filter,
        options={'TEXTEDIT_UPDATE'}
    )
    current_loaded: StringProperty(name="Currently Loaded", default="")

    # Export
    export_folder: StringProperty(
        name="Export Folder",
        description="Folder to export baked animations",
        default="", subtype='DIR_PATH'
    )

    # Body collision
    collision_enabled: BoolProperty(
        name="Body Collision",
        description="Create invisible proxy spheres on body bones so hair "
                    "bones bounce off them instead of clipping through",
        default=False
    )
    collision_bones: StringProperty(
        name="Collision Bones",
        description="Comma-separated list of body bones to use as colliders",
        default=""
    )
    collision_sphere_factor: FloatProperty(
        name="Radius Scale",
        description="Overall scale for all collision capsule radii. "
                    "1.0 = auto-sized per bone type.",
        default=1.0, min=0.05, max=3.0, step=5, precision=2,
    )


def _get_bone_names(props):
    return [item.bone_name for item in props.bones if item.bone_name.strip()]


# ---------------------------------------------------------------------------
#  Bake pipeline
# ---------------------------------------------------------------------------

def _bake_hair(context, arm, bone_names, intensity, coll_collection=None,
               hair_type='ponytail'):
    """Detect loop, preroll, bake with optional real-time collision.

    Same pipeline as boobs but with real-time collision mesh support
    and higher solver iterations for chain physics.
    """
    scene = context.scene

    if not arm.animation_data or not arm.animation_data.action:
        return False, "No animation loaded"

    action      = arm.animation_data.action
    frame_start = max(1, int(action.frame_range[0]))
    frame_end   = int(action.frame_range[1])

    if frame_end <= frame_start:
        return False, "Animation has no frames"

    scene.frame_start = 1
    scene.frame_end   = frame_end

    is_loop = _detect_animation_loops(action, frame_start, frame_end)

    eff = intensity if is_loop else min(intensity, 13)
    if eff != intensity:
        _apply_wiggle(context, arm, bone_names, eff, hair_type)

    select_armature(context, arm)
    bpy.ops.object.mode_set(mode='POSE')

    if coll_collection:
        _setup_hair_collision_props(arm, bone_names, coll_collection)

    arm.wiggle_freeze           = False
    scene.wiggle.bake_overwrite = True
    scene.wiggle.loop           = True

    # Ensure lastframe starts at 0 so the first bake frame gets a correct
    # frames_elapsed (1 instead of a wild delta from a previous animation).
    scene.frame_set(0)
    bpy.ops.wiggle.reset()

    old_iterations = scene.wiggle.iterations
    scene.wiggle.iterations = max(old_iterations, 10)

    if is_loop:
        # --- ITERATIVE BAKE FOR PERFECT LOOP ---
        # Same approach as boobs: bake from T-pose, let physics state
        # carry over from frame_end to next pass's frame_start.
        # Each pass converges. 5 passes gives tight convergence and
        # eliminates the 1-2 frame settling glitch at the loop seam.
        max_passes = 5
        for pass_num in range(max_passes):
            cur_action = arm.animation_data.action
            if cur_action and pass_num > 0:
                strip_physics_keyframes(cur_action, bone_names)

            if pass_num == 0:
                bpy.ops.wiggle.reset()

            scene.wiggle.is_preroll = True
            for f in range(frame_start, frame_end + 1):
                scene.frame_set(f)
            scene.wiggle.is_preroll = False

        # Final bake
        cur_action = arm.animation_data.action
        if cur_action:
            strip_physics_keyframes(cur_action, bone_names)

        bpy.ops.wiggle.select()
        # Protect converged physics state: nla.bake() may internally visit
        # frame 0, whose handler normally resets all physics.  is_preroll
        # tells the handler to skip the reset so the loop-steady state
        # that took 5 preroll passes to build is preserved.
        # Manual bake for loops (same frame_set path as preroll).
        # Collect values first, insert keyframes AFTER to avoid interfering
        # with the physics during the frame_set loop.
        action = arm.animation_data.action
        if not action:
            scene.wiggle.iterations = old_iterations
            if coll_collection:
                _clear_hair_collision_props(arm, bone_names)
            return False, "No action to bake into"

        physics_pbs = [arm.pose.bones[n] for n in bone_names if n and n in arm.pose.bones]

        # Phase 1: run physics and STORE values
        stored = {}
        scene.wiggle.is_preroll = True

        # Main cycle. Skip NaN frames — one bad frame used to leave every
        # subsequent bone pose at identity (the "frame 157 → T-pose" bug).
        for f in range(frame_start, frame_end + 1):
            scene.frame_set(f)
            for pb in physics_pbs:
                q = tuple(pb.rotation_quaternion)
                loc = tuple(pb.location)
                sc  = tuple(pb.scale)
                if not (all(math.isfinite(v) for v in q) and
                        all(math.isfinite(v) for v in loc) and
                        all(math.isfinite(v) for v in sc)):
                    continue
                stored[(pb.name, f)] = (q, loc, sc)

        # Extra settling pass for first frames
        SETTLE = 8
        settle_end = min(frame_start + SETTLE, frame_end)
        for f in range(frame_start, settle_end + 1):
            scene.frame_set(f)
            for pb in physics_pbs:
                q = tuple(pb.rotation_quaternion)
                loc = tuple(pb.location)
                sc  = tuple(pb.scale)
                if not (all(math.isfinite(v) for v in q) and
                        all(math.isfinite(v) for v in loc) and
                        all(math.isfinite(v) for v in sc)):
                    continue
                stored[(pb.name, f)] = (q, loc, sc)

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
        # --- NON-LOOP: MANUAL FRAME-BY-FRAME BAKE ---
        #
        # Previously this path used bpy.ops.nla.bake, which had two problems:
        #   1) It never stripped existing physics keyframes first, so stale
        #      keys from a previous preview leaked in.
        #   2) If Wiggle 2's NaN-detection (physics.py:266) fired mid-bake on
        #      a specific frame, reset_bone() snapped the bone to identity,
        #      and if whatever produced the NaN persisted (e.g. the rig
        #      enters a pose where a collision mesh engulfs the hair), EVERY
        #      subsequent frame baked an identity quaternion — the bone
        #      would flip to T-pose at some frame N and stay there until
        #      frame_end. This was the "hair stops at frame 157 in a 262-
        #      frame animation" bug.
        #
        # The manual bake gives us per-frame visibility and — combined with
        # the post-bake clamp/spike passes — stops one bad frame from
        # corrupting everything downstream of it.
        cur_action = arm.animation_data.action
        if cur_action:
            strip_physics_keyframes(cur_action, bone_names)
        scene.wiggle.loop = False

        # Reset physics cleanly at frame 0 (bind pose), then step forward.
        scene.frame_set(0)
        bpy.ops.wiggle.reset()
        scene.frame_set(frame_start)
        bpy.ops.wiggle.reset()

        physics_pbs = [arm.pose.bones[n] for n in bone_names
                       if n and n in arm.pose.bones]
        action = arm.animation_data.action
        if not action:
            scene.wiggle.iterations = old_iterations
            if coll_collection:
                _clear_hair_collision_props(arm, bone_names)
            return False, "No action to bake into"

        # Phase 1: run physics frame by frame, store values.
        # is_preroll=True suppresses the "frame<=frame_start → reset" path
        # in wiggle_post (physics.py:514) so the physics state carries
        # from the start frame forward without being re-zeroed.
        stored = {}
        scene.wiggle.is_preroll = True
        try:
            for f in range(frame_start, frame_end + 1):
                scene.frame_set(f)
                for pb in physics_pbs:
                    q = tuple(pb.rotation_quaternion)
                    loc = tuple(pb.location)
                    sc  = tuple(pb.scale)
                    # Guard against NaN from the sim. If any channel is
                    # non-finite, skip this frame entirely — the post-bake
                    # pass will fill it via LINEAR interpolation from the
                    # neighbors. This stops one bad frame from locking the
                    # bone to identity for the rest of the animation.
                    if not (all(math.isfinite(v) for v in q) and
                            all(math.isfinite(v) for v in loc) and
                            all(math.isfinite(v) for v in sc)):
                        continue
                    stored[(pb.name, f)] = (q, loc, sc)
        finally:
            scene.wiggle.is_preroll = False

        # Phase 2: insert keyframes. Missing entries (NaN frames, if any)
        # are simply not inserted, leaving Blender to LINEAR-interpolate
        # across the gap from surrounding valid frames.
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

    scene.wiggle.iterations = old_iterations

    if coll_collection:
        _clear_hair_collision_props(arm, bone_names)

    # --- Post-bake ---
    action = arm.animation_data.action
    if action:
        # Spike smoother catches isolated 1-frame physics pops. Non-cascading
        # so legitimate fast swings are preserved. No rotation clamp is used
        # for hair — long hair legitimately swings 90°+ from rest on dramatic
        # end poses, and the phase-1 NaN guard already handles actual blow-
        # ups without clipping natural motion.
        smooth_physics_spikes(action, bone_names)
        _set_linear_interpolation(action, bone_names)
        _clean_tpose_keyframes(action, bone_names)
        if is_loop:
            _copy_loop_end_to_start(action, frame_start, frame_end, bone_names)
            _fix_quaternion_continuity(action, bone_names)
        else:
            _fix_quaternion_continuity(action, bone_names)
            _restore_nonloop_start_to_tpose(action, frame_start, bone_names)
            _smooth_boundary_frames(action, frame_start, frame_end, bone_names, smooth_ends='start')

    arm.wiggle_freeze = True
    return True, "Baked successfully"


# ---------------------------------------------------------------------------
#  Operators
# ---------------------------------------------------------------------------

class HAIR_OT_AutoDetectCollisionBones(Operator):
    """Scan the armature for common spine/head/arm bones and fill the collision list"""
    bl_idname  = "hair_physics.auto_detect_collision"
    bl_label   = "Auto-Detect Collision Bones"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.hair_physics
        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}
        found = find_default_hair_collision_bones(armature_obj)
        if found:
            props.collision_bones         = ", ".join(found)
            props.collision_sphere_factor = 1.0
            self.report({'INFO'}, f"Found {len(found)} bones: {', '.join(found)}")
        else:
            self.report({'WARNING'}, "No spine/head/shoulder bones found — enter names manually.")
        return {'FINISHED'}


class HAIR_OT_SelectAllCollisionBones(Operator):
    """Add all armature bones as colliders, excluding physics bones and utility bones"""
    bl_idname  = "hair_physics.select_all_collision"
    bl_label   = "Select All Bones"
    bl_options = {'REGISTER'}

    # Bone name substrings to exclude (case-insensitive).
    _EXCLUDE_SUBSTRINGS = ('buffbone', 'snap', 'world', 'root')

    def execute(self, context):
        props        = context.scene.hair_physics
        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        # Bones used for hair physics — these should not collide with themselves.
        physics_bones = {b.lower() for b in _get_bone_names(props) if b}

        coll_bones = []
        for bone in armature_obj.pose.bones:
            bl = bone.name.lower()
            if bl in physics_bones:
                continue
            if any(sub in bl for sub in self._EXCLUDE_SUBSTRINGS):
                continue
            coll_bones.append(bone.name)

        if coll_bones:
            props.collision_bones = ", ".join(coll_bones)
            self.report({'INFO'}, f"Added {len(coll_bones)} collision bones")
        else:
            self.report({'WARNING'}, "No eligible bones found")
        return {'FINISHED'}


class HAIR_OT_AddBone(Operator):
    bl_idname  = "hair_physics.add_bone"
    bl_label   = "Add Bone"
    bl_options = {'REGISTER'}

    branch: IntProperty(default=0, min=0, max=7)

    def execute(self, context):
        item = context.scene.hair_physics.bones.add()
        item.branch = self.branch
        return {'FINISHED'}


class HAIR_OT_RemoveBone(Operator):
    bl_idname  = "hair_physics.remove_bone"
    bl_label   = "Remove Bone"
    bl_options = {'REGISTER'}

    # If -1, remove the last bone globally. Otherwise, remove the last bone
    # whose branch index matches.
    branch: IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.hair_physics
        if not props.bones:
            return {'FINISHED'}

        if self.branch < 0:
            target_idx = len(props.bones) - 1
        else:
            target_idx = -1
            for i in range(len(props.bones) - 1, -1, -1):
                if props.bones[i].branch == self.branch:
                    target_idx = i
                    break
            if target_idx < 0:
                return {'FINISHED'}

        props.bones.remove(target_idx)
        props.active_bone_index = max(0, len(props.bones) - 1)
        return {'FINISHED'}


class HAIR_OT_DeleteBranch(Operator):
    """Delete an entire hair branch — all bones in that branch plus the slot"""
    bl_idname  = "hair_physics.delete_branch"
    bl_label   = "Delete Branch"
    bl_options = {'REGISTER'}

    branch: IntProperty(default=0, min=0, max=7)

    def execute(self, context):
        props = context.scene.hair_physics

        # Remove all bones belonging to the deleted branch.
        to_remove = [i for i, it in enumerate(props.bones)
                     if it.branch == self.branch]
        for i in reversed(to_remove):
            props.bones.remove(i)

        # Shift higher branch indices down so there are no gaps.
        for it in props.bones:
            if it.branch > self.branch:
                it.branch -= 1

        # Decrement the branch count, but keep at least 1 so the UI still
        # renders a valid state.
        props.num_branches = max(1, props.num_branches - 1)
        props.active_bone_index = max(0, len(props.bones) - 1)

        self.report({'INFO'}, f"Deleted branch {self.branch + 1}")
        return {'FINISHED'}


class HAIR_OT_Preview(Operator):
    bl_idname  = "hair_physics.preview"
    bl_label   = "Preview Hair Jiggle"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.hair_physics
        arm   = find_armature(context)
        if not arm:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        bone_names = _get_bone_names(props)
        if not bone_names:
            self.report({'ERROR'}, "Add at least one hair bone"); return {'CANCELLED'}

        if not arm.animation_data or not arm.animation_data.action:
            self.report({'ERROR'}, "Load an animation first"); return {'CANCELLED'}

        action = arm.animation_data.action

        # Snapshot for undo.
        if props.backup_action_name:
            old = bpy.data.actions.get(props.backup_action_name)
            if old:
                bpy.data.actions.remove(old)
        bkp                      = action.copy()
        bkp.name                 = f"__hair_bkp_{action.name}"
        bkp.use_fake_user        = True
        props.backup_action_name = bkp.name

        strip_physics_keyframes(action, bone_names)
        arm.wiggle_freeze = False
        configured = _apply_wiggle(context, arm, bone_names,
                                   props.jiggle_intensity, props.hair_type)
        if not configured:
            self.report({'ERROR'}, "None of the listed bones found in armature")
            return {'CANCELLED'}

        # --- Real-time collision: create temp meshes for the simulation ---
        coll_collection = None
        coll_objects    = []
        coll_bones      = []
        if props.collision_enabled:
            coll_bones = [n.strip() for n in props.collision_bones.split(',') if n.strip()]
            if coll_bones:
                props.status_text = "Creating collision meshes..."
                coll_collection, coll_objects = _create_collision_meshes(
                    context, arm, coll_bones, props.collision_sphere_factor
                )

        props.status_text = "Baking hair physics..."
        ok, msg = _bake_hair(context, arm, bone_names, props.jiggle_intensity,
                             coll_collection=coll_collection,
                             hair_type=props.hair_type)

        # Clean up collision meshes (done with the simulation).
        if coll_collection:
            _cleanup_collision_meshes(coll_collection, coll_objects)

        if not ok:
            props.status_text = f"Failed: {msg}"
            self.report({'ERROR'}, msg); return {'CANCELLED'}

        # Gentle post-bake safety net: catches any minor remaining penetrations
        # that the real-time collision missed (fast inter-frame motion, etc.).
        if coll_bones:
            props.status_text = "Final collision cleanup..."
            n_fixed = post_bake_collision_correct(
                context, arm, bone_names, coll_bones,
                sphere_factor=props.collision_sphere_factor,
                max_rot_deg=25,
            )
            if n_fixed:
                self.report({'INFO'}, f"Collision cleanup: corrected {n_fixed} frames")

        # Fix quaternion signs after collision correction
        act = arm.animation_data and arm.animation_data.action
        if act:
            _fix_quaternion_continuity(act, bone_names)

        clear_wiggle_from_bones(context, arm, bone_names)
        props.status_text = "Hair preview ready!"
        self.report({'INFO'}, f"Hair baked: {', '.join(configured)}")
        ensure_object_mode(context)
        return {'FINISHED'}


class HAIR_OT_Undo(Operator):
    bl_idname  = "hair_physics.undo"
    bl_label   = "Undo Hair Preview"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.hair_physics
        arm   = find_armature(context)
        if not arm:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        arm.wiggle_freeze = False
        clear_wiggle_from_bones(context, arm, _get_bone_names(props))

        if props.backup_action_name:
            bkp = bpy.data.actions.get(props.backup_action_name)
            if bkp and arm.animation_data:
                baked = arm.animation_data.action
                arm.animation_data.action = bkp
                bkp.name          = bkp.name.replace("__hair_bkp_", "", 1)
                bkp.use_fake_user = False
                if baked and baked != bkp:
                    bpy.data.actions.remove(baked)
                props.backup_action_name = ""

                context.scene.frame_start = 1
                context.scene.frame_end   = int(bkp.frame_range[1])
                ensure_object_mode(context)
                context.scene.frame_set(1)
                props.status_text = "Ready"
                self.report({'INFO'}, "Restored pre-preview state")
                return {'FINISHED'}

        props.status_text = "Ready"
        self.report({'WARNING'}, "No backup found")
        return {'FINISHED'}


class HAIR_OT_BrowseFolder(Operator):
    """Browse for animations folder"""
    bl_idname    = "hair_physics.browse_folder"
    bl_label     = "Browse Animations Folder"
    bl_description = "Choose a folder containing .anm animation files"
    bl_options   = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        if self.directory:
            context.scene.hair_physics.custom_folder = self.directory.rstrip('/\\')
            bpy.ops.hair_physics.refresh_anims()
        return {'FINISHED'}

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class HAIR_OT_BrowseExportFolder(Operator):
    """Browse for export folder"""
    bl_idname    = "hair_physics.browse_export_folder"
    bl_label     = "Browse Export Folder"
    bl_description = "Choose a folder to export baked animations"
    bl_options   = {'REGISTER'}

    directory: StringProperty(subtype='DIR_PATH')

    def execute(self, context):
        if self.directory:
            context.scene.hair_physics.export_folder = self.directory.rstrip('/\\')
        return {'FINISHED'}

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class HAIR_OT_ClearFolder(Operator):
    bl_idname  = "hair_physics.clear_folder"
    bl_label   = "Clear Custom Folder"
    bl_options = {'REGISTER'}

    def execute(self, context):
        context.scene.hair_physics.custom_folder = ""
        bpy.ops.hair_physics.refresh_anims()
        return {'FINISHED'}


class HAIR_OT_RefreshAnims(Operator):
    """Scan the animations folder and refresh the list"""
    bl_idname  = "hair_physics.refresh_anims"
    bl_label   = "Refresh Animations"
    bl_options = {'REGISTER'}

    def execute(self, context):
        props = context.scene.hair_physics
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


class HAIR_OT_LoadAnimation(Operator):
    """Load a single animation for preview"""
    bl_idname  = "hair_physics.load_anim"
    bl_label   = "Load Animation"
    bl_options = {'REGISTER', 'UNDO'}

    filepath:  StringProperty()
    anim_name: StringProperty()
    index:     IntProperty(default=-1)

    def execute(self, context):
        props = context.scene.hair_physics

        if not self.filepath or not os.path.exists(self.filepath):
            self.report({'ERROR'}, "Animation file not found"); return {'CANCELLED'}

        armature_obj = find_armature(context)
        if not armature_obj:
            self.report({'ERROR'}, "No armature found"); return {'CANCELLED'}

        select_armature(context, armature_obj)
        armature_obj.wiggle_freeze = False

        # Full physics-state reset: a previous preview leaves the current action
        # with baked hair keyframes, a stale backup action, and possibly live
        # wiggle properties on the bones. All of that must be cleared before the
        # new action is assigned, otherwise the hair pose from the previous clip
        # leaks into the new one (no reset between animations bug).
        hair_bones = _get_bone_names(props)
        if hair_bones:
            clear_wiggle_from_bones(context, armature_obj, hair_bones)

        # Drop the backup action left behind by the previous preview — it
        # references the previous animation, not the one we're loading.
        if props.backup_action_name:
            old_bkp = bpy.data.actions.get(props.backup_action_name)
            if old_bkp:
                old_bkp.use_fake_user = False
                bpy.data.actions.remove(old_bkp)
            props.backup_action_name = ""

        # Strip any hair keyframes still sitting on the CURRENT action (the one
        # that was previewed) so switching away doesn't keep stale physics
        # around as a cached action.
        cur_act = armature_obj.animation_data and armature_obj.animation_data.action
        if cur_act and hair_bones:
            strip_physics_keyframes(cur_act, hair_bones)

        # Go to frame 0 so the wiggle handler's bind-frame reset path runs
        # (clears velocity/position state) before the new action is assigned.
        context.scene.frame_set(0)

        # Reset hair bones to identity (pose-level) before loading.
        bpy.ops.object.mode_set(mode='POSE')
        for bname in hair_bones:
            pb = armature_obj.pose.bones.get(bname)
            if pb:
                pb.location            = (0.0, 0.0, 0.0)
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
                pb.rotation_euler      = (0.0, 0.0, 0.0)
                pb.scale               = (1.0, 1.0, 1.0)
        bpy.ops.object.mode_set(mode='OBJECT')

        armature_obj.wiggle_freeze = False
        props.status_text = "Ready"

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


class HAIR_OT_ApplyToAll(Operator):
    """Apply hair physics to all animations in the folder, bake, and export"""
    bl_idname    = "hair_physics.apply_all"
    bl_label     = "Apply to All Animations"
    bl_description = "Import each animation, apply hair physics, bake, and export to the output folder"
    bl_options   = {'REGISTER'}

    def execute(self, context):
        props        = context.scene.hair_physics
        armature_obj = find_armature(context)

        if not armature_obj:
            self.report({'ERROR'}, "No armature found in scene"); return {'CANCELLED'}

        bone_names = _get_bone_names(props)
        if not bone_names:
            self.report({'ERROR'}, "No hair bones selected"); return {'CANCELLED'}

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

        # --- Real-time collision: create temp meshes ONCE for the whole batch ---
        coll_collection    = None
        coll_objects       = []
        coll_bones_for_batch = []
        precomputed_coll_radii = None
        if props.collision_enabled:
            coll_bones_for_batch = [n.strip() for n in props.collision_bones.split(',') if n.strip()]
            if coll_bones_for_batch:
                coll_collection, coll_objects = _create_collision_meshes(
                    context, armature_obj, coll_bones_for_batch,
                    props.collision_sphere_factor
                )
                # Also pre-compute analytical radii for the post-bake safety net.
                precomputed_coll_radii = precompute_collision_radii(
                    armature_obj, coll_bones_for_batch
                )

        # Hide all meshes to stop per-frame vertex deformation recalculation.
        # Note: collision meshes are already hidden (hide_viewport=True) so
        # hide_meshes_for_batch() won't touch them.
        disabled_mods, hidden_objs = hide_meshes_for_batch(context)

        # Configure wiggle once after mesh hiding.
        _apply_wiggle(context, armature_obj, bone_names,
                      props.jiggle_intensity, props.hair_type)

        for idx, anim_item in enumerate(props.animations):
            props.status_text = f"Processing {idx + 1}/{total}: {anim_item.name}..."
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

            try:
                # Hard-reset physics so velocity/position from the previous
                # animation doesn't bleed into the next preroll.
                armature_obj.wiggle_freeze = False

                # Jump to frame 0 first so the Wiggle handler resets
                # lastframe to 0.  Without this, lastframe stays at the
                # previous animation's frame_end, and the frames_elapsed
                # calculation on the first frame of the new bake can go
                # negative (or very large), producing a broken dt that
                # reverses forces or massively overshoots.
                context.scene.frame_set(0)

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

                # Strip any imported hair-bone keyframes so the physics
                # simulation starts from rest pose — same as preview does.
                strip_physics_keyframes(new_action, bone_names)

                # Re-apply wiggle each iteration: _bake_hair may lower effective
                # intensity for non-loop clips, so loops that follow need it reset.
                _apply_wiggle(context, armature_obj, bone_names,
                              props.jiggle_intensity, props.hair_type)

                ok, msg = _bake_hair(context, armature_obj, bone_names,
                                     props.jiggle_intensity,
                                     coll_collection=coll_collection,
                                     hair_type=props.hair_type)
                if not ok:
                    print(f"Bake failed for {anim_item.name}: {msg}")
                    fail_count += 1
                    # Remove the failed action to free memory.
                    cur = armature_obj.animation_data.action
                    if cur and cur != original_action:
                        armature_obj.animation_data.action = None
                        bpy.data.actions.remove(cur)
                    continue

                # Gentle post-bake safety net for any remaining penetrations.
                if coll_bones_for_batch:
                    post_bake_collision_correct(
                        context, armature_obj, bone_names, coll_bones_for_batch,
                        sphere_factor=props.collision_sphere_factor,
                        max_rot_deg=25,
                        precomputed_radii=precomputed_coll_radii,
                    )
                # Fix quaternion signs after collision correction
                act = armature_obj.animation_data.action
                if act:
                    _fix_quaternion_continuity(act, bone_names)

                out_path = os.path.join(export_dir, f"{anim_item.name}.anm")
                export_anm.write_anm(out_path, armature_obj, fps)
                success_count += 1

                # Remove the exported action to free memory during batch.
                cur = armature_obj.animation_data.action
                if cur and cur != original_action:
                    armature_obj.animation_data.action = None
                    bpy.data.actions.remove(cur)

            except Exception as e:
                print(f"Failed on {anim_item.name}: {e}")
                fail_count += 1

        clear_wiggle_from_bones(context, armature_obj, bone_names)

        try:
            armature_obj.animation_data.action = original_action
        except Exception:
            pass

        armature_obj.wiggle_freeze = False
        ensure_object_mode(context)
        restore_meshes_after_batch(disabled_mods, hidden_objs)

        # Clean up collision meshes AFTER restoring meshes (they're separate).
        if coll_collection:
            _cleanup_collision_meshes(coll_collection, coll_objects)

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

class HAIR_UL_AnimList(UIList):
    """Scrollable animation list with search filtering"""

    def draw_item(self, context, layout, _data, item, _icon, _active_data, _active_propname, index):
        props = context.scene.hair_physics
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            row.label(text="", icon='PLAY' if item.name == props.current_loaded else 'ACTION')
            op = row.operator("hair_physics.load_anim", text=item.name, emboss=False)
            op.filepath  = item.filepath
            op.anim_name = item.name
            op.index     = index
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.name, icon='ACTION')

    def filter_items(self, context, data, propname):
        props       = context.scene.hair_physics
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

class HAIR_PT_Physics(Panel):
    bl_label       = "Hair Physics"
    bl_idname      = "VIEW3D_PT_hair_physics"
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'Misc LoL Tools'
    bl_options     = {'DEFAULT_CLOSED'}

    def draw_header(self, _context):
        self.layout.label(text="", icon_value=icons.get_icon("icon_52"))

    def draw(self, context):
        layout = self.layout
        props  = context.scene.hair_physics
        arm    = find_armature(context)

        layout.box().label(text=props.status_text, icon='INFO')

        # --- Bone list (grouped by branch) ---
        box = layout.box()
        row = box.row(align=True)
        row.label(text="Hair Bones", icon='BONE_DATA')
        row.prop(props, "num_branches", text="Branches")

        if arm:
            nb = max(1, props.num_branches)
            # Auto-migrate: if num_branches shrank, collapse out-of-range
            # branch ids onto the last valid branch so those bones don't
            # vanish from the UI.
            max_branch = nb - 1
            for item in props.bones:
                if item.branch > max_branch:
                    item.branch = max_branch

            # For a single-branch setup, draw a flat list (same UX as before).
            if nb == 1:
                idx = 0
                for item in props.bones:
                    idx += 1
                    box.row(align=True).prop_search(
                        item, "bone_name", arm.pose, "bones", text=f"Bone {idx}"
                    )
                row = box.row(align=True)
                op = row.operator("hair_physics.add_bone",    text="Add Bone", icon='ADD')
                op.branch = 0
                op_rm = row.operator("hair_physics.remove_bone", text="Remove",   icon='REMOVE')
                op_rm.branch = -1
            else:
                # Multi-branch: one labelled sub-box per branch.
                # Suggested names help users know which chain to use for what
                # ("Back", "Left", "Right" for 3 branches, etc).
                _branch_labels = {
                    2: ["Left",  "Right"],
                    3: ["Back",  "Left",  "Right"],
                    4: ["Back",  "Left",  "Right", "Front"],
                    5: ["Back",  "Left",  "Right", "Front", "Top"],
                    6: ["Back",  "Left",  "Right", "Front", "Top", "Extra"],
                }
                labels = _branch_labels.get(nb, [f"Branch {i+1}" for i in range(nb)])
                for b_idx in range(nb):
                    sub = box.box()
                    sub.label(text=f"Branch {b_idx + 1}: {labels[b_idx]}",
                              icon='GROUP_BONE')
                    branch_bones = [(i, it) for i, it in enumerate(props.bones)
                                    if it.branch == b_idx]
                    if not branch_bones:
                        sub.label(text="(no bones yet)", icon='INFO')
                    else:
                        for count, (_, item) in enumerate(branch_bones, start=1):
                            sub.row(align=True).prop_search(
                                item, "bone_name", arm.pose, "bones",
                                text=f"Bone {count}"
                            )
                    row = sub.row(align=True)
                    op = row.operator("hair_physics.add_bone",
                                      text="Add Bone", icon='ADD')
                    op.branch = b_idx
                    op_rm = row.operator("hair_physics.remove_bone",
                                         text="Remove Last", icon='REMOVE')
                    op_rm.branch = b_idx
                    op_del = row.operator("hair_physics.delete_branch",
                                          text="", icon='TRASH')
                    op_del.branch = b_idx
        else:
            box.label(text="No armature in scene", icon='ERROR')

        # --- Intensity + hair type ---
        box = layout.box()
        box.label(text="Hair Settings", icon='MOD_WAVE')
        col = box.column(align=True)
        col.prop(props, "hair_type", text="Type")
        col.prop(props, "jiggle_intensity", slider=True)
        row = col.row()
        row.alignment = 'CENTER'
        i     = props.jiggle_intensity
        label = "▸ Stiff" if i <= 3 else "▸ Subtle" if i <= 7 else "▸ Natural" if i <= 13 \
            else "▸ Flowing" if i <= 17 else "▸ Wild!"
        row.label(text=label)

        # --- Body collision ---
        box = layout.box()
        box.row().prop(props, "collision_enabled", text="Body Collision", icon='MESH_UVSPHERE')
        if props.collision_enabled:
            col = box.column(align=True)
            col.label(text="Collision Bones (comma-separated):", icon='BONE_DATA')
            col.prop(props, "collision_bones", text="")
            col.operator("hair_physics.select_all_collision",
                         text="Select All Bones", icon='GROUP_BONE')
            col.operator("hair_physics.auto_detect_collision",
                         text="Auto-Detect Arm Bones", icon='VIEWZOOM')
            col.separator()
            col.prop(props, "collision_sphere_factor", slider=True, text="Radius Scale  (1.0 = auto)")

        # --- Preview ---
        has_anim  = bool(arm and arm.animation_data and arm.animation_data.action)
        has_bones = bool(_get_bone_names(props))

        box = layout.box()
        box.label(text="Preview", icon='PLAY')
        col = box.column(align=True)
        col.scale_y = 1.3
        row = col.row(align=True)
        row.enabled = has_anim and has_bones
        row.operator("hair_physics.preview", text="Preview Hair Jiggle", icon='MOD_WAVE')
        row = col.row(align=True)
        row.operator("hair_physics.undo", text="Undo Preview", icon='LOOP_BACK')
        if props.current_loaded:
            box.label(text=f"Animation: {props.current_loaded}", icon='ANIM')

        # --- Batch processing ---
        box = layout.box()
        box.label(text="Batch Processing", icon='FILE_FOLDER')

        row = box.row(align=True)
        row.operator("hair_physics.refresh_anims", text="Refresh", icon='FILE_REFRESH')
        row.operator("hair_physics.browse_folder", text="", icon='FILEBROWSER')

        row = box.row(align=True)
        row.scale_y = 0.7
        if props.animations_folder:
            folder_name = os.path.basename(props.animations_folder)
            parent_name = os.path.basename(os.path.dirname(props.animations_folder))
            row.label(text=f".../{parent_name}/{folder_name}", icon='FILE_FOLDER')
            if props.custom_folder:
                row.operator("hair_physics.clear_folder", text="", icon='X')
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
            box.row().template_list("HAIR_UL_AnimList", "",
                                    props, "animations", props, "active_index", rows=8)
            box.row(align=True).prop(props, "search_filter", text="", icon='VIEWZOOM')

        # --- Export ---
        layout.separator()
        box = layout.box()
        box.label(text="Export", icon='EXPORT')
        row = box.row(align=True)
        row.label(text="Output Folder:", icon='FILE_FOLDER')
        row.operator("hair_physics.browse_export_folder", text="", icon='FILEBROWSER')
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
        col.operator("hair_physics.apply_all", text="⚡ Apply to All Animations", icon='MOD_WAVE')


# ---------------------------------------------------------------------------
#  Registration
# ---------------------------------------------------------------------------

classes = [
    HairBoneItem,
    HairAnimListItem,
    HairPhysicsProperties,
    HAIR_OT_AutoDetectCollisionBones,
    HAIR_OT_SelectAllCollisionBones,
    HAIR_OT_AddBone,
    HAIR_OT_RemoveBone,
    HAIR_OT_DeleteBranch,
    HAIR_OT_Preview,
    HAIR_OT_Undo,
    HAIR_OT_BrowseFolder,
    HAIR_OT_BrowseExportFolder,
    HAIR_OT_ClearFolder,
    HAIR_OT_RefreshAnims,
    HAIR_OT_LoadAnimation,
    HAIR_OT_ApplyToAll,
    HAIR_UL_AnimList,
    HAIR_PT_Physics,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hair_physics = PointerProperty(type=HairPhysicsProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types.Scene, 'hair_physics'):
        del bpy.types.Scene.hair_physics
