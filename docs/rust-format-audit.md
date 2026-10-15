# Rust release format audit

Compared on 2026-10-12 against the locally downloaded reference sources:

- [lol_maya](https://github.com/tarngaina/lol_maya), commit `362f3e219c28d91eee3aaf8b3082faf9256618b5`.
- [LtMAO / Lemon3D Maya](https://github.com/tarngaina/LtMAO/tree/hai/src/LtMAO/lemon3d/lemon_maya), commit `9c6ed6763c53f61dc43ce56893012f491f7dd63f`.
- [SKN layout reference](https://wiki.leaguetoolkit.dev/reference/file-formats/skn/) for the vertex layouts and BGRA channel order.

The reference checkouts are under `Desktop/Maya-References`. Only LtMAO's `src` tree was checked out; its bundled runtime was not installed. Maya itself was not installed or run. The Rust changes implement field handling independently; no Maya implementation code was copied into the library.

## Findings and changes

| Area | Finding and resulting behavior |
| --- | --- |
| SKN bounds | The current Rust builder already computes the AABB and bounding sphere from exported positions. Verified the Blender export with Lemon's independent SKN reader and nonzero, off-origin geometry. No replacement bounds algorithm was needed. |
| SKN vertex data | Rust could parse colors and tangents but the Python binding and Blender path discarded them. Binding now exposes/writes BGRA colors and float4 tangents; Blender preserves colors, tangent directions and handedness. Geometry/normal/UV edits invalidate the stored tangent basis and trigger recalculation. Color and tangent seams participate in exported vertex splitting. |
| SKN container | Preserve flags, prefix bytes and trailing bytes. Relative-index files are rebuilt from absolute Blender indices and correctly rebased per submesh on disk. Reject incompatible metadata when merging imported meshes. |
| SKN validation | Reject nonfinite geometry, negative/nonfinite weights, incorrect optional-buffer lengths, invalid submesh ranges, overlapping index ranges, overlong names and undeclared prefix data before serialization. Degenerate triangle removal keeps material assignments aligned. |
| SKL | Preserve skeleton name, asset, skeleton flags, joint flags and joint radius. Detect duplicate IDs/names, missing parents and cyclic ancestry before building an armature. Existing bind/inverse-bind and influence-palette handling remains in place. |
| Material identity | Keep source submesh/material names independently of Blender's automatically added suffixes. Names containing dots no longer lose their suffix during texture lookup. |
| BIN textures | Follow local linked BIN dependencies with cycle handling and root-document precedence. Recognize legacy as well as current diffuse sampler-name fields. Exact skin selection prevents `skin1` selecting `skin10`; full asset paths take priority over ambiguous local basenames. |
| DDS | Route skin, map and texture-reload paths through the existing Rust DDS decoder, with row order, channels and alpha checked through Blender. |
| SCO | Default export now matches unscaled import units. Scale is a multiplier, consistent with the UI. Preserve central/pivot values and per-face materials; remove creation of an unusable zero-length pivot bone. |
| SCB | Preserve file name, per-face materials, BGRA vertex colors and opaque trailing bytes. Fix absolute-versus-relative seeks in the optional `riot.scb` reader. New files no longer declare an unwritten optional tail. Reject unsupported layouts, invalid indices, conflicting per-corner colors and face-layout changes that would invalidate retained trailing data. |

Earlier fixes in this working tree also cover animation insertion, custom/split normals, concave triangulation, and avoiding silent loss of MAPGEO spatial data.

## Custom bones: comparison only

Lemon's Maya translators (`translator/skin.py` and `translator/anm.py`) export actual local joint transforms and the current parent hierarchy. Aventurine additionally keeps imported native bind data and applies special offset-holder, duplicate-bone and channel-pinning rules. The existing [custom-bone investigation](custom-bone-investigation.md) reproduces failures in those rules, including inserted intermediate parents and duplicated bones carrying copied native properties.

This audit does not rewrite that workflow. The comparison supports fixing the Blender transform/identity logic together across SKL and ANM rather than treating new bones as a missing file-format field.

## Validation and remaining limits

- 27 Blender 4.0 integration tests pass, including the independent Lemon SKN parse. Run `tests/test_format_sweep.py`; it includes the earlier backend tests. Set `LEMON_FORMATS` to LtMAO's `src/LtMAO/pyRitoFile` directory to enable the independent-reader case.
- Python-binding suite: 72 passed, 98 skipped because external sample assets are absent. New `test_mesh_metadata.py` uses synthetic assets and runs without private fixtures.
- The bundled Windows x64 extension was rebuilt from the library source. `vendor/wheel.json` records the wheel digest, base commit and digest of `vendor/ritoshark-source.patch`. Apply that patch to the base commit to recover the modified build source.
- Real champion/skin assets and in-game playback still need testing. Passing synthetic round trips is not a guarantee of every game asset's compatibility. The tests use Blender 4.0; newer Blender animation APIs require their own validation.
- SKN export targets v4.1 Basic/Color/Tangent layouts, not every historical or newer extended layout. Blender export retains its existing 65,535-vertex and 32-submesh limits.
- Visual-pose export of tangent-bearing SKNs currently stops with an explicit message; apply the pose before exporting. It does not silently write tangents from the undeformed mesh.
- SKN prefix/tail and SCB tail bytes are preserved opaquely. Their full semantics are not editable. SCB face-layout changes with retained tail data are rejected; local-origin/pivot tail semantics still need representative game assets for broader authoring support.
- BIN loading resolves available local dependencies; it does not fetch missing game files or reproduce arbitrary runtime patch/shader behavior.
- MAPGEO grid rebuilding and the custom-bone redesign remain separate work. See the earlier investigation and the explicit MAPGEO export modes.

No changes were made to the separate `Desktop/Ritoshark-Crates` checkout containing existing animation work.


## Texture lookup follow-up (2026-10-15)

The real Seraphine Jade export reproduced a texture-loading failure despite successful BIN discovery. Its `texture` fields use BIN `file` values (64-bit XXH64 references), which the Rust parser correctly returns as integers. The Blender loader treated them as path strings and raised `TypeError` while displaying the texture mapping.

Skin and material BIN adapters now resolve these references using the existing Rust `wad_hash` function and texture paths within the current extraction. String references remain supported. Converted DDS/PNG aliases are considered only when unambiguous, and missing hashes leave the affected material untextured rather than aborting every texture or falling back to an unrelated body texture. No Rust binary rebuild was required.

Validated all six Seraphine materials using the actual export, including packed image data and the body/hair/UltSpeaker assignments. The expanded suite passed 30 tests on Blender 4.0 with the optional reference and Seraphine fixtures enabled; the three texture-fix tests also passed on the user's Steam Blender 4.5.14 LTS. To include the real-asset check, set `AVENTURINE_SERAPHINE_SKN` to the exported `seraphine_base.skn` before running `tests/test_format_sweep.py`. That fixture is not distributed with the repository.

## SCB Rust migration and build flow (2026-10-16)

- SCB decoding and encoding now run through RitoShark, including the optional `riot.scb` reference reader. Blender retains coordinate conversion, mesh/material construction and export collection. The binding exposes version, flags, BGRA vertex colors and trailing bytes, and accepts those fields plus an explicit central point when building a mesh. Bounds are computed from exported geometry. Legacy 2.1/2.2/3.1 and modern 3.2 work; newly added vertex colors require 3.2.
- Rust validates authored names, finite geometry/UVs, indices, color counts/layout and the VCP tail length. The reader rejects unknown vertex layouts and invalid indices without allocating directly from unchecked file counts. Replacing geometry with an incompatible face layout cannot silently reuse its opaque tail. SCB local-origin/pivot and VCP tail bytes remain preserved metadata, not fully editable Blender attributes.
- The updater accepts packaged Windows x64 release assets only. It checks archive structure, embedded version and native-wheel metadata before swapping staged directories. It never falls back to overwriting loaded DLLs; a failed second rename restores the original folder. Old DLLs remain in the backup until restart. Five isolated updater tests cover malformed packages, unsupported platforms, locked folders, rollback and successful installation. A separate check using the real loaded DLL also successfully swapped a disposable installation and retained the old DLL.
- Normal PR/main/codex-branch CI builds fresh Rust, runs all fixture-free integration tests and publishes a test artifact. The release workflow also runs Rust mesh tests. Manual publication on `main` can recover a failed release without another version bump, while existing tags remain protected. Both workflow files pass actionlint 1.7.12; hosted Actions execution still requires pushing these changes.
- `scripts/build.py` snapshots local library source with a separate Git index, builds and validates a candidate add-on, then refreshes the development bundle and emits a ZIP. It pins Rust 1.98.1, Maturin 1.15.0 and Cargo.lock, supports Blender's embedded Python, and removes temporary tools/compiler output on completion or failure. The full command was exercised end to end. Nine release/build-flow tests pass.
- Skin imports and texture reloads now report missing BIN dependencies, unresolved texture references, missing files and decode failures through Blender warnings as well as the console.

Validation: 33 Blender integration tests pass on Blender 4.0 and 4.5.14, including the actual Seraphine skin and all 29 SCB files beside it. Direct Rust read/write preserves those SCBs byte-for-byte; Blender round trips preserve coordinates, faces, materials, colors, central points, flags and tails (bounds are regenerated, UVs use floating-point comparison). The Python-binding suite passes 84 tests; 98 external-fixture cases remain skipped. Rust mesh tests pass, with external-file cases returning early when their sample directory is absent. ZIP registration/native-loading smoke checks pass. No in-game test or GitHub publication was performed.

MAPGEO and legacy SCO retain their Python geometry implementations. The custom-bone redesign remains separate.
