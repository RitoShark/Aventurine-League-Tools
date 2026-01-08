import bpy
from bpy.types import Operator
from mathutils import Matrix
import json


class POSE_OT_go_to_bind_pose(Operator):
    """Return armature to its saved bind pose"""
    bl_idname = "pose.go_to_bind_pose"
    bl_label = "Go to Bind Pose"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and
                context.mode == 'POSE')
    
    def execute(self, context):
        armature = context.active_object
        
        # Check if bind pose data exists
        if "lol_bind_pose" not in armature:
            self.report({'WARNING'}, "No bind pose saved for this armature. Use 'Set New Bind Pose' first.")
            return {'CANCELLED'}
        
        # Retrieve the stored bind pose
        bind_pose_json = armature["lol_bind_pose"]
        try:
            bind_pose_data = json.loads(bind_pose_json)
        except:
            self.report({'ERROR'}, "Failed to parse bind pose data")
            return {'CANCELLED'}
        
        # Apply the bind pose using matrix_basis (local transformations)
        # This avoids the iterative distortion caused by world-space matrices
        for pose_bone in armature.pose.bones:
            if pose_bone.name in bind_pose_data:
                matrix_list = bind_pose_data[pose_bone.name]
                # Reconstruct the matrix from the stored list
                matrix = Matrix([
                    matrix_list[0:4],
                    matrix_list[4:8],
                    matrix_list[8:12],
                    matrix_list[12:16]
                ])
                # Use matrix_basis for local transformation (relative to parent)
                # This is the key to instant, accurate restoration
                pose_bone.matrix_basis = matrix
        
        # Single update to refresh the view
        context.view_layer.update()
        
        self.report({'INFO'}, f"Restored bind pose for {armature.name}")
        return {'FINISHED'}


class POSE_OT_set_bind_pose(Operator):
    """Save the current pose as the new bind pose"""
    bl_idname = "pose.set_bind_pose"
    bl_label = "Set New Bind Pose"
    bl_options = {'REGISTER', 'UNDO'}
    
    @classmethod
    def poll(cls, context):
        return (context.active_object and 
                context.active_object.type == 'ARMATURE' and
                context.mode == 'POSE')
    
    def execute(self, context):
        armature = context.active_object
        
        # Store the current pose for all bones using matrix_basis
        bind_pose_data = {}
        for pose_bone in armature.pose.bones:
            # Store matrix_basis (local transformation) as a flat list (4x4 = 16 values)
            # This is the local transformation relative to the parent bone
            matrix = pose_bone.matrix_basis
            matrix_list = []
            for row in matrix:
                matrix_list.extend(row)
            bind_pose_data[pose_bone.name] = matrix_list
        
        # Save to custom property as JSON
        armature["lol_bind_pose"] = json.dumps(bind_pose_data)
        
        self.report({'INFO'}, f"Saved current pose as bind pose for {armature.name}")
        return {'FINISHED'}


def register():
    bpy.utils.register_class(POSE_OT_go_to_bind_pose)
    bpy.utils.register_class(POSE_OT_set_bind_pose)


def unregister():
    bpy.utils.unregister_class(POSE_OT_go_to_bind_pose)
    bpy.utils.unregister_class(POSE_OT_set_bind_pose)
