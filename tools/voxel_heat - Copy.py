"""
Geodesic Heat Diffuse Skinning - Reliable Python Implementation
================================================================

This implementation uses a geodesic distance approach that's more reliable
than pure voxel methods. It combines:
1. Direct vertex-to-bone distance for initial seeding
2. Mesh-aware propagation through vertex connectivity
3. Smooth falloff based on geodesic distance

Author: Aventurine Team
License: Same as Aventurine addon
"""

import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import intersect_point_line
import time
from collections import deque


def get_bone_distance(point, bone_head, bone_tail):
    """Get shortest distance from point to bone segment."""
    closest_point, t = intersect_point_line(point, bone_head, bone_tail)
    
    # Clamp to bone segment
    if t < 0.0:
        closest_point = bone_head
    elif t > 1.0:
        closest_point = bone_tail
    
    distance = (point - closest_point).length
    return distance, closest_point


def build_vertex_adjacency(mesh):
    """Build adjacency list for geodesic propagation."""
    adjacency = {i: set() for i in range(len(mesh.vertices))}
    edge_lengths = {}
    
    for edge in mesh.edges:
        v1, v2 = edge.vertices
        adjacency[v1].add(v2)
        adjacency[v2].add(v1)
        
        # Store edge length for geodesic distance
        length = (mesh.vertices[v1].co - mesh.vertices[v2].co).length
        edge_lengths[(v1, v2)] = length
        edge_lengths[(v2, v1)] = length
    
    return adjacency, edge_lengths


def compute_geodesic_distances(mesh_obj, bone_head, bone_tail, adjacency, edge_lengths, max_distance=None):
    """
    Compute geodesic distances from vertices to a bone using Dijkstra-like propagation.
    Returns dict of vertex_index -> distance
    """
    mesh = mesh_obj.data
    matrix = mesh_obj.matrix_world
    
    # Initialize distances
    distances = {}
    
    # Find closest vertices to bone (seed points)
    seed_vertices = []
    bone_length = (bone_tail - bone_head).length
    search_radius = max(bone_length * 2.0, 0.1)
    
    for vert in mesh.vertices:
        vert_world = matrix @ vert.co
        dist, _ = get_bone_distance(vert_world, bone_head, bone_tail)
        
        if dist < search_radius:
            distances[vert.index] = dist
            seed_vertices.append((dist, vert.index))
    
    if not seed_vertices:
        # Fallback: use closest vertex
        min_dist = float('inf')
        closest_idx = 0
        for vert in mesh.vertices:
            vert_world = matrix @ vert.co
            dist, _ = get_bone_distance(vert_world, bone_head, bone_tail)
            if dist < min_dist:
                min_dist = dist
                closest_idx = vert.index
        seed_vertices = [(min_dist, closest_idx)]
        distances[closest_idx] = min_dist
    
    # Priority queue for Dijkstra propagation
    seed_vertices.sort()
    queue = deque(seed_vertices)
    visited = set()
    
    # Propagate distances through mesh
    while queue:
        current_dist, current_idx = queue.popleft()
        
        if current_idx in visited:
            continue
        visited.add(current_idx)
        
        # Check if we've exceeded max distance
        if max_distance and current_dist > max_distance:
            continue
        
        # Propagate to neighbors
        for neighbor_idx in adjacency[current_idx]:
            if neighbor_idx in visited:
                continue
            
            edge_key = (current_idx, neighbor_idx)
            edge_len = edge_lengths.get(edge_key, 0.01)
            
            new_dist = current_dist + edge_len
            
            # Update if better distance found
            if neighbor_idx not in distances or new_dist < distances[neighbor_idx]:
                distances[neighbor_idx] = new_dist
                queue.append((new_dist, neighbor_idx))
    
    return distances


def compute_bone_weights(mesh_obj, armature, enabled_bones, falloff_power=2.0, normalize=True):
    """
    Compute weights using geodesic distances.
    
    Args:
        mesh_obj: Mesh object
        armature: Armature object
        enabled_bones: Set of bone names
        falloff_power: How sharply weights fall off (higher = sharper)
        normalize: Whether to normalize weights per vertex
    
    Returns:
        dict: vertex_index -> [(bone_name, weight), ...]
    """
    mesh = mesh_obj.data
    arm_matrix = armature.matrix_world
    
    print("  Building mesh adjacency...")
    adjacency, edge_lengths = build_vertex_adjacency(mesh)
    
    # Compute distances for each bone
    bone_distances = {}
    bone_lengths = {}
    
    print(f"  Computing geodesic distances for {len(enabled_bones)} bones...")
    for bone in armature.data.bones:
        if bone.name not in enabled_bones:
            continue
        
        head_world = arm_matrix @ bone.head_local
        tail_world = arm_matrix @ bone.tail_local
        bone_length = (tail_world - head_world).length
        bone_lengths[bone.name] = bone_length
        
        # Compute geodesic distances with adaptive max distance
        max_dist = bone_length * 4.0  # Bones influence up to 4x their length
        distances = compute_geodesic_distances(
            mesh_obj, head_world, tail_world, 
            adjacency, edge_lengths, max_dist
        )
        
        bone_distances[bone.name] = distances
    
    print("  Converting distances to weights...")
    vertex_weights = {}
    
    for vert_idx in range(len(mesh.vertices)):
        bone_weights = []
        
        for bone_name in enabled_bones:
            if bone_name not in bone_distances:
                continue
            
            distances = bone_distances[bone_name]
            if vert_idx not in distances:
                continue
            
            dist = distances[vert_idx]
            bone_len = bone_lengths[bone_name]
            
            # Adaptive falloff based on bone length
            falloff_radius = max(bone_len * 2.0, 0.05)
            
            if dist < falloff_radius:
                # Smooth falloff
                t = dist / falloff_radius
                weight = (1.0 - t) ** falloff_power
                
                if weight > 0.001:
                    bone_weights.append((bone_name, weight))
        
        if bone_weights:
            # Sort and take top 4
            bone_weights.sort(key=lambda x: x[1], reverse=True)
            bone_weights = bone_weights[:4]
            
            # Normalize
            if normalize:
                total = sum(w for _, w in bone_weights)
                if total > 0:
                    bone_weights = [(name, w / total) for name, w in bone_weights]
            
            vertex_weights[vert_idx] = bone_weights
    
    return vertex_weights


def smooth_weights_geodesic(mesh_obj, vertex_weights, adjacency, iterations=3, strength=0.5):
    """Smooth weights using geodesic propagation."""
    for iteration in range(iterations):
        new_weights = {}
        
        # Get all bone names
        all_bones = set()
        for weights in vertex_weights.values():
            for bone_name, _ in weights:
                all_bones.add(bone_name)
        
        # Build bone->vertex weight map
        bone_vertex_weights = {bone: {} for bone in all_bones}
        for vert_idx, weights in vertex_weights.items():
            for bone_name, weight in weights:
                bone_vertex_weights[bone_name][vert_idx] = weight
        
        # Smooth each bone's weights
        for bone_name in all_bones:
            weights = bone_vertex_weights[bone_name]
            
            for vert_idx in weights:
                current_weight = weights[vert_idx]
                
                # Average with neighbors
                neighbor_sum = 0.0
                neighbor_count = 0
                
                for neighbor_idx in adjacency.get(vert_idx, []):
                    if neighbor_idx in weights:
                        neighbor_sum += weights[neighbor_idx]
                        neighbor_count += 1
                
                if neighbor_count > 0:
                    neighbor_avg = neighbor_sum / neighbor_count
                    smoothed = current_weight * (1 - strength) + neighbor_avg * strength
                    
                    if bone_name not in bone_vertex_weights:
                        bone_vertex_weights[bone_name] = {}
                    bone_vertex_weights[bone_name][vert_idx] = smoothed
        
        # Rebuild vertex_weights
        new_vertex_weights = {}
        for vert_idx in vertex_weights:
            bone_weights = []
            for bone_name in all_bones:
                if vert_idx in bone_vertex_weights[bone_name]:
                    weight = bone_vertex_weights[bone_name][vert_idx]
                    if weight > 0.001:
                        bone_weights.append((bone_name, weight))
            
            if bone_weights:
                # Normalize
                bone_weights.sort(key=lambda x: x[1], reverse=True)
                bone_weights = bone_weights[:4]
                total = sum(w for _, w in bone_weights)
                if total > 0:
                    bone_weights = [(name, w / total) for name, w in bone_weights]
                new_vertex_weights[vert_idx] = bone_weights
        
        vertex_weights = new_vertex_weights
    
    return vertex_weights


def apply_voxel_heat_weights(mesh_obj, armature, vertex_weights, enabled_bones):
    """Apply computed weights to mesh vertex groups."""
    # Clear existing vertex groups for enabled bones
    for bone_name in enabled_bones:
        if bone_name in mesh_obj.vertex_groups:
            mesh_obj.vertex_groups.remove(mesh_obj.vertex_groups[bone_name])
        mesh_obj.vertex_groups.new(name=bone_name)
    
    # Apply weights
    for vert_idx, weights in vertex_weights.items():
        for bone_name, weight in weights:
            if bone_name in mesh_obj.vertex_groups:
                mesh_obj.vertex_groups[bone_name].add([vert_idx], weight, 'REPLACE')
    
    # Setup armature modifier
    arm_mod = None
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE':
            arm_mod = mod
            break
    
    if arm_mod is None:
        arm_mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
    
    arm_mod.object = armature
    arm_mod.use_vertex_groups = True
    
    # Parent mesh to armature
    mesh_obj.parent = armature
    mesh_obj.matrix_parent_inverse = armature.matrix_world.inverted()


def voxel_heat_diffuse_skinning(context, mesh_obj, armature, enabled_bones,
                                 resolution=80, iterations=15, falloff=0.85,
                                 max_influences=4):
    """
    Main entry point - now uses geodesic approach instead of voxels.
    Parameters kept for compatibility but some are repurposed:
    - iterations: smoothing iterations
    - falloff: falloff power (0.5-1.0 maps to power 1-4)
    """
    print("Starting Geodesic Heat Skinning...")
    start_time = time.time()
    
    # Set armature to REST pose
    original_pose = armature.data.pose_position
    armature.data.pose_position = 'REST'
    
    try:
        # Ensure mesh data is up to date
        mesh_obj.data.update()
        
        # Build adjacency once
        adjacency, edge_lengths = build_vertex_adjacency(mesh_obj.data)
        
        # Compute bone weights
        falloff_power = 1.0 + (falloff * 3.0)  # Map 0.5-1.0 to power 1.5-4.0
        vertex_weights = compute_bone_weights(
            mesh_obj, armature, enabled_bones,
            falloff_power=falloff_power,
            normalize=True
        )
        
        if not vertex_weights:
            print("  No weights computed!")
            return False
        
        # Smooth weights
        smooth_iterations = max(1, iterations // 5)  # Use parameter
        print(f"  Smoothing weights ({smooth_iterations} iterations)...")
        vertex_weights = smooth_weights_geodesic(
            mesh_obj, vertex_weights, adjacency,
            iterations=smooth_iterations, strength=0.5
        )
        
        # Apply weights
        apply_voxel_heat_weights(mesh_obj, armature, vertex_weights, enabled_bones)
        
        elapsed = time.time() - start_time
        print(f"Geodesic Heat Skinning completed in {elapsed:.2f} seconds")
        
        return True
        
    except Exception as e:
        print(f"Geodesic Heat Skinning failed: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Restore pose position
        armature.data.pose_position = original_pose