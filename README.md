# Aventurine: League Tools

> A comprehensive Blender addon for working with League of Legends game assets

Aventurine provides a native solution for handling League of Legends meshes, skeletons, and animations directly within Blender 4.0 and newer, eliminating the need for external converters.

![Aventurine Preview](https://github.com/user-attachments/assets/ccb4b2fd-de63-4a3e-aa69-49d37d3dba53)

---

## Table of Contents

- [Features](#features)
- [Installation](#installation)
- [Usage](#usage)
  - [Import and Export](#import-and-export)
  - [Physics Simulation](#physics-simulation)
  - [Animation Retargeting](#animation-retargeting)
  - [Updating](#updating)
- [Technical Details](#technical-details)
- [Credits](#credits)
- [License](#license)

---

## Features

**Core Functionality**
- **SKN Models**: Full import and export support for most SKN versions
- **SKL Skeletons**: Complete skeleton import and export capabilities
- **ANM Animations**: Native animation file handling

**Advanced Tools**
- **Physics System**: Built-in physics simulation based on Wiggle 2
- **Animation Retargeting**: Port animations between different character rigs
- **Auto-Updater**: Keep your addon current with the latest GitHub releases

---

## Installation

### Quick Start

1. Download the latest release from the [Releases](../../releases) page
2. Open Blender and navigate to **Edit → Preferences → Add-ons**
3. Click **Install...** and select the downloaded ZIP file
4. Search for "Aventurine" in the addon list
5. Enable the checkbox next to the addon

The addon is now ready to use.

---

## Usage

Access all Aventurine tools through the **N-panel** in the 3D Viewport (press `N` to toggle) under the **"Aventurine LoL"** tab.

### Import and Export

The main panel provides controls for importing and exporting League of Legends asset files:

- **SKN files**: Mesh data with vertex weights and materials
- **SKL files**: Skeletal armature structures
- **ANM files**: Animation data compatible with League rigs

**Important**: When exporting, verify your settings to ensure the correct armature or mesh is selected for export.

### Physics Simulation

Enable Animation Tools in the addon preferences to access the **Animation Tools** tab in the N-panel. This feature automates secondary motion for elements like hair, cloth, and accessories, providing realistic movement without manual keyframing.

### Animation Retargeting

The **LoL Retarget** option (available when enabled in preferences) allows you to transfer animations between different skeletons. This is particularly useful for:

- Adapting animations to custom rigs
- Sharing animations across different champions
- Testing animations on various character models

### Updating

Check for updates directly within Blender:

1. Navigate to **Edit → Preferences → Add-ons**
2. Find Aventurine in the addon list
3. Expand the addon details
4. Click **Check for Updates** in the preferences panel
5. If an update is available, install it and restart Blender before continuing work

The updater requires a packaged Windows x64 release ZIP, validates it before swapping folders, and retains the old installation until restart. If Windows locks the installation folder, it leaves the current add-on intact and asks you to close Blender and install the release ZIP manually. It never overwrites a loaded Rust DLL in place.

---

## Technical Details

**Compatibility**
- Blender 4.0 or newer
- Release ZIPs bundle the Windows x64 RitoShark binding (CPython 3.9+ abi3)
- Supports multiple SKN format versions
- Cross-compatible with League of Legends game files

**Architecture**
- SKN/SKL/ANM import and export use the RitoShark Python binding, shared with RitoShark Maya
- BIN material extraction and TEX decoding use RitoShark; textures load directly into Blender pixels
- No external dependencies or converters required
- Modular design for easy maintenance and updates

SCB import/export also uses Rust, including per-vertex BGRA colors, per-face materials/UVs, flags, central point and retained trailing metadata. Legacy SCB 2.1/2.2/3.1 and modern 3.2 are supported; adding vertex colors upgrades a legacy export to 3.2. Opaque tail data still requires unchanged face layout. MAPGEO geometry and legacy SCO remain on their Python implementations; MAPGEO materials and textures already use Rust.

The binding lives in `vendor/ritoshark`, loaded as an addon-local package without changing
Blender's Python search path. `vendor/wheel.json` records the exact wheel, SHA-256, and library source commit.
Restart Blender after updating the addon so its loaded native module is replaced.

To replace the bundled binding with a wheel built from RitoShark-Crates:

```sh
python scripts/vendor_ritoshark.py path/to/ritoshark-0.1.0-cp39-abi3-win_amd64.whl --source-commit FULL_LIBRARY_COMMIT_SHA
```

Build the wheel from a clean RitoShark-Crates checkout and obtain its full commit with `git rev-parse HEAD`. The source commit records which library code is bundled; editing the library checkout alone does not update the addon.

Use a wheel matching the host OS and architecture. Release packaging includes `vendor`
and its licenses. The committed wheel supports Windows x64 only.

Run integration tests with Python 3.11 and Blender's Python module, or a matching Blender
Python environment:

```sh
python -m pip install bpy==4.2.0
python tests/test_backend.py -v
```

---

## Credits

**Development Team**
- **Bud** - Creator/Developer
- **Frog** - Developer

**Acknowledgments**
- Based on the `lol_maya` plugin by [tarngaina](https://github.com/tarngaina)
- Physics implementation derived from [Wiggle 2](https://github.com/shteeve3d/wiggle) by shteeve3d
- File-format backend powered by [RitoShark](https://github.com/RitoShark/RitoShark-Crates)

See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for full license details of third-party components.

---

## License

This project is licensed under the **GNU General Public License v3.0 (GPLv3)**.

The GPLv3 license is required because the physics module incorporates code from Wiggle 2, which is distributed under the same license. This ensures compliance with open-source licensing requirements and allows the community to freely use, modify, and distribute the addon.

For full license details, see the [LICENSE](LICENSE) file in this repository.

---

**Questions or Issues?** Open an issue on the [GitHub Issues](../../issues) page.

## Development validation

The backend tests cover animation insertion (including overlapping clips), custom and
hard-edge normals, nonuniform object scaling, native DDS decoding, and MAPGEO spatial-data
preservation, alongside the original mesh/skeleton/animation round trips.

MAPGEO export defaults to **Preserve original** spatial data. It now stops without writing
when the source is missing, truncated, or uses a different version, instead of silently
exporting disabled bucket grids. **Disabled spatial data** remains an explicit experimental
choice; this does not generate new grids or guarantee in-game compatibility.

See [custom-bone investigation](docs/custom-bone-investigation.md) for reproduced issues
in the existing custom-bone workflow. That behavior has not been changed by these fixes.

See the [Rust format audit](docs/rust-format-audit.md) for the Maya comparison, field-preservation fixes, validation results and remaining limits. The current bundled extension includes local library changes: `vendor/wheel.json` records the base commit and a source patch with its SHA-256. Rebuild from that base plus the patch, or from the matching local library checkout.

To record a locally patched build, add `--source-patch path/to/source.patch` to the vendoring command. Run `tests/test_format_sweep.py` in Blender for the expanded integration suite.


## Automatic Rust releases

The `Release` GitHub Actions workflow retains the version-bump release flow for Windows x64:

1. Increase `bl_info["version"]` in `__init__.py` (for example, `3.2.0` to `3.2.1`).
2. Commit with the desired release notes in the commit message, then push or merge to `main`.
3. GitHub checks out the Rust commit configured in `rust-build.json`, verifies and applies its optional source patch, and builds a fresh ABI3 extension on a Windows runner.
4. Binding tests, Blender 4.2 integration tests and an extracted-ZIP registration check must pass before publication. Sample-dependent library tests skip when their fixtures are absent; the optional Lemon-reader test skips unless its source path is supplied.
5. GitHub publishes `Aventurine-VERSION.zip`. The release body is the full message of the pushed HEAD commit (the merge/squash commit when applicable), and the release tag points to that exact commit.

Pushing without increasing the version does not publish. Existing version tags/releases are left intact. Builds also upload a downloadable Actions artifact, retained for 14 days. To retry a failed release attempt, rerun its original workflow run; after changing code, run the workflow on `main` with **publish** enabled to release that same version. An existing tag is never replaced; if publication already created a tag, rerun the original failed publish job.

Use **Actions > Release > Run workflow** to build a test artifact from a chosen branch without publishing a release. GitHub exposes manual runs once this workflow is present on the default branch. The manual **publish** checkbox defaults off and is allowed only on `main`. This development branch does not publish automatically.

For Python-only changes, no library changes are needed. For Rust changes, either push the library changes to `RitoShark/RitoShark-Crates` and update `rust-build.json` to that commit, or update `vendor/ritoshark-source.patch` and its SHA-256 in `rust-build.json`. Remove the patch fields when the pinned library commit already includes those fixes. The workflow never follows an unpinned upstream branch.

`rust-build.json` selects the source for the next build. `vendor/wheel.json` records the provenance of the extension currently bundled in the checkout; CI refreshes this record in its release ZIP. Editing the build configuration does not change the local DLL. You do not need to commit a newly compiled DLL for GitHub to build a release.

The build uses Python 3.11, the Rust compiler version pinned in `rust-build.json`, Maturin 1.15.0 and Cargo's lockfile. Rust and Blender test dependencies are installed on GitHub's runner. The downloadable add-on contains the native extension, Python code and licenses, without the build toolchain. Linux and macOS builds are not configured.

For a local ZIP after vendoring the matching Windows wheel, run `python scripts/package_addon.py --output-dir dist`. The packager checks source provenance and uses an explicit runtime-file list, excluding tests, caches and unrelated local projects. `python tests/test_release.py -v` validates the release conditions without Blender.


### One-command local build

With Python 3.11 and `bpy==4.2.0` available:

```powershell
python scripts/build.py --library ..\RitoShark-Crates-Aventurine --output-dir dist
```

Or use Blender's embedded Python and a separate Blender executable for validation:

```powershell
& 'C:\Program Files\Blender Foundation\Blender 4.0\4.0\python\bin\python.exe' scripts/build.py --library ..\RitoShark-Crates-Aventurine --blender 'C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe' --output-dir dist
```

Git, Rust/rustup and Visual Studio C++ Build Tools must be installed. The script temporarily installs Maturin/pytest, reuses a matching installed Rust compiler, and builds from a clean snapshot. `--library` captures local crate edits and new source files into the source patch without changing the library's Git index. Its HEAD must match `rust-build.json`; update that pin explicitly when moving to another base commit. Omitting `--library` fetches the configured upstream commit and applies the recorded patch.

The command runs Rust mesh tests, Python binding tests, the Blender format suite and ZIP registration checks before refreshing the checkout's bundled extension/provenance and writing the ZIP. Temporary tools and compiler output are deleted, including on failure; Cargo's shared downloaded dependency cache remains. The script updates the development checkout, not Blender's installed add-on. Restart Blender after installing the resulting ZIP.

Pull requests and pushes to `main` or `codex/**` now use this same build command, exercising a freshly built Rust extension and the full fixture-free Blender suite. CI artifacts are retained for seven days. Local optional Seraphine/reference fixtures are not uploaded. Run `python tests/test_updater.py -v` and `python tests/test_release.py -v` for the dependency-free updater/build-flow checks.
