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
5. If an update is available, install it with one click

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

MAPGEO geometry and SCB/SCO retain their existing Python format implementation for now.
The binding does not yet expose all of their editable lighting, vertex-color, flag,
and pivot metadata, or an SCO writer. These paths remain available with their existing
options; MAPGEO material and texture loading already uses RitoShark.

The binding lives in `vendor/ritoshark`, loaded as an addon-local package without changing
Blender's Python search path. `vendor/wheel.json` records the exact wheel and SHA-256.
Restart Blender after updating the addon so its loaded native module is replaced.

To replace the bundled binding with a wheel built from RitoShark-Crates:

```sh
python scripts/vendor_ritoshark.py path/to/ritoshark-0.1.0-cp39-abi3-win_amd64.whl
```

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
