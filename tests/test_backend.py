import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import bpy
import mathutils
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aventurine', ROOT / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)

from aventurine.utils.backend import require_ritoshark
from aventurine.utils.format_data import packed, materials_from_bin, textures_from_bin
from aventurine.utils.texture_manager import load_tex_image, parse_bin_for_textures
from aventurine.io import import_skn, import_skl, import_anm, export_skn, export_skl, export_anm, export_mapgeo

rs = require_ritoshark()


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        if bpy.context.object and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='SELECT')
        bpy.ops.object.delete(use_global=False)

    def assertVector(self, actual, expected, places=5):
        self.assertEqual(len(actual), len(expected))
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=places)

    def skeleton(self):
        joints = [
            rs.Joint('root', 0, -1, 2.1, (0, 0, 0), (1, 1, 1), (0, 0, 0, 1),
                     (0, 0, 0), (1, 1, 1), (0, 0, 0, 1)),
            rs.Joint('child', 1, 0, 2.1, (0, 100, 0), (1, 1, 1), (0, 0, 0, 1),
                     (0, -100, 0), (1, 1, 1), (0, 0, 0, 1)),
        ]
        path = self.directory / 'skin.skl'
        rs.Skl.new(joints, [1, 0]).to_path(str(path))
        return path

    def test_skin_and_skeleton_blender_roundtrip(self):
        joints, influences = import_skl.read_skl(self.skeleton())
        self.assertEqual(influences, [1, 0])
        armature = import_skl.create_armature(joints, bone_orient='NATIVE')
        self.assertVector(armature.data.bones['child'].head_local, (0, 0, 1))
        path = self.directory / 'skin.skn'
        source = rs.Skn.new(
            positions=packed('f', [(0, 0, 0), (100, 0, 0), (0, 100, 0)]),
            normals=packed('f', [(0, 0, 1)] * 3),
            uvs=packed('f', [(0, 0), (1, 0), (0, 1)]),
            blend_indices=packed('I', [(0, 1, 0, 0)] * 3),
            blend_weights=packed('f', [(0.75, 0.25, 0, 0)] * 3),
            indices=packed('I', [(0, 1, 2)]),
            submeshes=[rs.Submesh('body', 0, 3, 0, 3)],
        )
        source.to_path(str(path))
        indices, vertices, submeshes = import_skn.read_skn(path)
        obj = import_skn.create_mesh(indices, vertices, submeshes, 'skin', armature, joints, influences)
        self.assertAlmostEqual(obj.vertex_groups['child'].weight(0), 0.75)
        self.assertAlmostEqual(obj.vertex_groups['root'].weight(0), 0.25)
        bpy.context.view_layer.update()
        out = self.directory / 'export.skn'
        _, count, palette = export_skn.write_skn_multi(str(out), [obj], armature)
        self.assertEqual(count, 3)
        self.assertEqual(palette, [0, 1])
        export_skl.write_skl(str(out.with_suffix('.skl')), armature, influences=palette)
        exported = rs.Skn.from_path(str(out))
        skeleton = rs.Skl.from_path(str(out.with_suffix('.skl')))
        self.assertEqual(skeleton.influences, palette)
        self.assertVector(skeleton.joints[1].local_translation, (0, 100, 0))
        self.assertVector(skeleton.joints[1].inverse_bind_translation, (0, -100, 0))
        weights = list(struct.iter_unpack('<4f', exported.blend_weights))
        slots = list(struct.iter_unpack('<4I', exported.blend_indices))
        for row, blend in zip(slots, weights):
            by_joint = {palette[slot]: weight for slot, weight in zip(row, blend) if weight}
            self.assertAlmostEqual(by_joint[1], 0.75)
            self.assertAlmostEqual(by_joint[0], 0.25)

    def test_animation_data_roundtrip(self):
        path = self.directory / 'motion.anm'
        frames = [rs.AnimFrame(i / 30, (0, 0, 0.6, 0.8), (i * 100, 200, 300), (1, 2, 3))
                  for i in range(3)]
        rs.Anm.new(30, [rs.AnimTrack(123, frames)]).to_path(str(path))
        imported = import_anm.read_anm(path)
        self.assertEqual(imported.frame_count, 3)
        self.assertVector(imported.tracks[0].poses[1].translation, (1, 2, 3))
        self.assertVector(imported.tracks[0].poses[1].rotation, (0.8, 0, 0, 0.6))
        out = self.directory / 'roundtrip.anm'
        export_anm.write_anm_from_data(str(out), imported)
        actual = rs.Anm.from_path(str(out))
        for expected, got in zip(frames, actual.tracks[0].frames):
            self.assertVector(got.translation, expected.translation)
            self.assertVector(got.rotation, expected.rotation)
            self.assertVector(got.scale, expected.scale)

    def test_bin_skin_links_and_material_samplers(self):
        document = {'entries': {
            1: {0x45ff5904: {0x3c6468f4: 'base.tex', 0x24725910: [
                {0xaad7612c: 'body', 0xd2e4d060: 2},
                {0xaad7612c: 'eyes', 0x3c6468f4: 'eyes.tex'},
            ]}},
            2: {'__class__': 0xff9d3409, 0x8d39bde6: 'material', 0x0a6f0eb5: [
                {0xb311d4ef: 'Normal_Texture', 0xf0a363e3: 'normal.tex'},
                {0xb311d4ef: 'Diffuse_Texture', 0xf0a363e3: 'body.tex'},
                {0x02e7fb4c: 'Legacy', 0xf0a363e3: 'legacy.tex'},
            ]},
        }}
        self.assertEqual(textures_from_bin(document),
                         {'BASE': 'base.tex', 'body': 'body.tex', 'eyes': 'eyes.tex'})
        materials = materials_from_bin(document)['materials']
        self.assertEqual(materials[0]['key'], '00000002')
        self.assertEqual(materials[0]['samplers'][2], {'name': 'Legacy', 'texture': 'legacy.tex'})

    def test_animation_blender_roundtrip(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        armature = import_skl.create_armature(joints, bone_orient='NATIVE')
        armature.animation_data_create()
        armature.animation_data.action = bpy.data.actions.new('motion')
        source = rs.Skl.from_path(str(self.directory / 'skin.skl'))
        path = self.directory / 'source.anm'
        rs.Anm.new(30, [rs.AnimTrack(joint.hash, [
            rs.AnimFrame(i / 30, (0, 0, 0, 1),
                         (i * 10, 100 if joint.id else 0, 0), (1, 1, 1))
            for i in range(3)
        ]) for joint in source.joints]).to_path(str(path))
        import_anm.apply_anm(import_anm.read_anm(path), armature)
        out = self.directory / 'export.anm'
        export_anm.write_anm(str(out), armature, fps=30)
        actual = rs.Anm.from_path(str(out))
        self.assertEqual(actual.frame_count, 3)
        expected = {track.joint_hash: track for track in rs.Anm.from_path(str(path)).tracks}
        for track in actual.tracks:
            for frame, original in zip(track.frames, expected[track.joint_hash].frames):
                self.assertVector(frame.translation, original.translation, places=4)
                self.assertVector(frame.rotation, original.rotation, places=4)

    def test_tex_pixels(self):
        path = self.directory / 'colors.tex'
        path.write_bytes(struct.pack('<IHHBBBB', 0x00584554, 1, 2, 0, 20, 0, 0)
                         + bytes([0, 0, 255, 255, 255, 0, 0, 128]))
        image = load_tex_image(str(path))
        self.addCleanup(bpy.data.images.remove, image)
        self.assertVector(image.pixels[:4], (0, 0, 1, 128 / 255), places=4)
        self.assertVector(image.pixels[4:], (1, 0, 0, 1), places=4)
        self.assertTrue(image.packed_file)

    def test_insert_animation_preserves_existing_keys_and_replaces_overlap(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        armature = import_skl.create_armature(joints, bone_orient='NATIVE')
        armature.animation_data_create()
        action = bpy.data.actions.new('insert_motion')
        armature.animation_data.action = action
        source = rs.Skl.from_path(str(self.directory / 'skin.skl'))
        path = self.directory / 'insert.anm'
        rs.Anm.new(30, [rs.AnimTrack(joint.hash, [
            rs.AnimFrame(i / 30, (0, 0, 0, 1),
                         (i * 10, 100 if joint.id else 0, 0), (1, 1, 1))
            for i in range(3)
        ]) for joint in source.joints]).to_path(str(path))
        animation = import_anm.read_anm(path)
        import_anm.apply_anm(animation, armature)
        curve = action.fcurves[0]
        curve.keyframe_points[0].interpolation = 'CONSTANT'
        original = {p.co.x: p.co.y for p in curve.keyframe_points}
        import_anm.apply_anm(animation, armature, frame_offset=10)
        after = {p.co.x: p.co.y for p in curve.keyframe_points}
        for frame, value in original.items():
            self.assertAlmostEqual(after[frame], value)
        self.assertEqual(curve.keyframe_points[0].interpolation, 'CONSTANT')
        self.assertGreater(max(after), max(original))
        counts = [len(fc.keyframe_points) for fc in action.fcurves]
        import_anm.apply_anm(animation, armature, frame_offset=10)
        self.assertEqual(counts, [len(fc.keyframe_points) for fc in action.fcurves])
        self.assertEqual(len(curve.keyframe_points), len({p.co.x for p in curve.keyframe_points}))

    def test_custom_normals_survive_skin_roundtrip_and_visual_export(self):
        joints, influences = import_skl.read_skl(self.skeleton())
        armature = import_skl.create_armature(joints, bone_orient='NATIVE')
        path = self.directory / 'normals.skn'
        source = rs.Skn.new(
            positions=packed('f', [(0, 0, 0), (100, 0, 0), (0, 100, 0)]),
            normals=packed('f', [(0.6, 0, 0.8)] * 3),
            uvs=packed('f', [(0, 0), (1, 0), (0, 1)]),
            blend_indices=packed('I', [(0, 0, 0, 0)] * 3),
            blend_weights=packed('f', [(1, 0, 0, 0)] * 3),
            indices=packed('I', [(0, 1, 2)]),
            submeshes=[rs.Submesh('body', 0, 3, 0, 3)],
        )
        source.to_path(str(path))
        indices, vertices, submeshes = import_skn.read_skn(path)
        obj = import_skn.create_mesh(indices, vertices, submeshes, 'normals', armature, joints, influences)
        bpy.context.view_layer.update()
        for visual in (False, True):
            with self.subTest(visual=visual):
                out = self.directory / 'normals-out.skn'
                export_skn.write_skn_multi(str(out), [obj], armature, use_visual_pose=visual)
                for normal in struct.iter_unpack('<3f', rs.Skn.from_path(str(out)).normals):
                    self.assertVector(normal, (0.6, 0, 0.8), places=3)

    def test_hard_edges_and_nonuniform_scale_keep_corner_normals(self):
        mesh = bpy.data.meshes.new('hard_edges')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)], [],
                         [(0, 1, 2), (0, 3, 1)])
        mesh.uv_layers.new()
        obj = bpy.data.objects.new('hard_edges', mesh)
        bpy.context.scene.collection.objects.link(obj)
        data = export_skn.collect_mesh_data(obj, None, {}, 'body')
        self.assertEqual(len(data['vertices']), 6)
        self.assertVector(data['vertices'][0]['normal'], (0, -1, 0))
        self.assertVector(data['vertices'][3]['normal'], (0, 0, 1))
        if hasattr(mesh, 'use_auto_smooth'):
            mesh.use_auto_smooth = True
        for polygon in mesh.polygons:
            polygon.use_smooth = True
        mesh.normals_split_custom_set([(0.6, 0.8, 0)] * len(mesh.loops))
        obj.scale = (2, 1, 1)
        bpy.context.view_layer.update()
        data = export_skn.collect_mesh_data(obj, None, {}, 'body', disable_transforms=True)
        expected = mathutils.Vector((0.3, 0.8, 0)).normalized()
        for vertex in data['vertices']:
            self.assertVector(vertex['normal'], expected, places=3)

    def test_concave_polygon_export_uses_valid_triangles(self):
        mesh = bpy.data.meshes.new('concave')
        points = [(0, 0, 0), (3, 0, 0), (3, 3, 0), (2, 3, 0),
                  (2, 1, 0), (1, 1, 0), (1, 3, 0), (0, 3, 0)]
        mesh.from_pydata(points, [], [tuple(range(8))])
        mesh.uv_layers.new()
        obj = bpy.data.objects.new('concave', mesh)
        bpy.context.scene.collection.objects.link(obj)
        data = export_skn.collect_mesh_data(obj, None, {}, 'body', disable_scaling=True)
        self.assertEqual(len(data['indices']), 18)
        area = 0
        for offset in range(0, len(data['indices']), 3):
            a, b, c = [data['vertices'][i]['pos'] for i in data['indices'][offset:offset+3]]
            area += (b-a).cross(c-a).length / 2
        self.assertAlmostEqual(area, 7)

    def test_map_export_does_not_silently_discard_spatial_data(self):
        out = self.directory / 'existing.mapgeo'
        out.write_bytes(b'keep existing file')
        context = SimpleNamespace(scene={})
        with self.assertRaisesRegex(ValueError, 'Cannot preserve'):
            export_mapgeo.write_mapgeo(context, None, 18, str(out))
        self.assertEqual(out.read_bytes(), b'keep existing file')
        source = self.directory / 'source.mapgeo'
        scene = {'mapgeo_source': str(source), 'mapgeo_trailing_offset': 8, 'mapgeo_version': 18}
        context.scene = scene
        source.write_bytes(b'OEGM' + struct.pack('<I', 18) + b'spatial data')
        self.assertEqual(export_mapgeo._source_trailing_bytes(context, 18), b'spatial data')
        self.assertIsNone(export_mapgeo._source_trailing_bytes(context, 17))
        source.write_bytes(b'OEGM')
        self.assertIsNone(export_mapgeo._source_trailing_bytes(context, 18))

    def test_bundled_dds_decoder(self):
        header = [124, 0x100F, 1, 1, 4, 0, 1] + [0] * 11
        header += [32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000]
        header += [0x1000, 0, 0, 0, 0]
        data = b'DDS ' + struct.pack('<31I', *header) + bytes([30, 20, 10, 40])
        image = rs.Dds.from_bytes(data)
        self.assertEqual((image.width, image.height), (1, 1))
        self.assertEqual(image.rgba, bytes([10, 20, 30, 40]))

    def test_map_export_preserves_tail_and_allows_explicit_disabled_mode(self):
        mesh = bpy.data.meshes.new('map')
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        mesh.uv_layers.new()
        obj = bpy.data.objects.new('map', mesh)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.view_layer.update()
        source = self.directory / 'map-source.mapgeo'
        tail = b'preserved spatial data'
        source.write_bytes(b'OEGM' + struct.pack('<I', 18) + tail)
        context = SimpleNamespace(scene={'mapgeo_source': str(source),
                                        'mapgeo_trailing_offset': 8, 'mapgeo_version': 18})
        out = self.directory / 'map-out.mapgeo'
        collection = bpy.context.scene.collection
        count, mode = export_mapgeo.write_mapgeo(context, collection, 18, str(out))
        self.assertEqual((count, mode), (1, 'original'))
        self.assertTrue(out.read_bytes().endswith(tail))
        context.scene.clear()
        count, mode = export_mapgeo.write_mapgeo(context, collection, 18, str(out), bucket_grid_mode='DISABLED')
        self.assertEqual((count, mode), (1, 'disabled'))
        self.assertFalse(out.read_bytes().endswith(tail))

    def test_invalid_files_raise_format_error(self):
        path = self.directory / 'bad.bin'
        path.write_bytes(b'not a game file')
        for reader in (import_skn.read_skn, import_skl.read_skl, import_anm.read_anm,
                       parse_bin_for_textures):
            with self.subTest(reader=reader.__name__), self.assertRaises(rs.FormatError):
                reader(str(path))


if __name__ == '__main__':
    unittest.main()
