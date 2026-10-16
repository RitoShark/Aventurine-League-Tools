"""ANM animation importer - Matrix-based robust transform conversion"""
import bpy
import mathutils
import math
import os
from ..utils.binary_utils import Hash
from ..utils.backend import require_ritoshark
from . import import_skl


class ANMPose:
    __slots__ = ('translation', 'rotation', 'scale')
    
    def __init__(self):
        self.translation = None
        self.rotation = None
        self.scale = None


class ANMTrack:
    __slots__ = ('joint_hash', 'poses')
    
    def __init__(self, joint_hash):
        self.joint_hash = joint_hash
        self.poses = {} # f -> ANMPose


class ANMData:
    __slots__ = ('fps', 'duration', 'tracks', 'frame_count')
    
    def __init__(self):
        self.fps = 30.0
        self.duration = 0.0
        self.tracks = []
        self.frame_count = 0


def read_anm(filepath):
    source = require_ritoshark().Anm.from_path(str(filepath))
    anm = ANMData()
    anm.fps = source.fps
    anm.frame_count = source.frame_count
    anm.duration = source.frame_count / source.fps
    for item in source.tracks:
        track = ANMTrack(item.joint_hash)
        for frame in item.frames:
            pose = ANMPose()
            x, y, z, w = frame.rotation
            pose.rotation = mathutils.Quaternion((w, x, y, z))
            pose.translation = mathutils.Vector(frame.translation) * import_skl.IMPORT_SCALE
            pose.scale = mathutils.Vector(frame.scale)
            track.poses[int(round(frame.time * source.fps))] = pose
        anm.tracks.append(track)
    return anm


def apply_anm(anm, armature_obj, frame_offset=0, flip=False, adapt_to_edits=False, skip_custom_parent_pin=False):
    """Apply full TRS tracks; neutral inserted parents preserve source motion.

    A clip with tracks for added joints uses the current exported hierarchy.
    A stock clip uses the recorded source local frames; inserted controls then
    layer their authored transforms onto the original motion without pinning it.
    """
    from .skeleton import ExportPlan, stored_matrix, P
    if armature_obj.type != 'ARMATURE':
        return
    plan = ExportPlan(armature_obj)
    armature_obj.animation_data_create()
    if armature_obj.animation_data.action is None:
        armature_obj.animation_data.action = bpy.data.actions.new('Animation')
    scene = bpy.context.scene
    scene.render.fps = max(1, round(anm.fps))
    if frame_offset == 0:
        scene.frame_start, scene.frame_end = 0, max(0, anm.frame_count)
    tracks = {t.joint_hash: t for t in anm.tracks}
    def track_for(pb):
        return (tracks.get(Hash.elf(plan.names[pb.name])) or tracks.get(Hash.elf(pb.name))
                or (tracks.get(Hash.elf(pb.get('native_source_name', pb.name))) if pb.name in plan.native else None))
    current_hierarchy = any(track_for(pb) for pb in plan.bones if pb.name not in plan.native)
    source_names = {pb.get('native_source_name', pb.name): pb for pb in plan.owners.values()}
    source_names.update({pb.name: pb for pb in plan.owners.values()})
    bone_keyframes = {}
    pi = P.inverted()
    for pb in plan.bones:
        track = track_for(pb)
        # An untracked custom joint is a user control, not an identity track.
        # In particular preserve its static offset and keys outside inserted clips.
        if track is None and pb.name not in plan.native:
            continue
        pb.rotation_mode = 'QUATERNION'
        k = plan.corrections[pb.name]
        if pb.name in plan.native and not current_hierarchy:
            v = stored_matrix(pb, 'native_matrix_local')
            if v is None:
                v = pb.bone.matrix_local.copy()
            parent = source_names.get(pb.get('native_parent', ''))
            vp = stored_matrix(parent, 'native_matrix_local') if parent else None
            if parent and vp is None:
                vp = parent.bone.matrix_local.copy()
            rest = vp.inverted() @ v if vp is not None else v
        else:
            parent = pb.parent
            rest = parent.bone.matrix_local.inverted() @ pb.bone.matrix_local if parent else pb.bone.matrix_local.copy()
        kp = plan.corrections[parent.name] if parent else mathutils.Matrix.Identity(4)
        # Rest local in the same exported joint frames as SKL and ANM.
        default = pi @ kp.inverted() @ rest @ k @ P
        dt, dr, ds = default.decompose()
        data = {'location': {}, 'rotation_quaternion': {}, 'scale': {}}
        def sample(frame, pose=None):
            t = pose.translation.copy() if pose and pose.translation is not None else dt.copy()
            r = pose.rotation.copy() if pose and pose.rotation is not None else dr.copy()
            sc = pose.scale.copy() if pose and pose.scale is not None else ds.copy()
            if flip and pose:
                t.x = -t.x
                r.y, r.z = -r.y, -r.z
            native = P @ mathutils.Matrix.LocRotScale(t, r, sc) @ pi
            basis = rest.inverted() @ kp @ native @ k.inverted()
            loc, rot, scale = basis.decompose()
            data['location'][frame] = tuple(loc)
            data['rotation_quaternion'][frame] = tuple(rot)
            data['scale'][frame] = tuple(scale)
        if frame_offset == 0:
            sample(0)
        if track:
            for f_id, pose in track.poses.items():
                sample(frame_offset + f_id + 1, pose)
        else:
            sample(frame_offset + 1)
            sample(frame_offset + max(1, anm.frame_count))
        bone_keyframes[pb.name] = data

    # --- 3b. Fix quaternion sign continuity ---
    # The "smallest three" quaternion compression always reconstructs the
    # dropped component as positive (via sqrt), but different components may
    # be dropped at adjacent frames.  This can flip the overall quaternion
    # sign, causing Blender's interpolation to take the long path and
    # produce wild rotation artifacts at in-between frames.
    for bone_name, bdata in bone_keyframes.items():
        rots = bdata.get('rotation_quaternion')
        if not rots or len(rots) < 2:
            continue
        sorted_frames = sorted(rots.keys())
        prev_q = rots[sorted_frames[0]]
        for i in range(1, len(sorted_frames)):
            f = sorted_frames[i]
            cur_q = rots[f]
            dot = (prev_q[0] * cur_q[0] + prev_q[1] * cur_q[1]
                   + prev_q[2] * cur_q[2] + prev_q[3] * cur_q[3])
            if dot < 0:
                cur_q = (-cur_q[0], -cur_q[1], -cur_q[2], -cur_q[3])
                rots[f] = cur_q
            prev_q = cur_q


    # --- 4. Write keyframes - hybrid approach with Blender 5.0 compatibility ---
    # Use keyframe_insert once to create FCurves, then direct access for speed
    # Blender 5.0 moved fcurves to a new location in the layered action system

    action = armature_obj.animation_data.action
    total_keyframes = 0

    # Detect how to access fcurves based on Blender version/API
    fcurves_collection = None
    use_fast_path = False
    
    # Try different methods to access fcurves
    if hasattr(action, 'fcurves'):
        # Blender 4.3 and earlier - fcurves directly on action
        fcurves_collection = action.fcurves
        use_fast_path = True
    elif hasattr(action, 'layers') and len(action.layers) > 0:
        # Blender 4.4+ / 5.0+ - Layered/Slotted Actions
        # FCurves are now at: action.layers[0].strips[0].channelbag(slot).fcurves
        # But for armatures, we need to ensure we have the right slot binding
        try:
            layer = action.layers[0]
            if len(layer.strips) > 0:
                strip = layer.strips[0]
                # Get the appropriate slot - for armatures, usually the first or default
                if hasattr(action, 'slots') and len(action.slots) > 0:
                    slot = action.slots[0]
                    channelbag = strip.channelbag(slot)
                    if hasattr(channelbag, 'fcurves'):
                        fcurves_collection = channelbag.fcurves
                        use_fast_path = True
        except:
            # If anything fails, fall back to slow but safe method
            use_fast_path = False

    for bone_name, bone_data in bone_keyframes.items():

        pbone = armature_obj.pose.bones.get(bone_name)
        if not pbone:
            continue

        # Process each property type
        for prop_name, num_channels in [('location', 3), ('rotation_quaternion', 4), ('scale', 3)]:
            frames_dict = bone_data[prop_name]
            if not frames_dict:
                continue

            sorted_frames = sorted(frames_dict.keys())
            if not sorted_frames:
                continue

            if use_fast_path and fcurves_collection is not None:
                # ULTRA-FAST PATH: C-level memory copying via foreach_set
                data_path = pbone.path_from_id(prop_name)
                
                for i in range(num_channels):
                    fc = fcurves_collection.find(data_path, index=i)
                    if not fc:
                        fc = fcurves_collection.new(data_path, index=i, action_group=bone_name)
                        
                    if len(fc.keyframe_points):
                        # Insert replaces matching frames and preserves keys/handles
                        # outside the inserted clip. foreach_set requires the entire
                        # collection, so it is only safe on a new, empty curve.
                        for frame in sorted_frames:
                            fc.keyframe_points.insert(
                                float(frame), float(frames_dict[frame][i]), options={'FAST'})
                    else:
                        fc.keyframe_points.add(len(sorted_frames))
                        coords = [value for frame in sorted_frames
                                  for value in (float(frame), float(frames_dict[frame][i]))]
                        fc.keyframe_points.foreach_set('co', coords)
                    fc.update()
                    
                    total_keyframes += len(sorted_frames)
            else:
                # Slow path for Blender 5.0+: use keyframe_insert for all frames
                for frame in sorted_frames:
                    value = frames_dict[frame]
                    setattr(pbone, prop_name, value)
                    pbone.keyframe_insert(data_path=prop_name, frame=frame)
                    total_keyframes += num_channels

    # Blender 4.4/4.5 slotted actions: the legacy action.fcurves proxy creates
    # keyframes inside a slot, but doesn't auto-bind that slot to the armature.
    # Without this, the animation data exists but Blender never evaluates it.
    # See https://projects.blender.org/blender/blender/issues/135236
    anim_data = armature_obj.animation_data
    if anim_data and anim_data.action:
        if hasattr(anim_data, 'action_slot') and anim_data.action_slot is None:
            act = anim_data.action
            if hasattr(act, 'slots') and len(act.slots) > 0:
                anim_data.action_slot = act.slots[0]
        # Remember which mode was used to import this action so export can default to it
        anim_data.action["lol_adapt_to_edits"] = bool(adapt_to_edits)

    bpy.context.view_layer.objects.active = armature_obj
    if armature_obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Force update and reset to start frame
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_start)


def load(operator, context, filepath, create_new_action=True, insert_frame=0, flip=False, adapt_to_edits=False):
    armature_obj = context.active_object
    if not armature_obj or armature_obj.type != 'ARMATURE':
        for obj in context.scene.objects:
            if obj.type == 'ARMATURE':
                armature_obj = obj
                break
                
    if not armature_obj:
        operator.report({'ERROR'}, "No active armature found to apply animation to")
        return {'CANCELLED'}
        
    try:
        anm = read_anm(filepath)
        
        # Get action name from filename (without extension)
        action_name = os.path.splitext(os.path.basename(filepath))[0]
        
        if create_new_action:
            # Create a new action
            if not armature_obj.animation_data:
                armature_obj.animation_data_create()
            
            # Create new action with the ANM filename
            new_action = bpy.data.actions.new(name=action_name)
            armature_obj.animation_data.action = new_action
            
            # Apply animation starting at frame 0 (with +1 offset for bind pose)
            apply_anm(anm, armature_obj, frame_offset=0, flip=flip, adapt_to_edits=adapt_to_edits)
            
            # Store info on the action
            new_action["lol_anm_filepath"] = filepath
            new_action["lol_anm_filename"] = os.path.basename(filepath)
            
            operator.report({'INFO'}, f"Imported animation '{action_name}': {anm.frame_count} frames")
        else:
            # Insert into existing action at specified frame
            if not armature_obj.animation_data or not armature_obj.animation_data.action:
                operator.report({'ERROR'}, "No existing action to insert into. Use 'New Action' mode first.")
                return {'CANCELLED'}
            
            # Apply animation with frame offset
            apply_anm(anm, armature_obj, frame_offset=insert_frame, flip=flip, adapt_to_edits=adapt_to_edits)
            
            # Extend scene end frame if needed
            new_end = insert_frame + anm.frame_count
            if context.scene.frame_end < new_end:
                context.scene.frame_end = new_end
            
            operator.report({'INFO'}, f"Inserted '{action_name}' at frame {insert_frame}: {anm.frame_count} frames")
        
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed to load ANM: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
