"""Génère mods.json automatiquement à partir du dossier mods/ et de config.json.

Exécuté par GitHub Actions à chaque modification du dossier mods/.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote

repo = os.environ.get("GITHUB_REPOSITORY", "Felix-webdev/mon-serveur-mods")
branch = os.environ.get("GITHUB_REF_NAME", "main")

config = json.loads(Path("config.json").read_text(encoding="utf-8"))
mods = []
for jar in sorted(Path("mods").glob("*.jar")):
    sha = hashlib.sha256(jar.read_bytes()).hexdigest()
    mods.append({
        "filename": jar.name,
        "url": f"https://raw.githubusercontent.com/{repo}/{branch}/mods/{quote(jar.name)}",
        "sha256": sha,
    })

manifest = {**config, "mods": mods}
Path("mods.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(mods)} mod(s) dans mods.json")
