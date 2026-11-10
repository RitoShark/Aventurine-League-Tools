"""Blender integration checks, included in test_format_sweep.py."""
import math
import struct
from types import SimpleNamespace

import bpy
from mathutils import Quaternion

from test_backend import addon, rs, import_skl, export_anm


class AnimationExportChecks:
    def _animation_export_rig(self):
        joints, _ = import_skl.read_skl(self.skeleton())
        arm = import_skl.create_armature(joints, bone_orient='NATIVE')
        arm.animation_data_create()
        arm.animation_data.action = bpy.data.actions.new('format_motion')
        for index, bone in enumerate(arm.pose.bones):
            bone.rotation_mode = 'QUATERNION'
            for frame in (1, 6):
                bone.location = ((frame - 1) * .02, index * .01, 0)
                bone.rotation_quaternion = Quaternion((0, 0, 1), (frame - 1) * .03)
                bone.scale = (1 + (frame - 1) * .002,) * 3
                for channel in ('location', 'rotation_quaternion', 'scale'):
                    bone.keyframe_insert(data_path=channel, frame=frame)
        for curve in arm.animation_data.action.fcurves:
            for key in curve.keyframe_points:
                key.interpolation = 'LINEAR'
        bpy.context.view_layer.objects.active = arm
        bpy.context.scene.frame_start = 1
        bpy.context.scene.frame_end = 6
        bpy.context.scene.render.fps = 30
        bpy.context.scene.render.fps_base = 1
        bpy.context.scene.frame_set(3, subframe=.25)
        return arm

    def _register_anm_export(self):
        bpy.utils.register_class(addon.ExportANM)
        self.addCleanup(bpy.utils.unregister_class, addon.ExportANM)

    def _assert_anm_format(self, path, expected):
        data = path.read_bytes()
        if expected == 'canm':
            self.assertEqual(data[:8], b'r3d2canm')
        else:
            self.assertEqual(data[:8], b'r3d2anmd')
            self.assertEqual(struct.unpack_from('<I', data, 8)[0], int(expected[1:]))

    def test_animation_export_formats_validate_hierarchy_and_preserve_motion(self):
        arm = self._animation_export_rig()
        # Cleaned names, object transforms, mirroring and export scale must all
        # match the skeleton used for motion validation, not the source SKL.
        arm.pose.bones['child'].name = 'cape-bone'
        arm.location = (.1, .2, .3)
        arm.rotation_euler = (.1, 0, .2)
        options = dict(fps=30 / 1.001, anim_scale=1.2, flip=True)
        results = {}
        for format in ('v4', 'v5', 'canm'):
            path = self.directory / f'{format}.anm'
            result = export_anm.write_anm(path, arm, export_format=format, **options)
            self.assertEqual(result.requested_format, format)
            self.assertEqual(result.format, format)
            self.assertEqual(result.fallbacks, [])
            self.assertIsNotNone(result.metrics['model_space'])
            self.assertLessEqual(result.metrics['model_space'], 1.0)
            self._assert_anm_format(path, format)
            self.assertEqual(bpy.context.scene.frame_current, 3)
            self.assertAlmostEqual(bpy.context.scene.frame_subframe, .25)
            actual = rs.Anm.from_path(str(path))
            self.assertEqual(actual.frame_count, 6)
            self.assertAlmostEqual(actual.fps, options['fps'], places=4)
            results[format] = {track.joint_hash: track.frames for track in actual.tracks}
        for format in ('v5', 'canm'):
            self.assertEqual(set(results[format]), set(results['v4']))
            for hash, expected in results['v4'].items():
                for actual, reference in zip(results[format][hash], expected):
                    self.assertLessEqual(max(abs(a-b) for a, b in zip(actual.translation, reference.translation)), .5)
                    self.assertLessEqual(max(abs(a-b) for a, b in zip(actual.scale, reference.scale)), .005)
                    a = Quaternion((actual.rotation[3], *actual.rotation[:3]))
                    b = Quaternion((reference.rotation[3], *reference.rotation[:3]))
                    dot = min(1.0, abs(a.normalized().dot(b.normalized())))
                    self.assertLessEqual(2 * math.acos(dot), math.radians(2))

    def test_anm_operator_defaults_to_v5_and_uses_fractional_fps(self):
        arm = self._animation_export_rig()
        self._register_anm_export()
        prop = bpy.ops.export_scene.anm.get_rna_type().properties['export_format']
        self.assertEqual(prop.default, 'v5')
        self.assertEqual({item.identifier for item in prop.enum_items}, {'v5', 'canm', 'v4'})
        self.assertTrue(all(item.description for item in prop.enum_items))
        bpy.context.scene.render.fps_base = 1.001
        path = self.directory / 'default.anm'
        status = bpy.ops.export_scene.anm(filepath=str(path), target_armature_name=arm.name)
        self.assertEqual(status, {'FINISHED'})
        self._assert_anm_format(path, 'v5')
        self.assertAlmostEqual(rs.Anm.from_path(str(path)).fps, 30 / 1.001, places=4)

    def test_anm_single_and_batch_operators_use_selected_format(self):
        for action in list(bpy.data.actions):
            bpy.data.actions.remove(action, do_unlink=True)
        arm = self._animation_export_rig()
        original = arm.animation_data.action
        other = original.copy()
        other.name = 'second_motion'
        self._register_anm_export()
        for format in ('v5', 'canm', 'v4'):
            with self.subTest(format=format):
                path = self.directory / format / 'single.anm'
                path.parent.mkdir()
                status = bpy.ops.export_scene.anm(filepath=str(path), target_armature_name=arm.name,
                                                  export_format=format)
                self.assertEqual(status, {'FINISHED'})
                self._assert_anm_format(path, format)
                status = bpy.ops.export_scene.anm(filepath=str(path), target_armature_name=arm.name,
                                                  export_format=format, batch_export_all_actions=True)
                self.assertEqual(status, {'FINISHED'})
                for action in (original, other):
                    self._assert_anm_format(path.parent / f'{action.name}.anm', format)
                self.assertEqual(arm.animation_data.action, original)
                self.assertEqual(bpy.context.scene.frame_current, 3)
                self.assertAlmostEqual(bpy.context.scene.frame_subframe, .25)

    def test_canm_fallback_is_reported_with_actual_output(self):
        arm = self._animation_export_rig()
        arm.animation_data.action = bpy.data.actions.new('extreme_motion')
        root = arm.pose.bones['root']
        root.location = (0, 0, 0)
        root.keyframe_insert(data_path='location', frame=1)
        root.location = (10000, 0, 0)
        root.keyframe_insert(data_path='location', frame=2)
        messages = []
        operator = SimpleNamespace(report=lambda level, message: messages.append((level, message)))
        path = self.directory / 'fallback.anm'
        status = export_anm.save(operator, bpy.context, str(path), target_armature=arm, export_format='canm')
        self.assertEqual(status, {'FINISHED'})
        self._assert_anm_format(path, 'v5')
        self.assertTrue(any(level == {'WARNING'} and 'V5 instead of CANM' in message
                            for level, message in messages))

    def test_animation_failed_export_preserves_file_and_scene(self):
        arm = self._animation_export_rig()
        path = self.directory / 'existing.anm'
        path.write_bytes(b'original file')
        with self.assertRaises(ValueError):
            export_anm.write_anm(path, arm, export_format='invalid')
        self.assertEqual(path.read_bytes(), b'original file')
        self.assertEqual(bpy.context.scene.frame_current, 3)
        self.assertAlmostEqual(bpy.context.scene.frame_subframe, .25)
