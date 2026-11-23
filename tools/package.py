"""Pack a release: one zip per format, and a list of checksums.

    python -I tools/package.py 1.0.0 /tmp/liminal-release

Writes liminal-arena-v<version>-<kind>.zip into the directory, and SHA256SUMS beside them:

    glb        the dressed arena and its manifest
    fbx        the same, as FBX
    obj        the same, as OBJ with its .mtl and textures
    blend      the same, as a .blend
    blockout   the grey blockout, as a GLB
    renders    every picture in the README

Each zip carries README.md and LICENSE, so that one downloaded on its own explains itself.
"""
import hashlib
import sys
import zipfile
from pathlib import Path

version, out = sys.argv[1], Path(sys.argv[2])
root = Path(__file__).resolve().parents[1]
liminal = root / "models" / "liminal"

# kind -> what goes in the zip, as (where it is, where it lands)
PACKS = {
    "glb": [(liminal / "arena_liminal.glb", "arena_liminal.glb"), (liminal / "arena_liminal.json", "arena_liminal.json")],
    "fbx": [(liminal / "arena_liminal.fbx", "arena_liminal.fbx")],
    "obj": [(p, f"obj/{p.name}") for p in sorted((liminal / "obj").iterdir())],
    "blend": [(liminal / "arena_liminal.blend", "arena_liminal.blend")],
    "blockout": [(root / "models" / "arena_blockout.glb", "arena_blockout.glb")],
    "renders": [(p, f"images/{p.name}") for p in sorted((root / "images").iterdir()) if p.suffix in (".png", ".jpg")],
}

out.mkdir(parents=True, exist_ok=True)
sums = []
for kind, files in PACKS.items():
    path = out / f"liminal-arena-v{version}-{kind}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as pack:
        for src, name in [(root / "README.md", "README.md"), (root / "LICENSE", "LICENSE"), *files]:
            pack.write(src, name)
    sums.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    print(f"{path.name:38} {path.stat().st_size // 1024:5} KB   {len(files)} files")
(out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
print(f"OK - release {version} packed into {out}")
