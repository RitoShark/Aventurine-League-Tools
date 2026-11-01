# SKN material-count safeguard

Surveyed on 2026-11-01 using installed game build **16.21.834.2754**.

## Findings

- Scanned **174 champion WAD archives** and **5,812 unique SKN assets** resolved by the local WAD hash dictionary.
- These include main skins, chromas, props and particle meshes; this is not a count of distinct skins.
- Maximum: **31 submesh ranges**. Four files have 31; none have 32 or more. No inspected SKN header failed to parse.
- Coverage is limited to known SKN path hashes in those installed archives, not unknown hashes or future patches.

| Riot skin | Submeshes | File |
| --- | ---: | --- |
| Immortalized Legend Tristana | 31 | `assets/characters/tristana/skins/skin80/tristana_skin80.skn` |
| Prestige HEARTSTEEL Yone | 31 | `assets/characters/yone/skins/skin46/yone_skin46.skn` |
| Sunken Shadows Lucian | 31 | `assets/characters/lucian/skins/skin72/lucian_skin72.skn` |
| Winterblessed Senna | 31 | `assets/characters/senna/skins/skin56/senna_skin56.skn` |
| After Hours Spirit Blossom Springs Ahri | 30 | `assets/characters/ahri/skins/skin89/ahri_skin89.skn` |
| HEARTSTEEL Live My Life Kayn | 30 | `assets/characters/kayn/skins/skin32/kayn_skin32.skn` |
| Petals of Spring Lillia | 30 | `assets/characters/lillia/skins/skin46/lillia_skin46.skn` |
| Peacemaker High Noon Yone | 29 | `assets/characters/yone/skins/skin58/yone_skin58.skn` |
| Coven Janna | 24 | `assets/characters/janna/skins/skin67/janna_skin67.skn` |
| True Damage Know My Name Akali | 13 | `assets/characters/akali/skins/skin103/akali_skin103.skn` |

Counts come from the decompressed SKN headers and range tables, not Blender material slots.
Skin names were resolved using Riot Data Dragon 16.21.1 champion catalogs and the local patch catalog.
For example: [Lucian catalog](https://ddragon.leagueoflegends.com/cdn/16.21.1/data/en_US/champion/Lucian.json),
[Tristana catalog](https://ddragon.leagueoflegends.com/cdn/16.21.1/data/en_US/champion/Tristana.json).

## Controlled in-game comparison

Test 11 rendered with 31 sections. Test 15 split its final section at a disconnected
geometry boundary, producing 32 uniquely named, nonempty ranges. Vertex/index buffers,
normals, UVs, weights, bounds and all other mod files stayed byte-identical. The user
reported that test 15 became invisible. The extra small section used the default material.
This is strong evidence for a practical export safeguard; it does not establish the
engine implementation or guarantee the same boundary in every game version.

## Export behavior

- Reject more than 31 exported nonempty submesh/material sections before writing the SKN.
- Keep importing larger SKNs supported so users can merge sections and repair them.
- Do not automatically delete materials, vertices, or triangles.
- Count actual output sections, not unused Blender slots or distinct material names.
- Regression coverage checks 31 exported sections with an unused 32nd Blender slot,
  plus successful 32-section import and rejected export without overwriting the target file.

Validation: all 76 integration tests pass in Blender 4.0 and 4.5.15 with the local
Seraphine assets and independent LtMAO format fixtures enabled.
