"""What a GLB holds, read as the bytes it is.

    python -I tools/glbinfo.py models/liminal/arena_liminal.glb
"""
import json
import struct
import sys
from collections import Counter

data = open(sys.argv[1], "rb").read()
magic, version, total, length, kind = struct.unpack_from("<4sIII4s", data, 0)   # header, first chunk's length and type
doc = json.loads(data[20:20 + length])
bin_len = struct.unpack_from("<I", data, 20 + length)[0]
print(f"glb {magic.decode()} v{version}: {total} bytes = json {length} + bin {bin_len} + headers; "
      f"declared total matches the file: {total == len(data)}")
print("asset:", doc["asset"])
print("extensionsUsed:", doc.get("extensionsUsed"), " required:", doc.get("extensionsRequired"))
print("scenes:", [(s.get("name"), len(s["nodes"])) for s in doc["scenes"]], " root nodes:",
      [doc["nodes"][i].get("name") for i in doc["scenes"][doc.get("scene", 0)]["nodes"]])
for root in (doc["nodes"][i] for i in doc["scenes"][doc.get("scene", 0)]["nodes"]):
    print(f"  {root.get('name')}: " + ", ".join(doc["nodes"][c].get("name") for c in root.get("children", []))[:300])
print("nodes:", len(doc["nodes"]), " meshes:", len(doc["meshes"]), " materials:", len(doc["materials"]),
      " images:", len(doc.get("images", [])), " textures:", len(doc.get("textures", [])), " samplers:", len(doc.get("samplers", [])))
print("samplers (mag, min, wrapS, wrapT):",
      Counter((s.get("magFilter"), s.get("minFilter"), s.get("wrapS"), s.get("wrapT")) for s in doc.get("samplers", [])))
print("images:", Counter((i.get("mimeType"), "embedded" if "bufferView" in i else i.get("uri")) for i in doc.get("images", [])))
for m in doc["materials"]:
    pbr, tex = m.get("pbrMetallicRoughness", {}), lambda t: (t or {}).get("index")
    print(f"  {m['name']:36} base texture {tex(pbr.get('baseColorTexture'))!s:>4}  emissive texture {tex(m.get('emissiveTexture'))!s:>4}"
          f"  emissive strength {(m.get('extensions') or {}).get('KHR_materials_emissive_strength', {}).get('emissiveStrength', '-')}")
lights = doc.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])
print("lights:", len(lights), Counter(l["type"] for l in lights), " first:", lights[:1])
tris = sum(doc["accessors"][p["indices"]]["count"] // 3 for m in doc["meshes"] for p in m["primitives"])
print("triangles:", tris, " TEXCOORD_0 on every primitive:",
      all("TEXCOORD_0" in p["attributes"] for m in doc["meshes"] for p in m["primitives"]))
for n in doc["nodes"]:
    if n.get("name") in ("v1", "c1", "Spawn_Attack", "lamp_0", "door_lookout_n", "crate_a1"):
        print(f"  node {n.get('name'):14} translation {n.get('translation')}  extras {n.get('extras')}")
print("axes in the file are glTF's: x, y up, z south -- so (x, z, -y) of the numbers in examples/")
