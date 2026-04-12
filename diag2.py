"""
Aventurine Visual-Export Diagnostic 2
Paste into Blender Scripting tab and Run Script.
Checks: (1) which version of code is loaded, (2) backup ANM integrity,
(3) simulated visual-SKL globals for custom-parent bones.
Open Window > Toggle System Console before running.
"""
import bpy
import mathutils
import sys
import os
import inspect
import struct

P     = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
P_inv = P.inverted()

# ─────────────────────────────────────────────────────────────────────────────
# 1. Which modules are loaded and do they contain the fixes?
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 1. MODULE VERSIONS ===")

KEY_MODULES = ('export_skl', 'export_anm', 'import_anm', 'export_skn')
for mod_name, mod in sorted(sys.modules.items()):
    if not any(k in mod_name for k in KEY_MODULES):
        continue
    fpath = getattr(mod, '__file__', None) or '?'
    mtime = '?'
    if fpath != '?':
        try:
            import datetime
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass
    print(f"  {mod_name}")
    print(f"    file   : {fpath}")
    print(f"    mtime  : {mtime}")

    # Check for specific fix markers in source
    if 'export_skl' in mod_name:
        try:
            fn = getattr(mod, 'write_skl', None) or getattr(mod, 'calc_league_matrix', None)
            src = inspect.getsource(mod)
            has_chain_fix = 'custom_chain = np_global.inverted() @ bp_global' in src
            print(f"    has custom_chain fix : {has_chain_fix}  ← should be True")
        except Exception as e:
            print(f"    (source check failed: {e})")

    if 'import_anm' in mod_name:
        try:
            src = inspect.getsource(mod)
            has_hcp = '_has_custom_parent_set' in src
            has_identity_override = 'is_custom_parent and skip_custom_parent_pin' in src
            print(f"    has _has_custom_parent_set   : {has_hcp}  ← should be True")
            print(f"    has identity override block  : {has_identity_override}  ← should be True")
        except Exception as e:
            print(f"    (source check failed: {e})")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Backup ANM check — are the backups native or already-visual?
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 2. BACKUP ANM INTEGRITY ===")

arm = None
for obj in bpy.context.scene.objects:
    if obj.type == 'ARMATURE':
        arm = obj
        break

if not arm:
    print("ERROR: No armature found — skipping backup check")
else:
    skn_path = arm.get("lol_skn_filepath", "")
    if not skn_path:
        print("No lol_skn_filepath on armature — trying scene objects for a .skn path")
        for obj in bpy.context.scene.objects:
            p = obj.get("lol_skn_filepath", "")
            if p:
                skn_path = p
                break

    if skn_path:
        backup_dir = os.path.join(os.path.dirname(skn_path), "Unmodified_anm_backup")
        anim_dir   = os.path.join(os.path.dirname(skn_path), "animations")
        print(f"  SKN dir   : {os.path.dirname(skn_path)}")
        print(f"  backup dir: {backup_dir}  exists={os.path.isdir(backup_dir)}")
        print(f"  anim  dir : {anim_dir}   exists={os.path.isdir(anim_dir)}")

        # Find R_Clavicle pose bone
        rclav_pb = arm.pose.bones.get("R_Clavicle")
        rclav_native_t = rclav_pb.get("native_bind_t") if rclav_pb else None
        rclav_native_r = rclav_pb.get("native_bind_r") if rclav_pb else None

        if rclav_native_t:
            print(f"  native_bind_t for R_Clavicle: {[round(x,5) for x in rclav_native_t]}")
        if rclav_native_r:
            print(f"  native_bind_r for R_Clavicle: {[round(x,5) for x in rclav_native_r]}")

        # Hash("R_Clavicle")
        def elf_hash(s):
            h = 0
            for c in s:
                h = ((h << 4) + ord(c)) & 0xFFFFFFFF
                g = h & 0xF0000000
                if g:
                    h ^= g >> 24
                h &= ~g & 0xFFFFFFFF
            return h
        rclav_hash = elf_hash("R_Clavicle")
        print(f"  hash('R_Clavicle') = 0x{rclav_hash:08X}")

        def read_v4_anm_rclav(filepath):
            """Read a v4 uncompressed ANM and return R_Clavicle's first frame translation+rotation."""
            try:
                with open(filepath, 'rb') as f:
                    magic = f.read(8).decode('ascii', errors='replace')
                    version = struct.unpack('<I', f.read(4))[0]
                    if magic != 'r3d2anmd' or version != 4:
                        return f"NOT v4 uncompressed (magic={magic!r} ver={version})"
                    filesize = struct.unpack('<I', f.read(4))[0]
                    format_token, unk, flags = struct.unpack('<III', f.read(12))
                    joint_count, frame_count = struct.unpack('<II', f.read(8))
                    frame_duration = struct.unpack('<f', f.read(4))[0]
                    fps = 1.0 / frame_duration if frame_duration > 0 else 30.0
                    f.read(12)  # unused offsets
                    vecs_offset = struct.unpack('<i', f.read(4))[0] + 12
                    quats_offset = struct.unpack('<i', f.read(4))[0] + 12
                    frames_offset = struct.unpack('<i', f.read(4))[0] + 12

                    # Read vec palette
                    f.seek(vecs_offset)
                    num_vecs = (quats_offset - vecs_offset) // 12
                    vecs = []
                    for _ in range(num_vecs):
                        x, y, z = struct.unpack('<fff', f.read(12))
                        vecs.append((x, y, z))

                    # Read quat palette
                    f.seek(quats_offset)
                    num_quats = (frames_offset - quats_offset) // 16
                    quats = []
                    for _ in range(num_quats):
                        x, y, z, w = struct.unpack('<ffff', f.read(16))
                        quats.append((w, x, y, z))  # (w,x,y,z)

                    # Find R_Clavicle in frame 0
                    f.seek(frames_offset)
                    for j in range(joint_count):
                        h, t_id, s_id, r_id, pad = struct.unpack('<IHHHh', f.read(12))
                        if h == rclav_hash:
                            t = vecs[t_id] if t_id < len(vecs) else (0,0,0)
                            r = quats[r_id] if r_id < len(quats) else (1,0,0,0)
                            return (t, r, joint_count, frame_count, fps)
                return "R_Clavicle hash not found in frame 0"
            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"ERROR: {e}"

        # Check one backup file
        if os.path.isdir(backup_dir):
            backup_files = sorted(f for f in os.listdir(backup_dir) if f.lower().endswith('.anm'))
            if backup_files:
                test_file = os.path.join(backup_dir, backup_files[0])
                print(f"\n  Checking backup: {backup_files[0]}")
                result = read_v4_anm_rclav(test_file)
                if isinstance(result, tuple):
                    t, r, jcount, fcount, fps = result
                    print(f"    joints={jcount}  frames={fcount}  fps={fps:.1f}")
                    print(f"    R_Clavicle frame-0 translation: {tuple(round(x,5) for x in t)}")
                    print(f"    R_Clavicle frame-0 rotation(wxyz): {tuple(round(x,5) for x in r)}")
                    # Diagnose: native ANM has non-zero translation; visual ANM would have ≈0 translation
                    t_len = (t[0]**2 + t[1]**2 + t[2]**2)**0.5
                    r_dist = abs(r[0] - 1.0) + abs(r[1]) + abs(r[2]) + abs(r[3])
                    is_identity_like = t_len < 0.01 and r_dist < 0.01
                    print(f"    |translation| = {t_len:.5f},  rot_dist_from_identity = {r_dist:.5f}")
                    if is_identity_like:
                        print(f"    *** BACKUP LOOKS LIKE VISUAL ANM (identity pose) — not original native! ***")
                        print(f"    *** You need to restore original native ANMs to the backup folder! ***")
                    else:
                        print(f"    Backup looks like native ANM (non-identity R_Clavicle pose) ✓")
                else:
                    print(f"    Result: {result}")
            else:
                print("  No .anm files in backup_dir")
        else:
            print("  Backup dir does not exist — first run will create it from anim_dir")
            # Check if anim_dir has native ANMs
            if os.path.isdir(anim_dir):
                anim_files = sorted(f for f in os.listdir(anim_dir) if f.lower().endswith('.anm'))
                if anim_files:
                    test_file = os.path.join(anim_dir, anim_files[0])
                    print(f"\n  Checking anim dir: {anim_files[0]}")
                    result = read_v4_anm_rclav(test_file)
                    if isinstance(result, tuple):
                        t, r, jcount, fcount, fps = result
                        print(f"    R_Clavicle frame-0 t: {tuple(round(x,5) for x in t)}")
                        print(f"    R_Clavicle frame-0 r(wxyz): {tuple(round(x,5) for x in r)}")
    else:
        print("  No lol_skn_filepath found — skipping backup check")

# ─────────────────────────────────────────────────────────────────────────────
# 3. Simulate visual-SKL globals for R_Clavicle using current bone data
# ─────────────────────────────────────────────────────────────────────────────
print("\n=== 3. SIMULATED VISUAL-SKL GLOBALS (use_visual_pose=True path) ===")

if not arm:
    print("No armature — skip")
else:
    def fmt_mat_trs(m, prefix=""):
        t, r, s = m.decompose()
        print(f"{prefix}translation: ({t.x:.5f}, {t.y:.5f}, {t.z:.5f})")
        print(f"{prefix}rotation(wxyz): ({r.w:.4f}, {r.x:.4f}, {r.y:.4f}, {r.z:.4f})")

    for bone_name in ("R_Clavicle", "R_Clavicle.001"):
        pb = arm.pose.bones.get(bone_name)
        if not pb:
            print(f"  {bone_name!r}: not found")
            continue
        print(f"\n  Bone: {bone_name!r}")
        print(f"    Blender parent: {pb.parent.name if pb.parent else None!r}")

        # use_visual_pose path: b_local = parent_mat_local.inv @ bone_mat_local
        b_global = pb.bone.matrix_local.copy()  # rest matrix in armature space
        if pb.parent:
            parent_b_global = pb.parent.bone.matrix_local.copy()
            try:
                b_local = parent_b_global.inverted() @ b_global
            except:
                b_local = b_global
        else:
            b_local = b_global
        l_mat_local = P_inv @ b_local @ P
        fmt_mat_trs(l_mat_local, prefix="    local  (visual-pose path): ")

        # Also show what the native_bind path gives (if data exists)
        nb_t = pb.get("native_bind_t")
        nb_r = pb.get("native_bind_r")
        nb_s = pb.get("native_bind_s")
        if nb_t and nb_r and nb_s:
            lm_t = mathutils.Matrix.Translation(mathutils.Vector(nb_t))
            lm_r = mathutils.Quaternion(nb_r).to_matrix().to_4x4()
            lm_s = mathutils.Matrix.Diagonal((nb_s[0], nb_s[1], nb_s[2], 1.0))
            nb_local = lm_t @ lm_r @ lm_s
            t2, r2, s2 = nb_local.decompose()
            print(f"    local  (native_bind):         t=({t2.x:.5f},{t2.y:.5f},{t2.z:.5f})  r=({r2.w:.4f},{r2.x:.4f},{r2.y:.4f},{r2.z:.4f})")

    # Compute global for R_Clavicle.001 and R_Clavicle using visual-pose path
    print(f"\n  Global chain (visual-pose path):")
    globals_vp = {}
    for pb in arm.pose.bones:
        b_global = pb.bone.matrix_local.copy()
        if pb.parent:
            try:
                b_local = pb.parent.bone.matrix_local.inverted() @ b_global
            except:
                b_local = b_global
        else:
            b_local = b_global
        l_local = P_inv @ b_local @ P
        p_global = globals_vp.get(pb.parent.name if pb.parent else None, mathutils.Matrix.Identity(4))
        globals_vp[pb.name] = p_global @ l_local

    for name in ("Chest", "R_Clavicle.001", "R_Clavicle"):
        g = globals_vp.get(name)
        if g:
            t, r, s = g.decompose()
            print(f"    {name}: global t=({t.x:.5f},{t.y:.5f},{t.z:.5f})")

    # Now show original globals from stored native_global_rest_mat
    print(f"\n  Original globals (native_global_rest_mat):")
    for name in ("Chest", "R_Clavicle"):
        pb = arm.pose.bones.get(name)
        if not pb:
            continue
        stored = pb.get("native_global_rest_mat")
        if stored and len(stored) == 16:
            g = mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
            t, r, s = g.decompose()
            print(f"    {name}: global t=({t.x:.5f},{t.y:.5f},{t.z:.5f})")
        else:
            print(f"    {name}: no native_global_rest_mat stored")

    # Check if visual-pose global of R_Clavicle.001 ≈ original global of R_Clavicle
    g_001 = globals_vp.get("R_Clavicle.001")
    pb_rc = arm.pose.bones.get("R_Clavicle")
    if g_001 and pb_rc:
        stored = pb_rc.get("native_global_rest_mat")
        if stored and len(stored) == 16:
            g_orig = mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
            diff = g_001.inverted() @ g_orig
            dt, dr, ds = diff.decompose()
            t_err = dt.length
            r_err = abs(dr.w - 1.0) + abs(dr.x) + abs(dr.y) + abs(dr.z)
            print(f"\n  G_001 ≈ G_R_Clavicle_original? t_err={t_err:.6f}  r_err={r_err:.6f}")
            if t_err < 1e-3 and r_err < 1e-3:
                print("    ✓ MATCH — visual SKL positions R_Clavicle.001 at R_Clavicle's original position")
            else:
                print("    ✗ MISMATCH — there may be an issue with the bone placement")

print("\n=== Done ===")
