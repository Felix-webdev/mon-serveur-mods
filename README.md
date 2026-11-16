# Mon serveur - mods et launcher

## 1. Fichiers à mettre sur GitHub (dans le dépôt)
| Fichier / dossier | Rôle |
|---|---|
| `launcher.py` | Le vrai launcher (interface, mods, fichiers du serveur, lancement du jeu) |
| `bootstrap.py` | Le petit démarreur que les joueurs installent : il télécharge et met à jour le launcher |
| `config.json` | **A modifier ici** : version de Minecraft, loader (Forge), adresse du serveur, pack de ressources à activer |
| `mods/` | Les fichiers `.jar` des mods |
| `fichiers/` | Fichiers personnalisés **toujours synchronisés** (pack de ressources, configs des mods...) |
| `fichiers_defaut/` | Fichiers **copiés une seule fois** (ex. `options.txt` par défaut) : le joueur peut ensuite les modifier |
| `mods.json` | Liste des mods et fichiers, générée automatiquement (ne pas modifier à la main) |
| `generate_manifest.py` | Script qui génère `mods.json` |
| `installer.iss` | Plan de l'installateur Windows |
| `build.bat` | Construction locale des exe Windows (optionnel) |
| `.github/workflows/generate-mods.yml` | Génère `mods.json` quand `mods/`, `fichiers/`, `fichiers_defaut/` ou `config.json` changent |
| `.github/workflows/build-launcher.yml` | Construit le launcher, le démarreur et l'installateur |

## 2. Fichiers créés automatiquement dans Releases (tag « launcher »)
- Pour les joueurs : `MonLauncher-Setup.exe` (Windows, recommandé), `MonLauncher-Windows.exe`, `MonLauncher-macOS.zip`, `MonLauncher-Linux.tar.gz`
- Pour le démarreur uniquement : `app-Windows.exe`, `app-macOS.zip`, `app-Linux.tar.gz` (le vrai launcher) et `launcher.json` (numéro de version et empreintes)

## 3. Fichiers sur l'ordinateur du joueur (Windows)
| Emplacement | Contenu | Créé par |
|---|---|---|
| `%LOCALAPPDATA%\Programs\MonLauncher\MonLauncher.exe` | Le démarreur (raccourcis menu Démarrer et bureau) | l'installateur |
| `%LOCALAPPDATA%\MonLauncher\app\app-Windows.exe` | Le vrai launcher (mis à jour automatiquement) | le démarreur |
| `%LOCALAPPDATA%\MonLauncher\version.txt` et `logs\` | Version installée, journal du démarreur | le démarreur |
| `Documents\MonLauncher\` | Le jeu : `mods/`, `saves/` (mondes), `config/`, `resourcepacks/`, `options.txt`... | le démarreur et le launcher |
| `%USERPROFILE%\.mon_launcher\settings.json` | Pseudo, mémoire, dossier choisi | le launcher |

## 4. Comment ça marche
1. Le joueur télécharge et installe `MonLauncher-Setup.exe` (une seule fois).
2. A chaque ouverture, le démarreur vérifie sur GitHub s'il existe une nouvelle version du launcher et la télécharge si besoin. Hors connexion, il lance la version installée.
3. Le launcher lit `mods.json`, installe Minecraft/Forge si besoin, puis **ne télécharge que ce qui manque ou a changé** : mods, puis fichiers du serveur.
4. Il active le pack de ressources indiqué dans `config.json`, puis lance Minecraft et connecte au serveur.
5. Si tu modifies `bootstrap.py`, les joueurs doivent réinstaller `MonLauncher-Setup.exe` (le démarreur ne se met pas à jour lui-même).

## 5. Personnaliser le jeu (logo de chargement, menus, options)
1. Crée un pack de ressources (zip) avec tes images (logo de chargement, logo du menu, fond, boutons...).
2. Mets-le dans `fichiers/resourcepacks/MonPack.zip`.
3. Dans `config.json`, écris `"resourcepack": "MonPack.zip"`.
4. Pour des options par défaut (langue, distance d'affichage...), mets un `options.txt` dans `fichiers_defaut/`.
5. Pour des menus entièrement personnalisés, ajoute un mod de menus (ex. FancyMenu) dans `mods/` et ses réglages dans `fichiers/config/`.

## 6. Ajouter des mods
Dépose les `.jar` dans `mods/` : `mods.json` se met à jour en environ une minute.

## 7. Réglage GitHub à vérifier
Settings → Actions → General → Workflow permissions → **Read and write permissions**.
