"""Blender diagnostic: compare exported SKL+ANM skinning against evaluated poses.

Run with Blender --background --factory-startup --python this.py --
  --addon PATH --output DIRECTORY [--real-skl PATH --real-anm PATH]
This writes only diagnostic files under --output; it never edits source assets.
The shared-frame exporter below is a research prototype, not a production path.
"""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--addon', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--real-skl', type=Path)
parser.add_argument('--real-anm', type=Path)
args = parser.parse_args(argv)
args.output.mkdir(parents=True, exist_ok=True)
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('aventurine', args.addon / '__init__.py')
addon = importlib.util.module_from_spec(spec)
sys.modules['aventurine'] = addon
spec.loader.exec_module(addon)
from aventurine.io import import_skl, import_anm, export_skl, export_anm, export_skn, bone_utils
from aventurine.utils.backend import require_ritoshark
from aventurine.utils.binary_utils import Hash
rs = require_ritoshark()
P = Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
Pi = P.inverted()


def trs(t, r, s):
    x, y, z, w = r
    return Matrix.LocRotScale(Vector(t), Quaternion((w, x, y, z)), Vector(s))


def game(matrix):
    result = Pi @ matrix @ P
    result.translation *= 100
    return result


def parts(matrix):
    t, r, s = matrix.decompose()
    return tuple(t), (r.x, r.y, r.z, r.w), tuple(s)


def compose(locals, parents):
    result = {}
    def get(name):
        if name not in result:
            parent = parents[name]
            result[name] = get(parent) @ locals[name] if parent else locals[name]
        return result[name]
    for name in locals:
        get(name)
    return result


def clean_scene():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)


def synthetic():
    joints = []
    globals = []
    for i, (name, t, axis, angle) in enumerate([
        ('root', (10, 20, 30), (0, 1, 0), .35),
        ('child', (0, 100, 0), (0, 0, 1), .2),
        ('tip', (15, 65, 5), (1, 0, 0), -.15),
    ]):
        r = Quaternion(axis, angle)
        local = Matrix.LocRotScale(Vector(t), r, Vector((1, 1, 1)))
        global_m = globals[-1] @ local if i else local
        globals.append(global_m)
        it, ir, iss = parts(global_m.inverted())
        joints.append(rs.Joint(name, i, i-1, 2.1, t, (1, 1, 1), (r.x, r.y, r.z, r.w), it, iss, ir))
    sk = args.output / 'source.skl'
    an = args.output / 'source.anm'
    rs.Skl.new(joints, list(range(len(joints)))).to_path(str(sk))
    tracks = []
    for joint in joints:
        frames = []
        t0 = Vector(joint.local_translation)
        q0 = Quaternion((joint.local_rotation[3], *joint.local_rotation[:3]))
        for i in range(4):
            r = q0 @ Quaternion((0, 0, 1), i * .12)
            t = t0 + Vector((i * 6, i * 2, -i * 3))
            sc = 1 + i * .04
            frames.append(rs.AnimFrame(i / 30, (r.x, r.y, r.z, r.w), tuple(t), (sc, sc, sc)))
        tracks.append(rs.AnimTrack(joint.hash, frames))
    rs.Anm.new(30, tracks).to_path(str(an))
    return sk, an


def add_bone(arm, kind, target_name):
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones
    target = eb[target_name]
    if kind.startswith('duplicate'):
        bpy.ops.armature.select_all(action='DESELECT')
        target.select = target.select_head = target.select_tail = True
        eb.active = target
        bpy.ops.armature.duplicate()
        new = eb.active
        if kind == 'duplicate_renamed':
            new.name = 'cape'
        new.head += Vector((.4, .2, .3))
        new.tail += Vector((.4, .2, .3))
    else:
        new = eb.new('helper' if kind in ('insert_parent', 'insert_root') else 'cape')
        new.head = target.head + Vector((.4, .2, .3))
        new.tail = new.head + Vector((.1, .3, .1))
        if kind in ('insert_parent', 'insert_root'):
            new.parent = target.parent
            target.parent = new
            target.use_connect = False
        else:
            new.parent = target
    name = new.name
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.context.view_layer.update()
    return name


def prototype(arm, original_names, directory, count):
    """Use one export frame per joint for both bind and animated globals.

    B = evaluated Blender pose, V = imported Blender rest, N = native rest.
    A genuine source joint has K=V^-1 N; an added joint has K=I.
    Export rest/pose globals = current rest/pose @ K. Both writers then
    derive locals against the current exported parent, never an old ancestor.
    """
    bones = sorted((b for b in arm.pose.bones if b.name in original_names),
                   key=lambda b: b.get('native_bone_index', 0))
    bones += [b for b in arm.pose.bones if b.name not in original_names]
    ids = {b.name: i for i, b in enumerate(bones)}
    names = {b.name: bone_utils.sanitize_illegal_chars(b.name) for b in bones}
    assert len({Hash.elf(n) for n in names.values()}) == len(bones)
    correction = {}
    rests = {}
    for b in bones:
        correction[b.name] = Matrix.Identity(4)
        if b.name in original_names:
            v, n = b.get('native_matrix_local'), b.get('native_global_rest_mat')
            if v and n:
                v = Matrix([v[i:i+4] for i in range(0, 16, 4)])
                n = Matrix([n[i:i+4] for i in range(0, 16, 4)])
                correction[b.name] = v.inverted() @ n
        rests[b.name] = game(arm.matrix_world @ b.bone.matrix_local @ correction[b.name])
    joints = []
    for b in bones:
        local = rests[b.parent.name].inverted() @ rests[b.name] if b.parent else rests[b.name]
        t, r, s = parts(local)
        it, ir, iss = parts(rests[b.name].inverted())
        joints.append(rs.Joint(names[b.name], ids[b.name], ids[b.parent.name] if b.parent else -1,
                               2.1, t, s, r, it, iss, ir))
    sk = directory / 'prototype.skl'
    an = directory / 'prototype.anm'
    rs.Skl.new(joints, list(range(len(joints)))).to_path(str(sk))
    tracks = {b.name: [] for b in bones}
    for f in range(1, count+1):
        bpy.context.scene.frame_set(f)
        evaluated = {b.name: game(arm.matrix_world @ b.matrix @ correction[b.name]) for b in bones}
        for b in bones:
            local = evaluated[b.parent.name].inverted() @ evaluated[b.name] if b.parent else evaluated[b.name]
            t, r, s = parts(local)
            tracks[b.name].append(rs.AnimFrame((f-1)/30, r, t, s))
    rs.Anm.new(30, [rs.AnimTrack(Hash.elf(names[n]), frames) for n, frames in tracks.items()]).to_path(str(an))
    return measure(arm, sk, an, count)


def measure(arm, sk, an, count, visual_bind=False):
    saved_frame = bpy.context.scene.frame_current
    if visual_bind:
        bpy.context.scene.frame_set(0)
    rests = {b.name: (b.matrix.copy() if visual_bind else b.bone.matrix_local.copy()) for b in arm.pose.bones}
    bpy.context.scene.frame_set(saved_frame)
    skeleton, anim = rs.Skl.from_path(str(sk)), rs.Anm.from_path(str(an))
    names = {j.id: j.name for j in skeleton.joints}
    parents = {j.name: names[j.parent_id] if j.parent_id >= 0 else None for j in skeleton.joints}
    bind = {j.name: trs(j.local_translation, j.local_rotation, j.local_scale) for j in skeleton.joints}
    inverse = {j.name: trs(j.inverse_bind_translation, j.inverse_bind_rotation, j.inverse_bind_scale) for j in skeleton.joints}
    bind_globals = compose(bind, parents)
    tracks = {t.joint_hash: t for t in anim.tracks}
    matched = {b.name: next((j for j in skeleton.joints if j.name == bone_utils.sanitize_illegal_chars(b.name)), None)
               for b in arm.pose.bones}
    bind_error, deform_error = 0., 0.
    worst = None
    bind_inverse_error = 0.
    for b in arm.pose.bones:
        j = matched[b.name]
        if j:
            bind_error = max(bind_error, (bind_globals[j.name].translation - game(arm.matrix_world @ rests[b.name]).translation).length)
    for j in skeleton.joints:
        point = bind_globals[j.name].translation
        skin_rest = bind_globals[j.name] @ inverse[j.name]
        for offset in ((0, 0, 0), (20, 0, 0), (0, 20, 0), (0, 0, 20)):
            p = point + Vector(offset)
            bind_inverse_error = max(bind_inverse_error, (skin_rest @ p - p).length)
    for f in range(1, min(count, anim.frame_count)+1):
        bpy.context.scene.frame_set(f)
        local = dict(bind)
        for j in skeleton.joints:
            track = tracks.get(j.hash)
            if track:
                frame = track.frames[f-1]
                local[j.name] = trs(frame.translation, frame.rotation, frame.scale)
        globals = compose(local, parents)
        for b in arm.pose.bones:
            j = matched[b.name]
            if not j:
                continue
            expected = game(arm.matrix_world @ b.matrix @ rests[b.name].inverted() @ arm.matrix_world.inverted())
            actual = globals[j.name] @ inverse[j.name]
            point = game(arm.matrix_world @ rests[b.name]).translation
            for offset in ((0, 0, 0), (20, 0, 0), (0, 20, 0), (0, 0, 20)):
                p = point + Vector(offset)
                error = (actual @ p - expected @ p).length
                if error > deform_error:
                    deform_error = error
                    worst = {'bone': b.name, 'frame': f}

    return {'bind_head_error_game_units': bind_error, 'deformation_error_game_units': deform_error,
            'worst_deformation': worst, 'bind_inverse_identity_error_game_units': bind_inverse_error,
            'joint_order': [j.name for j in skeleton.joints],
            'missing_tracks': [j.name for j in skeleton.joints if j.hash not in tracks],
            'unmatched_bones': [name for name, joint in matched.items() if joint is None],
            'unmatched_tracks': [t.joint_hash for t in anim.tracks if t.joint_hash not in {j.hash for j in skeleton.joints}]}


def run(sk, an, orientation, edit, before_import=True, real=False):
    clean_scene()
    authored = edit.startswith('authored_')
    actual_edit = edit.removeprefix('authored_')
    joints, _ = import_skl.read_skl(sk)
    arm = import_skl.create_armature(joints, bone_orient=orientation)
    names = {j.name for j in joints}
    target = next((j.name for j in joints if 'clavicle' in j.name.lower()), joints[1].name) if real else 'child'
    if edit == 'insert_root':
        target = next(j.name for j in joints if j.parent < 0)
    new = None
    if edit != 'unchanged' and before_import:
        new = add_bone(arm, actual_edit, target)
    arm.animation_data_create()
    arm.animation_data.action = bpy.data.actions.new('audit')
    animation = import_anm.read_anm(an)
    import_anm.apply_anm(animation, arm)
    if edit != 'unchanged' and not before_import:
        new = add_bone(arm, actual_edit, target)
    count = min(animation.frame_count, 8) if real else animation.frame_count
    if authored and new:
        pb = arm.pose.bones[new]
        pb.rotation_mode = 'QUATERNION'
        for f in range(1, count+1):
            bpy.context.scene.frame_set(f)
            pb.location = (.12*f, -.08*f, .05*f)
            pb.rotation_quaternion = Quaternion((1, 0, 0), .15*f)
            pb.scale = (1+.04*f,) * 3
            for path in ('location', 'rotation_quaternion', 'scale'):
                pb.keyframe_insert(data_path=path, frame=f)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, count
    directory = args.output / f'{"real" if real else "synthetic"}_{orientation}_{edit}_{"before" if before_import else "after"}'
    directory.mkdir(exist_ok=True)
    record = {'asset': 'Seraphine' if real else 'synthetic', 'orientation': orientation,
              'edit': edit, 'edit_before_animation_import': before_import,
              'target': target, 'added': new, 'clones': bone_utils.detect_offset_clones(arm)}
    record['stock_clip_vs_scene'] = measure(arm, sk, an, count)
    record['target_basis_samples'] = []
    for f in (1, count):
        bpy.context.scene.frame_set(f)
        pb = arm.pose.bones[target]
        record['target_basis_samples'].append({'frame': f, 'location': list(pb.location), 'scale': list(pb.scale)})
    for visual in (False, True):
        sk_out = directory / f'{visual}.skl'
        an_out = directory / f'{visual}.anm'
        export_skl.write_skl(str(sk_out), arm, use_visual_pose=visual)
        export_anm.write_anm(str(an_out), arm, visual_mode=visual, use_scene_range=True)
        record['visual_export' if visual else 'default_export'] = measure(arm, sk_out, an_out, count, visual_bind=visual)
    record['prototype_export'] = prototype(arm, names, directory, count)
    if authored and new and new == 'cape':
        poses = {}
        for f in range(1, count+1):
            bpy.context.scene.frame_set(f)
            poses[f] = arm.pose.bones[new].matrix.copy()
        old_action = arm.animation_data.action
        arm.animation_data.action = bpy.data.actions.new('custom_track_reimport')
        import_anm.apply_anm(import_anm.read_anm(directory / 'prototype.anm'), arm)
        max_error = 0.
        for f in range(1, count+1):
            bpy.context.scene.frame_set(f)
            max_error = max(max_error, (poses[f].translation - arm.pose.bones[new].matrix.translation).length * 100)
        record['matching_custom_track_import_head_error_game_units'] = max_error
        arm.animation_data.action = old_action
    if new:
        stored = arm.pose.bones[new].get('native_bone_index')
        record['duplicate_native_index'] = int(stored) if stored is not None else None
        before = {b.name: b.parent.name if b.parent else None for b in arm.pose.bones}
        fixes = export_skn.fix_custom_bone_parenting(arm)
        after = {b.name: b.parent.name if b.parent else None for b in arm.pose.bones}
        record['export_button_reparenting'] = [(n, before[n], after[n]) for n in before if before[n] != after[n]]
    print(json.dumps({k: record[k] for k in ('asset', 'orientation', 'edit', 'edit_before_animation_import')}))
    print('errors:', [(key, round(record[key]['deformation_error_game_units'], 6)) for key in ('default_export','visual_export','prototype_export')])
    return record


sk, an = synthetic()
results = []
for orientation in ('NATIVE', 'VISUAL'):
    for edit in ('unchanged', 'leaf', 'duplicate', 'duplicate_renamed', 'insert_parent', 'insert_root', 'authored_leaf', 'authored_duplicate', 'authored_duplicate_renamed'):
        for before in ((True, False) if edit in ('insert_parent', 'duplicate') else (True,)):
            try:
                results.append(run(sk, an, orientation, edit, before))
            except Exception as error:
                import traceback
                traceback.print_exc()
                results.append({'orientation': orientation, 'edit': edit, 'before': before, 'error': str(error)})
if args.real_skl and args.real_anm:
    for orientation in ('NATIVE', 'VISUAL'):
        for edit in ('unchanged', 'leaf', 'duplicate_renamed', 'insert_parent'):
            try:
                results.append(run(args.real_skl, args.real_anm, orientation, edit, real=True))
            except Exception as error:
                import traceback
                traceback.print_exc()
                results.append({'asset':'Seraphine', 'orientation':orientation, 'edit':edit, 'error':str(error)})
(args.output / 'results.json').write_text(json.dumps(results, indent=2))
errors = [r for r in results if 'error' in r]
(args.output / 'provenance.json').write_text(json.dumps({
    'blender': bpy.app.version_string,
    'case_count': len(results),
    'failed_cases': errors,
    'real_clip_frames_sampled': 8 if args.real_anm else 0,
    'notes': 'Expected regressions are measurements, not assertions. Prototype uses known original names from source SKL; production identity resolution is not implemented.'
}, indent=2))
print('Diagnostic results:', args.output / 'results.json')
if errors:
    raise RuntimeError(f'{len(errors)} diagnostic cases could not run')
