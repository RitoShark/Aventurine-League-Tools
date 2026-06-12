"""
LoL Animation Retarget
Transfer animations between LoL skeletons with different bone structures
"""

import bpy
import re
import mathutils
from bpy.types import Panel, Operator, PropertyGroup
from bpy.props import StringProperty, PointerProperty, CollectionProperty, EnumProperty
from ..ui import icons


# Common bone name aliases for LoL rigs
BONE_ALIASES = {
    # Spine variations
    'spine3': ['chest', 'buffbone_glb_chest', 'c_chest'],
    'chest': ['spine3', 'buffbone_glb_chest', 'c_spine3'],
    'buffbone_glb_chest': ['chest', 'spine3', 'c_chest'],
    
    # Root variations  
    'root': ['pelvis', 'c_pelvis', 'c_root'],
    'pelvis': ['root', 'c_root', 'c_pelvis'],
    
    # Common prefixed versions
    'c_spine': ['spine', 'spine1'],
    'c_spine1': ['spine1', 'spine'],
    'c_spine2': ['spine2'],
}


def normalize_bone_name(name):
    """Normalize bone name for comparison - remove common prefixes and lowercase"""
    name = name.lower()
    # Remove common prefixes
    prefixes = ['c_', 'l_', 'r_', 'buffbone_', 'glb_', 'cstm_']
    for prefix in prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name


def bone_side(name):
    """'L', 'R' or '' — which side of the body a bone name refers to."""
    n = name.lower()
    if n.startswith(('l_', 'left_', 'left')) or n.endswith(('_l', '.l')):
        return 'L'
    if n.startswith(('r_', 'right_', 'right')) or n.endswith(('_r', '.r')):
        return 'R'
    return ''


def find_best_match(source_bone_name, target_bones):
    """Find the best matching target bone for a source bone"""
    source_normalized = normalize_bone_name(source_bone_name)
    source_side = bone_side(source_bone_name)

    def side_ok(target_name):
        # Never match across body sides (normalization strips L_/R_ prefixes,
        # which would otherwise let a missing R_Elbow grab L_Elbow).
        return bone_side(target_name) == source_side

    # Priority 1: Exact name match
    if source_bone_name in target_bones:
        return source_bone_name

    # Priority 2: Normalized name match
    for target_name in target_bones:
        if side_ok(target_name) and normalize_bone_name(target_name) == source_normalized:
            return target_name

    # Priority 3: Check aliases
    source_lower = source_bone_name.lower()
    if source_lower in BONE_ALIASES:
        for alias in BONE_ALIASES[source_lower]:
            for target_name in target_bones:
                if side_ok(target_name) and (target_name.lower() == alias or normalize_bone_name(target_name) == alias):
                    return target_name

    # Priority 4: Partial match (contains)
    for target_name in target_bones:
        if side_ok(target_name) and (source_normalized in normalize_bone_name(target_name)
                                     or normalize_bone_name(target_name) in source_normalized):
            return target_name

    return None  # No match found


class BoneMappingItem(PropertyGroup):
    """Single bone mapping entry"""
    source_bone: StringProperty(name="Source Bone")
    target_bone: StringProperty(name="Target Bone")
    enabled: bpy.props.BoolProperty(name="Enabled", default=True)


class LOLRetargetProperties(PropertyGroup):
    """Properties for the retarget panel"""
    source_armature: PointerProperty(
        name="Source Armature",
        description="Armature with the animation to copy",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    target_armature: PointerProperty(
        name="Target Armature", 
        description="Armature to apply the animation to",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    bone_mappings: CollectionProperty(type=BoneMappingItem)
    map_root_to_pelvis: bpy.props.BoolProperty(
        name="Map Root to Pelvis",
        description="Map Source Root to Target Pelvis/Hip, and skip Source Pelvis. Useful if Source uses Root for movement but Target uses Pelvis.",
        default=False
    )
    ignore_extras: bpy.props.BoolProperty(
        name="Ignore Extra Bones",
        description="Exclude Buffbones, Hair, Face, Weapon, etc. from mapping",
        default=True
    )
    mapping_generated: bpy.props.BoolProperty(default=False)
    active_mapping_index: bpy.props.IntProperty(default=0)
    transfer_mode: EnumProperty(
        name="Transfer",
        description="How the animation is transferred onto the target",
        items=[
            ('WORLD', "World Bake (same skeleton)",
             "Exact world-space transfer for rigs sharing the same skeleton "
             "(e.g. an FBX import and an SKL import of the same character, or a "
             "full vs joint-reduced skeleton). Axis, scale and mirror differences "
             "between the rigs are detected and handled automatically"),
            ('ROTATION', "Rotations Only (different skeleton)",
             "Transfer world-space bone rotations onto a skeleton with different "
             "proportions; the target keeps its own bone lengths. Top-level mapped "
             "bones (root/hips) also receive position, scaled to the target's size"),
            ('RAW', "Raw Curve Copy (legacy)",
             "Copy keyframe values directly between mapped bones. Only correct "
             "when both rigs have identical rest poses"),
        ],
        default='WORLD'
    )


class LOL_OT_GenerateMapping(Operator):
    """Generate automatic bone mapping between source and target"""
    bl_idname = "lol_retarget.generate_mapping"
    bl_label = "Generate Mapping"
    bl_description = "Auto-detect bone mapping between source and target armatures"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.lol_retarget
        
        if not props.source_armature or not props.target_armature:
            self.report({'ERROR'}, "Select both source and target armatures")
            return {'CANCELLED'}
        
        if props.source_armature == props.target_armature:
            self.report({'ERROR'}, "Source and target must be different armatures")
            return {'CANCELLED'}
        
        source_bones = [b.name for b in props.source_armature.data.bones]
        target_bones = [b.name for b in props.target_armature.data.bones]
        
        # Clear existing mappings
        props.bone_mappings.clear()
        
        matched = 0
        unmatched = 0
        
        # Core Bone Whitelist (Normalized)
        # These are the ONLY bones allowed when strict mode (Ignore Extras) is on
        core_bones = [
            'root', 'pelvis', 'hip', 'spine', 'spine1', 'spine2', 'spine3', 'chest', 'neck', 'head',
            'clavicle', 'shoulder', 'elbow', 'hand',
            'thumb1', 'thumb2', 'thumb3', 
            'index1', 'index2', 'index3', 
            'middle1', 'middle2', 'middle3', 
            'ring1', 'ring2', 'ring3', 
            'pinky1', 'pinky2', 'pinky3',
            'knee', 'kneelower', 'kneeupper', 'foot', 'toe', 'ball'
            # Removed facial bones (jaw, eye, mouth etc) as per request
        ]
        
        # Helper to check if name is core bone
        def is_core_bone(name):
            # Strict rejection of buffbones/helpers
            if 'buffbone' in name.lower() or 'helper' in name.lower():
                return False
                
            norm = normalize_bone_name(name)
            # Check exact list
            if norm in core_bones:
                return True
            # Check prefix variations (e.g. L_Knee is core if 'knee' is core)
            for core in core_bones:
                if norm == core:
                    return True
            return False

        for source_bone in source_bones:
            item = props.bone_mappings.add()
            item.source_bone = source_bone
            
            norm_name = normalize_bone_name(source_bone)
            
            # STRICT MODE: Only allow whitelisted bones
            if props.ignore_extras:
                if not is_core_bone(source_bone):
                    item.target_bone = ""
                    item.enabled = False
                    unmatched += 1
                    continue
            
            # Special handling for Root -> Pelvis
            if props.map_root_to_pelvis:
                # If this is the Source Pelvis/Hip, skip it
                if norm_name in ['pelvis', 'hip']:
                    item.target_bone = ""
                    item.enabled = False
                    unmatched += 1
                    continue
                # If this is the Source Root, try to find Target Pelvis/Hip
                if norm_name == 'root':
                    # Look for pelvis/hip in target
                    found_pelvis = False
                    for t_bone in target_bones:
                        if normalize_bone_name(t_bone) in ['pelvis', 'hip']:
                            item.target_bone = t_bone
                            item.enabled = True
                            matched += 1
                            found_pelvis = True
                            break
                    if found_pelvis:
                        continue
            
            # Find match using standard fuzzy/alias logic
            match = find_best_match(source_bone, target_bones)
            
            if match:
                # Extra safety for strict mode: Target must also be a core bone
                if props.ignore_extras:
                    if not is_core_bone(match):
                        item.target_bone = ""
                        item.enabled = False
                        unmatched += 1
                        continue
                    
                    # Prevent partial overlapping names like "Hip_Helper" matching "Hip"
                    # If we are here, both source and target are "core bones" roughly speaking,
                    # but normalize_bone_name("L_Hip_Helper") -> "Hip_Helper" which is NOT in core_bones list.
                    # Wait, my is_core_bone function logic above handles this naturally:
                    # "Hip_Helper" norm is "hip_helper", which isn't in core_bones list.
                    # So "L_Hip_Helper" would be rejected at the start of the loop.
                    
                    pass 

                item.target_bone = match
                item.enabled = True
                matched += 1
            else:
                item.target_bone = ""
                item.enabled = False
                unmatched += 1
        
        props.mapping_generated = True
        self.report({'INFO'}, f"Mapping generated: {matched} matched, {unmatched} unmatched")
        return {'FINISHED'}


def _fit_alignment(point_pairs):
    """Best-fit similarity transform A (4x4) mapping source rest positions onto
    target rest positions (Umeyama). Reflections are permitted — rigs imported
    through different pipelines are frequently mirrored relative to each other
    (the League axis conversion contains an X-mirror)."""
    import numpy as np
    if len(point_pairs) < 3:
        return mathutils.Matrix.Identity(4)
    sp = np.array([[p[0].x, p[0].y, p[0].z] for p in point_pairs])
    tp = np.array([[p[1].x, p[1].y, p[1].z] for p in point_pairs])
    cs, ct = sp.mean(axis=0), tp.mean(axis=0)
    s0, t0 = sp - cs, tp - ct
    var = float((s0 ** 2).sum())
    if var < 1e-12:
        return mathutils.Matrix.Identity(4)
    H = s0.T @ t0
    U, S, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T                       # best orthogonal map (may be a reflection)
    scale = float(S.sum()) / var
    t = ct - scale * (R @ cs)
    A = mathutils.Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            A[i][j] = scale * float(R[i][j])
        A[i][3] = float(t[i])
    return A


def _bake_retarget(context, props, operator):
    """World-space transfer: per frame, map each source bone's delta-from-rest
    through the rig alignment and re-express it on the target.

    WORLD mode transfers the full transform (exact for same-skeleton rigs;
    bones missing on a reduced target fold their motion into descendants).
    ROTATION mode transfers rotations only — the target keeps its own bone
    lengths — plus aligned positions for top-level mapped bones (root/hips).
    """
    mode = props.transfer_mode
    src_obj, tgt_obj = props.source_armature, props.target_armature
    src_action = src_obj.animation_data.action

    mapping = {}
    for item in props.bone_mappings:
        if (item.enabled and item.target_bone
                and item.source_bone in src_obj.pose.bones
                and item.target_bone in tgt_obj.pose.bones):
            mapping[item.source_bone] = item.target_bone
    if not mapping:
        operator.report({'ERROR'}, "No valid bone mappings")
        return False
    target_to_source = {}
    for s, t in mapping.items():
        if t in target_to_source:
            operator.report({'WARNING'}, f"Multiple sources map to '{t}' — using '{s}'")
        target_to_source[t] = s

    M_src = src_obj.matrix_world.copy()
    M_tgt = tgt_obj.matrix_world.copy()
    M_tgt_inv = M_tgt.inverted()
    src_rest = {b.name: M_src @ b.matrix_local for b in src_obj.data.bones}
    tgt_rest = {b.name: M_tgt @ b.matrix_local for b in tgt_obj.data.bones}
    tgt_ml = {b.name: b.matrix_local.copy() for b in tgt_obj.data.bones}

    A = _fit_alignment([(src_rest[s].to_translation(), tgt_rest[t].to_translation())
                        for s, t in mapping.items()])
    A_inv = A.inverted()
    A3 = A.to_3x3()          # uniform scale cancels in conjugation
    A3_inv = A3.inverted()

    # Hierarchy order and top-level mapped bones (no mapped ancestor).
    order = []
    def _walk(b):
        order.append(b.name)
        for c in b.children:
            _walk(c)
    for b in tgt_obj.data.bones:
        if b.parent is None:
            _walk(b)

    def _has_mapped_ancestor(name):
        p = tgt_obj.data.bones[name].parent
        while p is not None:
            if p.name in target_to_source:
                return True
            p = p.parent
        return False
    top_mapped = {t for t in target_to_source if not _has_mapped_ancestor(t)}

    # Fresh (or reused) action on the target.
    new_name = f"{src_action.name}_retargeted"
    new_action = bpy.data.actions.get(new_name)
    if new_action:
        new_action.fcurves.clear()
    else:
        new_action = bpy.data.actions.new(name=new_name)
    if not tgt_obj.animation_data:
        tgt_obj.animation_data_create()
    tgt_obj.animation_data.action = new_action
    for pb in tgt_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'

    frame_start = int(src_action.frame_range[0])
    frame_end = int(src_action.frame_range[1])
    current_frame = context.scene.frame_current

    try:
        for f in range(frame_start, frame_end + 1):
            context.scene.frame_set(f)
            context.view_layer.update()
            src_world = {s: M_src @ src_obj.pose.bones[s].matrix for s in mapping}

            desired = {}     # target bone name -> desired WORLD matrix this frame
            for tname in order:
                parent = tgt_obj.data.bones[tname].parent
                if parent is not None:
                    rest_local = tgt_rest[parent.name].inverted() @ tgt_rest[tname]
                    hold = desired[parent.name] @ rest_local
                else:
                    rest_local = None
                    hold = tgt_rest[tname].copy()

                s_name = target_to_source.get(tname)
                if s_name is None:
                    desired[tname] = hold      # unmapped bones hold rest
                    continue

                W_s = src_world[s_name]
                if mode == 'WORLD':
                    try:
                        delta = W_s @ src_rest[s_name].inverted()
                    except ValueError:
                        desired[tname] = hold
                        continue
                    desired[tname] = A @ delta @ A_inv @ tgt_rest[tname]
                else:  # ROTATION
                    try:
                        r_delta = A3 @ (W_s.to_3x3() @ src_rest[s_name].to_3x3().inverted()) @ A3_inv
                    except ValueError:
                        desired[tname] = hold
                        continue
                    r_world = r_delta @ tgt_rest[tname].to_3x3()
                    if tname in top_mapped:
                        pos = A @ W_s.to_translation()
                    else:
                        pos = hold.to_translation()
                    m = r_world.to_4x4()
                    m.translation = pos
                    desired[tname] = m

            # Convert to pose-channel values and keyframe (mapped bones only).
            for tname in order:
                if tname not in target_to_source:
                    continue
                parent = tgt_obj.data.bones[tname].parent
                M_arm = M_tgt_inv @ desired[tname]
                if parent is not None:
                    M_p_arm = M_tgt_inv @ desired[parent.name]
                    rest_local_arm = tgt_ml[parent.name].inverted() @ tgt_ml[tname]
                    try:
                        basis = rest_local_arm.inverted() @ M_p_arm.inverted() @ M_arm
                    except ValueError:
                        continue
                else:
                    try:
                        basis = tgt_ml[tname].inverted() @ M_arm
                    except ValueError:
                        continue
                loc, rot, sca = basis.decompose()
                pb = tgt_obj.pose.bones[tname]
                pb.location = loc
                pb.rotation_quaternion = rot
                pb.scale = sca if mode == 'WORLD' else (1.0, 1.0, 1.0)
                pb.keyframe_insert("location", frame=f)
                pb.keyframe_insert("rotation_quaternion", frame=f)
                pb.keyframe_insert("scale", frame=f)
    finally:
        context.scene.frame_set(current_frame)
        context.view_layer.update()

    operator.report({'INFO'},
                    f"Baked {len(target_to_source)} bones over frames "
                    f"{frame_start}-{frame_end} to '{new_action.name}' ({mode})")
    return True


class LOL_OT_ApplyRetarget(Operator):
    """Apply the animation from source to target using the bone mapping"""
    bl_idname = "lol_retarget.apply"
    bl_label = "Apply Retarget"
    bl_description = "Transfer animation from source to target armature"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.lol_retarget
        
        if not props.source_armature or not props.target_armature:
            self.report({'ERROR'}, "Select both source and target armatures")
            return {'CANCELLED'}
        
        if not props.mapping_generated:
            self.report({'ERROR'}, "Generate mapping first")
            return {'CANCELLED'}
        
        source_arm = props.source_armature
        target_arm = props.target_armature
        
        if source_arm == target_arm:
            self.report({'ERROR'}, "Source and Target must be different armatures")
            return {'CANCELLED'}
        
        # Check source has animation
        if not source_arm.animation_data or not source_arm.animation_data.action:
            self.report({'ERROR'}, "Source armature has no animation")
            return {'CANCELLED'}

        # World-space bake modes (the legacy raw curve copy continues below)
        if props.transfer_mode in {'WORLD', 'ROTATION'}:
            ok = _bake_retarget(context, props, self)
            return {'FINISHED'} if ok else {'CANCELLED'}

        source_action = source_arm.animation_data.action
        
        # Handle Action Creation (Reuse or Create)
        new_action_name = f"{source_action.name}_retargeted"
        new_action = bpy.data.actions.get(new_action_name)
        
        if new_action:
            # Clear existing action data to overwrite
            new_action.fcurves.clear()
        else:
            # Create new
            new_action = bpy.data.actions.new(name=new_action_name)
        
        # Ensure target has animation data
        if not target_arm.animation_data:
            target_arm.animation_data_create()
        target_arm.animation_data.action = new_action
        
        # Force Target to Pose Mode to see results
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')
        context.view_layer.objects.active = target_arm
        target_arm.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')
        
        # Set target bones to quaternion mode
        for pbone in target_arm.pose.bones:
            pbone.rotation_mode = 'QUATERNION'
        
        # Build mapping dict
        mapping = {}
        for item in props.bone_mappings:
            if item.enabled and item.target_bone:
                mapping[item.source_bone] = item.target_bone
        
        # Copy keyframes
        copied_curves = 0
        
        for fcurve in source_action.fcurves:
            # Parse the data path to get bone name
            # Format: pose.bones["BoneName"].location/rotation_quaternion/scale
            match = re.match(r'pose\.bones\["(.+?)"\]\.(.+)', fcurve.data_path)
            if not match:
                continue
            
            source_bone_name = match.group(1)
            property_name = match.group(2)
            
            # Check if this bone is mapped
            if source_bone_name not in mapping:
                continue
            
            target_bone_name = mapping[source_bone_name]
            
            # Check target bone exists
            if target_bone_name not in target_arm.pose.bones:
                continue
            
            # Create new fcurve for target - check if exists first
            new_data_path = f'pose.bones["{target_bone_name}"].{property_name}'
            
            # Check if this curve already exists
            existing_curve = new_action.fcurves.find(new_data_path, index=fcurve.array_index)
            
            if existing_curve:
                # If it exists, clear its keyframes so we overwrite it properly
                existing_curve.keyframe_points.clear()
                new_fcurve = existing_curve
            else:
                # Create new
                new_fcurve = new_action.fcurves.new(data_path=new_data_path, index=fcurve.array_index)
            
            # Copy all keyframes
            for kp in fcurve.keyframe_points:
                new_fcurve.keyframe_points.insert(kp.co.x, kp.co.y)
            
            copied_curves += 1
        
        # Final update
        context.view_layer.update()
        
        self.report({'INFO'}, f"Retargeted to {target_arm.name} (Action: {new_action_name})")
        return {'FINISHED'}


class LOL_OT_ClearMapping(Operator):
    """Clear the current bone mapping"""
    bl_idname = "lol_retarget.clear_mapping"
    bl_label = "Clear Mapping"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        props = context.scene.lol_retarget
        props.bone_mappings.clear()
        props.mapping_generated = False
        self.report({'INFO'}, "Mapping cleared")
        return {'FINISHED'}


class LOL_UL_BoneMapping(bpy.types.UIList):
    """UI List for displaying bone mappings"""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Draw each row
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            
            # Checkbox
            row.prop(item, "enabled", text="")
            
            # Source Bone (Label)
            row.label(text=item.source_bone)
            
            # Arrow
            row.label(text="", icon='FORWARD')
            
            # Target Bone (Editable)
            if item.target_bone:
                row.prop(item, "target_bone", text="", icon='BONE_DATA')
            else:
                row.prop(item, "target_bone", text="", icon='ERROR', placeholder="No Match")
                
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text=item.source_bone)


class LOL_PT_RetargetPanel(Panel):
    """Animation Retarget Panel"""
    bl_label = "LoL Retarget"
    bl_idname = "VIEW3D_PT_lol_retarget"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Misc LoL Tools'
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon_value=icons.get_icon("icon_51"))
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.lol_retarget
        
        # Armature Selection
        box = layout.box()
        box.label(text="Armatures", icon='ARMATURE_DATA')
        box.prop(props, "source_armature", text="Source (has anim)")
        box.prop(props, "target_armature", text="Target (receives anim)")
        
        # Settings
        box = layout.box()
        box.label(text="Options", icon='PREFERENCES')
        box.prop(props, "map_root_to_pelvis", text="Map Source Root → Target Hip")
        box.prop(props, "ignore_extras", text="Ignore Extras (Buffbones/Hair)")
        
        # Actions
        row = layout.row(align=True)
        row.operator("lol_retarget.generate_mapping", text="Generate Mapping", icon='FILE_REFRESH')
        row.operator("lol_retarget.clear_mapping", text="", icon='X')
        
        # Mapping Preview
        if props.mapping_generated and len(props.bone_mappings) > 0:
            box = layout.box()
            box.label(text="Bone Mapping", icon='BONE_DATA')
            
            # Count stats
            matched = sum(1 for m in props.bone_mappings if m.target_bone and m.enabled)
            total = len(props.bone_mappings)
            box.label(text=f"Matched: {matched}/{total}")
            
            # Scrolable UI List
            row = box.row()
            row.template_list("LOL_UL_BoneMapping", "", props, "bone_mappings", props, "active_mapping_index", rows=10)
        
        # Transfer mode + Apply Button
        layout.separator()
        layout.prop(props, "transfer_mode", text="Mode")
        row = layout.row()
        row.scale_y = 1.5
        row.operator("lol_retarget.apply", text="Apply Retarget", icon='PLAY')


# Registration
classes = [
    BoneMappingItem,
    LOLRetargetProperties,
    LOL_OT_GenerateMapping,
    LOL_OT_ApplyRetarget,
    LOL_OT_ClearMapping,
    LOL_UL_BoneMapping,
    LOL_PT_RetargetPanel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.lol_retarget = PointerProperty(type=LOLRetargetProperties)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.lol_retarget
