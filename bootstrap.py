"""
Mon Launcher - démarreur (bootstrap).

Petit programme que le joueur installe une seule fois. A chaque ouverture il :
  1. crée les dossiers nécessaires,
  2. regarde sur GitHub s'il existe une nouvelle version du launcher,
  3. la télécharge si besoin (vérification SHA-256),
  4. lance le launcher.

Il n'utilise que la bibliothèque standard de Python (+ certifi) : l'exe reste léger.
"""

import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path

import tkinter as tk

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

# ---------------------------------------------------------------- CONFIG ---
APP_NAME = "Mon Launcher"
REPO = "Felix-webdev/mon-serveur-mods"
# Dossier de la release GitHub qui contient launcher.json et les fichiers app-*
BASE_URL = os.environ.get(
    "MONLAUNCHER_BASE_URL", f"https://github.com/{REPO}/releases/download/launcher"
)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    PLATFORM = "Windows"
elif sys.platform == "darwin":
    PLATFORM = "macOS"
else:
    PLATFORM = "Linux"


def _data_dir() -> Path:
    override = os.environ.get("MONLAUNCHER_DATA")
    if override:
        return Path(override)
    if PLATFORM == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "MonLauncher"
    if PLATFORM == "macOS":
        return Path.home() / "Library" / "Application Support" / "MonLauncher"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "MonLauncher"


DATA_DIR = _data_dir()                  # fichiers du launcher (téléchargé automatiquement)
APP_DIR = DATA_DIR / "app"              # le launcher lui-même
LOG_DIR = DATA_DIR / "logs"
VERSION_FILE = DATA_DIR / "version.txt"
_DOCS = Path.home() / "Documents"
GAME_DIR = (_DOCS if _DOCS.exists() else Path.home()) / "MonLauncher"  # dossier du jeu (visible)


def log(text: str) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "bootstrap.log", "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {text}\n")
    except Exception:
        pass


def fmt_size(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def http_open(url: str, timeout: float = 15):
    req = urllib.request.Request(
        url, headers={"User-Agent": "MonLauncher-Bootstrap", "Cache-Control": "no-cache"}
    )
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)


def target_for(name: str) -> Path:
    """Chemin du programme à lancer, d'après le nom du fichier téléchargé."""
    if name.endswith(".tar.gz"):
        return APP_DIR / name[: -len(".tar.gz")]
    if name.endswith(".zip"):
        return APP_DIR / (name[: -len(".zip")] + ".app")
    return APP_DIR / name


def install_package(pkg: Path, name: str) -> Path:
    """Installe le fichier téléchargé dans APP_DIR et retourne le programme à lancer."""
    target = target_for(name)
    if name.endswith(".tar.gz"):  # Linux
        with tarfile.open(pkg) as t:
            try:
                t.extractall(APP_DIR, filter="data")
            except TypeError:  # Python < 3.12
                t.extractall(APP_DIR)
        target.chmod(0o755)
        pkg.unlink()
    elif name.endswith(".zip"):  # macOS (.app)
        if target.exists():
            shutil.rmtree(target)
        subprocess.run(["ditto", "-x", "-k", str(pkg), str(APP_DIR)], check=True)
        pkg.unlink()
    else:  # Windows (.exe)
        os.replace(pkg, target)
    return target


def run_update(report) -> Path:
    """Prépare les dossiers, met à jour le launcher si besoin, retourne le programme à lancer."""
    for d in (DATA_DIR, APP_DIR, LOG_DIR, GAME_DIR):
        d.mkdir(parents=True, exist_ok=True)

    report("Recherche de mises à jour…", None)
    local_version = VERSION_FILE.read_text().strip() if VERSION_FILE.exists() else ""

    try:
        with http_open(f"{BASE_URL}/launcher.json") as r:
            info = json.loads(r.read().decode("utf-8"))
        entry = info["files"][PLATFORM]
        remote_version = str(info["version"])
        name = entry["name"]
    except Exception as e:
        log(f"Impossible de lire launcher.json : {e}")
        installed = [p for p in APP_DIR.iterdir() if p.name.startswith("app-") and not p.name.endswith(".download")] \
            if APP_DIR.exists() else []
        if installed and VERSION_FILE.exists():
            report("Pas de connexion : lancement de la version installée…", 1.0)
            return installed[0]
        raise RuntimeError(
            "Impossible de contacter GitHub et aucun launcher n'est installé.\n"
            "Vérifie ta connexion internet puis réessaie."
        )

    target = target_for(name)
    if target.exists() and local_version == remote_version:
        log(f"Launcher à jour (version {local_version}).")
        report("Launcher à jour", 1.0)
        return target

    log(f"Mise à jour : {local_version or 'aucune'} -> {remote_version} ({name})")
    tmp = APP_DIR / (name + ".download")
    last_error = None
    for attempt in range(1, 3):
        try:
            h = hashlib.sha256()
            with http_open(f"{BASE_URL}/{name}", timeout=30) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                started = time.time()
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    speed = done / max(time.time() - started, 0.001)
                    detail = f"{fmt_size(done)} / {fmt_size(total)} · {fmt_size(speed)}/s" if total else fmt_size(done)
                    report(
                        "Téléchargement du launcher…" if not local_version else "Mise à jour du launcher…",
                        (done / total) if total else None,
                        detail,
                    )
            expected = (entry.get("sha256") or "").lower()
            if expected and h.hexdigest() != expected:
                tmp.unlink(missing_ok=True)
                raise RuntimeError("Fichier téléchargé corrompu (empreinte invalide).")
            break
        except Exception as e:
            last_error = e
            log(f"Téléchargement raté (essai {attempt}) : {e}")
            time.sleep(1.5)
    else:
        raise RuntimeError(f"Le téléchargement a échoué : {last_error}")

    report("Installation…", 1.0)
    try:
        target = install_package(tmp, name)
    except PermissionError:
        raise RuntimeError("Le launcher est déjà ouvert. Ferme-le, puis relance ce programme.")
    VERSION_FILE.write_text(remote_version)
    log(f"Launcher installé : {target}")
    return target


def launch(target: Path) -> None:
    quiet = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if PLATFORM == "Windows":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP : le launcher continue après la fermeture du démarreur
        subprocess.Popen([str(target)], cwd=str(APP_DIR), creationflags=0x00000008 | 0x00000200, **quiet)
    elif PLATFORM == "macOS":
        subprocess.Popen(["open", str(target)], **quiet)
    else:
        subprocess.Popen([str(target)], cwd=str(APP_DIR), start_new_session=True, **quiet)


# ------------------------------------------------------------------- GUI ---
BG, TRACK, FG, MUTED = "#16181d", "#2b2f38", "#ffffff", "#9aa0a6"
ACCENT, ERR = "#4f8cff", "#ff6b6b"
BAR_W = 420


class BootApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.configure(bg=BG)
        self.resizable(False, False)
        w, h = 500, 330
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+{(self.winfo_screenheight() - h) // 3}")

        tk.Label(self, text="⛏", bg=BG, fg=ACCENT, font=("Segoe UI Emoji", 34)).pack(pady=(24, 0))
        tk.Label(self, text=APP_NAME, bg=BG, fg=FG, font=("Segoe UI", 22, "bold")).pack()
        self.status = tk.Label(self, text="Démarrage…", bg=BG, fg=MUTED,
                               font=("Segoe UI", 11), wraplength=440, justify="center")
        self.status.pack(pady=(12, 10))
        self.canvas = tk.Canvas(self, width=BAR_W, height=10, bg=TRACK, highlightthickness=0)
        self.canvas.pack()
        self.fill = self.canvas.create_rectangle(0, 0, 0, 10, fill=ACCENT, width=0)
        self.detail = tk.Label(self, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9))
        self.detail.pack(pady=(6, 0))
        self.buttons = tk.Frame(self, bg=BG)
        self.after(200, self.start)

    def start(self):
        self.buttons.pack_forget()
        self.status.configure(fg=MUTED)
        threading.Thread(target=self.worker, daemon=True).start()

    def worker(self):
        try:
            target = run_update(self.report)
            self.report("Lancement du launcher…", 1.0)
            launch(target)
            self.after(1200, self.destroy)
        except Exception as e:
            log(f"Erreur : {e}")
            self.after(0, self.show_error, str(e))

    def report(self, text, fraction=None, detail=""):
        self.after(0, self._apply, text, fraction, detail)

    def _apply(self, text, fraction, detail):
        self.status.configure(text=text)
        self.detail.configure(text=detail)
        if fraction is not None:
            self.canvas.coords(self.fill, 0, 0, BAR_W * max(0.0, min(fraction, 1.0)), 10)

    def show_error(self, message):
        self.status.configure(text=message, fg=ERR)
        self.detail.configure(text="")
        for w in self.buttons.winfo_children():
            w.destroy()
        style = dict(bg=TRACK, fg=FG, relief="flat", padx=18, pady=6, bd=0, activebackground=ACCENT)
        tk.Button(self.buttons, text="Réessayer", command=self.start, **style).pack(side="left", padx=6)
        tk.Button(self.buttons, text="Fermer", command=self.destroy, **style).pack(side="left", padx=6)
        self.buttons.pack(pady=14)


if __name__ == "__main__":
    try:  # texte net sur les écrans à haute résolution (Windows)
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    BootApp().mainloop()
