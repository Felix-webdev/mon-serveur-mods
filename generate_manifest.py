"""Génère mods.json automatiquement à partir de config.json, du dossier mods/
et des dossiers fichiers/ et fichiers_defaut/.

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
        "size": jar.stat().st_size,
    })

# Fichiers personnalisés : fichiers/ (toujours synchronisés) et fichiers_defaut/ (copiés si absents)
files = []
for folder, mode in (("fichiers", "sync"), ("fichiers_defaut", "default")):
    base = Path(folder)
    if not base.exists():
        continue
    for f in sorted(p for p in base.rglob("*") if p.is_file() and p.name not in ("LISEZMOI.txt", ".gitkeep")):
        rel = f.relative_to(base).as_posix()
        files.append({
            "path": rel,
            "mode": mode,
            "url": f"https://raw.githubusercontent.com/{repo}/{branch}/{folder}/{quote(rel)}",
            "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            "size": f.stat().st_size,
        })

manifest = {**config, "mods": mods, "files": files}
Path("mods.json").write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
)
print(f"{len(mods)} mod(s) et {len(files)} fichier(s) personnalisé(s) dans mods.json")
