"""
Aventurine Visual-Export Diagnostic
Paste into Blender Scripting tab and Run Script.
Prints key matrix data for every is_custom_parent bone to the System Console.
(Window > Toggle System Console to see output)
"""
import bpy
import mathutils

P = mathutils.Matrix(((-1, 0, 0, 0), (0, 0, -1, 0), (0, 1, 0, 0), (0, 0, 0, 1)))
P_inv = P.inverted()

arm = None
for obj in bpy.context.scene.objects:
    if obj.type == 'ARMATURE':
        arm = obj
        break

if not arm:
    print("ERROR: No armature found")
else:
    print(f"\n=== Armature: {arm.name} ===")

    # Build native bone set (same logic as apply_anm)
    seen_idx = {}
    native_bone_names = set()
    for pb in arm.pose.bones:
        idx = pb.get("native_bone_index")
        if idx is None:
            continue
        has_suffix = '.' in pb.name
        if idx not in seen_idx:
            seen_idx[idx] = (has_suffix, pb.name)
            native_bone_names.add(pb.name)
        elif not has_suffix and seen_idx[idx][0]:
            old = seen_idx[idx][1]
            native_bone_names.discard(old)
            seen_idx[idx] = (False, pb.name)
            native_bone_names.add(pb.name)

    def is_native(pb):
        return pb.name in native_bone_names

    def get_stored_ml(pb):
        stored = pb.get("native_matrix_local")
        if stored and len(stored) == 16:
            return mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
        return pb.bone.matrix_local.copy()

    # Corrections
    def get_native_global(pb):
        stored = pb.get("native_global_rest_mat")
        if stored and len(stored) == 16:
            return mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
        return pb.bone.matrix_local.copy()

    corrections = {}
    for pb in arm.pose.bones:
        v_g = get_stored_ml(pb)
        n_g = get_native_global(pb)
        try:
            corrections[pb.name] = n_g.inverted() @ v_g
        except:
            corrections[pb.name] = mathutils.Matrix.Identity(4)

    print(f"\nNative bones ({len(native_bone_names)}): {sorted(native_bone_names)}")
    custom_bones = [pb.name for pb in arm.pose.bones if pb.name not in native_bone_names]
    print(f"Custom bones: {custom_bones}")

    has_custom_parent_set = set()
    for pb in arm.pose.bones:
        if pb.name in native_bone_names:
            if pb.parent is not None and not is_native(pb.parent):
                has_custom_parent_set.add(pb.name)

    print(f"\n_has_custom_parent_set (native bones with custom Blender parent): {has_custom_parent_set}")

    print("\n--- Per-bone detail for custom-parent chain ---")
    for pb in arm.pose.bones:
        if pb.name not in native_bone_names:
            continue

        is_cp = pb.parent is not None and not is_native(pb.parent)
        native_parent = pb.get("native_parent", "")
        in_custom_set = native_parent in has_custom_parent_set or (pb.parent and pb.parent.name in has_custom_parent_set)

        if not is_cp and not in_custom_set:
            continue  # skip unaffected bones

        print(f"\n  Bone: {pb.name!r}")
        print(f"    blender_parent: {pb.parent.name if pb.parent else None!r}")
        print(f"    native_parent (stored): {native_parent!r}")
        print(f"    native_bone_index: {pb.get('native_bone_index')}")
        print(f"    is_custom_parent: {is_cp}")
        print(f"    in _has_custom_parent_set via parent: {in_custom_set}")

        # Check native bind data
        nb_t = pb.get("native_bind_t")
        nb_r = pb.get("native_bind_r")
        nb_s = pb.get("native_bind_s")
        print(f"    has native_bind_t/r/s: {bool(nb_t)}/{bool(nb_r)}/{bool(nb_s)}")
        if nb_t:
            print(f"    native_bind_t: {[round(x,5) for x in nb_t]}")

        # Correction matrix — is it identity?
        C = corrections.get(pb.name, mathutils.Matrix.Identity(4))
        is_id = all(abs(C[i][j] - (1.0 if i==j else 0.0)) < 1e-4 for i in range(4) for j in range(4))
        print(f"    corrections[{pb.name!r}] is_identity: {is_id}")
        if not is_id:
            t, r, s = C.decompose()
            print(f"      correction rot (wxyz): ({r.w:.4f}, {r.x:.4f}, {r.y:.4f}, {r.z:.4f})")

        if is_cp:
            # Find native ancestor
            native_anc = None
            cur = pb.parent
            while cur:
                if is_native(cur):
                    native_anc = cur
                    break
                cur = cur.parent
            print(f"    native ancestor (walk): {native_anc.name if native_anc else None!r}")

            # rest_v_local that apply_anm would use
            if native_anc:
                try:
                    rvl = get_stored_ml(native_anc).inverted() @ get_stored_ml(pb)
                    print(f"    rest_v_local = M_{native_anc.name}⁻¹ @ M_{pb.name}")
                    rvl_t, rvl_r, rvl_s = rvl.decompose()
                    print(f"      rvl rot (wxyz): ({rvl_r.w:.4f}, {rvl_r.x:.4f}, {rvl_r.y:.4f}, {rvl_r.z:.4f})")
                    print(f"      rvl translation: ({rvl_t.x:.5f}, {rvl_t.y:.5f}, {rvl_t.z:.5f})")
                except Exception as e:
                    print(f"    rest_v_local ERROR: {e}")

            # What blender internally uses
            if pb.parent:
                try:
                    blender_rvl = pb.parent.bone.matrix_local.inverted() @ pb.bone.matrix_local
                    brvl_t, brvl_r, brvl_s = blender_rvl.decompose()
                    print(f"    Blender internal rest_v_local (M_{pb.parent.name}⁻¹ @ M_{pb.name}):")
                    print(f"      rot (wxyz): ({brvl_r.w:.4f}, {brvl_r.x:.4f}, {brvl_r.y:.4f}, {brvl_r.z:.4f})")
                    print(f"      translation: ({brvl_t.x:.5f}, {brvl_t.y:.5f}, {brvl_t.z:.5f})")
                except Exception as e:
                    print(f"    Blender rvl ERROR: {e}")

        # For bones in has_custom_parent_set via native_parent
        if in_custom_set and not is_cp:
            print(f"    (This bone's native parent '{native_parent}' is in _has_custom_parent_set)")
            Cp = corrections.get(native_parent, mathutils.Matrix.Identity(4))
            is_id2 = all(abs(Cp[i][j] - (1.0 if i==j else 0.0)) < 1e-4 for i in range(4) for j in range(4))
            print(f"    corrections['{native_parent}'] is_identity: {is_id2}")

    # SKL export check: does native_parent match Blender parent for custom-parent bones?
    print("\n--- SKL export: native_parent vs blender_parent mismatch ---")
    for pb in arm.pose.bones:
        if pb.name not in native_bone_names:
            continue
        native_parent = pb.get("native_parent", "")
        blender_parent = pb.parent.name if pb.parent else ""
        if native_parent and blender_parent and native_parent != blender_parent:
            print(f"  {pb.name!r}: native_parent={native_parent!r}, blender_parent={blender_parent!r} <- MISMATCH")
            np_pb = arm.pose.bones.get(native_parent)
            bp_pb = pb.parent
            if np_pb and bp_pb:
                # Show what the custom_chain would be in League space
                def get_stored_ng(p):
                    stored = p.get("native_global_rest_mat")
                    if stored and len(stored) == 16:
                        return mathutils.Matrix([stored[0:4], stored[4:8], stored[8:12], stored[12:16]])
                    return p.bone.matrix_local.copy()
                # Approximate League globals via stored data
                ng_np = get_stored_ng(np_pb)  # native global of native parent
                ng_bp = get_stored_ng(bp_pb)  # native global of blender parent (custom bone — fallback to matrix_local)
                try:
                    custom_chain = ng_np.inverted() @ ng_bp
                    cc_t, cc_r, cc_s = custom_chain.decompose()
                    print(f"    custom_chain (native_parent.ng⁻¹ @ blender_parent.ng):")
                    print(f"      translation: ({cc_t.x:.5f}, {cc_t.y:.5f}, {cc_t.z:.5f})")
                    print(f"      rot (wxyz): ({cc_r.w:.4f}, {cc_r.x:.4f}, {cc_r.y:.4f}, {cc_r.z:.4f})")
                    is_chain_id = (cc_t.length < 1e-4 and abs(cc_r.w - 1.0) < 1e-4)
                    print(f"      is_identity (approx): {is_chain_id}")
                except Exception as e:
                    print(f"    custom_chain ERROR: {e}")

    print("\n=== Done ===")
