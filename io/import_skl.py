"""SKL skeleton importer - Visual skeleton layout without leaf bones"""
import bpy
import mathutils
from ..utils.backend import require_ritoshark

# Scale factor to match Maya/LtMAO units (0.01 = 100x smaller)
IMPORT_SCALE = 0.01
EXPORT_SCALE = 1.0 / IMPORT_SCALE  # = 100, to scale back up for export


class SKLJoint:
    __slots__ = ('name', 'parent', 'local_translation', 'local_scale', 'local_rotation', 'global_pos',
                 'raw_trans', 'raw_rot', 'raw_scale', 'flags', 'radius')
    
    def __init__(self):
        self.name = None
        self.parent = None
        self.local_translation = None
        self.local_scale = None
        self.local_rotation = None
        self.global_pos = None
        # Raw native components
        self.raw_trans = None
        self.raw_rot = None
        self.raw_scale = None
        self.flags = 0
        self.radius = 2.1


class SKLJoints(list):
    pass


def read_skl(filepath):
    skeleton = require_ritoshark().Skl.from_path(str(filepath))
    source = skeleton.joints
    index_by_id = {joint.id: index for index, joint in enumerate(source)}
    if len(index_by_id) != len(source) or len({joint.name for joint in source}) != len(source):
        raise ValueError('SKL contains duplicate joint IDs or names')
    # Validate ancestry before the recursive global-transform calculation.
    completed = set()
    for item in source:
        current = item.id
        chain = set()
        while current != -1 and current not in completed:
            if current in chain or current not in index_by_id:
                raise ValueError('SKL contains a cyclic or missing parent')
            chain.add(current)
            current = source[index_by_id[current]].parent_id
        completed.update(chain)
    joints = SKLJoints()
    joints.name, joints.asset, joints.flags = skeleton.name, skeleton.asset, skeleton.flags
    P = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
    P_inv = P.inverted()
    for item in source:
        joint = SKLJoint()
        joint.name = item.name
        joint.flags, joint.radius = item.flags, item.radius
        joint.parent = -1 if item.parent_id == -1 else index_by_id[item.parent_id]
        joint.raw_trans = mathutils.Vector(item.local_translation)
        joint.raw_scale = mathutils.Vector(item.local_scale)
        x, y, z, w = item.local_rotation
        joint.raw_rot = mathutils.Quaternion((w, x, y, z))
        local = (mathutils.Matrix.Translation(joint.raw_trans)
                 @ joint.raw_rot.to_matrix().to_4x4()
                 @ mathutils.Matrix.Diagonal((*joint.raw_scale, 1.0)))
        translation, rotation, scale = (P @ local @ P_inv).decompose()
        joint.local_translation = translation * IMPORT_SCALE
        joint.local_rotation = rotation
        joint.local_scale = scale
        joints.append(joint)
    return joints, [index_by_id[joint_id] for joint_id in skeleton.influences]


def create_armature(joints, name="Armature", bone_orient='VISUAL'):
    """Build a Blender armature from SKL joints.

    bone_orient:
      'VISUAL' — classic look: connected bones pointing at children, cosmetic
                 roll. Bone rest frames do NOT match the native joint frames;
                 exporters reconcile the two via the stored correction props.
      'NATIVE' — exact joints: every bone's rest frame equals the native joint
                 frame from the file (tail length is cosmetic only). Corrections
                 evaluate to identity, and animations stay 1:1 compatible with
                 rigs from other tools (FBX etc.) that share the skeleton.
    """
    # Pass 1: Global positions via matrices (including scale in the chain)
    # Recursive/memoized so that joints whose parent has a higher index (e.g.
    # visual SKLs where custom bones are appended after native bones but are
    # actually parents of some of those native bones) are handled correctly.
    global_pos = [mathutils.Vector((0,0,0))] * len(joints)
    mats = [None] * len(joints)

    def compute_mat(i):
        if mats[i] is not None:
            return mats[i]
        joint = joints[i]
        mat_t = mathutils.Matrix.Translation(joint.local_translation)
        mat_r = joint.local_rotation.to_matrix().to_4x4()
        mat_s = mathutils.Matrix.Diagonal((*joint.local_scale, 1.0))
        local_mat = mat_t @ mat_r @ mat_s
        if joint.parent >= 0:
            mats[i] = compute_mat(joint.parent) @ local_mat
        else:
            mats[i] = local_mat
        return mats[i]

    for i, joint in enumerate(joints):
        joint.global_pos = compute_mat(i).to_translation()
    
    armature_data = bpy.data.armatures.new(name)
    armature_obj = bpy.data.objects.new(name, armature_data)
    armature_obj['lol_skl_name'] = getattr(joints, 'name', '')
    armature_obj['lol_skl_asset'] = getattr(joints, 'asset', '')
    armature_obj['lol_skl_flags'] = getattr(joints, 'flags', 0)
    bpy.context.scene.collection.objects.link(armature_obj)
    bpy.context.view_layer.objects.active = armature_obj
    
    bpy.ops.object.mode_set(mode='EDIT')

    if bone_orient == 'NATIVE':
        # Exact joints: bone frame = native joint frame; length is cosmetic.
        # Use the distance to the nearest child for length so bones stay
        # clickable, falling back to the parent's value for leaf joints.
        child_dist = [0.0] * len(joints)
        for i, joint in enumerate(joints):
            dists = [(joints[c].global_pos - joint.global_pos).length
                     for c in range(len(joints)) if joints[c].parent == i]
            child_dist[i] = min((d for d in dists if d > 0.005), default=0.0)

        for i, joint in enumerate(joints):
            length = child_dist[i]
            if length <= 0.005 and joint.parent >= 0:
                length = child_dist[joint.parent]
            if length <= 0.005:
                length = 0.05

            bone = armature_data.edit_bones.new(joint.name)
            bone.head = (0.0, 0.0, 0.0)
            bone.tail = (0.0, length, 0.0)
            # Bones cannot carry rest scale — keep translation+rotation only.
            # (Scaled/degenerate joints keep their full data in the props, and
            # the correction math reconciles the residual difference.)
            t, r, _s = mats[i].decompose()
            bone.matrix = mathutils.Matrix.LocRotScale(t, r, None)

        for i, joint in enumerate(joints):
            if joint.parent >= 0:
                bone = armature_data.edit_bones[joint.name]
                bone.parent = armature_data.edit_bones[joints[joint.parent].name]

        bpy.ops.object.mode_set(mode='OBJECT')
        # Octahedrons are unreadable on native joint orientations.
        armature_data.display_type = 'STICK'
        _store_native_props(joints, mats, armature_obj)
        return armature_obj

    # Pass 2: Create bones and set heads
    for i, joint in enumerate(joints):
        bone = armature_data.edit_bones.new(joint.name)
        bone.head = joint.global_pos
        # Temporary tail
        bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))

    # Pass 3: Set Parenting
    for i, joint in enumerate(joints):
        if joint.parent >= 0:
            bone = armature_data.edit_bones[joint.name]
            parent_bone = armature_data.edit_bones[joints[joint.parent].name]
            bone.parent = parent_bone

    # Pass 4: Set tails (Point to Child or Centroid of Children)
    for bone in armature_data.edit_bones:
        if bone.children:
            if len(bone.children) == 1:
                # Single child - point directly to it
                child = bone.children[0]
                if (child.head - bone.head).length > 0.001:
                    bone.tail = child.head
                else:
                    bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))
            else:
                # Multiple children - point to centroid of all children's heads
                centroid = mathutils.Vector((0, 0, 0))
                for child in bone.children:
                    centroid += child.head
                centroid /= len(bone.children)

                if (centroid - bone.head).length > 0.001:
                    bone.tail = centroid
                else:
                    bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))
        else:
            # TERMINAL BONE: Apply rotation/direction from parent
            if bone.parent:
                # Inherit direction from parent
                parent_dir = (bone.parent.tail - bone.parent.head)
                if parent_dir.length > 0.001:
                    bone.tail = bone.head + parent_dir.normalized() * (bone.parent.length * 0.5)
                else:
                    bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))
            else:
                bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))
        
        # Ensure no zero length
        if (bone.tail - bone.head).length < 0.001:
            bone.tail = bone.head + mathutils.Vector((0, 0, 0.1))

    bpy.ops.object.mode_set(mode='OBJECT')

    _store_native_props(joints, mats, armature_obj)
    return armature_obj


def _store_native_props(joints, mats, armature_obj):
    # Offset-clone joints (numeric-suffix duplicates of another joint in the
    # same file) carry their scale dial in the bind local. Restore it to the
    # pose channel — the addon's authoring convention for clone scale — so the
    # viewport previews the scaled subtree and a re-export round-trips it.
    # Deliberately NOT done for ordinary scaled native joints: their scale is
    # already baked into the computed bone positions.
    import re as _re
    _names = {j.name for j in joints}
    for joint in joints:
        base = _re.sub(r'\.\d+$', '', joint.name)
        if base == joint.name or base not in _names:
            continue
        s = joint.local_scale
        if abs(s.x - 1.0) + abs(s.y - 1.0) + abs(s.z - 1.0) > 1e-4:
            armature_obj.pose.bones[joint.name].scale = (s.x, s.y, s.z)

    # Pass 5: Store bind pose for animator (scale is already baked into bone positions)
    for i, joint in enumerate(joints):
        pbone = armature_obj.pose.bones[joint.name]
        # Don't apply scale to pose bones - it's already in the bone positions
        # Store bind pose for animator fallback (absolute local relative to parent in Blender space)
        pbone["bind_translation"] = joint.local_translation
        pbone["bind_rotation"] = joint.local_rotation
        pbone["bind_scale"] = joint.local_scale
        
        # Store RAW native components for robust fallback in ANM tracks
        # These are what was read from the file before any coordinate conversion.
        # We store them as lists/tuples so they are easy to retrieve.
        # The rotation is stored as mathutils.Quaternion (w,x,y,z)
        # Scale native_bind_t to match visual bones (both at 0.01 scale)
        # This is required for correction matrix math to work correctly
        pbone["native_bind_t"] = [joint.raw_trans.x * IMPORT_SCALE, joint.raw_trans.y * IMPORT_SCALE, joint.raw_trans.z * IMPORT_SCALE]
        pbone["native_bind_r"] = [joint.raw_rot.w, joint.raw_rot.x, joint.raw_rot.y, joint.raw_rot.z]
        pbone["native_bind_s"] = [joint.raw_scale.x, joint.raw_scale.y, joint.raw_scale.z]
        # Store original bone index for stable export ordering
        pbone["native_bone_index"] = i
        pbone["lol_joint_flags"] = joint.flags
        pbone["lol_joint_radius"] = joint.radius
        # Store original parent name so animation correction can find the right
        # C_parent even if the user reparents bones in Blender
        if joint.parent >= 0:
            pbone["native_parent"] = joints[joint.parent].name
        else:
            pbone["native_parent"] = ""
        # Store native global rest matrix (Blender-space, flat 4x4) so animation
        # correction matrices stay correct even when the user modifies the parent chain
        # (e.g. inserting a bone before the shoulder).  The hierarchy walk in
        # import_anm / export_anm would use the *current* parents with the *original*
        # local transforms, producing wrong globals.  Stored globals avoid that.
        mat = mats[i]
        pbone["native_global_rest_mat"] = [
            mat[0][0], mat[0][1], mat[0][2], mat[0][3],
            mat[1][0], mat[1][1], mat[1][2], mat[1][3],
            mat[2][0], mat[2][1], mat[2][2], mat[2][3],
            mat[3][0], mat[3][1], mat[3][2], mat[3][3],
        ]
        # Store the bone's matrix_local at import time. Used by "Adapt to armature
        # edits" mode (Option B) — keeps V_global stable so corrections don't drift
        # when bones are edited, while rest_v_local follows the current positions.
        ml = armature_obj.data.bones[joint.name].matrix_local
        pbone["native_matrix_local"] = [
            ml[0][0], ml[0][1], ml[0][2], ml[0][3],
            ml[1][0], ml[1][1], ml[1][2], ml[1][3],
            ml[2][0], ml[2][1], ml[2][2], ml[2][3],
            ml[3][0], ml[3][1], ml[3][2], ml[3][3],
        ]

    return armature_obj


def load(operator, context, filepath, bone_orient='VISUAL'):
    try:
        import os
        joints, influences = read_skl(filepath)
        armature_obj = create_armature(joints, bone_orient=bone_orient)
        
        # Store import path for export convenience
        armature_obj["lol_skl_filepath"] = filepath
        armature_obj["lol_skl_filename"] = os.path.basename(filepath)
        
        operator.report({'INFO'}, f'Imported {len(joints)} bones')
        return {'FINISHED'}
    
    except Exception as e:
        operator.report({'ERROR'}, f'Failed: {str(e)}')
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
