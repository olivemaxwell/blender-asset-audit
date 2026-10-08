# Blender asset audit and export recorder

A single-pass audit for a Blender scene destined for a game engine. It checks every
mesh, writes a CSV report, and optionally exports each mesh to FBX while recording
the exact export settings used. Version 1.1 adds a vehicle readiness check for
assets with moving parts.

## What it checks

- Triangle count with modifiers applied, measured in world space
- Scale applied, so nothing arrives in engine at the wrong size
- UV map present
- Ngons, which the FBX exporter cannot compute tangent space for
- Empty or missing material slots
- Mesh data named to match its object
- Texel density per mesh, measured in pixels per centimetre against a target or the
  scene median
- LOD chains: every LODn must have fewer triangles than LODn-1
- **Vehicle readiness (v1.1):** pivots, hierarchy and wheel geometry. See below.

## Blocking versus advisory

**Blocking** issues stop that mesh from exporting: scale not applied, a missing UV
map, a missing material or empty slot, a LOD that fails to drop, and the five
blocking vehicle checks. **Advisory** issues still export, and `export_record.json`
lists them by name: ngons, texel density variation, wheel size and triangle share.

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

## Vehicle readiness check (v1.1)

Checks that a vehicle will rig and animate correctly before it reaches the engine.
Built and tested on an airport baggage cart with eight moving parts: a steering front
axle, a hinged drawbar, four wheels and two drop-down side panels.

| # | Check | Passes when | Type |
|---|---|---|---|
| 1 | Wheel pivot centred | Origin within 0.1 cm of the wheel's own centre | Blocking |
| 2 | Spin axes agree | All wheels spin around the same local axis | Blocking |
| 3 | Hierarchy correct | Front wheels and drawbar are parented to the front axle | Blocking |
| 4 | Rotation applied | No leftover rotation on any part at rest | Blocking |
| 5 | Wheel size | Diameter matches the real tyre, within 1 cm | Advisory |
| 6 | Triangle share | No single mesh uses more than 25% of the asset's triangles | Advisory |
| 7 | Left and right wheels match | Same diameter, height and distance from the centreline, within 0.1 cm | Blocking |

All settings sit at the top of the script, so the check works on any vehicle, not
just this cart: `WHEEL_PATTERN`, `FRONT_AXLE_PATTERN`, `FRONT_AXLE_CHILDREN`,
`WHEEL_DIAMETER_CM`, `DIAMETER_TOLERANCE_CM`, `PIVOT_TOLERANCE_CM`,
`MATCH_TOLERANCE_CM` and `TRI_SHARE_WARN`. The CSV gains columns for pivot offset,
spin axis, hierarchy, rotation, diameter, height, track and triangle share.

### Result on the baggage cart

- **9 meshes, 15,880 triangles, no blocking issues.** Advisories only: ngons
  (triangulated on export), texel density spread across the three texture sets, and
  the drawbar at 29% of the triangles, accepted because the spring is the cart's most
  detailed part. Output: `asset_audit_baggage_cart.csv`.
- **Proved on a broken copy:** each of the seven checks caught the fault planted for it.
- **The triangle-share check came from a real problem.** On the first audit, the
  drawbar was 21,300 triangles, 65% of the whole cart, from the default torus
  resolution on the spring coils. Reducing the coil segments and raising the bevel
  angle limit brought it to 4,564, and the cart from 32.6k to 15.9k. The check makes
  that something the tool catches, not something I have to spot.

### Fixed in v1.1: texel density in centimetre scenes

On the cart, the audit reported texel density as 0.03 to 0.23 px/cm, about 100
times too low. The script assumed 1 Blender unit is 1 metre, but the cart uses a
Unit Scale of 0.01 (centimetres). Face areas are now converted to square metres
using the scene's unit scale before the density is worked out. The corrected values
are 3.30 to 22.82 px/cm. Scenes at a Unit Scale of 1.0 are unaffected.

### Limitations

- The vehicle checks read their tolerances in Blender units, so they assume a
  centimetre scene (Unit Scale 0.01).
- Parts are found by name, so the vehicle must follow the naming in the settings.
- The texel target defaults to the scene median, so it shifts when one mesh changes.
  Set `TARGET_TEXEL_DENSITY` (px per metre) for a fixed target.
- Triangle share is relative, so one heavy mesh can hide another. A fixed triangle
  budget is planned.
- There is no material consistency check yet. On the cart, one wheel had the body
  material assigned. The audit passed it, and it only showed up in Substance Painter.

## Usage

1. Open the .blend, go to the Scripting workspace, open this file in the Text Editor.
2. Adjust the SETTINGS block: texture resolution, texel target and tolerance, an
   optional naming pattern, the vehicle settings, and whether to export.
3. With `ONLY_SELECTED = True`, select the meshes to audit (A in the viewport).
4. Run. The report prints to the system console and writes `asset_audit.csv` beside
   the .blend. With `EXPORT_FBX = True` it also writes FBX files and
   `export_record.json` into an `export` folder.

Tested in Blender 5.0 and 5.1.

## Credit

Written and maintained by Olive Maxwell. Vehicle readiness checks 1 to 6 were typed
with step-by-step guidance from an AI assistant; check 7 was written independently.
Tested on my own production files and on deliberately broken copies.

## Changelog

**v1.1**
- Vehicle readiness check: 7 checks for pivots, hierarchy and wheel geometry
- Triangle-share warning (any mesh over 25% of the asset's triangles)
- Fix: texel density is now correct in scenes with a unit scale other than 1.0

**v1.0**
- Mesh audit, CSV report, blocking and advisory issues, FBX export with recorded settings
