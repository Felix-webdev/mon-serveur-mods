"""
Launcher Minecraft maison - mods synchronisés depuis GitHub, connexion auto au serveur.

Installation :
    pip install minecraft-launcher-lib requests customtkinter pillow

Lancement :
    python launcher.py
"""

import base64
import hashlib
import io
import json
import os
import re
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tkinter import filedialog

import requests
import customtkinter as ctk
import minecraft_launcher_lib as mll
from PIL import Image

# ---------------------------------------------------------------- CONFIG ---
APP_NAME = "Mon Launcher"
MANIFEST_URL = "https://raw.githubusercontent.com/Felix-webdev/mon-serveur-mods/main/mods.json"
CONFIG_DIR = Path.home() / ".mon_launcher"      # réglages du launcher (emplacement fixe, ne pas toucher)
_DOCS = Path.home() / "Documents"
DEFAULT_GAME_DIR = (_DOCS if _DOCS.exists() else Path.home()) / "MonLauncher"  # dossier du jeu par défaut
GAME_DIR = DEFAULT_GAME_DIR                      # dossier du jeu actuel (modifiable dans l'appli)
SETTINGS_FILE = CONFIG_DIR / "settings.json"
STATUS_REFRESH_SECONDS = 30
# ---------------------------------------------------------------------------

GAME_DIR_README = """DOSSIER DU JEU - Mon Launcher

Ce dossier contient tout ce que le launcher installe pour Minecraft.

  mods/            Les mods (ceux du serveur sont gérés par le launcher)
  saves/           Tes mondes (sauvegardes)
  config/          Les réglages des mods
  resourcepacks/   Tes packs de textures
  shaderpacks/     Tes shaders
  screenshots/     Tes captures d'écran
  logs/            Les journaux du jeu

  versions/, libraries/, assets/, runtime/
                   Fichiers d'installation de Minecraft, de Forge et de Java.
                   A ne pas modifier : le launcher les répare tout seul.

  managed_mods.json  Liste des mods installés par le launcher.

Tu peux ajouter tes propres mods dans mods/ : le launcher ne touche qu'à ceux
qu'il a installés lui-même.
Pour changer de dossier, utilise le bouton "Choisir..." dans le launcher.
Les réglages du launcher (pseudo, mémoire) sont dans le dossier .mon_launcher
de ton dossier utilisateur.
"""


def set_game_dir(path) -> None:
    """Change le dossier où Minecraft et les mods sont installés."""
    global GAME_DIR
    new_dir = Path(path)
    new_dir.mkdir(parents=True, exist_ok=True)
    GAME_DIR = new_dir
    readme = new_dir / "LISEZMOI.txt"
    if new_dir == DEFAULT_GAME_DIR and not readme.exists():
        readme.write_text(GAME_DIR_README, encoding="utf-8")


def open_folder(path: Path) -> None:
    """Ouvre un dossier dans l'explorateur de fichiers."""
    path.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def managed_file() -> Path:
    """Liste des mods installés par le launcher dans le dossier du jeu actuel."""
    return GAME_DIR / "managed_mods.json"


# ------------------------------------------------------------ AUTH (plugin) --
class OfflineAuth:
    """Connexion simple par pseudo. A remplacer par MicrosoftAuth plus tard.

    Toute classe d'auth doit exposer login(username) -> dict avec les clés
    username, uuid, token.
    """

    def login(self, username: str) -> dict:
        username = username.strip()
        if not (3 <= len(username) <= 16) or not re.fullmatch(r"\w+", username):
            raise ValueError("Pseudo invalide : 3 à 16 caractères (lettres, chiffres, _).")
        digest = bytearray(hashlib.md5(f"OfflinePlayer:{username}".encode()).digest())
        digest[6] = (digest[6] & 0x0F) | 0x30
        digest[8] = (digest[8] & 0x3F) | 0x80
        return {"username": username, "uuid": str(uuid.UUID(bytes=bytes(digest))), "token": "0"}


AUTH = OfflineAuth()


# ---------------------------------------------------------- STATUT SERVEUR --
def _varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(sock) -> int:
    result = 0
    for i in range(5):
        raw = sock.recv(1)
        if not raw:
            raise ConnectionError("connexion fermée")
        b = raw[0]
        result |= (b & 0x7F) << (7 * i)
        if not b & 0x80:
            return result
    raise ValueError("varint trop long")


def ping_server(address: str, timeout: float = 5.0) -> dict:
    """Interroge un serveur Minecraft (Server List Ping) et renvoie son statut JSON."""
    host, _, port_s = address.partition(":")
    port = int(port_s) if port_s else 25565
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.settimeout(timeout)
        host_b = host.encode()
        data = b"\x00" + _varint(765) + _varint(len(host_b)) + host_b + struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(data)) + data)
        s.sendall(b"\x01\x00")  # demande de statut
        _read_varint(s)  # taille du paquet
        _read_varint(s)  # id du paquet
        length = _read_varint(s)
        buf = b""
        while len(buf) < length:
            chunk = s.recv(length - len(buf))
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8", errors="replace"))


def _flatten_motd(desc) -> str:
    if isinstance(desc, str):
        return desc
    if isinstance(desc, dict):
        return desc.get("text", "") + "".join(_flatten_motd(e) for e in desc.get("extra", []))
    if isinstance(desc, list):
        return "".join(_flatten_motd(e) for e in desc)
    return ""


def motd_text(desc) -> str:
    return re.sub(r"§.", "", _flatten_motd(desc)).strip()


# ------------------------------------------------------------------ MODS ---
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_manifest() -> dict:
    r = requests.get(MANIFEST_URL, timeout=15, headers={"Cache-Control": "no-cache"})
    r.raise_for_status()
    return r.json()


def load_managed() -> list:
    mf = managed_file()
    if mf.exists():
        try:
            return json.loads(mf.read_text())
        except Exception:
            return []
    return []


def compute_mod_states(manifest: dict) -> dict:
    """Pour chaque mod : 'ok' (à jour), 'download' (à télécharger) ou 'remove' (à supprimer)."""
    mods_dir = GAME_DIR / "mods"
    states = {}
    wanted = {m["filename"]: m for m in manifest.get("mods", [])}
    for filename, mod in wanted.items():
        f = mods_dir / filename
        expected = mod.get("sha256", "").lower()
        ok = f.exists() and (not expected or sha256_of(f) == expected)
        states[filename] = "ok" if ok else "download"
    for old in load_managed():
        if old not in wanted and (mods_dir / old).exists():
            states[old] = "remove"
    return states


def fmt_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def fmt_time(seconds) -> str:
    if seconds is None:
        return "…"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    return f"{seconds // 60} min {seconds % 60:02d} s"


def download_file(url: str, dest: Path, expected_sha: str, on_bytes, retries: int = 3) -> None:
    """Téléchargement en flux : reprise (.part), nouvelles tentatives, vérification du hash."""
    part = dest.with_name(dest.name + ".part")
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            resume = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={resume}-"} if resume else {}
            with requests.get(url, stream=True, timeout=30, headers=headers) as r:
                if r.status_code != 416:  # 416 = le fichier partiel est déjà complet
                    r.raise_for_status()
                    if resume and r.status_code != 206:
                        resume = 0  # le serveur ne gère pas la reprise : on repart de zéro
                    total = resume + int(r.headers.get("Content-Length") or 0)
                    done = resume
                    with open(part, "ab" if resume else "wb") as f:
                        for chunk in r.iter_content(65536):
                            if chunk:
                                f.write(chunk)
                                done += len(chunk)
                                on_bytes(done, total)
            if expected_sha and sha256_of(part) != expected_sha:
                part.unlink(missing_ok=True)
                raise RuntimeError("empreinte (hash) invalide")
            os.replace(part, dest)
            return
        except Exception as e:
            last_error = e
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"échec après {retries} tentatives ({last_error})")


def sync_mods(manifest: dict, log, bar, on_mod, on_speed, workers: int = 3) -> None:
    """Télécharge (en parallèle) les mods manquants ou modifiés, supprime ceux retirés."""
    mods_dir = GAME_DIR / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    managed = load_managed()
    wanted = {m["filename"]: m for m in manifest.get("mods", [])}

    for old in managed:
        if old not in wanted:
            p = mods_dir / old
            if p.exists():
                p.unlink()
                log(f"Mod retiré : {old}")
            on_mod(old, "removed")

    pending = []
    for filename, mod in wanted.items():
        dest = mods_dir / filename
        expected = mod.get("sha256", "").lower()
        if dest.exists() and (not expected or sha256_of(dest) == expected):
            on_mod(filename, "ok")
        else:
            pending.append((filename, mod))

    errors = []
    if pending:
        sizes = {f: int(m.get("size") or 0) for f, m in pending}
        total_bytes = sum(sizes.values())
        total_files = len(pending)
        state = {"done": {f: 0 for f in sizes}, "finished": 0}
        lock = threading.Lock()
        started = time.time()
        last_ui = [0.0]
        log(f"{total_files} mod(s) à télécharger" + (f" ({fmt_size(total_bytes)})" if total_bytes else "") + ".")

        def report(force=False):
            now = time.time()
            if not force and now - last_ui[0] < 0.2:
                return
            last_ui[0] = now
            done_bytes = sum(state["done"].values())
            speed = done_bytes / max(now - started, 0.001)
            remaining = None
            if total_bytes:
                bar(done_bytes, total_bytes)
                if speed > 0:
                    remaining = max(total_bytes - done_bytes, 0) / speed
            else:
                bar(state["finished"], total_files)
            on_speed(state["finished"], total_files, done_bytes, total_bytes, speed, remaining)

        def worker(filename, mod):
            dest = mods_dir / filename
            on_mod(filename, "downloading")
            log(f"Téléchargement : {filename}")
            last_pct = [-1]

            def on_bytes(done, total):
                with lock:
                    state["done"][filename] = done
                    report()
                if total:
                    pct = int(done * 100 / total)
                    if pct != last_pct[0]:
                        last_pct[0] = pct
                        on_mod(filename, "downloading", f"{pct} %")

            try:
                download_file(mod["url"], dest, mod.get("sha256", "").lower(), on_bytes)
            except Exception:
                on_mod(filename, "error")
                raise
            with lock:
                state["done"][filename] = dest.stat().st_size
                state["finished"] += 1
                report(force=True)
            on_mod(filename, "ok")
            log(f"✔ {filename} ({fmt_size(dest.stat().st_size)})", "ok")

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, f, m): f for f, m in pending}
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as e:
                    errors.append(f"{futures[fut]} : {e}")
                    log(f"✖ {futures[fut]} : {e}", "err")

    bar(1, 1)
    managed_file().write_text(json.dumps([f for f in wanted if (mods_dir / f).exists()]))
    if errors:
        raise RuntimeError(f"{len(errors)} mod(s) n'ont pas pu être téléchargés.")
    log(f"Mods à jour : {len(wanted)} au total.", "ok")


def managed_files_file() -> Path:
    """Liste des fichiers du serveur (mode 'sync') installés par le launcher."""
    return GAME_DIR / "managed_files.json"


def safe_target(rel: str) -> Path:
    """Chemin dans le dossier du jeu ; refuse tout chemin qui en sortirait."""
    root = GAME_DIR.resolve()
    target = (GAME_DIR / rel).resolve()
    if root != target and root not in target.parents:
        raise ValueError(f"Chemin refusé : {rel}")
    return target


def sync_files(manifest: dict, log, bar) -> None:
    """Synchronise les fichiers personnalisés du serveur (pack de ressources, configs, options...).

    mode "sync"    : le fichier est toujours identique à celui de GitHub (remplacé s'il change).
    mode "default" : copié seulement s'il n'existe pas (le joueur peut ensuite le modifier).
    Seuls les fichiers qui manquent ou qui ont changé sont téléchargés.
    """
    files = manifest.get("files", [])
    mf = managed_files_file()
    try:
        managed = json.loads(mf.read_text()) if mf.exists() else []
    except Exception:
        managed = []

    wanted_sync = {f["path"] for f in files if f.get("mode", "sync") == "sync"}
    for old in managed:
        if old not in wanted_sync:
            p = safe_target(old)
            if p.exists():
                p.unlink()
                log(f"Fichier retiré : {old}")

    pending = []
    for f in files:
        dest = safe_target(f["path"])
        if f.get("mode", "sync") == "default":
            if not dest.exists():
                pending.append(f)
        else:
            expected = f.get("sha256", "").lower()
            if not (dest.exists() and (not expected or sha256_of(dest) == expected)):
                pending.append(f)

    errors = []
    n = len(pending)
    if n:
        log(f"{n} fichier(s) du serveur à télécharger.")
    for i, f in enumerate(pending):
        dest = safe_target(f["path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        log(f"Téléchargement : {f['path']}")

        def on_bytes(done, total, i=i):
            bar(i * 1000 + (int(done * 1000 / total) if total else 0), n * 1000)

        try:
            download_file(f["url"], dest, f.get("sha256", "").lower(), on_bytes)
            log(f"✔ {f['path']} ({fmt_size(dest.stat().st_size)})", "ok")
        except Exception as e:
            errors.append(f"{f['path']} : {e}")
            log(f"✖ {f['path']} : {e}", "err")
    bar(1, 1)

    mf.write_text(json.dumps(sorted(p for p in wanted_sync if safe_target(p).exists())))
    if errors:
        raise RuntimeError(f"{len(errors)} fichier(s) du serveur n'ont pas pu être téléchargés.")
    if not files:
        log("Aucun fichier personnalisé à synchroniser.")
    elif not n:
        log("Fichiers du serveur à jour.", "ok")


def enable_resourcepack(name: str, log) -> None:
    """Active le pack de ressources `name` (dans resourcepacks/) via options.txt."""
    if not name:
        return
    if not (GAME_DIR / "resourcepacks" / name).exists():
        log(f"Pack de ressources introuvable : {name}", "err")
        return
    options = GAME_DIR / "options.txt"
    entry = f"file/{name}"
    lines = options.read_text(encoding="utf-8").splitlines() if options.exists() else []
    changed = False
    for i, line in enumerate(lines):
        if line.startswith("resourcePacks:"):
            try:
                packs = json.loads(line.split(":", 1)[1])
            except Exception:
                packs = ["vanilla"]
            if entry not in packs:
                packs.append(entry)
                lines[i] = "resourcePacks:" + json.dumps(packs, separators=(",", ":"))
                changed = True
            break
    else:
        lines.append("resourcePacks:" + json.dumps(["vanilla", entry], separators=(",", ":")))
        changed = True
    if changed:
        options.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log(f"Pack de ressources activé : {name}", "ok")


# ------------------------------------------------------------ MINECRAFT ----
def install_game(manifest: dict, callback: dict, log) -> str:
    """Installe Minecraft, Java et le loader (Forge/Fabric). Retourne l'id de version."""
    mc = manifest["minecraft"]
    loader = manifest.get("loader", "forge").lower()

    log(f"Installation de Minecraft {mc}…")
    mll.install.install_minecraft_version(mc, str(GAME_DIR), callback=callback)

    java_path = None
    try:
        vjson = json.loads((GAME_DIR / "versions" / mc / f"{mc}.json").read_text())
        component = vjson["javaVersion"]["component"]
        log("Installation de Java…")
        mll.runtime.install_jvm_runtime(component, str(GAME_DIR), callback=callback)
        java_path = mll.runtime.get_executable_path(component, str(GAME_DIR))
    except Exception:
        log("Java embarqué indisponible, utilisation du Java du système.")

    version_id = mc
    if loader == "forge":
        forge_version = manifest.get("loader_version") or mll.forge.find_forge_version(mc)
        if not forge_version:
            raise RuntimeError(f"Aucune version de Forge trouvée pour Minecraft {mc}.")
        version_id = mll.forge.forge_to_installed_version(forge_version)
        installed = [v["id"] for v in mll.utils.get_installed_versions(str(GAME_DIR))]
        if version_id not in installed:
            log(f"Installation de Forge {forge_version}…")
            profiles = GAME_DIR / "launcher_profiles.json"
            if not profiles.exists():  # requis par l'installeur de Forge
                profiles.write_text('{"profiles": {}, "version": 3}')
            mll.forge.install_forge_version(
                forge_version, str(GAME_DIR), callback=callback, java=java_path
            )
        else:
            log(f"Forge {forge_version} déjà installé.")
    elif loader == "fabric":
        log("Installation de Fabric…")
        mll.fabric.install_fabric(mc, str(GAME_DIR), callback=callback)
        for v in mll.utils.get_installed_versions(str(GAME_DIR)):
            if v["id"].startswith("fabric-loader-") and v["id"].endswith(mc):
                version_id = v["id"]
                break
    elif loader != "vanilla":
        raise RuntimeError(f"Loader non géré : {loader}")

    return version_id


def version_uses_quickplay(version_id: str) -> bool:
    """True si la version de Minecraft est >= 1.20 (support de Quick Play)."""
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?$", version_id)
    if not m:
        return True
    return (int(m.group(1)), int(m.group(2))) >= (1, 20)


def launch_game(version_id: str, account: dict, ram_mb: int, server: str, on_line) -> int:
    """Lance le jeu, renvoie chaque ligne de sortie à on_line, retourne le code de sortie."""
    options = {
        "username": account["username"],
        "uuid": account["uuid"],
        "token": account["token"],
        "jvmArguments": [f"-Xmx{ram_mb}M", f"-Xms{min(512, ram_mb)}M"],
        "launcherName": APP_NAME,
        "launcherVersion": "0.3",
    }
    if server:
        host, _, port = server.partition(":")
        port = port or "25565"
        if version_uses_quickplay(version_id):
            options["quickPlayMultiplayer"] = f"{host}:{port}"
        else:
            options["server"] = host
            options["port"] = port

    cmd = mll.command.get_minecraft_command(version_id, str(GAME_DIR), options)
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        cwd=str(GAME_DIR),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    for line in proc.stdout:
        on_line(line.rstrip())
    return proc.wait()


# ------------------------------------------------------------------- GUI ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

STEPS = ["Liste des mods", "Minecraft & Forge", "Mods", "Fichiers du serveur", "Lancement"]

MOD_STATES = {
    "ok": ("✔ À jour", "#6bff95"),
    "download": ("⬇ À télécharger", "#ffb347"),
    "downloading": ("… Téléchargement", "#5cb3ff"),
    "remove": ("✖ À supprimer", "#ff6b6b"),
    "removed": ("Supprimé", "#9aa0a6"),
    "error": ("⚠ Erreur", "#ff6b6b"),
}
GREY = "#9aa0a6"


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("1000x760")
        self.minsize(920, 700)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.settings = self.load_settings()
        try:
            set_game_dir(self.settings.get("game_dir") or DEFAULT_GAME_DIR)
        except OSError:
            set_game_dir(DEFAULT_GAME_DIR)  # dossier introuvable : retour au défaut
        self.manifest = None
        self.mod_state = {}
        self.mod_rows = {}
        self.busy = False
        self.favicon_img = None

        # --- En-tête
        header = ctk.CTkFrame(self, corner_radius=0, fg_color=("#2b2b2b", "#1a1a1a"))
        header.pack(fill="x")
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=26, weight="bold")).pack(
            anchor="w", padx=20, pady=(14, 0)
        )
        self.subtitle = ctk.CTkLabel(header, text="Chargement…", text_color=GREY,
                                     font=ctk.CTkFont(size=13))
        self.subtitle.pack(anchor="w", padx=20, pady=(0, 14))

        # --- Corps : colonne gauche (serveur, réglages) / colonne droite (mods)
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(14, 0))
        body.grid_columnconfigure(0, weight=0)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color="transparent", width=330)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 12))

        self.build_server_card(left)
        self.build_settings_card(left)

        self.play_btn = ctk.CTkButton(
            left, text="JOUER", height=52, font=ctk.CTkFont(size=18, weight="bold"),
            command=self.on_play,
        )
        self.play_btn.pack(fill="x", pady=(10, 0))

        right = ctk.CTkFrame(body)
        right.grid(row=0, column=1, sticky="nsew")
        top = ctk.CTkFrame(right, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        self.mods_title = ctk.CTkLabel(top, text="Mods", font=ctk.CTkFont(size=16, weight="bold"))
        self.mods_title.pack(side="left")
        ctk.CTkButton(top, text="↻ Actualiser", width=110, height=28,
                      command=self.refresh_all).pack(side="right")
        ctk.CTkButton(top, text="📁 Dossier des mods", width=150, height=28, fg_color="#444444",
                      hover_color="#555555", command=self.open_mods_dir).pack(side="right", padx=(0, 8))
        self.mod_list = ctk.CTkScrollableFrame(right, fg_color="transparent")
        self.mod_list.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        self.mod_empty = ctk.CTkLabel(self.mod_list, text="Chargement de la liste…", text_color=GREY)
        self.mod_empty.pack(pady=20)

        # --- Progression
        prog = ctk.CTkFrame(self)
        prog.pack(fill="x", padx=20, pady=(12, 6))
        self.step_label = ctk.CTkLabel(prog, text="En attente", anchor="w",
                                       font=ctk.CTkFont(size=14, weight="bold"))
        self.step_label.pack(fill="x", padx=14, pady=(10, 4))
        bar_row = ctk.CTkFrame(prog, fg_color="transparent")
        bar_row.pack(fill="x", padx=14)
        bar_row.grid_columnconfigure(0, weight=1)
        self.bar = ctk.CTkProgressBar(bar_row)
        self.bar.grid(row=0, column=0, sticky="ew")
        self.bar.set(0)
        self.percent = ctk.CTkLabel(bar_row, text="0 %", width=48)
        self.percent.grid(row=0, column=1, padx=(10, 0))
        self.detail = ctk.CTkLabel(prog, text="", anchor="w", text_color=GREY)
        self.detail.pack(fill="x", padx=14, pady=(4, 10))

        # --- Journal
        self.logbox = ctk.CTkTextbox(self, height=130, font=ctk.CTkFont(family="Consolas", size=12))
        self.logbox.pack(fill="x", padx=20, pady=(0, 16))
        self.logbox.tag_config("err", foreground="#ff6b6b")
        self.logbox.tag_config("ok", foreground="#6bff95")
        self.logbox.configure(state="disabled")

        self.log("Launcher prêt.")
        self.log(f"Dossier du jeu : {GAME_DIR}")
        self.refresh_all()
        self.after(STATUS_REFRESH_SECONDS * 1000, self.tick)

    # ---- construction des cartes
    def build_server_card(self, parent):
        card = ctk.CTkFrame(parent)
        card.pack(fill="x")
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(14, 6))
        self.icon_label = ctk.CTkLabel(row, text="⛏", width=64, height=64,
                                       font=ctk.CTkFont(size=34), fg_color=("#3a3a3a", "#2a2a2a"),
                                       corner_radius=8)
        self.icon_label.pack(side="left")
        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side="left", padx=12, fill="x", expand=True)
        self.server_name = ctk.CTkLabel(info, text="Serveur", anchor="w",
                                        font=ctk.CTkFont(size=16, weight="bold"))
        self.server_name.pack(fill="x")
        self.server_status = ctk.CTkLabel(info, text="● Vérification…", anchor="w", text_color=GREY)
        self.server_status.pack(fill="x")
        self.server_motd = ctk.CTkLabel(card, text="", anchor="w", justify="left",
                                        wraplength=290, text_color="#cfd3d7")
        self.server_motd.pack(fill="x", padx=14, pady=(2, 2))
        self.server_players = ctk.CTkLabel(card, text="", anchor="w", justify="left", wraplength=290)
        self.server_players.pack(fill="x", padx=14)
        self.server_version = ctk.CTkLabel(card, text="", anchor="w", text_color=GREY)
        self.server_version.pack(fill="x", padx=14, pady=(0, 14))

    def build_settings_card(self, parent):
        form = ctk.CTkFrame(parent)
        form.pack(fill="x", pady=(10, 0))
        form.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(form, text="Pseudo").grid(row=0, column=0, padx=14, pady=(14, 6), sticky="w")
        self.username = ctk.CTkEntry(form, placeholder_text="Ton pseudo")
        self.username.grid(row=0, column=1, padx=(0, 14), pady=(14, 6), sticky="ew")
        self.username.insert(0, self.settings.get("username", ""))
        ctk.CTkLabel(form, text="Mémoire").grid(row=1, column=0, padx=14, pady=(6, 14), sticky="w")
        ram_box = ctk.CTkFrame(form, fg_color="transparent")
        ram_box.grid(row=1, column=1, padx=(0, 14), pady=(6, 14), sticky="ew")
        ram_box.grid_columnconfigure(0, weight=1)
        self.ram = ctk.CTkSlider(ram_box, from_=1, to=16, number_of_steps=15,
                                 command=self.on_ram_change)
        self.ram.grid(row=0, column=0, sticky="ew")
        self.ram.set(self.settings.get("ram_gb", 4))
        self.ram_label = ctk.CTkLabel(ram_box, text="", width=46)
        self.ram_label.grid(row=0, column=1, padx=(8, 0))
        self.on_ram_change(self.ram.get())

        ctk.CTkLabel(form, text="Dossier du jeu").grid(
            row=2, column=0, columnspan=2, padx=14, pady=(0, 0), sticky="w")
        self.dir_label = ctk.CTkLabel(form, text="", anchor="w", justify="left",
                                      wraplength=290, text_color=GREY)
        self.dir_label.grid(row=3, column=0, columnspan=2, padx=14, pady=(2, 6), sticky="w")
        btns = ctk.CTkFrame(form, fg_color="transparent")
        btns.grid(row=4, column=0, columnspan=2, padx=14, pady=(0, 14), sticky="ew")
        btns.grid_columnconfigure((0, 1), weight=1)
        grey = {"fg_color": "#444444", "hover_color": "#555555"}
        ctk.CTkButton(btns, text="Choisir…", height=28, command=self.choose_dir).grid(
            row=0, column=0, padx=(0, 4), pady=(0, 6), sticky="ew")
        ctk.CTkButton(btns, text="📁 Ouvrir", height=28, command=self.open_game_dir).grid(
            row=0, column=1, padx=(4, 0), pady=(0, 6), sticky="ew")
        ctk.CTkButton(btns, text="Mon .minecraft", height=28, command=self.use_minecraft_dir, **grey).grid(
            row=1, column=0, padx=(0, 4), sticky="ew")
        ctk.CTkButton(btns, text="Par défaut", height=28, command=self.use_default_dir, **grey).grid(
            row=1, column=1, padx=(4, 0), sticky="ew")
        self.update_dir_label()

    # ---- dossier du jeu
    def update_dir_label(self):
        self.dir_label.configure(text=str(GAME_DIR))

    def change_game_dir(self, path: str):
        try:
            set_game_dir(path)
        except OSError as e:
            self.log(f"Impossible d'utiliser ce dossier : {e}", "err")
            return
        self.save_settings()
        self.update_dir_label()
        self.log(f"Dossier du jeu : {GAME_DIR}")
        self.log("Seuls les mods installés par le launcher sont gérés ; tes autres fichiers ne sont pas touchés.")
        self.refresh_all()

    def choose_dir(self):
        if self.busy:
            return
        path = filedialog.askdirectory(title="Choisis le dossier du jeu", initialdir=str(GAME_DIR))
        if path:
            self.change_game_dir(path)

    def open_game_dir(self):
        try:
            open_folder(GAME_DIR)
        except Exception as e:
            self.log(f"Impossible d'ouvrir le dossier : {e}", "err")

    def open_mods_dir(self):
        try:
            open_folder(GAME_DIR / "mods")
        except Exception as e:
            self.log(f"Impossible d'ouvrir le dossier des mods : {e}", "err")

    def use_default_dir(self):
        if self.busy:
            return
        self.change_game_dir(str(DEFAULT_GAME_DIR))

    def use_minecraft_dir(self):
        if self.busy:
            return
        self.change_game_dir(mll.utils.get_minecraft_directory())

    # ---- réglages
    def load_settings(self) -> dict:
        if SETTINGS_FILE.exists():
            try:
                return json.loads(SETTINGS_FILE.read_text())
            except Exception:
                return {}
        return {}

    def save_settings(self):
        SETTINGS_FILE.write_text(
            json.dumps({
                "username": self.username.get().strip(),
                "ram_gb": int(self.ram.get()),
                "game_dir": str(GAME_DIR),
            })
        )

    def on_ram_change(self, value):
        self.ram_label.configure(text=f"{int(value)} Go")

    # ---- helpers UI (sûrs depuis un thread)
    def _ui(self, fn, *args, **kwargs):
        self.after(0, lambda: fn(*args, **kwargs))

    def log(self, text: str, tag: str = ""):
        stamp = time.strftime("%H:%M:%S")

        def append():
            self.logbox.configure(state="normal")
            self.logbox.insert("end", f"[{stamp}] {text}\n", tag)
            if int(self.logbox.index("end-1c").split(".")[0]) > 1500:
                self.logbox.delete("1.0", "500.0")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")

        self.after(0, append)

    def set_step(self, n: int, text: str):
        self._ui(self.step_label.configure, text=f"Étape {n}/{len(STEPS)} · {text}")
        self._ui(self.bar.set, 0)
        self._ui(self.percent.configure, text="0 %")
        self._ui(self.detail.configure, text="")

    def set_bar(self, value: int, maximum: int):
        ratio = 0 if maximum <= 0 else min(max(value / maximum, 0), 1)
        self._ui(self.bar.set, ratio)
        self._ui(self.percent.configure, text=f"{int(ratio * 100)} %")

    def set_detail(self, text: str):
        self._ui(self.detail.configure, text=text[:120])

    def set_speed(self, finished, total_files, done, total, speed, remaining):
        size = f"{fmt_size(done)} / {fmt_size(total)}" if total else fmt_size(done)
        self.set_detail(
            f"{finished}/{total_files} mods · {size} · {fmt_size(speed)}/s · reste {fmt_time(remaining)}"
        )

    def mll_callback(self) -> dict:
        state = {"max": 1}

        def set_max(v):
            state["max"] = max(int(v), 1)

        return {
            "setStatus": self.set_detail,
            "setMax": set_max,
            "setProgress": lambda v: self.set_bar(int(v), state["max"]),
        }

    # ---- liste des mods
    def show_manifest(self, manifest: dict, states: dict):
        self.manifest = manifest
        server = manifest.get("server", "")
        self.subtitle.configure(
            text=f"Minecraft {manifest['minecraft']} · {manifest.get('loader', 'forge').capitalize()}"
        )
        self.server_name.configure(text=server or "Aucun serveur")

        for w in self.mod_list.winfo_children():
            w.destroy()
        self.mod_rows = {}
        self.mod_state = dict(states)
        if not states:
            ctk.CTkLabel(self.mod_list, text="Aucun mod dans la liste.", text_color=GREY).pack(pady=20)
        for filename in states:
            row = ctk.CTkFrame(self.mod_list, fg_color=("#333333", "#262626"), corner_radius=6)
            row.pack(fill="x", padx=4, pady=3)
            name = re.sub(r"\.jar$", "", filename)
            ctk.CTkLabel(row, text=name, anchor="w").pack(side="left", padx=12, pady=7)
            lbl = ctk.CTkLabel(row, text="", anchor="e")
            lbl.pack(side="right", padx=12)
            self.mod_rows[filename] = lbl
            self.apply_mod_state(filename, states[filename])
        self.update_mods_title()

    def apply_mod_state(self, filename: str, state: str, extra: str = ""):
        text, color = MOD_STATES[state]
        if extra and state == "downloading":
            text = f"⬇ {extra}"
        self.mod_state[filename] = state
        lbl = self.mod_rows.get(filename)
        if lbl is not None:
            lbl.configure(text=text, text_color=color)

    def set_mod_state(self, filename: str, state: str, extra: str = ""):
        def apply():
            self.apply_mod_state(filename, state, extra)
            self.update_mods_title()
        self.after(0, apply)

    def update_mods_title(self):
        counted = [s for s in self.mod_state.values() if s not in ("remove", "removed")]
        ok = sum(1 for s in counted if s == "ok")
        self.mods_title.configure(text=f"Mods · {ok}/{len(counted)} à jour")

    # ---- statut du serveur
    def refresh_server(self, server: str):
        if not server:
            return
        try:
            data = ping_server(server)
        except Exception:
            self._ui(self.show_server_offline)
            return
        self._ui(self.show_server_status, data)

    def show_server_offline(self):
        self.server_status.configure(text="● Statut indisponible", text_color="#ff9f43")
        self.server_motd.configure(text="")
        self.server_players.configure(text="")
        self.server_version.configure(text="")

    def show_server_status(self, data: dict):
        self.server_status.configure(text="● En ligne", text_color="#6bff95")
        self.server_motd.configure(text=motd_text(data.get("description", "")))
        players = data.get("players", {})
        text = f"👥 {players.get('online', 0)}/{players.get('max', '?')} joueurs"
        names = [p.get("name", "") for p in players.get("sample", []) if p.get("name")]
        if names:
            text += "\n" + ", ".join(names[:8])
        self.server_players.configure(text=text)
        self.server_version.configure(text=data.get("version", {}).get("name", ""))
        fav = data.get("favicon", "")
        if fav.startswith("data:image") and "," in fav:
            try:
                img = Image.open(io.BytesIO(base64.b64decode(fav.split(",", 1)[1])))
                self.favicon_img = ctk.CTkImage(light_image=img, dark_image=img, size=(64, 64))
                self.icon_label.configure(image=self.favicon_img, text="")
            except Exception:
                pass

    # ---- rafraîchissement
    def refresh_all(self):
        threading.Thread(target=self._refresh_worker, daemon=True).start()

    def _refresh_worker(self):
        try:
            manifest = fetch_manifest()
            states = compute_mod_states(manifest)
        except Exception as e:
            self.log(f"Impossible de lire la liste des mods : {e}", "err")
            self._ui(self.subtitle.configure, text="Liste des mods indisponible")
            return
        self._ui(self.show_manifest, manifest, states)
        self.refresh_server(manifest.get("server", ""))

    def tick(self):
        """Met à jour le statut du serveur à intervalle régulier."""
        if self.manifest and not self.busy:
            server = self.manifest.get("server", "")
            threading.Thread(target=self.refresh_server, args=(server,), daemon=True).start()
        self.after(STATUS_REFRESH_SECONDS * 1000, self.tick)

    # ---- jouer
    def on_play(self):
        try:
            account = AUTH.login(self.username.get())
        except ValueError as e:
            self.log(str(e), "err")
            self.step_label.configure(text="Pseudo invalide")
            return
        self.save_settings()
        ram_mb = int(self.ram.get()) * 1024
        self.busy = True
        self.play_btn.configure(state="disabled", text="EN COURS…")
        threading.Thread(target=self.run_pipeline, args=(account, ram_mb), daemon=True).start()

    def run_pipeline(self, account: dict, ram_mb: int):
        try:
            self.set_step(1, STEPS[0])
            self.log("Récupération de la liste des mods sur GitHub…")
            manifest = fetch_manifest()
            server = manifest.get("server", "")
            self._ui(self.show_manifest, manifest, compute_mod_states(manifest))
            self.log(f"{len(manifest.get('mods', []))} mod(s) dans la liste.", "ok")

            self.set_step(2, STEPS[1])
            version_id = install_game(manifest, self.mll_callback(), self.log)
            self.log("Minecraft prêt.", "ok")

            self.set_step(3, STEPS[2])
            sync_mods(manifest, self.log, self.set_bar, self.set_mod_state, self.set_speed)

            self.set_step(4, STEPS[3])
            sync_files(manifest, self.log, self.set_bar)
            enable_resourcepack(manifest.get("resourcepack", ""), self.log)

            self.set_step(5, STEPS[4])
            self.set_bar(1, 1)
            self.log(f"Lancement du jeu{' et connexion à ' + server if server else ''}…")
            self._ui(self.play_btn.configure, text="JEU EN COURS")

            def on_line(line: str):
                bad = any(k in line for k in ("ERROR", "Exception", "FATAL"))
                self.log(line, "err" if bad else "")

            code = launch_game(version_id, account, ram_mb, server, on_line)
            if code == 0:
                self.log("Le jeu s'est fermé normalement.", "ok")
                self._ui(self.step_label.configure, text="Jeu fermé")
            else:
                self.log(f"Le jeu s'est arrêté avec le code {code} (plantage ?).", "err")
                self._ui(self.step_label.configure, text="Le jeu s'est arrêté de façon inattendue")
        except requests.RequestException as e:
            self.log(f"Problème de connexion : {e}", "err")
            self._ui(self.step_label.configure, text="Erreur réseau")
        except Exception as e:
            self.log(f"Erreur : {e}", "err")
            self._ui(self.step_label.configure, text="Erreur")
        finally:
            self.busy = False
            self._ui(self.play_btn.configure, state="normal", text="JOUER")


if __name__ == "__main__":
    App().mainloop()
