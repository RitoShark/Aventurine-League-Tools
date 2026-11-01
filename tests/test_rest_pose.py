"""Rest-pose binding integration tests, mixed into test_format_sweep."""
from mathutils import Vector
from test_backend import bpy, rs, packed, import_skl, import_skn
from aventurine.tools import rest_pose
from aventurine.io import skin_normals
import types


class RestPoseChecks:
    def fitted_fixture(self):
        joints,palette=import_skl.read_skl(self.skeleton())
        arm=import_skl.create_armature(joints)
        path=self.directory/'mesh.skn'
        rs.Skn.new(positions=packed('f',[(0,100,0),(40,100,0),(0,140,0)]),
            normals=packed('f',[(0,0,1)]*3),uvs=packed('f',[(0,0),(1,0),(0,1)]),
            blend_indices=packed('I',[(0,0,0,0)]*3),blend_weights=packed('f',[(1,0,0,0)]*3),
            indices=packed('I',[(0,1,2)]),submeshes=[rs.Submesh('prop',0,3,0,3)]).to_path(str(path))
        mesh=import_skn.create_mesh(*import_skn.read_skn(path),'prop',arm,joints,palette)
        bpy.context.view_layer.objects.active=arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')
        return arm,mesh

    def snapshot_evaluated(self,obj):
        bpy.context.view_layer.update()
        evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get());mesh=evaluated.to_mesh()
        try:return [v.co.copy() for v in mesh.vertices]
        finally:evaluated.to_mesh_clear()

    def test_rest_bake_keeps_hidden_bound_meshes_and_unbound_shared_mesh(self):
        arm,visible=self.fitted_fixture()
        hidden=visible.copy();hidden.data=visible.data.copy();bpy.context.scene.collection.objects.link(hidden)
        unbound=bpy.data.objects.new('Unbound replacement',visible.data)
        bpy.context.scene.collection.objects.link(unbound)
        original=[v.co.copy() for v in visible.data.vertices]
        weights=[[(g.group,g.weight) for g in v.groups] for v in visible.data.vertices]
        uv=[tuple(x.uv) for x in visible.data.uv_layers.active.data]
        p=arm.pose.bones['child'];p.rotation_mode='XYZ';p.rotation_euler=(.2,.3,.4);p.location=(.15,.1,.05)
        expected=self.snapshot_evaluated(visible)
        self.assertGreater(max((a-b).length for a,b in zip(original,expected)),.1)
        hidden.hide_set(True);hidden.hide_viewport=True
        self.assertEqual(rest_pose.apply_preserving_meshes(bpy.context,arm),2)
        for obj in (visible,hidden):
            for a,b in zip(expected,obj.data.vertices):self.assertVector(b.co,a)
            self.assertEqual(weights,[[(g.group,g.weight) for g in v.groups] for v in obj.data.vertices])
            self.assertEqual(uv,[tuple(x.uv) for x in obj.data.uv_layers.active.data])
            self.assertEqual(obj.modifiers[0].object,arm)
            self.assertIsNotNone(skin_normals.preserved(obj.data))
            self.assertFalse(obj.data.has_custom_normals)
            self.assertFalse(any(p.use_smooth for p in obj.data.polygons))
        self.assertTrue(hidden.hide_get());self.assertTrue(hidden.hide_viewport)
        self.assertEqual(original,[v.co for v in unbound.data.vertices])
        for a,b in zip(expected,self.snapshot_evaluated(visible)):self.assertVector(a,b)
        hidden.hide_set(False);hidden.hide_viewport=False
        # A second neutral commit must not deform the mesh again.
        rest_pose.apply_preserving_meshes(bpy.context,arm)
        for a,b in zip(expected,self.snapshot_evaluated(visible)):self.assertVector(a,b)

    def test_rest_bake_handles_excluded_collection_and_downstream_modifier(self):
        arm,obj=self.fitted_fixture()
        collection=bpy.data.collections.new('Excluded props');bpy.context.scene.collection.children.link(collection)
        for old in list(obj.users_collection):old.objects.unlink(obj)
        collection.objects.link(obj)
        bpy.context.view_layer.layer_collection.children[collection.name].exclude=True
        modifier=obj.modifiers.new('Keep downstream','SOLIDIFY');modifier.thickness=.05
        p=arm.pose.bones['child'];p.location=(.3,0,0)
        original=[v.co.copy() for v in obj.data.vertices]
        rest_pose.apply_preserving_meshes(bpy.context,arm)
        self.assertGreater(max((v.co-co).length for v,co in zip(obj.data.vertices,original)),.2)
        self.assertEqual(len(obj.data.vertices),3)
        self.assertEqual(len(obj.modifiers),2)
        self.assertEqual(obj.modifiers[1].type,'SOLIDIFY')
        self.assertTrue(bpy.context.view_layer.layer_collection.children[collection.name].exclude)
        # Restore inclusion so the suite's object cleanup can see it.
        bpy.context.view_layer.layer_collection.children[collection.name].exclude=False

    def test_rest_bake_rejects_unsupported_mesh_before_changing_anything(self):
        arm,obj=self.fitted_fixture();obj.shape_key_add(name='Basis')
        p=arm.pose.bones['child'];p.location=(.3,0,0)
        before=p.bone.matrix_local.copy();positions=[v.co.copy() for v in obj.data.vertices]
        with self.assertRaisesRegex(ValueError,'shape keys'):rest_pose.apply_preserving_meshes(bpy.context,arm)
        self.assertEqual(before,p.bone.matrix_local)
        self.assertVector(p.location,(.3,0,0))
        self.assertEqual(positions,[v.co for v in obj.data.vertices])

    def test_rest_bake_rolls_back_when_result_cannot_be_exported(self):
        from unittest.mock import patch
        arm,obj=self.fitted_fixture();p=arm.pose.bones['child'];p.location=(.3,0,0)
        bpy.context.view_layer.update()
        before=p.bone.matrix_local.copy();positions=[v.co.copy() for v in obj.data.vertices]
        mesh_count=len(bpy.data.meshes)
        with patch('aventurine.io.skeleton.ExportPlan.validate_bind_transforms',side_effect=ValueError('test validation failure')):
            with self.assertRaisesRegex(ValueError,'test validation'):rest_pose.apply_preserving_meshes(bpy.context,arm)
        self.assertEqual(before,arm.data.bones['child'].matrix_local)
        self.assertVector(arm.pose.bones['child'].location,(.3,0,0))
        self.assertEqual(positions,[v.co for v in obj.data.vertices])
        self.assertEqual(mesh_count,len(bpy.data.meshes))

    def test_rest_bake_rejects_live_animation_and_preserves_clip(self):
        arm,obj=self.fitted_fixture();arm.animation_data_create();arm.animation_data.action=bpy.data.actions.new('Stock clip')
        action=arm.animation_data.action
        with self.assertRaisesRegex(ValueError,'Unlink'):rest_pose.apply_preserving_meshes(bpy.context,arm)
        self.assertEqual(arm.animation_data.action,action)

    def test_rest_menu_routes_league_rigs_and_restores_other_callbacks(self):
        arm,obj=self.fitted_fixture()
        menu=bpy.types.VIEW3D_MT_pose_apply
        def extra(self,context):self.layout.label(text='Other add-on')
        menu.append(extra)
        original=list(menu.draw._draw_funcs)
        class Layout:
            def __init__(self):self.calls=[]
            def operator(self,op,**kw):self.calls.append(op);return types.SimpleNamespace()
            def separator(self):pass
            def label(self,**kw):pass
        rest_pose.register()
        try:
            layout=Layout();rest_pose._draw_apply(types.SimpleNamespace(layout=layout),bpy.context)
            self.assertEqual(layout.calls[0],'pose.aventurine_apply_rest')
            self.assertIn(extra,menu.draw._draw_funcs)
            self.assertEqual(bpy.ops.pose.aventurine_apply_rest(),{'FINISHED'})
        finally:
            rest_pose.unregister()
            self.assertEqual(menu.draw._draw_funcs,original)
            menu.remove(extra)

    def test_rest_bake_transforms_stored_normals_and_keeps_scaled_shape(self):
        arm,obj=self.fitted_fixture()
        source=skin_normals.preserved(obj.data)
        p=arm.pose.bones['child'];p.rotation_mode='XYZ';p.rotation_euler=(.35,.2,-.4);p.scale=(1.4,)*3
        bpy.context.view_layer.update()
        expected=self.snapshot_evaluated(obj)
        normal_matrix=(p.matrix @ p.bone.matrix_local.inverted()).to_3x3().inverted().transposed()
        desired=[(normal_matrix @ n).normalized() for n in source]
        rest_pose.apply_preserving_meshes(bpy.context,arm)
        for a,b in zip(expected,self.snapshot_evaluated(obj)):self.assertVector(a,b)
        for a,b in zip(desired,skin_normals.preserved(obj.data)):
            self.assertLess((a-b).length,0.0002)

    def test_rest_bake_undo_redo_restores_skeleton_and_mesh_together(self):
        arm,obj=self.fitted_fixture();arm.name='RestUndoRig';obj.name='RestUndoMesh'
        arm.pose.bones['child'].location=(.25,.1,.05)
        bpy.context.view_layer.update()
        def state():
            a=bpy.data.objects['RestUndoRig'];o=bpy.data.objects['RestUndoMesh']
            return ([list(r) for r in a.data.bones['child'].matrix_local],
                    [list(v.co) for v in o.data.vertices],
                    [list(r) for r in a.pose.bones['child'].matrix_basis])
        before=state();old_undo=bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo=True
        bpy.ops.ed.undo_push(message='Before rest bake')
        rest_pose.register()
        try:
            self.assertEqual(bpy.ops.pose.aventurine_apply_rest(),{'FINISHED'})
            after=state();self.assertNotEqual(before,after)
            bpy.ops.ed.undo_push(message='After rest bake')
            self.assertEqual(bpy.ops.ed.undo(),{'FINISHED'});self.assertEqual(before,state())
            self.assertEqual(bpy.ops.ed.redo(),{'FINISHED'});self.assertEqual(after,state())
        finally:
            rest_pose.unregister()
            bpy.context.preferences.edit.use_global_undo=old_undo
