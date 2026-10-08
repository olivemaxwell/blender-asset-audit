"""
om_asset_audit.py  |  Olive Maxwell
Blender asset audit and export recorder.

CREDIT: [edit before publishing, e.g. "Drafted with an AI coding assistant, then
tested, run on production files and extended by Olive Maxwell."]

WHY THIS EXISTS
Before every export of the A320 nose gear I was checking the same things by hand,
object by object: is scale applied, does it have a UV map, is the name right, is the
texel density close to target, did each LOD actually drop in triangle count. With 41
meshes and four LODs that is slow, and a single missed "scale not applied" only shows
up once the asset is already in Unreal at the wrong size.

This script does those checks in one pass, prints a report, writes it to CSV next to
the .blend, and can optionally export each mesh to FBX while recording the exact
export settings used (so the handover document can quote them instead of guessing).

HOW TO USE
1. Open the .blend, switch to the Scripting workspace, open this file in the Text Editor.
2. Edit the SETTINGS block below if your texture size or naming rule differs.
3. Press Run Script. Read the report in the system console (Window > Toggle System Console
   on Windows) or open the CSV it writes beside the .blend.

Tested in Blender 5.0. Uses only standard bpy and bmesh calls that also exist in 4.x.
"""

import bpy
import bmesh
import csv
import json
import math
import os
import re
from collections import defaultdict
from mathutils import Vector

# ---------------------------------------------------------------- SETTINGS
TEXTURE_RES = 2048              # texture size the UVs are laid out for (px)
TARGET_TEXEL_DENSITY = None     # px per metre; None = use the median of all meshes as target
TEXEL_TOLERANCE = 0.20          # flag anything more than 20% off target
NAME_PATTERN = r"^SM_BaggageCart_[A-Za-z_]+$"             # e.g. r"^SM_[A-Za-z0-9_]+$" to enforce an SM_ prefix; None = skip the check
LOD_PATTERN = r"^(?P<base>.+)_LOD(?P<lod>\d+)$"
ONLY_SELECTED = True           # True = audit selected objects only
EXPORT_FBX = False              # True = also export each passing mesh to FBX
EXPORT_FOLDER = "//export"      # // means "relative to the .blend file"

# Vehicle readiness check
WHEEL_PATTERN = "Wheel_"
FRONT_AXLE_PATTERN = "FrontAxle"
FRONT_AXLE_CHILDREN = ("Wheel_FL", "Wheel_FR", "Drawbar")
WHEEL_DIAMETER_CM = 46.0
DIAMETER_TOLERANCE_CM = 1.0
PIVOT_TOLERANCE_CM = 0.1
TRI_SHARE_WARN = 0.25
MATCH_TOLERANCE_CM = 0.1
PAIR_SWAP = {"_FL": "_FR", "_FR": "_FL", "_RL": "_RR", "_RR": "_RL"}

FBX_SETTINGS = {                # recorded verbatim in export_record.json
    "use_selection": True,
    "apply_unit_scale": True,
    "apply_scale_options": "FBX_SCALE_UNITS",
    "axis_forward": "-Y",
    "axis_up": "Z",
    "use_mesh_modifiers": True,
    "mesh_smooth_type": "FACE",
    "use_tspace": True,            # writes tangents, which normal maps need
    "use_triangles": True,         # triangulate on export: the FBX exporter cannot compute
                                   # tangent space for a mesh containing ngons, so without
                                   # this any ngon mesh ships with no tangents at all
    "add_leaf_bones": False,
    "bake_anim": False,
    "path_mode": "AUTO",
}


# ---------------------------------------------------------------- MEASURING
def world_bmesh(obj, depsgraph):
    """Mesh as it will actually export: modifiers applied, in world space."""
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.transform(obj.matrix_world)
    eval_obj.to_mesh_clear()
    return bm


def measure(obj, depsgraph):
    bm = world_bmesh(obj, depsgraph)
    tris = sum(len(f.verts) - 2 for f in bm.faces)
    ngons = sum(1 for f in bm.faces if len(f.verts) > 4)

    uv_layer = bm.loops.layers.uv.active
    world_area = sum(f.calc_area() for f in bm.faces)
    scale = bpy.context.scene.unit_settings.scale_length
    world_area *= scale ** 2   # Blender units to square metres
    uv_area = 0.0
    if uv_layer:
        for f in bm.faces:
            uvs = [l[uv_layer].uv for l in f.loops]
            # shoelace formula for the face's area in UV space
            a = 0.0
            for i in range(len(uvs)):
                x1, y1 = uvs[i]
                x2, y2 = uvs[(i + 1) % len(uvs)]
                a += x1 * y2 - x2 * y1
            uv_area += abs(a) * 0.5
    bm.free()

    # texel density: how many texture pixels cover one metre of surface
    density = None
    if uv_layer and world_area > 0 and uv_area > 0:
        density = math.sqrt(uv_area / world_area) * TEXTURE_RES

    return {
        "tris": tris,
        "ngons": ngons,
        "has_uv": uv_layer is not None,
        "texel_px_per_m": density,
    }


# ---------------------------------------------------------------- CHECKS
def scale_applied(obj):
    return all(abs(s - 1.0) < 1e-4 for s in obj.scale)

def rotation_applied(obj):
    return all(abs(a) < 1e-4 for a in obj.rotation_euler)

def material_problems(obj):
    if not obj.material_slots:
        return "no material"
    if any(slot.material is None for slot in obj.material_slots):
        return "empty material slot"
    return ""

# ---------------------------------------------------------------- VEHICLE CHECKS
def is_wheel(obj):
    return WHEEL_PATTERN in obj.name


def wheel_pivot_offset_cm(obj):
    """How far the origin sits from the centre of the wheel's own geometry."""
    corners = [Vector(c) for c in obj.bound_box]
    centre = sum(corners, Vector()) / 8
    return centre.length * bpy.context.scene.unit_settings.scale_length * 100

def wheel_spin_axis(obj):
    """The axle runs along the wheel's thinnest dimension."""
    d = obj.dimensions
    return "XYZ"[min(range(3), key=lambda i: d[i])]

def wheel_diameter_cm(obj):
    """A wheel's largest dimension is its diameter."""
    return max(obj.dimensions) * bpy.context.scene.unit_settings.scale_length * 100

def wheel_world_cm(obj):
    """World height, and distance from the centreline, of the wheel's pivot."""
    s = bpy.context.scene.unit_settings.scale_length * 100
    p = obj.matrix_world.translation
    return p.z * s, abs(p.x) * s

def hierarchy_problem(obj):
    """Front wheels and drawbar steer with FrontAxle; every other part hangs under the root."""
    parent = obj.parent
    if any(child in obj.name for child in FRONT_AXLE_CHILDREN):
        if parent is None or FRONT_AXLE_PATTERN not in parent.name:
            return f"should be parented to {FRONT_AXLE_PATTERN}"
    elif parent is None or parent.type != "EMPTY":
        return "should be parented to the root empty"
    return ""

def audit():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    pool = bpy.context.selected_objects if ONLY_SELECTED else bpy.context.scene.objects
    meshes = [o for o in pool if o.type == "MESH"]
    if not meshes:
        print("No mesh objects to audit.")
        return [], {}
    is_vehicle = any(FRONT_AXLE_PATTERN in o.name for o in meshes)

    rows = []
    for obj in sorted(meshes, key=lambda o: o.name):
        m = measure(obj, depsgraph)
        height, track = wheel_world_cm(obj) if is_wheel(obj) else (None, None)
        rows.append({
            "name": obj.name,
            "tris": m["tris"],
            "ngons": m["ngons"],
            "has_uv": m["has_uv"],
            "texel_px_per_m": m["texel_px_per_m"],
            "scale_applied": scale_applied(obj),
            "name_ok": NAME_PATTERN is None or re.match(NAME_PATTERN, obj.name) is not None,
            "materials": material_problems(obj),
            "data_name": obj.data.name,
            "pivot_offset_cm": wheel_pivot_offset_cm(obj) if is_wheel(obj) else None,
            "spin_axis": wheel_spin_axis(obj) if is_wheel(obj) else None,
            "hierarchy": hierarchy_problem(obj) if is_vehicle else "",
            "rotation_applied": rotation_applied(obj) if is_vehicle else True,
            "diameter_cm": wheel_diameter_cm(obj) if is_wheel(obj) else None,
            "height_cm": height,
            "track_cm": track,
        })

    # texel density target: fixed value, or the median of everything measured
    densities = sorted(r["texel_px_per_m"] for r in rows if r["texel_px_per_m"])
    target = TARGET_TEXEL_DENSITY or (densities[len(densities) // 2] if densities else None)
     # the axis most wheels spin on; any wheel that differs is the odd one out
    wheel_axes = [r["spin_axis"] for r in rows if r["spin_axis"]]
    common_axis = max(set(wheel_axes), key=wheel_axes.count) if wheel_axes else None
    total_tris = sum(r["tris"] for r in rows)
    rows_by_name = {r["name"]: r for r in rows}

    for r in rows:
        issues = []
        if not r["scale_applied"]:
            issues.append("scale not applied")
        if not r["rotation_applied"]:
            issues.append("rotation not applied")
        if not r["has_uv"]:
            issues.append("no UV map")
        if not r["name_ok"]:
            issues.append("name breaks convention")
        if r["ngons"]:
            issues.append(f"{r['ngons']} ngons")
        if r["materials"]:
            issues.append(r["materials"])
        off = r["pivot_offset_cm"]
        if off is not None and off > PIVOT_TOLERANCE_CM:
            issues.append(f"wheel pivot {off:.2f} cm off centre")
        if r["spin_axis"] and r["spin_axis"] != common_axis:
            issues.append(f"spin axis {r['spin_axis']}, other wheels spin on {common_axis}")
        if r["hierarchy"]:
            issues.append(r["hierarchy"])
        dia = r["diameter_cm"]
        if dia is not None and abs(dia - WHEEL_DIAMETER_CM) > DIAMETER_TOLERANCE_CM:
            issues.append(f"wheel {dia:.1f} cm across, real tyre is {WHEEL_DIAMETER_CM:.0f} cm")
        share = r["tris"] / total_tris if total_tris else 0
        r["tri_share"] = round(share, 3)
        if len(rows) > 1 and share > TRI_SHARE_WARN:
            issues.append(f"{share:.0%} of the asset's triangles")
        if r["diameter_cm"] is not None:
            for side, other in PAIR_SWAP.items():
                if side in r["name"]:
                    partner = rows_by_name.get(r["name"].replace(side, other))
                    if partner:
                        for label, key in (("diameter", "diameter_cm"),
                                           ("height", "height_cm"),
                                           ("distance from centre", "track_cm")):
                            a, b = r[key], partner[key]
                            if abs(a - b) > MATCH_TOLERANCE_CM:
                                issues.append(f"doesn't match {partner['name']}: {label} {a:.1f} vs {b:.1f} cm")
        if r["name"] != r["data_name"]:
            issues.append(f"mesh data named {r['data_name']}")
        d = r["texel_px_per_m"]
        if target and d and abs(d - target) / target > TEXEL_TOLERANCE:
            issues.append(f"texel density {d / target:.0%} of target")
        r["issues"] = "; ".join(issues)
       

    # LOD chains: every LODn should have fewer triangles than LODn-1
    chains = defaultdict(dict)
    for r in rows:
        hit = re.match(LOD_PATTERN, r["name"])
        if hit:
            chains[hit.group("base")][int(hit.group("lod"))] = r["tris"]
    lod_report = {}
    for base, lods in chains.items():
        levels = sorted(lods)
        steps = []
        for a, b in zip(levels, levels[1:]):
            ratio = lods[b] / lods[a] if lods[a] else 0
            steps.append({"from": a, "to": b, "tris": [lods[a], lods[b]],
                          "ratio": round(ratio, 3), "ok": lods[b] < lods[a]})
        lod_report[base] = steps

    # a LOD that does not drop in triangles is a failure on that mesh too,
    # so it is flagged in the table and never exported
    by_name = {r["name"]: r for r in rows}
    for base, steps in lod_report.items():
        for s in steps:
            if not s["ok"]:
                r = by_name[f"{base}_LOD{s['to']}"]
                note = f"LOD{s['to']} has more tris than LOD{s['from']}"
                r["issues"] = f"{r['issues']}; {note}" if r["issues"] else note

    return rows, {"target_px_per_m": target, "lods": lod_report}


# ---------------------------------------------------------------- REPORTING
def blend_dir():
    path = bpy.data.filepath
    return os.path.dirname(path) if path else bpy.app.tempdir


def report(rows, extra):
    target = extra["target_px_per_m"]
    print("\n" + "=" * 78)
    target_txt = f"{target / 100:.2f} px/cm" if target else "n/a"
    print(f"ASSET AUDIT  |  {len(rows)} meshes  |  texture {TEXTURE_RES}px  |  texel target {target_txt}")
    print("=" * 78)
    print(f"{'object':<34}{'tris':>8}{'px/cm':>8}  issues")
    for r in rows:
        d = f"{r['texel_px_per_m'] / 100:.2f}" if r["texel_px_per_m"] else "-"
        print(f"{r['name'][:33]:<34}{r['tris']:>8}{d:>8}  {r['issues'] or 'ok'}")

    total = sum(r["tris"] for r in rows)
    failing = [r for r in rows if r["issues"]]
    print("-" * 78)
    print(f"total triangles {total:,}   |   {len(failing)} of {len(rows)} meshes need attention")

    for base, steps in extra["lods"].items():
        chain = " > ".join(f"LOD{s['from']} {s['tris'][0]:,}" for s in steps)
        last = steps[-1]
        chain += f" > LOD{last['to']} {last['tris'][1]:,}"
        bad = [s for s in steps if not s["ok"]]
        print(f"LOD chain {base}: {chain}  {'OK' if not bad else 'LOD DOES NOT DROP'}")

    out = os.path.join(blend_dir(), "asset_audit.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"report written to {out}")
    return failing


# Issues that must stop an export, as opposed to advisory ones such as ngons or a
# texel density that differs from the median because it sits in a tighter set.
BLOCKING = ("scale not applied", "no UV map", "no material", "empty material slot",
            "LOD", "wheel pivot", "spin axis", "should be parented", "rotation not applied", "doesn't match")


def blocking_issues(issues):
    return [i for i in issues.split("; ") if i and any(i.startswith(b) for b in BLOCKING)]


def export(rows):
    folder = bpy.path.abspath(EXPORT_FOLDER)
    os.makedirs(folder, exist_ok=True)
    exported = []
    for r in rows:
        if blocking_issues(r["issues"]):
            continue                       # advisory issues still export; blocking ones do not
        obj = bpy.data.objects[r["name"]]
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        path = os.path.join(folder, obj.name + ".fbx")
        bpy.ops.export_scene.fbx(filepath=path, **FBX_SETTINGS)
        exported.append(os.path.basename(path))

    record = {
        "blend": os.path.basename(bpy.data.filepath) or "unsaved",
        "blender_version": bpy.app.version_string,
        "unit_scale": bpy.context.scene.unit_settings.scale_length,
        "fbx_settings": FBX_SETTINGS,
        "exported": exported,
        "skipped_blocked": [r["name"] for r in rows if blocking_issues(r["issues"])],
        "exported_with_advisories": {r["name"]: r["issues"] for r in rows
                                     if r["issues"] and not blocking_issues(r["issues"])},
    }
    with open(os.path.join(folder, "export_record.json"), "w") as fh:
        json.dump(record, fh, indent=2)
    print(f"exported {len(exported)} FBX files, settings recorded in export_record.json")


def main():
    rows, extra = audit()
    if not rows:
        return
    report(rows, extra)
    if EXPORT_FBX:
        export(rows)


if __name__ == "__main__":
    main()
