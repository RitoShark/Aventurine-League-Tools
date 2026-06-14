import bpy
import mathutils
import os
from ..utils.binary_utils import BinaryStream, Hash
from . import import_skl
from . import bone_utils

def write_skl(filepath, armature_obj, disable_scaling=False, disable_transforms=False, use_visual_pose=False,
              clean_names=True, apply_object_transform=True, model_scale=1.0):
    """Write Blender armature to SKL file (Version 0)"""

    # Coordinate conversion matrix P (X-mirror + Y-up to Z-up)
    if disable_transforms:
        P = mathutils.Matrix.Identity(4)
        P_inv = mathutils.Matrix.Identity(4)
    else:
        P = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
        P_inv = P.inverted()

    # The armature's OBJECT transform, expressed in League space, prepended onto
    # every root bone so the exported skeleton matches what's seen in Blender.
    # Without this the export uses armature-LOCAL matrices and silently drops the
    # object's rotation/scale/position — which for FBX/external rigs (where the
    # Y-up→Z-up correction and unit scale live in the object transform) ships the
    # character rotated/mis-scaled. It is an exact no-op when the object is at
    # identity (every normal League import), since O_league becomes identity.
    if apply_object_transform:
        O_league = P_inv @ armature_obj.matrix_world @ P
    else:
        O_league = mathutils.Matrix.Identity(4)
    
    # Get bones sorted by original import order (native bones first, new bones appended)
    bones = armature_obj.pose.bones
    native_bones = []
    new_bones = []
    for b in bones:
        idx = b.get("native_bone_index")
        if idx is not None:
            native_bones.append((int(idx), b))
        else:
            new_bones.append(b)
    native_bones.sort(key=lambda x: x[0])
    bone_list = [b for _, b in native_bones] + new_bones
    bone_name_to_index = {b.name: i for i, b in enumerate(bone_list)}

    # Names to write into the SKL file (and use for hash computation).
    # Normally we strip Blender's .001/.002 suffixes.  However, if the base name
    # already exists as a *different* bone in this export (e.g. the user duplicated
    # "L_Clavicle" to create "L_Clavicle.001" as an intermediate bone), keeping
    # the suffix is essential: without it both bones would be written as "L_Clavicle",
    # and on reimport the second bone would be silently renamed by Blender while
    # Pass 3 still looks it up by the original name — leaving it without a parent.
    all_bone_names_in_export = {b.name for b in bone_list}

    def get_export_name(bone_name):
        # Strip numeric duplicate suffixes only — non-numeric dot suffixes
        # (Hand.L, Bip01.Spine) are part of the bone's identity.
        clean = bone_utils.clean_export_bone_name(bone_name)
        # If the clean base name exists as another bone, keep the full suffix
        if clean != bone_name and clean in all_bone_names_in_export:
            clean = bone_name
        # Clean Names also makes the name Maya/League-legal (dashes, spaces -> _).
        if clean_names:
            clean = bone_utils.sanitize_illegal_chars(clean)
        return clean
    
    joint_count = len(bone_list)

    # --- Pre-calculate Matrices (Recursive) to handle Mix of Native/New bones ---
    # format: [bone_index] = (Matrix_Global_League, Matrix_Local_League)

    league_matrices = {}

    # Native global rest matrix in Blender space (falls back to the visual rest
    # for bones without stored data, e.g. user-added or non-League skeletons).
    # This is the same convention export_anm uses to build correction matrices.
    def get_native_global_blender(pb):
        stored = pb.get("native_global_rest_mat")
        if stored and len(stored) == 16:
            return mathutils.Matrix((stored[0:4], stored[4:8], stored[8:12], stored[12:16]))
        return pb.bone.matrix_local.copy()

    # Duplicates of native bones used as "offset holders" (Shift+D copies the
    # pose-bone custom props, so these would otherwise take the native-bind
    # branch below and silently export the ORIGINAL bone's bind as their own).
    offset_clones = bone_utils.detect_offset_clones(armature_obj)

    # If use_visual_pose, evaluate frame 0 to get the posed transforms
    frame0_matrices = {}
    if use_visual_pose:
        current_frame = bpy.context.scene.frame_current
        bpy.context.scene.frame_set(0)
        bpy.context.view_layer.update()
        for pbone in bone_list:
            # pbone.matrix is the final world-space posed matrix at frame 0
            frame0_matrices[pbone.name] = pbone.matrix.copy()
        bpy.context.scene.frame_set(current_frame)

    def calc_league_matrix(bone_idx):
        if bone_idx in league_matrices:
            return league_matrices[bone_idx]

        pbone = bone_list[bone_idx]

        # 1. Determine Local League Matrix
        nb_t = pbone.get("native_bind_t")
        nb_r = pbone.get("native_bind_r")
        nb_s = pbone.get("native_bind_s")

        clone_target = offset_clones.get(pbone.name) if not use_visual_pose else None

        if clone_target is not None:
            # Duplicated native bone.  When spliced above its original (the
            # "Shift+D, offset, export" workflow), write the AUTHORED world
            # delta (clone placement vs the original bone) conjugated into the
            # parent's native frame.  The original keeps its untouched native
            # bind below (custom-chain adjustment preserves its rest global),
            # so the bind pose stays identical to the authored mesh while every
            # stock animation composes through this local and plays the whole
            # subtree offset by exactly the authored delta.
            # An un-spliced duplicate is just an added bone: export it at its
            # visual placement like any other custom bone.
            target_pb = bones.get(clone_target)
            spliced = (target_pb is not None and target_pb.parent is not None
                       and target_pb.parent.name == pbone.name)
            if spliced and pbone.parent:
                n_p = get_native_global_blender(pbone.parent)
                delta = pbone.bone.matrix_local @ target_pb.bone.matrix_local.inverted()
                try:
                    b_local = n_p.inverted() @ delta @ n_p
                except ValueError:
                    b_local = pbone.bone.matrix_local
                # The clone's scale dial lives in its POSE channel — Blender
                # rest bones are scale-less, so this is the only authoring
                # channel that can survive to export. Composed last (T·R·S),
                # in Blender axes, before the League conversion below.
                ps = pbone.scale
                if abs(ps.x - 1.0) + abs(ps.y - 1.0) + abs(ps.z - 1.0) > 1e-5:
                    b_local = b_local @ mathutils.Matrix.Diagonal((ps.x, ps.y, ps.z, 1.0))
            elif pbone.parent:
                try:
                    b_local = get_native_global_blender(pbone.parent).inverted() @ pbone.bone.matrix_local
                except ValueError:
                    b_local = pbone.bone.matrix_local
            else:
                b_local = pbone.bone.matrix_local
            l_mat_local = P_inv @ b_local @ P
        elif nb_t and nb_r and nb_s and not use_visual_pose:
            # Use Original Native Bind (Preserves offsets and orientation)
            # This ignores Blender's visual edits to Head/Tail, which is GOOD for game compatibility
            lm_t = mathutils.Matrix.Translation(mathutils.Vector(nb_t))
            lm_r = mathutils.Quaternion(nb_r).to_matrix().to_4x4()
            lm_s = mathutils.Matrix.Diagonal((nb_s[0], nb_s[1], nb_s[2], 1.0))
            l_mat_local = lm_t @ lm_r @ lm_s

            # When custom bones were inserted between this native bone and its original
            # parent (e.g. R_Clavicle.001 between Chest and R_Clavicle), the stored
            # native_bind is relative to the native parent — not to the new Blender parent.
            # Without adjustment the computed global picks up an extra custom-bone
            # transform, breaking the inverse-bind matrix and skinning for the entire
            # subtree.  Adjust l_mat_local so the global stays identical to the original.
            native_parent_name = pbone.get("native_parent", "")
            if pbone.parent and native_parent_name and pbone.parent.name != native_parent_name:
                np_idx = bone_name_to_index.get(native_parent_name)
                bp_idx = bone_name_to_index.get(pbone.parent.name)
                if np_idx is not None and bp_idx is not None:
                    np_global, _ = calc_league_matrix(np_idx)
                    bp_global, _ = calc_league_matrix(bp_idx)
                    try:
                        # custom_chain = accumulated League transform from native parent
                        # to the Blender parent (the custom-bone chain).
                        # new_local = custom_chain⁻¹ @ original_local ensures:
                        #   blender_parent.global @ new_local = native_parent.global @ original_local
                        custom_chain = np_global.inverted() @ bp_global
                        l_mat_local = custom_chain.inverted() @ l_mat_local
                    except ValueError:
                        pass  # singular — keep original local unchanged
        elif use_visual_pose and pbone.name in frame0_matrices:
            # Experimental: Use the posed transform at frame 0
            b_global = frame0_matrices[pbone.name]
            if pbone.parent and pbone.parent.name in frame0_matrices:
                parent_global = frame0_matrices[pbone.parent.name]
                try:
                    b_local = parent_global.inverted() @ b_global
                except ValueError:
                    b_local = b_global
            else:
                b_local = b_global

            # Convert to League: L = P_inv @ B @ P
            l_mat_local = P_inv @ b_local @ P
        else:
            # New Bone or Missing Data: Convert Blender Rest Pose to League.
            #
            # The local must be expressed in the parent's NATIVE frame, because
            # the parent global it gets composed onto below was reconstructed
            # from native bind data — NOT from the parent's visual (Blender)
            # orientation, which differs from native by the per-bone correction
            # rotation (tails/rolls are cosmetic on import).  This mirrors what
            # export_anm writes for custom bones (n_local = C_parent @ v_local,
            # which at rest equals N_parent⁻¹ @ V_bone), so the bone's SKL bind
            # matches both its visual placement and its exported ANM tracks.
            # For skeletons without stored native data (e.g. FBX imports) the
            # native frame falls back to the visual frame, leaving them unchanged.
            if pbone.parent:
                try:
                    b_local = get_native_global_blender(pbone.parent).inverted() @ pbone.bone.matrix_local
                except ValueError:
                    b_local = pbone.bone.matrix_local
            else:
                b_local = pbone.bone.matrix_local

            # Convert to League: L = P_inv @ B @ P
            l_mat_local = P_inv @ b_local @ P

        # 2. Determine Global League Matrix
        parent_idx = bone_name_to_index.get(pbone.parent.name) if pbone.parent else -1

        if parent_idx >= 0:
            parent_global, _ = calc_league_matrix(parent_idx)
            l_mat_global = parent_global @ l_mat_local
        else:
            # Root bone: fold in the armature's object transform (identity when
            # apply_object_transform is off or the object is at identity). Its
            # local IS its global, and descendants chain from it — so doing this
            # only on roots transforms the whole skeleton while leaving every
            # child's parent-relative local (which is object-transform invariant)
            # untouched.
            l_mat_local = O_league @ l_mat_local
            l_mat_global = l_mat_local

        league_matrices[bone_idx] = (l_mat_global, l_mat_local)
        return l_mat_global, l_mat_local

    # Compute all
    for i in range(joint_count):
        calc_league_matrix(i)

    with open(filepath, 'wb') as f:
        bs = BinaryStream(f)
        
        # 1. Header 
        bs.write_uint32(0) # Resource size placeholder
        bs.write_uint32(0x22FD4FC3) # Magic
        bs.write_uint32(0) # Version
        
        bs.write_uint16(0) # Flags
        bs.write_uint16(joint_count)
        bs.write_uint32(joint_count) # Influence count
        
        # Offsets
        joints_offset = 64
        joint_indices_offset = joints_offset + joint_count * 100
        influences_offset = joint_indices_offset + joint_count * 8
        joint_names_offset = influences_offset + joint_count * 2
        
        bs.write_int32(joints_offset, joint_indices_offset, influences_offset, 0, 0, joint_names_offset)
        
        # 5 Reserved offsets
        for _ in range(5):
            bs.write_uint32(0xFFFFFFFF)
            
        # 2. Names
        name_offsets = {}
        bs.seek(joint_names_offset)
        for i, pbone in enumerate(bone_list):
            name_offsets[i] = bs.tell()
            name = get_export_name(pbone.name)
            bs.write_ascii(name)
            bs.write_uint8(0)
            
        # 3. Joint Data
        bs.seek(joints_offset)
        for i, pbone in enumerate(bone_list):
            bs.write_uint16(0) # flags
            bs.write_uint16(i) # id
            
            parent_idx = -1
            if pbone.parent:
                parent_idx = bone_name_to_index.get(pbone.parent.name, -1)
            bs.write_int16(parent_idx)
            
            bs.write_uint16(0) # flags
            bs.write_uint32(Hash.elf(get_export_name(pbone.name)))
            bs.write_float(2.1) # radius
            
            # Retrieve calculated matrices
            l_mat_global, l_mat_local = league_matrices[i]
            
            # Local Transform (TRS)
            l_t, l_r, l_s = l_mat_local.decompose()

            # Scale translations back to game units (native_bind_t is at 0.01 scale).
            # model_scale is a uniform size multiplier — keep it matched to the SKN
            # and ANM Scale so a shrunk/grown export stays internally consistent.
            scale = (1.0 if disable_scaling else import_skl.EXPORT_SCALE) * model_scale
            bs.write_vec3(l_t * scale)
            bs.write_vec3(l_s)
            bs.write_quat(l_r)
            
            # Inversed Global Transform (Bind Matrix Inv)
            # Some LoL bones have zero scale (used to hide bones), making the global
            # matrix singular. Fall back to identity since no verts are weighted to them.
            try:
                ig_mat = l_mat_global.inverted()
            except ValueError:
                ig_mat = mathutils.Matrix.Identity(4)
            ig_t, ig_r, ig_s = ig_mat.decompose()
            
            bs.write_vec3(ig_t * scale)
            bs.write_vec3(ig_s)
            bs.write_quat(ig_r)
            
            # Name offset
            curr_pos = bs.tell()
            bs.write_int32(name_offsets[i] - curr_pos)
            
        # 4. Indices and Influences
        bs.seek(joint_indices_offset)
        for i, pbone in enumerate(bone_list):
            bs.write_uint16(i)
            bs.write_uint16(0)
            bs.write_uint32(Hash.elf(get_export_name(pbone.name)))
            
        bs.seek(influences_offset)
        for i in range(joint_count):
            bs.write_uint16(i)
            
        # 6. Finalize Size
        total_size = bs.tell()
        bs.seek(0)
        bs.write_uint32(total_size)
        
    return True

def save(operator, context, filepath, target_armature=None, disable_scaling=False, disable_transforms=False, use_visual_pose=False, clean_names=True, apply_object_transform=True, model_scale=1.0):
    armature_obj = target_armature

    if not armature_obj:
        armature_obj = context.active_object
        if not armature_obj or armature_obj.type != 'ARMATURE':
            # Try to find an armature in the scene
            armature_obj = next((obj for obj in context.scene.objects if obj.type == 'ARMATURE'), None)

    if not armature_obj:
        operator.report({'ERROR'}, "No Armature found in scene")
        return {'CANCELLED'}

    try:
        for name in bone_utils.find_unbaked_pose_offsets(armature_obj):
            operator.report({'WARNING'},
                            f"'{name}' has an unbaked Pose Mode offset that will NOT export. "
                            f"Use 'Set Offset from Pose' (N-panel) or move it in Edit Mode.")
        write_skl(filepath, armature_obj, disable_scaling, disable_transforms, use_visual_pose,
                  clean_names=clean_names, apply_object_transform=apply_object_transform, model_scale=model_scale)
        operator.report({'INFO'}, f"Exported SKL: {filepath}")
        return {'FINISHED'}
    except Exception as e:
        operator.report({'ERROR'}, f"Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return {'CANCELLED'}
