"""Regression coverage for flat-preview normals after mesh edits."""
import struct
from mathutils import Vector
from test_backend import bpy, rs, packed, import_skn, import_skl, export_skn
from aventurine.io import skin_normals
from aventurine.tools import rest_pose


class SkinNormalChecks:
    def edited_skin_fixture(self):
        joints, palette = import_skl.read_skl(self.skeleton())
        arm = import_skl.create_armature(joints)
        path = self.directory / 'creased.skn'
        rs.Skn.new(
            positions=packed('f', [(0,0,0), (100,0,0), (0,100,0), (0,100,100)]),
            normals=packed('f', [(0,0,1)] * 4),
            uvs=packed('f', [(0,0), (1,0), (1,1), (0,1)]),
            blend_indices=packed('I', [(0,0,0,0)] * 4),
            blend_weights=packed('f', [(1,0,0,0)] * 4),
            indices=packed('I', [(0,1,2), (0,2,3)]),
            submeshes=[rs.Submesh('body', 0, 4, 0, 6)],
        ).to_path(str(path))
        obj = import_skn.create_mesh(*import_skn.read_skn(path), 'Edited', arm, joints, palette)
        obj.data.vertices[1].co.z += .2
        obj.data.update()
        self.assertIsNone(skin_normals.preserved(obj.data))
        bpy.context.view_layer.update()
        return arm, obj

    def export_edited_skin(self, arm, obj, **kwargs):
        path = self.directory / 'edited.skn'
        export_skn.write_skn_multi(path, [obj], arm, **kwargs)
        return rs.Skn.from_path(str(path))

    def test_edited_flat_preview_recalculates_normals_without_triangle_splits(self):
        arm, obj = self.edited_skin_fixture()
        original = obj.data
        before = len(bpy.data.meshes)
        result = self.export_edited_skin(arm, obj)
        self.assertEqual(result.vertex_count, 4)  # flat-face fallback produced 6
        self.assertEqual(len(result.indices) // 4, 6)
        self.assertEqual(obj.data, original)
        self.assertEqual(len(bpy.data.meshes), before)
        self.assertFalse(any(p.use_smooth for p in obj.data.polygons))
        self.assertFalse(obj.data.has_custom_normals)
        normals = list(struct.iter_unpack('<3f', result.normals))
        self.assertTrue(any(abs(n[0]) > .1 for n in normals))  # recalculated, not stale import
        for n in normals:
            self.assertAlmostEqual(Vector(n).length, 1, places=5)
        self.assertEqual(sorted(struct.iter_unpack('<2f', result.uvs)), [(0,0),(0,1),(1,0),(1,1)])
        for weights in struct.iter_unpack('<4f', result.blend_weights):
            self.assertEqual(weights, (1,0,0,0))
        # UV discontinuities still need distinct SKN vertices.
        obj.data.uv_layers.active.data[3].uv.x += .25
        self.assertEqual(self.export_edited_skin(arm, obj).vertex_count, 5)

    def test_imported_flat_preview_without_cached_normals_recalculates(self):
        arm, obj = self.edited_skin_fixture()
        obj.data.attributes.remove(obj.data.attributes[skin_normals.ATTRIBUTE])
        del obj.data[skin_normals.BASIS]
        self.assertEqual(self.export_edited_skin(arm, obj).vertex_count, 4)
        # An ordinary Blender mesh retains genuinely authored flat shading.
        del obj['lol_skn_flags']
        self.assertEqual(self.export_edited_skin(arm, obj).vertex_count, 6)

    def test_edited_preview_retains_sharp_edges_and_authored_custom_normals(self):
        arm, obj = self.edited_skin_fixture()
        edge = next(e for e in obj.data.edges if set(e.vertices) == {0,2})
        edge.use_edge_sharp = True
        self.assertEqual(self.export_edited_skin(arm, obj).vertex_count, 6)
        edge.use_edge_sharp = False
        for p in obj.data.polygons:
            p.use_smooth = True
        if hasattr(obj.data, 'use_auto_smooth'):
            obj.data.use_auto_smooth = True
        obj.data.normals_split_custom_set([(1,0,0)]*3 + [(0,1,0)]*3)
        result = self.export_edited_skin(arm, obj)
        self.assertEqual(result.vertex_count, 6)
        self.assertTrue(obj.data.has_custom_normals)
        normals = list(struct.iter_unpack('<3f', result.normals))
        for n in normals:
            self.assertLess(min((Vector(n)-Vector(e)).length for e in [(1,0,0),(0,0,1)]), .001)

    def test_old_imported_custom_normals_survive_shade_flat(self):
        arm, obj = self.edited_skin_fixture()
        obj.data.attributes.remove(obj.data.attributes[skin_normals.ATTRIBUTE])
        del obj.data[skin_normals.BASIS]
        for p in obj.data.polygons:
            p.use_smooth = True
        if hasattr(obj.data, 'use_auto_smooth'):
            obj.data.use_auto_smooth = True
        obj.data.normals_split_custom_set([(1,0,0)] * 6)
        for p in obj.data.polygons:
            p.use_smooth = False
        result = self.export_edited_skin(arm, obj)
        self.assertEqual(result.vertex_count, 4)
        for n in struct.iter_unpack('<3f', result.normals):
            self.assertVector(n, (1,0,0), places=3)
        self.assertFalse(any(p.use_smooth for p in obj.data.polygons))
        self.assertTrue(obj.data.has_custom_normals)

    def test_edited_preview_visual_export_and_failure_restore_mesh(self):
        arm, obj = self.edited_skin_fixture()
        original = obj.data
        count = len(bpy.data.meshes)
        arm.pose.bones['child'].rotation_mode = 'XYZ'
        arm.pose.bones['child'].rotation_euler.y = .4
        self.assertEqual(self.export_edited_skin(arm, obj, use_visual_pose=True).vertex_count, 4)
        with self.assertRaisesRegex(ValueError, 'palette'):
            self.export_edited_skin(arm, obj, influences=[])
        self.assertEqual(obj.data, original)
        self.assertEqual(len(bpy.data.meshes), count)
        self.assertFalse(any(p.use_smooth for p in obj.data.polygons))
        self.assertNotIn(skin_normals.ACTIVE, obj.data)

    def test_rest_pose_after_geometry_edit_keeps_file_normals_and_flat_preview(self):
        arm, obj = self.edited_skin_fixture()
        bpy.context.view_layer.objects.active = arm
        arm.select_set(True)
        bpy.ops.object.mode_set(mode='POSE')
        p = arm.pose.bones['child']
        p.rotation_mode = 'XYZ'
        p.rotation_euler.y = .4
        expected = self.snapshot_evaluated(obj)
        rest_pose.apply_preserving_meshes(bpy.context, arm)
        for vertex, co in zip(obj.data.vertices, expected):
            self.assertVector(vertex.co, co)
        self.assertIsNotNone(skin_normals.preserved(obj.data))
        self.assertFalse(any(p.use_smooth for p in obj.data.polygons))
        self.assertFalse(obj.data.has_custom_normals)
        self.assertEqual(self.export_edited_skin(arm, obj).vertex_count, 4)
