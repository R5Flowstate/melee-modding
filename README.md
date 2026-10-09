# Melee Modding

Turn a Blender mesh into a custom heirloom (melee weapon) mod for R5Flowstate. Your weapon rides the
animations of one of 23 stock heirlooms; the kit
builds the models, materials, client and server paks and weapon files, checks all of it, and packages a
mod you can install or upload to Thunderstore.

```
pip install -r requirements.txt
copy config.example.json config.json      (then fill in your game folder and tool paths)

python melee_modding.py hosts
python melee_modding.py new frostaxe --host axe --mod Me.FrostAxe --name "Frost Axe"
  ... model on heirlooms/frostaxe/skeleton/*.smd in Blender, export body.smd + w_body.smd,
      add textures and list them in heirlooms/frostaxe/spec.json ...
python melee_modding.py build frostaxe --install
```

In game: `sv_cheats 1` then `give me_frostaxe__frostaxe`, or pick it in Lab > Mods > Heirloom.

Needs: the R5Flowstate game, [rmdlconv](https://github.com/R5Flowstate/rmdlconv),
[RSX](https://github.com/R5Flowstate/rsx), [RePak](https://github.com/R5Flowstate/RePak),
[texconv](https://github.com/microsoft/DirectXTex/releases), Blender with
[Blender Source Tools](http://steamreview.org/BlenderSourceTools/), Python 3.10+.
No Source SDK or studiomdl.

The kit contains no game assets: host models, rigs, bones and weapon files are read from your own install.
Use your own art only. MIT licensed (see LICENSE).

Full guide (also written for AI agents): [SKILL.md](SKILL.md). Rig table: [hosts/hosts.md](hosts/hosts.md).
