"""The floor cells check_arena.py finds in a GLB, as JSON, so that two builds can be diffed.

    python -I tools/floor_cells.py tools/check_arena.py models/arena_blockout.glb /tmp/grey.json
    python -I tools/floor_cells.py tools/check_arena.py models/liminal/arena_liminal.glb /tmp/liminal.json

It runs the checker's own code up to its tests, then its `survey()`: every (0.5 m cell) -> the
floor heights it can stand on.
"""
import json
import sys

checker, glb, out = sys.argv[1:4]
source = open(checker).read().split("ATTACK, DEFEND =")[0]      # the import and the survey, not the tests
sys.argv = ["check_arena.py", glb]
namespace = {}
exec(compile(source, "check_arena_head", "exec"), namespace)
cells = namespace["survey"]()
json.dump({f"{i},{j}": z for (i, j), z in cells.items()}, open(out, "w"))
print("cells", len(cells), "->", out)
