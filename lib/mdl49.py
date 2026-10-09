"""Source MDL v49 (Portal 2 studiomdl) + VVD v4 + VTX v7 layouts, shared by the reader and the writer."""
from __future__ import annotations

import struct

HDR_FIELDS = [
    ("id", "i"), ("version", "i"), ("checksum", "i"), ("name", "64s"), ("length", "i"),
    ("eyeposition", "3f"), ("illumposition", "3f"), ("hull_min", "3f"), ("hull_max", "3f"),
    ("view_bbmin", "3f"), ("view_bbmax", "3f"), ("flags", "i"),
    ("numbones", "i"), ("boneindex", "i"), ("numbonecontrollers", "i"), ("bonecontrollerindex", "i"),
    ("numhitboxsets", "i"), ("hitboxsetindex", "i"), ("numlocalanim", "i"), ("localanimindex", "i"),
    ("numlocalseq", "i"), ("localseqindex", "i"), ("activitylistversion", "i"), ("eventsindexed", "i"),
    ("numtextures", "i"), ("textureindex", "i"), ("numcdtextures", "i"), ("cdtextureindex", "i"),
    ("numskinref", "i"), ("numskinfamilies", "i"), ("skinindex", "i"),
    ("numbodyparts", "i"), ("bodypartindex", "i"), ("numlocalattachments", "i"), ("localattachmentindex", "i"),
    ("numlocalnodes", "i"), ("localnodeindex", "i"), ("localnodenameindex", "i"),
    ("numflexdesc", "i"), ("flexdescindex", "i"), ("numflexcontrollers", "i"), ("flexcontrollerindex", "i"),
    ("numflexrules", "i"), ("flexruleindex", "i"), ("numikchains", "i"), ("ikchainindex", "i"),
    ("nummouths", "i"), ("mouthindex", "i"), ("numlocalposeparameters", "i"), ("localposeparamindex", "i"),
    ("surfacepropindex", "i"), ("keyvalueindex", "i"), ("keyvaluesize", "i"),
    ("numlocalikautoplaylocks", "i"), ("localikautoplaylockindex", "i"), ("mass", "f"), ("contents", "i"),
    ("numincludemodels", "i"), ("includemodelindex", "i"), ("virtualModel", "i"),
    ("szanimblocknameindex", "i"), ("numanimblocks", "i"), ("animblockindex", "i"), ("animblockModel", "i"),
    ("bonetablebynameindex", "i"), ("pVertexBase", "i"), ("pIndexBase", "i"),
    ("constdirectionallightdot", "B"), ("rootLOD", "B"), ("numAllowedRootLODs", "B"), ("unused0", "B"),
    ("unused4", "i"), ("numflexcontrollerui", "i"), ("flexcontrolleruiindex", "i"),
    ("flVertAnimFixedPointScale", "f"), ("surfacepropLookup", "i"), ("studiohdr2index", "i"), ("unused2", "i"),
]
HDR2_FIELDS = [
    ("numsrcbonetransform", "i"), ("srcbonetransformindex", "i"), ("illumpositionattachmentindex", "i"),
    ("flMaxEyeDeflection", "f"), ("linearboneindex", "i"), ("sznameindex", "i"),
    ("m_nBoneFlexDriverCount", "i"), ("m_nBoneFlexDriverIndex", "i"), ("reserved", "224s"),
]
BONE_FIELDS = [
    ("sznameindex", "i"), ("parent", "i"), ("bonecontroller", "6i"), ("pos", "3f"), ("quat", "4f"),
    ("rot", "3f"), ("posscale", "3f"), ("rotscale", "3f"), ("poseToBone", "12f"), ("qAlignment", "4f"),
    ("flags", "i"), ("proctype", "i"), ("procindex", "i"), ("physicsbone", "i"), ("surfacepropidx", "i"),
    ("contents", "i"), ("surfacepropLookup", "i"), ("unused", "28s"),
]
ATTACH_FIELDS = [("sznameindex", "i"), ("flags", "i"), ("localbone", "i"), ("local", "12f"), ("unused", "32s")]
HBSET_FIELDS = [("sznameindex", "i"), ("numhitboxes", "i"), ("hitboxindex", "i")]
BBOX_FIELDS = [("bone", "i"), ("group", "i"), ("bbmin", "3f"), ("bbmax", "3f"), ("szhitboxnameindex", "i"),
               ("unused", "32s")]
BODYPART_FIELDS = [("sznameindex", "i"), ("nummodels", "i"), ("base", "i"), ("modelindex", "i")]
MODEL_FIELDS = [
    ("name", "64s"), ("type", "i"), ("boundingradius", "f"), ("nummeshes", "i"), ("meshindex", "i"),
    ("numvertices", "i"), ("vertexindex", "i"), ("tangentsindex", "i"), ("numattachments", "i"),
    ("attachmentindex", "i"), ("numeyeballs", "i"), ("eyeballindex", "i"), ("pVertexData", "i"),
    ("pTangentData", "i"), ("unused", "32s"),
]
MESH_FIELDS = [
    ("material", "i"), ("modelindex", "i"), ("numvertices", "i"), ("vertexoffset", "i"), ("numflexes", "i"),
    ("flexindex", "i"), ("materialtype", "i"), ("materialparam", "i"), ("meshid", "i"), ("center", "3f"),
    ("modelvertexdata", "i"), ("numLODVertexes", "8i"), ("unused", "32s"),
]
TEXTURE_FIELDS = [("sznameindex", "i"), ("flags", "i"), ("used", "i"), ("unused1", "i"), ("material", "i"),
                  ("clientmaterial", "i"), ("unused", "40s")]
SEQ_FIELDS = [
    ("baseptr", "i"), ("szlabelindex", "i"), ("szactivitynameindex", "i"), ("flags", "i"), ("activity", "i"),
    ("actweight", "i"), ("numevents", "i"), ("eventindex", "i"), ("bbmin", "3f"), ("bbmax", "3f"),
    ("numblends", "i"), ("animindexindex", "i"), ("movementindex", "i"), ("groupsize", "2i"),
    ("paramindex", "2i"), ("paramstart", "2f"), ("paramend", "2f"), ("paramparent", "i"),
    ("fadeintime", "f"), ("fadeouttime", "f"), ("localentrynode", "i"), ("localexitnode", "i"),
    ("nodeflags", "i"), ("entryphase", "f"), ("exitphase", "f"), ("lastframe", "f"), ("nextseq", "i"),
    ("pose", "i"), ("numikrules", "i"), ("numautolayers", "i"), ("autolayerindex", "i"),
    ("weightlistindex", "i"), ("posekeyindex", "i"), ("numiklocks", "i"), ("iklockindex", "i"),
    ("keyvalueindex", "i"), ("keyvaluesize", "i"), ("cycleposeindex", "i"), ("activitymodifierindex", "i"),
    ("numactivitymodifiers", "i"), ("unused", "20s"),
]
ANIM_FIELDS = [
    ("baseptr", "i"), ("sznameindex", "i"), ("fps", "f"), ("flags", "i"), ("numframes", "i"),
    ("nummovements", "i"), ("movementindex", "i"), ("ikrulezeroframeindex", "i"), ("unused1", "20s"),
    ("animblock", "i"), ("animindex", "i"), ("numikrules", "i"), ("ikruleindex", "i"),
    ("animblockikruleindex", "i"), ("numlocalhierarchy", "i"), ("localhierarchyindex", "i"),
    ("sectionindex", "i"), ("sectionframes", "i"), ("zeroframespan", "h"), ("zeroframecount", "h"),
    ("zeroframeindex", "i"), ("zeroframestalltime", "f"),
]
LINEARBONE_FIELDS = [
    ("numbones", "i"), ("flagsindex", "i"), ("parentindex", "i"), ("posindex", "i"), ("quatindex", "i"),
    ("rotindex", "i"), ("posetoboneindex", "i"), ("posscaleindex", "i"), ("rotscaleindex", "i"),
    ("qalignmentindex", "i"), ("unused", "24s"),
]

VVD_HDR = [("id", "i"), ("version", "i"), ("checksum", "i"), ("numLODs", "i"), ("numLODVertexes", "8i"),
           ("numFixups", "i"), ("fixupTableStart", "i"), ("vertexDataStart", "i"), ("tangentDataStart", "i")]
VTX_HDR = [("version", "i"), ("vertCacheSize", "i"), ("maxBonesPerStrip", "H"), ("maxBonesPerFace", "H"),
           ("maxBonesPerVert", "i"), ("checkSum", "i"), ("numLODs", "i"), ("materialReplacementListOffset", "i"),
           ("numBodyParts", "i"), ("bodyPartOffset", "i")]
VTX_STRIPGROUP = [("numVerts", "i"), ("vertOffset", "i"), ("numIndices", "i"), ("indexOffset", "i"),
                  ("numStrips", "i"), ("stripOffset", "i"), ("flags", "B"), ("numTopologyIndices", "i"),
                  ("topologyOffset", "i")]
VTX_STRIP = [("numIndices", "i"), ("indexOffset", "i"), ("numVerts", "i"), ("vertOffset", "i"),
             ("numBones", "h"), ("flags", "B"), ("numBoneStateChanges", "i"), ("boneStateChangeOffset", "i"),
             ("numTopologyIndices", "i"), ("topologyOffset", "i")]


def fmt(fields) -> str:
    return "<" + "".join(f for _, f in fields)


def size(fields) -> int:
    return struct.calcsize(fmt(fields))


def unpack(fields, data: bytes, off: int) -> dict:
    vals = list(struct.unpack_from(fmt(fields), data, off))
    out = {}
    for name, f in fields:
        n = struct.calcsize("<" + f) // struct.calcsize("<" + f.lstrip("0123456789")) if f[0].isdigit() and not f.endswith("s") else 1
        if n == 1:
            out[name] = vals.pop(0)
        else:
            out[name] = tuple(vals[:n])
            del vals[:n]
    return out


def pack(fields, d: dict) -> bytes:
    vals = []
    for name, f in fields:
        v = d.get(name, b"" if f.endswith("s") else 0)
        if f[0].isdigit() and not f.endswith("s"):
            n = int(f[:-1])
            v = tuple(v) if v else (0,) * n
            if len(v) != n:
                raise ValueError(f"{name}: want {n} values")
            vals.extend(v)
        else:
            vals.append(v)
    return struct.pack(fmt(fields), *vals)


def cstr(data: bytes, off: int) -> str:
    return data[off:data.index(b"\0", off)].decode("ascii", "replace")


assert size(HDR_FIELDS) == 408, size(HDR_FIELDS)
assert size(HDR2_FIELDS) == 256
assert size(BONE_FIELDS) == 216
assert size(ATTACH_FIELDS) == 92
assert size(BBOX_FIELDS) == 68
assert size(MODEL_FIELDS) == 148
assert size(MESH_FIELDS) == 116
assert size(TEXTURE_FIELDS) == 64
assert size(SEQ_FIELDS) == 212, size(SEQ_FIELDS)
assert size(ANIM_FIELDS) == 100, size(ANIM_FIELDS)
assert size(VTX_STRIPGROUP) == 33
assert size(VTX_STRIP) == 35
