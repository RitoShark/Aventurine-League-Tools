import importlib.util
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import bpy

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('aventurine', ROOT / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)

from aventurine.utils.backend import require_ritoshark
from aventurine.utils.format_data import packed, materials_from_bin, textures_from_bin
from aventurine.utils.texture_manager import load_tex_image, parse_bin_for_textures
from aventurine.io import import_skn, import_skl, import_anm, export_skn, export_skl, export_anm

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

    def test_invalid_files_raise_format_error(self):
        path = self.directory / 'bad.bin'
        path.write_bytes(b'not a game file')
        for reader in (import_skn.read_skn, import_skl.read_skl, import_anm.read_anm,
                       parse_bin_for_textures):
            with self.subTest(reader=reader.__name__), self.assertRaises(rs.FormatError):
                reader(str(path))


if __name__ == '__main__':
    unittest.main()
