---
name: melee-modding
description: Make a custom heirloom (melee weapon) for R5Flowstate from a Blender mesh, end to end -- pick a stock heirloom rig, model on its skeleton, author materials, build client + server paks and weapon files, package the mod and install it so `give <ns>__<key>` works. Use for "custom heirloom", "make a knife/sword/axe heirloom", "my mesh on the kunai/axe/hammer animations", "heirloom invisible / T-pose / stuck on first frame".
---

# Melee Modding

A custom heirloom is your mesh riding a **stock heirloom rig**. Nothing is animated by you: the stock
animations play on your model because your skeleton is the host's, bone for bone. The kit never ships
game assets -- host models, materials, rigs, bone lists and weapon files are read from the user's own game
install (the first run builds `work/hosts.json` from the paks).

## Requirements (all free)

| what | where | used for |
|---|---|---|
| R5Flowstate (S21) game install | https://r5flowstate.org | host models, rigs, weapon files, testing |
| rmdlconv | https://github.com/R5Flowstate/rmdlconv (build Release x64) | model -> S21 format |
| RSX | https://github.com/R5Flowstate/rsx | reading the game paks, pak gates |
| RePak | https://github.com/R5Flowstate/RePak | writing the mod paks |
| texconv | https://github.com/microsoft/DirectXTex/releases | texture encoding |
| Blender + Blender Source Tools | https://www.blender.org, http://steamreview.org/BlenderSourceTools/ | modelling, SMD export |
| Python 3.10+ with `numpy`, `Pillow` | `pip install -r requirements.txt` | the kit |
| Windows PowerShell | built in | one model gate |

No Source SDK / studiomdl is needed: the kit compiles SMD itself (`lib/smd2mdl.py`).

Setup: copy `config.example.json` to `config.json` and fill in `game_dir` (the folder that holds
`paks/Win64/common.rpak`) and the four tool paths. `MELEE_GAME_DIR`, `MELEE_RMDLCONV`,
`MELEE_RSX`, `MELEE_REPAK`, `MELEE_TEXCONV` override it.
`MELEE_CONFIG` points at a config file elsewhere, and `MELEE_WORKSPACE` moves `heirlooms/` and `work/`
(your art and build scratch) out of the kit folder -- keeps a kit clone free of anything machine-local.

## The whole flow

```
python melee_modding.py hosts                                   # 1. pick a rig
python melee_modding.py new frostaxe --host axe --mod Me.FrostAxe --name "Frost Axe"
#    -> heirlooms/frostaxe/spec.json + skeleton/<host view>_skeleton.smd + skeleton/<host world>_skeleton.smd
#  2. Blender: model on the skeletons, export body.smd + w_body.smd into heirlooms/frostaxe/
#  3. put textures in heirlooms/frostaxe/, fill spec.json "textures"
python melee_modding.py build frostaxe --install                # 4. build, gate, package, install
#    in game: sv_cheats 1; give me_frostaxe__frostaxe
```

`build` without `--install` stops after packaging (`work/dist/<Team>-<Name>-<ver>.zip`, ready for
Thunderstore). `--install` closes the game, copies the mod into `<game>/mods/<Team>-<Name>` and enables it.
Restart the game (and any local server) after every install.

## 1. Pick the host

`python melee_modding.py hosts` (from your install, with each host's moving parts) and `hosts/hosts.md` list all 23. Pick by how the weapon should **move**:
animations, third-person set, lunge feel, inspect and holster come with the host.

- Any knife, dagger, card, short blade -> `kunai` (one mesh bone, simplest)
- Knife with flourishes -> `kukri`; balisong -> `butterfly` (mesh split into two handles)
- Axe, mace, sword, anything with a charm or chain -> `axe` (14-bone jiggle chain) or `warclub`
- Big two-hander (sledge, bat) -> `hammer`; two-hand sword -> `artifact_sword`
- Polearm, staff -> `spear`; scythe -> `scythe`
- Dual wield -> `batons`, `fistblades`, `gloves`; flail / chain -> `nunchuck`

## 2. Model (Blender)

1. Import `skeleton/<host view>_skeleton.smd` (Blender Source Tools). **Never move, add, rename or delete a
   bone** -- the stock animations address bones by name and rest pose; a missing bone the rig's IK uses
   crashes the game, a moved one makes the weapon float.
2. Model the weapon where the stock weapon sits. Skin a rigid weapon 100% to the host's **core bone**
   (`hosts.md`). Moving parts go on the host's moving-part bones. Up to 3 weights per vertex.
3. Name each Blender material after the last part of a spec material: `blade` -> `models/<ns>__<key>/blade`
   (or set `"smd_material"` on the spec material). Add one spec material per Blender material.
4. Export the viewmodel as `body.smd` (whole skeleton). Max 65535 vertices per material.
5. World model (third person, dropped weapon): same mesh on `skeleton/<host world>_skeleton.smd`, skinned to
   its root bone `ja_c_propGun`, exported as `w_body.smd`.

## 3. Materials and textures

Each spec material is a stock S21 material used as a template plus your textures. The default template
`0x57523F311A60CE34` is a plain 7-slot weapon material:

| slot | content | notes |
|---|---|---|
| `albedoTexture` | colour | |
| `normalTexture` | normal map | DirectX (green down) |
| `glossTexture` | glossiness | white = glossy |
| `specTexture` | specular colour | dark for non-metal, coloured for metal |
| `emissiveTexture` | glow mask | `solid:0,0,0,255` = none |
| `aoTexture`, `cavityTexture` | occlusion | `solid:255,255,255,255` = none |

- Any format texconv reads (PNG, TGA, DDS); **power-of-two sizes** only; every slot needs a texture.
- `solid:r,g,b,a` makes a flat texture.
- Values in the PNGs are used as-is (linear); keep colour maps reasonable, not over-bright.
- Optional per material: `"surfaceProp"`, `"uber": {"emissiveTint": [r,g,b], "albedoTint": [...]}` (glow strength
  lives in `emissiveTint`, 3-20 for a visible glow), `"template": "<any stock S21 material guid>"`.
- Use only your own art. Do not ship textures taken or derived from the game.

## 4. Spec reference (`heirlooms/<key>/spec.json`)

| key | meaning |
|---|---|
| `key` | `[a-z0-9_]`; weapon classes become `<ns>__<key>` (primary) and `<ns>__<key>_melee` |
| `host` | a name from `hosts.md` |
| `mod` | `id` `<Team>.<Name>` (namespace `<ns>` = id lowercased, `.` -> `_`), `author`, `name`, `version`, `description` |
| `view`, `world` | in-game model paths, must start `mdl/<ns>__<key>/` |
| `view_src`, `world_src` | your SMD (or a list of SMDs, one bodypart each), relative to the spec |
| `dedi` | `host` (default for SMD): the server uses the host's own model -- it never renders |
| `materials[]` | `name` / `pack_name` = `models/<ns>__<key>/<blender material>`, `template`, `textures` |
| `skins` | optional skin families: lists of Blender material names, family 0 = the SMD's own |
| `weapon.printname` | display name (kill feed, Lab > Mods > Heirloom) |
| `weapon.kv.{primary,melee}` | override any weapon KV, e.g. `"impact_effect_table"` |

The give name is always namespaced (`give me_frostaxe__frostaxe`); the mod system requires it.

## What the build does (and the gates that stop it)

1. `lib/smd2mdl.py`: SMD -> Source MDL v49; attachments and bone flags copied by name from the host model.
2. rmdlconv -> S21 model; skeleton aligned to the host (bone order, rest pose, flags).
   Gate: every bone the host rig's IK uses exists; shared bones match the host (parent, flags, pose within 0.05).
3. Textures encoded (BC7 colour, BC5 normal, BC4 masks, full mips); materials from the template.
   Gate: every model material is produced by a spec material; formats and mips correct.
4. Client pak (models, materials, textures) and server pak (host models under your paths, with the host rigs).
   Gate: RSX reads both paks and checks every reference resolves.
5. Weapon files written from the game's own host weapon files (only models and names change).
   Gate: parity -- every gameplay value equals the stock heirloom (servers refuse anything else).
6. Mod scaffold (`mod.vdf`, `manifest.json`, scripts, localisation, `icon.png` rendered from `w_body.smd`),
   package check, zip, optional install. Scaffold files you edit in `heirlooms/<key>/mod/` (mod.vdf,
   manifest, README, scripts, localisation, icon) are kept; paks and weapon files are rebuilt every time.

## When it goes wrong

| symptom | cause | fix |
|---|---|---|
| build: "required host bones missing" | a skeleton bone was deleted or renamed | re-import the skeleton SMD, re-skin |
| build: "bones differ from host" | a bone was moved or re-parented | do not edit bones; apply transforms to the mesh only |
| build: "SMD materials [...] match no spec material" | Blender material name | rename it or set `smd_material` |
| build: "must be power-of-two" | texture size | 512/1024/2048/4096 |
| build: "parity gate" | a `weapon.kv` override changed gameplay | only override cosmetic keys |
| weapon floats / offset in hand | mesh not where the stock weapon sits, or skinned to the wrong bone | model at the skeleton's weapon bone, skin to the core bone |
| hands stuck on the first frame | game or server not restarted after install | restart both |
| texture upside down | UV V flipped in the export | flip V in Blender |
| `give` says unknown weapon | mod disabled, or the server does not have it | enable in the launcher; servers need the mod too |

## Layout

```
melee_modding.py          the tool
config.example.json      copy to config.json
hosts/seed.json          23 hosts: stock melee class, model GUIDs, core bone, what it suits
hosts/hosts.md           the host table for people
                         (model paths, rigs and bones are read from your paks into work/hosts.json)
LICENSE                  MIT
lib/                     smd2mdl (SMD -> MDL v49), mdl49, rmdl_v17_dump, align_rmdl_to_host,
                         string_to_guid, host_skeleton, heirloom_parity_check, package_mod,
                         render_icon, verify_rmdl_v17.ps1
heirlooms/<key>/         your work (gitignored); work/ = exports, staging, dist (gitignored)
```
