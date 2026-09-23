# Blender asset audit and export recorder

A single-pass audit for a Blender scene destined for a game engine. It checks every
mesh, writes a CSV report, and optionally exports each mesh to FBX while recording
the exact export settings used.

## What it checks

- Triangle count with modifiers applied, measured in world space
- Scale applied, so nothing arrives in engine at the wrong size
- UV map present
- Ngons, which the FBX exporter cannot compute tangent space for
- Empty or missing material slots
- Texel density per mesh, measured in pixels per centimetre against a target or the
  scene median
- LOD chains: every LODn must have fewer triangles than LODn-1

## Blocking versus advisory

Scale not applied, a missing UV map, a missing material and a LOD that fails to drop
are **blocking**: those meshes are never exported. Ngons and texel density variation
are **advisory**: those meshes export, and `export_record.json` lists them by name so
the next person can see what was known at export time.

The first version blocked everything with any issue at all. On a real asset that let
through 4 meshes out of 41, which is a tool people turn off within a week.

## What it found on its first production run

Run against a 41 mesh A320 nose landing gear:

- 9,500 triangles total, confirming the LOD0 figure independently
- A measured texel density of 13.26 px/cm, where the asset's handover document had
  previously stated that density was enforced by method but never measured
- **29 tangent space warnings in one export run.** Every mesh containing an ngon was
  exporting with no tangent space at all, and the export still reported success.
  Unreal then recalculates tangents on import, so baked normal maps can read
  differently from what was authored.

Fixed by triangulating on export. Same 41 meshes, same script, one setting changed:
29 warnings became 0. `export_log.txt` in this repo contains both runs.

## Usage

1. Open the .blend, go to the Scripting workspace, open this file in the Text Editor.
2. Adjust the SETTINGS block: texture resolution, texel target and tolerance, an
   optional naming pattern, and whether to export.
3. Run. The report prints to the system console and writes `asset_audit.csv` beside
   the .blend. With `EXPORT_FBX = True` it also writes FBX files and
   `export_record.json` into an `export` folder.

Tested in Blender 5.0 and 5.1.
