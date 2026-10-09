# Heirloom hosts

A host is a stock heirloom whose animations your weapon borrows. `melee_modding.py new` exports its skeleton from
your game install to model on. Skin a rigid weapon entirely to the **core bone**; the host's other mesh bones are
moving parts the animations drive (folding blades, chains, jiggle joints) -- `melee_modding.py hosts` lists them
from your install. Bones named `*smear*` carry motion-blur cards, never the weapon body. Pick a host by how the
weapon should MOVE, not how it looks: animations, third-person set, lunge, inspect and holster all come with it.

| host | stock heirloom | core bone | good for |
|---|---|---|---|
| `artifact_dagger` | artifact_dagger | `def_c_base` | dagger with release wings and slider |
| `artifact_sword` | artifact_sword | `def_c_base` | two-hand sword feel |
| `axe` | bloodhound_axe | `def_magazine` | axe, mace, sword, anything with a charm or chain (14-bone jiggle chain) |
| `batons` | lifeline_baton | `def_c_drumstick_01_01` | dual sticks |
| `blackhole` | horizon_heirloom | `def_c_base` | device with spikes |
| `butterfly` | octane_knife | `def_r_handle` | balisong: mesh split into two handles |
| `crypto_sword` | crypto_heirloom | `def_c_hilt` | folding sword (arms + sword rigs) |
| `crypto_sword_rt01` | crypto_heirloom_rt01 | `def_c_hilt` | crypto_sword with the rethemed animations |
| `fan` | loba_heirloom | `def_c_base` | folding fan (arms + fan rigs) |
| `fistblades` | seer_heirloom | `def_r_blade` | claws, katars (both hands) |
| `gadget` | wattson_gadget | `def_c_base` | gadget with tapes and wires |
| `gloves` | pathfinder_gloves | `def_r_glove_base` | knuckles, gauntlets (both hands) |
| `guitar` | fuse_heirloom | `def_c_base` | guitar or axe with neck parts |
| `hammer` | caustic_hammer | `def_body` | big two-hander: sledge, bat, club (rifle holster, heavy swings) |
| `kukri` | bangalore_heirloom | `def_magazine` | knife with sharpening flourishes |
| `kunai` | wraith_kunai | `def_magazine` | any one-hand knife, dagger, card, short blade -- simplest host |
| `kunai_rt01` | wraith_kunai_rt01 | `def_magazine` | same skeleton as kunai, rethemed inspect |
| `nunchuck` | ash_heirloom | `def_c_upper_hook` | flail, kusarigama, chain weapon |
| `scythe` | revenant_scythe | `def_c_base` | scythe or folding polearm |
| `spear` | valkyrie_spear | `def_c_base` | polearm, staff, trident |
| `trophy` | mirage_statue | `def_c_statue` | statue or trophy club with many parts |
| `warclub` | gibraltar_club | `def_c_base` | club or mace with jiggle joints |
| `wrench` | rampart_wrench | `def_c_base` | wrench with flaps |
