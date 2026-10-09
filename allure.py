# Allure -- generateur de polaires de vitesse a partir de trames NMEA0183
# Copyright (C) 2026 ETDEL
#
# Ce programme est un logiciel libre : vous pouvez le redistribuer et/ou le
# modifier selon les termes de la GNU General Public License telle que
# publiee par la Free Software Foundation, soit la version 3 de la licence,
# soit (a votre choix) toute version ulterieure.
#
# Ce programme est distribue dans l'espoir qu'il sera utile, mais SANS
# AUCUNE GARANTIE, sans meme la garantie implicite de QUALITE MARCHANDE ou
# d'ADEQUATION A UN USAGE PARTICULIER. Voir la GNU General Public License
# pour plus de details.
#
# Vous devriez avoir recu une copie de la GNU General Public License avec ce
# programme (fichier LICENSE). Sinon, voir <https://www.gnu.org/licenses/>.

"""
allure.py -- "Allure"
=========================
Generateur de polaires de vitesse -- application autonome : bibliotheque
standard + Tkinter + matplotlib + les trois modules crees specifiquement
pour cet outil (allure_engine.py, allure_config.py, allure_buffer.py).

Organisation de l'interface :

- Un bandeau de 3 ETAPES numerotees (le parcours normal des donnees) :
    1. ENREGISTREMENT : une session, en DIRECT (ecoute UDP, avec l'onglet
       "Suivi en direct" qui apparait pendant la prise) ou en import d'un
       fichier .log -- annotation voiles/moteurs, puis "Traiter la
       session".
    2. ENTREPOT : les echantillons de chaque passe traitee s'y accumulent
       durablement (polar_store.json, jamais ecrase) ; interrogation des
       archives par date/heure, correction a posteriori d'une annotation,
       corbeille.
    3. POLAIRES : la polaire max (meilleure vitesse par case parmi les
       configurations cochees -- une seule cochee, c'est sa polaire),
       tracee automatiquement, guide de voilure, exports .pol / .csv /
       TimeZero.

- Les PARAMETRES s'ouvrent par la roue dentee en haut a droite : une page
  qui remplace le contenu courant, organisee par categories (bandeau de
  navigation a droite) -- voiles/moteurs, voies d'ecoute, lissage,
  tampon glissant, table polaire, sauvegardes. "Enregistrer" ramene a
  l'etape d'ou l'on venait.

Prerequis -- UNE SEULE commande (ou installer_dependances.bat) :
    python -m pip install matplotlib

Fichiers necessaires, tous dans le MEME dossier :
    allure.py      (ce fichier -- c'est lui qu'on lance)
    allure_engine.py   logique de calcul, sans interface
    allure_config.py   persistance des reglages et de l'entrepot
    allure_buffer.py   tampon d'enregistrement glissant

Lancement :
    python allure.py

Allure -- ETDEL 2026
"""

import math
import os
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from collections import deque
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

# =========================================================================
# Chronometre de demarrage
#
# "Entre l'invite de commande et la fenetre, plusieurs minutes sans rien" :
# le probleme n'etait pas seulement la lenteur, c'etait le SILENCE. Chaque
# etape du demarrage est donc chronometree et ANNONCEE -- sur la console
# quand il y en a une, et dans journaux/demarrage.log dans tous les cas.
# La ou passent les minutes cesse d'etre un mystere : c'est ecrit.
#
# Les deux coupables classiques sous Windows, hors de notre code :
#   - le premier import de matplotlib apres une installation ou une mise a
#     jour reconstruit son cache de polices (une a plusieurs minutes) ;
#   - l'antivirus qui inspecte un a un les centaines de fichiers de
#     matplotlib/numpy a CHAQUE lancement sur un disque lent.
# Dans les deux cas, le chronometre le montre : c'est la phase
# "bibliotheques" qui porte tout le temps, pas les donnees ni l'interface.
# =========================================================================
_BOOT_T0 = time.time()
_BOOT_PHASES = []


def _boot_phase(label):
    """Enregistre la fin d'une phase de demarrage, et l'annonce sur la
    console si elle existe (jamais d'erreur si elle n'existe pas)."""
    now = time.time()
    prev = _BOOT_PHASES[-1][1] if _BOOT_PHASES else _BOOT_T0
    _BOOT_PHASES.append((label, now, now - prev))
    if sys.stdout is not None:
        try:
            print(f"[Allure] {label}  ({now - prev:.1f} s)", flush=True)
        except Exception:
            pass


if sys.stdout is not None:
    try:
        print("[Allure] Demarrage -- chargement des bibliotheques (matplotlib)...\n"
              "[Allure] (le premier lancement apres une installation peut prendre "
              "une a plusieurs minutes :\n[Allure]  cache de polices de matplotlib, "
              "antivirus qui inspecte les fichiers -- les suivants sont rapides)",
              flush=True)
    except Exception:
        pass

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
_boot_phase("bibliotheques chargees (matplotlib, tkinter)")

import allure_engine as pe
import allure_config as pcfg
import allure_buffer as pbuf
_boot_phase("modules d'Allure charges")

# Dossier du tampon glissant (voir allure_buffer.py) -- un sous-dossier plutot
# que le dossier de l'application : le tampon contient des dizaines de
# fichiers horaires, qui noieraient les fichiers de l'appli.
# Le tampon et les journaux de prise vivent dans les dossiers definis par
# allure_config (voir la section "Ou vivent les fichiers") : un seul endroit
# decide de l'organisation sur disque, et de la migration des versions
# precedentes.
BUFFER_DIR = pcfg.BUFFER_DIR

# =========================================================================
# Palette -- theme clair sobre, coherent sur toute l'application (definie
# ici en dur, pas d'import de theme.py : ce programme reste stand-alone).
# Un seul bleu d'accent (marine) porte l'identite visuelle : titres,
# onglet actif, valeurs chiffrees. Les seules autres couleurs vives sont
# fonctionnelles (vert = action positive, rouge = destructif/alerte,
# orange = transition), jamais decoratives.
# =========================================================================

# --- Palettes clair / sombre -------------------------------------------
# Les couleurs restent accessibles sous leurs noms historiques (BG_APP,
# FG_LABEL...) : ce sont des variables de module, reassignees en bloc par
# _apply_palette(). Le passage d'un theme a l'autre EN COURS DE ROUTE se
# fait par App._set_theme, qui reecrit aussi les couleurs des widgets deja
# construits (voir _retheme_widget_tree) -- aucun etat n'est perdu, on peut
# changer de theme en pleine prise.
#
# Contrainte de conception des palettes : deux noms qui partagent la MEME
# valeur en clair (ex. BG_PANEL/BG_FIELD/BG_HEADER, tous blancs) doivent
# aussi partager la meme valeur en sombre -- la re-thematisation des widgets
# existants se fait par correspondance de VALEURS (ancienne -> nouvelle),
# elle serait ambigue sinon.
_PALETTE_CLAIR = {
    "BG_APP": "#eef1f5",        # fond general, gris tres legerement bleute
    "BG_PANEL": "#ffffff",      # cartes / panneaux
    "BG_FIELD": "#ffffff",
    "BG_HEADER": "#ffffff",     # bandeau de titre
    "BG_NAV": "#e2e7ee",        # onglets inactifs
    "BG_NAV_ACTIVE": "#1a5fb4", # onglet actif = bleu d'accent, texte blanc
    "FG_ON_ACCENT": "#ffffff",
    "FG_LABEL": "#1c2733",
    "FG_LABEL_DIM": "#5c6b7a",
    "FG_DIGIT": "#1a5fb4",
    "COLOR_STARBOARD": "#2e7d32",
    "COLOR_PORT": "#c62828",
    "COLOR_TICK": "#b6bfc9",
    "COLOR_ACCENT": "#e08a1e",
    "COLOR_BORDER": "#cdd5de",
    "GAUGE_PORT_TINT": "#fbe9e9",
    "GAUGE_STARBOARD_TINT": "#e8f5ec",
    # Couleurs SYSTEME par defaut des widgets tk crees sans couleur explicite
    # (boutons standards, champs de saisie, ascenseurs) : elles aussi doivent
    # basculer en sombre, sinon des ilots gris clair subsistent la nuit. Les
    # variantes hex/nom/Windows d'une meme couleur recoivent des cibles
    # sombres infimement differentes : la correspondance inverse (retour au
    # clair) reste ainsi univoque -- y compris sur la plateforme ou certains
    # noms ("systembuttonface"...) n'existent pas, auquel cas la reecriture
    # de l'option echoue silencieusement et une autre variante fait le
    # travail.
    "BTN_BG": "#d9d9d9",
    "BTN_BG_WIN": "systembuttonface",
    "BTN_ACTIVE_BG": "#ececec",
    "ENTRY_BG_NAME": "white",
    "ENTRY_BG_WIN": "systemwindow",
    "TROUGH_BG": "#b3b3b3",
    "FG_BLACK_HEX": "#000000",
    "FG_BLACK_NAME": "black",
    "FG_WIN_BTN": "systembuttontext",
    "FG_WIN_TEXT": "systemwindowtext",
}
# Version nocturne : fonds sombres legerement bleutes, textes desatures,
# accents eclaircis pour garder le meme contraste percu qu'en plein jour --
# concu pour ne pas eblouir dans une timonerie de nuit.
_PALETTE_SOMBRE = {
    "BG_APP": "#12161c",
    "BG_PANEL": "#1c222a",
    "BG_FIELD": "#1c222a",
    "BG_HEADER": "#1c222a",
    "BG_NAV": "#232b35",
    "BG_NAV_ACTIVE": "#2f6fc4",
    "FG_ON_ACCENT": "#ffffff",
    "FG_LABEL": "#d3dbe4",
    "FG_LABEL_DIM": "#8b99a8",
    "FG_DIGIT": "#6ea8e8",
    "COLOR_STARBOARD": "#4caf50",
    "COLOR_PORT": "#e05e5e",
    "COLOR_TICK": "#46515e",
    "COLOR_ACCENT": "#e8a04a",
    "COLOR_BORDER": "#333d49",
    "GAUGE_PORT_TINT": "#3a2426",
    "GAUGE_STARBOARD_TINT": "#20342a",
    "BTN_BG": "#39434f",
    "BTN_BG_WIN": "#39434e",
    "BTN_ACTIVE_BG": "#46515f",
    "ENTRY_BG_NAME": "#1c222c",
    "ENTRY_BG_WIN": "#1c222b",
    "TROUGH_BG": "#2a323d",
    "FG_BLACK_HEX": "#e2e8ee",
    "FG_BLACK_NAME": "#e2e8ed",
    "FG_WIN_BTN": "#e2e8ec",
    "FG_WIN_TEXT": "#e2e8eb",
}
THEMES = {"clair": _PALETTE_CLAIR, "sombre": _PALETTE_SOMBRE}
ACTIVE_THEME = "clair"


def _apply_palette(name):
    """Reassigne toutes les couleurs de module d'apres la palette 'name'
    ("clair"/"sombre"). N'agit que sur les CONSTANTES : les widgets deja
    construits gardent leurs couleurs -- voir App._set_theme pour la mise a
    jour de l'interface vivante."""
    global ACTIVE_THEME
    palette = THEMES.get(name) or _PALETTE_CLAIR
    globals().update(palette)
    ACTIVE_THEME = name if name in THEMES else "clair"


_apply_palette("clair")
FONT_LABEL = ("Segoe UI", 9)
FONT_LABEL_BOLD = ("Segoe UI", 9, "bold")
FONT_MONO = ("Consolas", 10)
FONT_NAV = ("Segoe UI", 10)
FONT_NAV_BOLD = ("Segoe UI", 10, "bold")

TWS_COLORS = ["#1a5fb4", "#e08a1e", "#2e7d32", "#c62828", "#8e44ad",
              "#00838f", "#6d4c41", "#ad1457", "#558b2f", "#546e7a"]
# Variante nocturne : sur fond sombre, la palette de jour tombait sous un
# contraste de 3:1 pour la moitie des vents (bleu, brun, framboise...).
TWS_COLORS_SOMBRE = ["#5b9bef", "#f0a54a", "#5fb865", "#ef6b6b", "#b67ad3",
                     "#26b6c4", "#b08a7e", "#e05596", "#8cc35a", "#8fa7b3"]


def tws_colors():
    """Couleurs des courbes de vent pour le theme COURANT."""
    return TWS_COLORS_SOMBRE if ACTIVE_THEME == "sombre" else TWS_COLORS

# Ordre + libelles des 3 etapes du bandeau de navigation -- le parcours
# normal des donnees, dans l'ordre. Les Parametres ne sont plus une etape :
# ils s'ouvrent par la roue dentee en haut a droite (voir _open_settings),
# comme dans la plupart des logiciels -- on ne "passe" pas par les
# parametres a chaque sortie, on les regle une fois puis on n'y revient
# que ponctuellement.
STEPS = [
    ("acquisition", "1", "Acquisition"),
    ("entrepot", "2", "Entrepot"),
    ("polaires", "3", "Polaires"),
]

# Nombre max d'entrees conservees dans la corbeille (voir allure_config.
# load_trash/save_trash) -- une passe supprimee peut embarquer des centaines
# d'echantillons ; sans plafond, une corbeille jamais videe grossirait sans
# fin. Les entrees les plus anciennes sont evincees en premier (voir
# App._push_trash_entry).
TRASH_MAX_ENTRIES = 15

# Reglages figes dans le SteadyStateSmoother au moment de sa creation : les
# modifier impose donc un moteur neuf, et invalide les echantillons deja
# calcules d'une session pas encore traitee. Ce sont les SEULS parametres
# qui entrent en conflit avec une session en attente -- voir _save_params,
# qui n'interroge l'utilisateur que pour ceux-la et laisse tous les autres
# se modifier librement.
SMOOTHING_KEYS = ("smoothing_window_s", "sample_period_s", "maneuver_twa_deg",
                  "maneuver_stw_frac", "min_fill_frac")


# =========================================================================
# Ecoute UDP (thread par port) -- meme pattern que les autres outils
# =========================================================================

class UdpListener(threading.Thread):
    def __init__(self, name, source_filter_ip, port, on_line, on_error=None, bind_ip="0.0.0.0"):
        super().__init__(daemon=True, name=f"UDP-{name}")
        self.port_name = name
        self.source_filter_ip = (source_filter_ip or "").strip()
        self.bind_ip = bind_ip or "0.0.0.0"
        self.port = port
        self.on_line = on_line
        self.on_error = on_error
        self._running = threading.Event()
        self._running.set()
        self._sock = None
        # Etat OBSERVABLE de la voie. Sans lui, une voie qui s'ouvre mais ne
        # recoit rien est indiscernable d'une voie qui marche : c'est
        # exactement la signature d'un pare-feu, et l'utilisateur n'avait
        # aucun moyen de le savoir.
        self.bound = None          # None = pas encore tente, True/False ensuite
        self.bind_error = None
        self.frames = 0
        self.started_at = time.time()
        self.last_frame_at = None

    def run(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind((self.bind_ip, self.port))
            self._sock.settimeout(1.0)
            self.bound = True
        except OSError as e:
            self.bound, self.bind_error = False, str(e)
            if self.on_error:
                self.on_error(self.port_name, str(e))
            return

        while self._running.is_set():
            try:
                payload, addr = self._sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break

            if self.source_filter_ip and self.source_filter_ip != "0.0.0.0":
                if addr[0] != self.source_filter_ip:
                    continue

            try:
                text = payload.decode("ascii", errors="ignore")
            except Exception:
                continue

            for line in text.replace("\r", "\n").split("\n"):
                line = line.strip()
                if line:
                    self.frames += 1
                    self.last_frame_at = time.time()
                    self.on_line(self.port_name, line)

        if self._sock:
            self._sock.close()

    def stop(self):
        self._running.clear()


# =========================================================================
# Petits utilitaires d'interface
# =========================================================================

def _format_t(t):
    """Horodatage -> texte 'HH:MM:SS.mmm', prefixe 'J+N ' si plusieurs jours
    se sont ecoules depuis le debut du fichier (voir allure_engine.replay_log_lines).
    Round-trip exact avec _parse_t()."""
    if t is None:
        return ""
    days = int(t // 86400)
    rem = t - days * 86400
    hh = int(rem // 3600)
    mm = int((rem % 3600) // 60)
    ss = rem % 60
    base = f"{hh:02d}:{mm:02d}:{ss:06.3f}"
    return f"J+{days} {base}" if days > 0 else base


def _parse_t(text):
    """Inverse de _format_t(). Leve ValueError avec un message explicite si
    le texte ne correspond pas au format attendu."""
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if text.upper().startswith("J+"):
        try:
            day_part, rest = text[2:].split(" ", 1)
            days = int(day_part)
            text = rest.strip()
        except ValueError:
            raise ValueError("Format attendu : 'J+N HH:MM:SS' ou 'HH:MM:SS'")
    parts = text.split(":")
    if len(parts) != 3:
        raise ValueError("Format attendu : HH:MM:SS (ex. 14:32:07 ou 14:32:07.500)")
    hh, mm, ss = parts
    try:
        total = int(hh) * 3600 + int(mm) * 60 + float(ss)
    except ValueError:
        raise ValueError("Heure invalide")
    return days * 86400.0 + total


def _parse_date(text):
    """'JJ/MM/AAAA' (format francais -- 'AAAA-MM-JJ' tolere en secours) ->
    epoch Unix de minuit (heure locale) ce jour-la. Leve ValueError avec un
    message explicite si le texte n'est pas une date valide."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Indiquez une date (JJ/MM/AAAA).")
    tm = None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            tm = time.strptime(text, fmt)
            break
        except ValueError:
            continue
    if tm is None:
        raise ValueError("Format de date attendu : JJ/MM/AAAA (ex. 20/08/2026)")
    return time.mktime((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, -1))


def _parse_hms(text):
    """'HH:MM' -> nombre de secondes depuis minuit. Les secondes n'ont pas
    d'interet pour une recherche d'archives (l'echantillonnage est a ~10 s
    pres de toute facon) -- 'HH:MM:SS' reste tolere, les secondes sont
    simplement acceptees telles quelles."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Indiquez un horaire (HH:MM).")
    parts = text.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("Format d'horaire attendu : HH:MM (ex. 14:32)")
    try:
        hh, mm = int(parts[0]), int(parts[1])
        ss = float(parts[2]) if len(parts) == 3 else 0.0
    except ValueError:
        raise ValueError("Horaire invalide")
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss < 60):
        raise ValueError("Horaire invalide")
    return hh * 3600 + mm * 60 + ss


def make_scrollable(parent, height=420, fill_height=False):
    """Zone defilante verticale (canvas + frame interne). Le point delicat :
    caler la scrollregion sur bbox("all") EN PERMANENCE (comme avant) rend le
    canvas "defilable" d'une poignee de pixels meme quand tout le contenu
    tient deja dans la zone visible (marges internes, arrondis de calcul...) --
    ca se traduisait par un contenu qui glissait legerement de haut en bas
    sans raison a la molette/au trackpad. Ici, quand le contenu (inner) est
    moins haut que la zone visible (canvas), la scrollregion est calee EXACTEMENT
    sur la hauteur visible : aucun defilement residuel n'est plus possible, et
    la molette n'y a rien a faire tant que tout est visible.

    La molette elle-meme n'est PAS geree ici : la zone se signale a
    l'aiguilleur commun (voir install_wheel_routing), qui decide pour toute
    l'application quelle zone defile.

    fill_height=True : le cadre rendu prend TOUTE la hauteur visible (ce qui
    y est pose en expand s'etire), et ne defile -- ascenseur affiche -- que
    si son contenu ne tient pas. C'est le cas d'une colonne entiere, qui doit
    remplir la fenetre quand elle est assez haute et defiler sinon, plutot
    que d'ecraser ce qu'elle contient (la colonne de gauche des Polaires)."""
    outer = tk.Frame(parent, bg=BG_APP)
    canvas = tk.Canvas(outer, bg=BG_APP, highlightthickness=0, height=height)
    canvas._allure_scroll_area = True
    # ttk et non tk : sous Windows, tk.Scrollbar ignore les couleurs et
    # restait gris clair en theme sombre ; le style TScrollbar (voir
    # App._apply_ttk_style) suit la palette.
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    # L'ascenseur est pose AVANT la zone : Tk sert les widgets dans l'ordre,
    # et un canvas pose le premier reclame sa largeur par defaut (pres de
    # 400 px) -- dans une colonne etroite, il ne restait plus rien pour
    # l'ascenseur, qui disparaissait (la liste des voilures des Polaires).
    # En fill_height, il n'est pose que s'il a quelque chose a faire (voir
    # _sync_scrollregion), toujours AVANT la zone.
    if not fill_height:
        vsb.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    inner = tk.Frame(canvas, bg=BG_APP)
    # Largeur FIXE des la creation, remplacee par la vraie au premier
    # <Configure> du canevas : sans elle, le cadre interieur suivait sa
    # largeur DEMANDEE, que chaque note/follow_width reduit de quelques
    # pixels en se repliant -- des centaines de mises en page avant que la
    # fenetre ne s'affiche (pres de 20 s au demarrage).
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw",
                                  width=max(1, canvas.winfo_reqwidth()))
    body = inner
    if fill_height:
        # Le contenu vit dans body, grille dans inner sur une rangee de
        # hauteur MINIMALE egale a la hauteur visible : inner demande donc
        # max(contenu, zone visible), et la fenetre du canevas suit cette
        # demande d'elle-meme. Une hauteur imposee a l'element du canevas
        # ne verrait plus le contenu grandir : aucun evenement ne le dit.
        body = tk.Frame(inner, bg=BG_APP)
        body.grid(row=0, column=0, sticky="nsew")
        inner.grid_columnconfigure(0, weight=1)
        inner.grid_rowconfigure(0, weight=1)

    state = {"canvas_h": height, "inner_h": 0}

    def _sync_scrollregion():
        width = canvas.winfo_width() or 1
        if state["inner_h"] <= state["canvas_h"]:
            canvas.configure(scrollregion=(0, 0, width, state["canvas_h"]))
        else:
            canvas.configure(scrollregion=(0, 0, width, state["inner_h"]))
        if fill_height:
            # Ascenseur seulement quand le contenu deborde. Sans risque de
            # va-et-vient : le retirer elargit la zone, ce qui ne peut que
            # raccourcir un contenu qui tenait deja.
            need = state["inner_h"] > state["canvas_h"]
            if need and not vsb.winfo_manager():
                vsb.pack(side="right", fill="y", before=canvas)
            elif not need and vsb.winfo_manager():
                vsb.pack_forget()

    def _on_inner_configure(_event):
        state["inner_h"] = inner.winfo_reqheight()
        _sync_scrollregion()

    def _on_canvas_configure(event):
        canvas.itemconfig(win_id, width=event.width)
        state["canvas_h"] = event.height
        if fill_height:
            inner.grid_rowconfigure(0, minsize=event.height)
        _sync_scrollregion()

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)

    return outer, body


# =========================================================================
# Molette de la souris -- UNE seule regle pour toute l'application
#
# La molette fait defiler la zone SOUS LA SOURIS : la plus interieure qui a
# reellement quelque chose a faire defiler. Une liste qui deborde (passes,
# archives, guide de voilure...) defile elle-meme, jusqu'au bout et pas
# au-dela -- arrivee en butee, elle ne passe pas la main a la page ; une
# liste qui tient entiere n'a rien a faire defiler et laisse defiler la page
# qui la contient. Un trace matplotlib garde la molette pour son zoom.
#
# Avant, chaque zone defilante s'emparait de la molette pour toute
# l'application en passant sous la souris (bind_all), et la relachait en
# sortant -- or Tk signale ces entrees/sorties de facon irreguliere des
# qu'on survole un widget enfant, et les listes defilaient en plus d'elles-
# memes. Selon le dernier mouvement, la molette faisait donc defiler la
# liste, la page, ou les DEUX a la fois. Un seul aiguilleur, pose une fois,
# decide desormais pour tout le monde.
# =========================================================================

# Classes que Tk fait defiler d'elles-memes a la molette : leur reaction
# native est retiree (elles defileraient sinon deux fois, par Tk PUIS par
# l'aiguilleur), c'est l'aiguilleur qui s'en charge.
_WHEEL_LIST_CLASSES = ("Treeview", "Listbox", "Text")
# Lignes par cran de molette dans une liste : la valeur native de Tk.
_WHEEL_LIST_LINES = 3


def _wheel_target(widget):
    """Zone a faire defiler pour une molette au-dessus de widget : la plus
    interieure qui deborde reellement, ou None."""
    w = widget
    while w is not None:
        if getattr(w, "_allure_wheel_owner", False):
            return None
        try:
            # Sur un ascenseur, c'est lui qui fait defiler sa liste (liaison
            # native de Tk) : la page qui la contient ne doit pas suivre.
            if w.winfo_class() in ("Scrollbar", "TScrollbar"):
                return None
            scrollable = (getattr(w, "_allure_scroll_area", False)
                          or w.winfo_class() in _WHEEL_LIST_CLASSES)
            if scrollable:
                first, last = w.yview()
                if first > 0.0 or last < 1.0:
                    return w
        except (tk.TclError, AttributeError, TypeError, ValueError):
            pass
        w = getattr(w, "master", None)
    return None


def install_wheel_routing(root):
    """Pose l'aiguilleur de molette (voir plus haut) -- une fois, a la
    creation de la fenetre principale ; il vaut pour toutes ses fenetres."""
    rest = {}

    def on_wheel(event):
        # Maj+molette (defilement horizontal, balayage lateral d'un pave
        # tactile) et Alt+molette restent aux liaisons natives des listes :
        # seule la molette nue est aiguillee.
        if not event.delta or event.state & (0x0001 | 0x20000):
            return
        try:
            under = root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        target = _wheel_target(under)
        if target is None:
            return
        # Un pave tactile envoie de petits pas plutot que des crans entiers :
        # ils se cumulent jusqu'a faire une ligne (ou un cran de page), au
        # lieu d'etre arrondis a zero un par un.
        step = (120.0 / _WHEEL_LIST_LINES
                if target.winfo_class() in _WHEEL_LIST_CLASSES else 120.0)
        key = str(target)
        acc = rest.get(key, 0.0) + event.delta
        n = int(acc / step)
        rest[key] = acc - n * step
        if n:
            target.yview_scroll(-n, "units")

    for cls in _WHEEL_LIST_CLASSES:
        root.bind_class(cls, "<MouseWheel>", "")
    root.bind_all("<MouseWheel>", on_wheel, add="+")


def apply_mask(text, groups, sep, pad=False):
    """Pose les separateurs d'un champ date/heure : "21082026" devient
    "21/08/2026", "1435" devient "14:35".

    groups : tailles des groupes de chiffres (ex. (2, 2, 4) pour une date).

    pad=True signale que l'utilisateur VIENT DE TAPER un separateur, ce qui
    veut dire "ce groupe est fini" : le groupe clos est alors complete a
    gauche, et ecrire 5/1/2027 donne 05/01/2027. Sans cette regle, un masque
    purement numerique lirait "5120 27" et rendrait 51/20/27 -- une date
    fausse, et silencieusement fausse, ce qui est pire.

    Ce complement ne doit surtout PAS s'appliquer quand le texte contient
    des separateurs simplement parce qu'ils sont deja la : effacer le "8" de
    "21/08/2026" laisse "21/0.../2026", que la regle transformerait en
    "21/00/2026" -- le champ corrigerait la correction.

    Autre regle : un separateur n'est pose que s'il est SUIVI d'au moins un
    chiffre, sans quoi effacer le dernier chiffre d'un groupe ferait
    aussitot revenir le separateur et l'on ne pourrait jamais reculer.
    """
    raw = str(text)
    if pad:
        # Decoupage sur les separateurs presents : chaque groupe clos par un
        # separateur est complete a gauche.
        tokens, current = [], ""
        for ch in raw:
            if ch.isdigit():
                current += ch
            else:
                tokens.append(current)
                current = ""
        trailing_sep = bool(raw) and not raw[-1].isdigit()
        tokens.append(current)
        digits = ""
        for i, tok in enumerate(tokens):
            if i >= len(groups):
                break
            size = groups[i]
            closed = (i < len(tokens) - 1) or trailing_sep
            if closed and tok:
                digits += tok[-size:].rjust(size, "0")
            else:
                digits += tok
    else:
        digits = raw
    digits = "".join(c for c in digits if c.isdigit())[:sum(groups)]

    out, i = [], 0
    for size in groups:
        chunk = digits[i:i + size]
        if not chunk:
            break
        if out:
            out.append(sep)
        out.append(chunk)
        i += size
    return "".join(out)


# Touches qui DEPLACENT ou EFFACENT : le masque se tait alors completement.
# Reformater sur une de ces touches reviendrait a corriger la correction que
# l'utilisateur est en train de faire.
_MASK_PASSIVE_KEYS = frozenset((
    "BackSpace", "Delete", "Left", "Right", "Up", "Down", "Home", "End",
    "Tab", "ISO_Left_Tab", "Return", "KP_Enter", "Escape",
))


def attach_mask(entry, var, groups, sep):
    """Fait qu'un champ pose ses separateurs tout seul pendant la frappe.

    Deux precautions, apprises a l'usage :

    1. Branche sur <KeyRelease> et non sur une trace de la variable : pendant
       l'ecriture de la variable, Tk n'a pas fini de deplacer le curseur, et
       le lire a cet instant donne l'ancienne position -- le curseur se
       retrouvait avant le chiffre qu'on venait de taper et la saisie
       s'inversait ("21082026" devenait "21/20/026").

    2. N'intervient QUE lorsqu'on ecrit au bout du champ. Des qu'on efface
       ou qu'on corrige au milieu, le masque se tait : c'est l'utilisateur
       qui reprend la main, et la lecture reste de toute facon tolerante au
       format (voir _parse_date / _parse_hms).
    """
    def _on_key(event=None):
        keysym = getattr(event, "keysym", "") if event is not None else ""
        if keysym in _MASK_PASSIVE_KEYS:
            return
        raw = var.get()
        try:
            pos = entry.index("insert")
        except tk.TclError:
            pos = len(raw)
        if pos != len(raw):
            return          # correction au milieu : on ne touche a rien
        # Le caractere qui vient d'etre tape est le DERNIER du champ (on ne
        # passe ici que curseur en bout). On le lit dans le texte plutot que
        # dans l'evenement : Tk ne renseigne pas toujours event.char, et une
        # detection qui echoue silencieusement rendrait 51/20/27 pour un
        # 5/1/2027 parfaitement legitime.
        typed_sep = bool(raw) and not raw[-1].isdigit()
        masked = apply_mask(raw, groups, sep, pad=typed_sep)
        if masked == raw:
            return
        var.set(masked)
        try:
            entry.icursor(len(masked))
        except tk.TclError:
            pass

    entry.bind("<KeyRelease>", _on_key, add="+")
    # Expose le formatage pour les tests et pour un collage a la souris, qui
    # ne passe par aucune touche.
    entry.reformat_now = _on_key


def section(parent, title, side="top", expand=False):
    """Cadre de section homogene sur toute l'application : titre en bleu
    d'accent, bordure fine et discrete (groove 1px, pas le relief epais par
    defaut des LabelFrame) -- c'est cette uniformite, plus qu'aucun ornement,
    qui donne l'aspect soigne d'ensemble.

    side="bottom" ancre la section au BAS de son parent. Tk sert les bords
    avant le centre : une section ainsi ancree ne peut plus etre repoussee
    hors de la fenetre par ce qui la precede -- c'est ainsi qu'une colonne
    tient dans la fenetre sans avoir a defiler."""
    f = tk.LabelFrame(parent, text=f" {title} ", bg=BG_APP, fg=FG_DIGIT,
                       font=FONT_LABEL_BOLD, labelanchor="nw",
                       bd=1, relief="groove")
    f.pack(side=side, fill="both" if expand else "x", expand=expand,
           padx=10, pady=(10, 4))
    return f


def note(parent, text, pady=(0, 8), wraplength="auto"):
    """wraplength="auto" (le defaut) : au lieu d'une largeur de retour a la
    ligne fixe en pixels (qui deborde du texte hors de la fenetre si celle-ci
    est plus etroite que prevu, ou gaspille de la place si elle est plus
    large), le label suit la largeur reellement disponible de son parent a
    chaque redimensionnement. Une valeur en pixels reste possible, pour une
    colonne de largeur connue.

    anchor="w" : avec fill="x", sans ancre, le bloc de texte replie etait
    centre dans la largeur et paraissait indente."""
    lbl = tk.Label(parent, text=text, bg=BG_APP, fg=FG_LABEL_DIM,
                    justify="left", anchor="w", font=("Segoe UI", 8))
    if wraplength == "auto":
        lbl.pack(anchor="w", fill="x", padx=6, pady=pady)
        lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(200, e.width - 12)))
    else:
        lbl.configure(wraplength=wraplength)
        lbl.pack(anchor="w", padx=6, pady=pady)
    return lbl


def follow_width(lbl, margin=12):
    """Retour a la ligne a la largeur REELLE du libelle -- meme regle que
    note(..., wraplength="auto") : un texte plus long que la place disponible
    (fenetre etroite, ecran zoome, nom de voie ou de passe long, message du
    systeme) passe a la ligne au lieu d'etre coupe. Le libelle doit etre
    pose en fill="x" : c'est la largeur qu'il RECOIT qui decide."""
    lbl.configure(justify="left", anchor="w")
    lbl.bind("<Configure>",
             lambda e: lbl.configure(wraplength=max(120, e.width - margin)), add="+")
    return lbl


# =========================================================================
# Suivi en direct -- petits composants d'affichage (voir
# App._build_live_view). Volontairement dans la MEME palette claire
# que le reste de l'application : une etape qui detonnerait visuellement
# donnerait l'impression d'un outil rapporte d'ailleurs, alors qu'il s'agit
# bien d'une etape du meme parcours. Ce qui distingue cette etape, ce n'est
# pas un habillage different, c'est la taille des valeurs (lisibles a un
# metre, en mer, sans se pencher sur l'ecran).
# =========================================================================

LIVE_STALE_S = 12.0  # au-dela, une lecture est consideree perimee (grisee, pas effacee)

# Les teintes des deux moities du cadran d'angle de vent (GAUGE_PORT_TINT /
# GAUGE_STARBOARD_TINT) font partie des palettes ci-dessus : assez pales
# pour rester lisibles sous les graduations et le texte, mais suffisantes
# pour identifier le bord d'un coup d'oeil.


class ValueReadout(tk.Frame):
    """Bloc "grande valeur" : intitule discret + valeur en gros chiffres.
    Une lecture perimee (voir LIVE_STALE_S) est GRISEE plutot qu'effacee --
    en mer, un cadran qui se vide est ambigu (panne ? valeur nulle ?),
    tandis qu'une derniere valeur connue estompee se lit sans hesitation."""

    def __init__(self, master, label_text, font_size=24):
        super().__init__(master, bg=BG_PANEL, relief="solid", borderwidth=1, padx=10, pady=5)
        tk.Label(self, text=label_text, bg=BG_PANEL, fg=FG_LABEL_DIM,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
        self.value_lbl = tk.Label(self, text="---", bg=BG_PANEL, fg=FG_DIGIT,
                                   font=("Consolas", font_size, "bold"), anchor="center")
        self.value_lbl.pack(fill="x")

    def set_value(self, text, stale=False):
        self.value_lbl.configure(text=text, fg=(FG_LABEL_DIM if stale else FG_DIGIT))


def _draw_twa_gauge_background(canvas, size=240):
    """
    Decor du cadran d'ANGLE DE VENT VRAI (TWA), bord-relatif : 0 en haut
    (vent dans l'etrave), 180 en bas (vent arriere), babord a gauche,
    tribord a droite. C'est l'axe meme de la polaire -- d'ou le choix de ce
    cadran plutot que d'une rose des vents "vraie" (direction par rapport au
    nord) : cette derniere demanderait un cap compas que l'installation ne
    fournit pas, et n'apparait nulle part dans une polaire.

    Convention d'angle du Canvas Tk : 0 deg = est (3h), sens anti-horaire --
    d'ou la conversion en degres "compas" (0 = haut, sens horaire) faite par
    l'appelant, voir App._update_instruments.
    """
    canvas.delete("all")
    # Marges et graduations proportionnelles a la taille (le cadran suit
    # l'echelle de l'ecran, voir _build_live_view) : en pixels fixes, elles
    # rapetissaient a 150 % et les chiffres venaient toucher les traits.
    k = size / 240.0
    cx = cy = size / 2.0
    r = size / 2.0 - 24 * k
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=180,
                       fill=GAUGE_PORT_TINT, outline="", style="pieslice")
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=270, extent=180,
                       fill=GAUGE_STARBOARD_TINT, outline="", style="pieslice")
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=COLOR_TICK, width=1)
    for mag in (30, 60, 90, 120, 150):
        for side in (-1, 1):  # -1 = babord (gauche), +1 = tribord (droite)
            ang = math.radians(90 - side * mag)
            x1, y1 = cx + (r - 7 * k) * math.cos(ang), cy - (r - 7 * k) * math.sin(ang)
            x2, y2 = cx + r * math.cos(ang), cy - r * math.sin(ang)
            canvas.create_line(x1, y1, x2, y2, fill=COLOR_TICK, width=1)
            tx, ty = cx + (r - 20 * k) * math.cos(ang), cy - (r - 20 * k) * math.sin(ang)
            canvas.create_text(tx, ty, text=str(mag), fill=FG_LABEL_DIM, font=("Segoe UI", 7))
    # Axes 0/180 marques par un simple trait plus frappe -- AUCUN texte dans
    # ni autour du cadran (ni "etrave"/"arriere", ni "babord"/"tribord") :
    # les teintes des deux moities et la position des chiffres suffisent, et
    # tout texte finirait de toute facon sous l'une des aiguilles.
    for mag in (0, 180):
        ang = math.radians(90 - mag)
        x1, y1 = cx + (r - 10 * k) * math.cos(ang), cy - (r - 10 * k) * math.sin(ang)
        x2, y2 = cx + r * math.cos(ang), cy - r * math.sin(ang)
        canvas.create_line(x1, y1, x2, y2, fill=FG_LABEL_DIM, width=max(2, round(2 * k)))

    # Silhouette du bateau au centre, etrave en haut : sans elle, une fleche
    # de vent posee sur un cadran vide ne dit pas VERS QUOI elle souffle, et
    # c'est precisement ce qui rendait le cadran illisible -- les fleches
    # semblaient partir du bateau alors que le vent y arrive.
    hl, hw = r * 0.30, r * 0.115   # demi-longueur et demi-largeur de coque
    canvas.create_polygon(
        cx, cy - hl,                                   # etrave
        cx + hw, cy - hl * 0.25,
        cx + hw * 0.82, cy + hl * 0.80,
        cx, cy + hl,                                   # tableau arriere
        cx - hw * 0.82, cy + hl * 0.80,
        cx - hw, cy - hl * 0.25,
        fill=BG_PANEL, outline=FG_LABEL_DIM, width=1, smooth=False)
    return cx, cy, r


def _draw_wind_arrow(canvas, cx, cy, r, wind_from_deg, speed_frac, color,
                     tag="wind", width=5):
    """Fleche de vent au sens MARIN : elle part du bord du cadran, a l'angle
    d'ou vient le vent, et pointe VERS le bateau dessine au centre -- le vent
    souffle vers le bateau, il n'en sort pas. C'est l'inverse exact de
    l'ancienne aiguille facon compteur, qui partait du centre et donnait
    l'impression absurde d'un vent emis par le bord.

    wind_from_deg : angle bord-relatif d'ou vient le vent (0 = etrave,
    positif = tribord), ou None pour effacer la fleche.
    speed_frac : longueur de la fleche, de 0 a 1, PROPORTIONNELLE a la
    vitesse du vent et sur une echelle COMMUNE aux deux fleches (voir
    _update_instruments) : le vent le plus fort est donc toujours le plus
    long. Une fleche courte mais plus rapide serait le genre de contresens
    qu'un tableau de bord ne peut pas se permettre.
    """
    canvas.delete(tag)
    if wind_from_deg is None:
        return
    # Convention Canvas Tk : 0 deg = est, sens anti-horaire. L'angle
    # bord-relatif (0 = haut, sens horaire) s'y ramene par 90 - angle.
    ang = math.radians(90.0 - wind_from_deg)
    cos_a, sin_a = math.cos(ang), math.sin(ang)

    r_tip = r * 0.36                    # la pointe s'arrete au ras de la coque
    r_max = r * 0.97                    # queue au plus loin, jamais hors cadran
    frac = max(0.0, min(1.0, speed_frac))
    # Plancher de longueur : meme dans un souffle, la fleche doit rester une
    # fleche lisible plutot qu'un moignon colle a la coque.
    r_tail = r_tip + (r_max - r_tip) * (0.22 + 0.78 * frac)

    def pt(rad):
        return cx + rad * cos_a, cy - rad * sin_a

    tip = pt(r_tip)
    head_len = min(r * 0.20, (r_tail - r_tip) * 0.55)
    neck = pt(r_tip + head_len)
    tail = pt(r_tail)
    px, py = sin_a, cos_a                # perpendiculaire, en coordonnees ecran
    head_w = max(4.0, width * 1.7)

    canvas.create_line(tail[0], tail[1], neck[0], neck[1], fill=color, width=width,
                       tags=tag, capstyle="round")
    canvas.create_polygon(tip[0], tip[1],
                          neck[0] + px * head_w, neck[1] + py * head_w,
                          neck[0] - px * head_w, neck[1] - py * head_w,
                          fill=color, outline="", tags=tag)


# =========================================================================
# Application
# =========================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        install_wheel_routing(self)
        self.title(f"{pcfg.APP_NAME} v{pcfg.APP_VERSION} — {pcfg.APP_TAGLINE}")
        # Le logo d'Allure (disque bleu, voile blanche) en icone de fenetre
        # et de barre des taches : sans cela Tk affiche sa plume -- y compris
        # sur Allure.exe, dont l'icone de fichier ne s'applique pas a la
        # fenetre. Meme dessin que la zone de notification et que l'exe
        # (voir allure_config.app_icon_png).
        try:
            import base64
            self._app_icons = [tk.PhotoImage(data=base64.b64encode(pcfg.app_icon_png(s)))
                               for s in (64, 32)]
            self.iconphoto(True, *self._app_icons)
        except tk.TclError:
            pass
        self.configure(bg=BG_APP)

        self.config_data = pcfg.load_config()
        self.persistent_store = pe.PolarSampleStore.from_list(pcfg.load_store_data())
        self.sessions_index = pcfg.load_sessions_index()
        # Plages ecartees a l'interieur des passes : appliquees des le
        # chargement, sinon la premiere polaire tracee au demarrage
        # compterait des mesures que l'utilisateur avait deja mises de cote.
        self._apply_excluded_ranges()
        self.ui_state = pcfg.load_ui_state()
        self._place_main_window()
        # Corbeille (passes supprimees, individuellement ou via une
        # reinitialisation complete) -- voir _delete_session/_reset_store et
        # allure_config.load_trash(). Persistee sur disque : une passe
        # supprimee represente des donnees de mer potentiellement
        # irremplacables.
        self.trash = pcfg.load_trash()
        _boot_phase(f"donnees relues ({len(self.persistent_store)} echantillon(s), "
                    f"{len(self.sessions_index)} passe(s))")
        # Tampon glissant : tourne en permanence (quand il est actif) et
        # accumule le flux brut, independamment de toute prise. Voir
        # allure_buffer.py et _sync_listeners().
        self.buffer = pbuf.NmeaBuffer(BUFFER_DIR, self.config_data.get("buffer_retention_h", 48))

        self._engine_lock = threading.Lock()
        self.engine = None
        # "live" ou "import" -- non plus un mode dans lequel l'utilisateur
        # bascule, mais la nature du traitement en cours, posee
        # explicitement par _process_session et lue par les quelques
        # fonctions qui ont besoin de savoir d'ou vient la session.
        self.mode = tk.StringVar(value="live")
        # Duree maximale demandee au demarrage (modale de configuration).
        self.duration_var = tk.StringVar(
            value=str(self.config_data.get("default_recording_duration_min", 60)))
        self.seg_start_var = tk.StringVar()
        self.seg_end_var = tk.StringVar()
        # Widgets de la modale de configuration : inexistants tant qu'elle
        # n'est pas ouverte (voir _ask_config / _forget_config_widgets).
        self.sails_frame = None
        self.engines_frame = None
        self.sail_checkbuttons = {}
        self.engine_checkbuttons = {}
        self.duration_spinbox = None

        self.listeners = {}
        self.recording_active = False
        self.recording_deadline = None
        # Debut de l'enregistrement en cours -- sert uniquement au compteur de
        # duree ecoulee du suivi en direct (recording_deadline, lui, ne donne
        # que le temps RESTANT, et vaut None pour un enregistrement illimite).
        self.recording_started_at = None
        # Minuit du jour de la session directe en cours. Un journal ecrit en
        # direct ne porte que des heures ; pour le rejouer sur la meme base de
        # temps que ses segments (horodates en temps absolu), il faut savoir
        # de quel jour il s'agit. Survit a l'arret de l'enregistrement, car
        # c'est justement APRES l'arret qu'une re-analyse a lieu (voir
        # _reanalyse_pending_session).
        self.session_epoch_base = None
        self.session_log_path = None
        self.session_log_fh = None
        # Alerte manoeuvre en attente de reponse : l'evenement du guetteur
        # (None au repos) et la boite de dialogue non bloquante qui
        # l'accompagne. Le bandeau et la boite pointent vers la MEME
        # resolution (_resolve_maneuver) : le premier qui repond gagne.
        self._man_event = None
        self._man_dialog = None
        # Erreurs deja consignees par la boucle de rafraichissement : on ne
        # reecrit pas dix fois par seconde la meme trace dans le journal.
        self._tick_errors_seen = set()

        self.import_path = None
        self.import_t0 = None
        self.import_t1 = None
        self.import_count = 0

        # Variables voiles/moteurs : elles vivent dans l'application (et non
        # dans la modale de configuration), pour que la selection precedente
        # soit reproposee au prochain demarrage. Creees des maintenant, avant
        # toute construction d'interface.
        self.sail_vars = {}
        self.engine_vars = {}
        self._sync_config_vars()

        self.FEED_MAX_LINES = 400
        self._feed_queue = deque()
        self._feed_lock = threading.Lock()
        self._feed_line_count = 0

        # Theme applique AVANT la construction de l'interface : les widgets
        # naissent directement aux bonnes couleurs. En mode "auto" sans
        # position GPS encore connue (moteur pas demarre), la derniere
        # position memorisee (ui_state["last_gps"]) sert de repli.
        self._tray_icon = None
        self._theme_check_counter = 0
        initial_theme = self._desired_theme_name()
        if initial_theme != ACTIVE_THEME:
            _apply_palette(initial_theme)
        self.configure(bg=BG_APP)

        self._new_session_engine()
        self._build_ui()
        _boot_phase("interface construite")
        if ACTIVE_THEME != "clair":
            # Demarrage directement en sombre : les widgets construits avec
            # les couleurs de la palette sont deja bons, mais ceux restes aux
            # couleurs systeme par defaut (boutons standards, champs de
            # saisie) naissent clairs -- cette passe les rattrape.
            self._retheme_all("clair", ACTIVE_THEME)
        # Rouvre sur la derniere etape visitee (voir _show_step) plutot que de
        # toujours reprendre a Parametres -- cette etape n'a besoin d'etre
        # revisitee qu'une fois, au tout premier lancement ou pour ajuster un
        # reglage ponctuel ; ensuite, un usage courant se fait typiquement
        # entre Enregistrement/Entrepot/Polaires. Repli sur Parametres si
        # l'etat memorise est absent/invalide (ex. premier lancement).
        start_step = self.ui_state.get("last_step")
        if start_step == "instruments":
            # Etat memorise par une version ou le Suivi en direct etait une
            # etape a part du bandeau : il vit desormais DANS l'etape
            # Acquisition (le live EST le contenu de cette etape).
            start_step = "acquisition"
        if start_step not in self._step_frames:
            start_step = "parametres"
        self._show_step(start_step)
        # Purge d'abord (l'appli a pu rester fermee plusieurs jours : le
        # tampon contient alors des tranches largement perimees), puis
        # ouverture des voies UDP si le tampon doit tourner.
        self.buffer.purge()
        self._sync_listeners()
        # Des echantillons illisibles au chargement ne sont jamais passes
        # sous silence : l'utilisateur doit savoir que son entrepot a perdu
        # quelque chose, et pouvoir aller chercher une sauvegarde.
        if self.persistent_store.dropped_on_load:
            self.after(0, lambda n=self.persistent_store.dropped_on_load: messagebox.showwarning(
                "Entrepot cumulatif",
                f"{n} echantillon(s) de l'entrepot n'ont pas pu etre relus et ont ete ecartes "
                "(fichier abime, ou ecriture interrompue).\n\nLe reste de l'entrepot a ete charge "
                "normalement. Si ces donnees comptent, restaurez une sauvegarde portable depuis "
                "l'etape Entrepot avant d'enregistrer quoi que ce soit de nouveau."))
        self._refresh_tick()
        _boot_phase("voies reseau ouvertes, fenetre prete a s'afficher")
        self._write_boot_log()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # =====================================================================
    # Session (moteur d'ingestion courant)
    # =====================================================================

    def _new_session_engine(self):
        cfg = self.config_data
        smoother = pe.SteadyStateSmoother(
            window_s=cfg["smoothing_window_s"], sample_period_s=cfg["sample_period_s"],
            maneuver_twa_deg=cfg["maneuver_twa_deg"], maneuver_stw_frac=cfg["maneuver_stw_frac"],
            min_fill_frac=cfg["min_fill_frac"],
        )
        with self._engine_lock:
            self.engine = pe.PolarEngine(smoother=smoother, port_priority=cfg.get("priority"),
                                          source_by_type=cfg.get("sources"),
                                          allow_fallback=cfg.get("source_fallback", True))
            # Au repos, le moteur ECOUTE (les lectures en direct restent
            # vivantes) mais ne collecte pas : seule une acquisition
            # explicitement demarree produit des echantillons.
            self.engine.collecting = bool(self.recording_active)
            self.engine.maneuver_watch = self._make_maneuver_watch(cfg)

    def _make_maneuver_watch(self, cfg):
        """Guetteur d'arret automatique regle selon la config -- ou None si
        l'option est coupee (le moteur ne paie alors strictement rien)."""
        if not cfg.get("maneuver_stop_enabled", False):
            return None
        return pe.ManeuverWatch(
            window_s=cfg.get("maneuver_stop_window_s", 45.0),
            cog_threshold_deg=cfg.get("maneuver_stop_cog_deg", 30.0),
            sog_threshold_frac=cfg.get("maneuver_stop_sog_frac", 0.35),
            cooldown_s=cfg.get("maneuver_stop_cooldown_s", 120.0),
        )


    # =====================================================================
    # Interface -- structure generale : bandeau de titre (nom de l'appli +
    # roue dentee des Parametres), bandeau des 3 etapes numerotees, zone de
    # contenu (un frame par etape, affiche/masque via _show_step). Les
    # Parametres ne sont pas une etape du parcours : ils s'ouvrent par la
    # roue dentee, remplacent le contenu courant, et "Enregistrer" ramene
    # la ou on etait (voir _save_params).
    # =====================================================================

    def _build_ui(self):
        outer = tk.Frame(self, bg=BG_APP)
        outer.pack(fill="both", expand=True)

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        self._apply_ttk_style()

        self._build_nav(outer)

        # Barre d'acquisition PERMANENTE, entre le bandeau des etapes et le
        # contenu : elle suit l'utilisateur partout. Une prise qui tourne
        # doit se voir depuis l'Entrepot, depuis les Polaires et depuis les
        # Parametres -- et pouvoir s'arreter sans avoir a retourner la
        # chercher.
        self._build_acq_bar(outer)

        self.content_area = tk.Frame(outer, bg=BG_APP)
        self.content_area.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._step_frames = {}
        self.tab_parametres = tk.Frame(self.content_area, bg=BG_APP)
        self.tab_acquisition = tk.Frame(self.content_area, bg=BG_APP)
        self.tab_entrepot = tk.Frame(self.content_area, bg=BG_APP)
        self.tab_polaires = tk.Frame(self.content_area, bg=BG_APP)
        self._step_frames = {
            "parametres": self.tab_parametres,
            "acquisition": self.tab_acquisition,
            "entrepot": self.tab_entrepot,
            "polaires": self.tab_polaires,
        }

        self._build_step_parametres(self.tab_parametres)
        self._build_step_acquisition(self.tab_acquisition)
        self._build_step_entrepot(self.tab_entrepot)
        self._build_step_polaires(self.tab_polaires)

        self._current_step = None
        # Etape d'ou les Parametres ont ete ouverts -- "Enregistrer les
        # parametres" y ramene (voir _show_step et _save_params).
        self._settings_return_step = None

    def _build_nav(self, parent):
        """Bandeau de titre (nom + version + credit, roue dentee a droite)
        puis bandeau HORIZONTAL des 3 etapes : les etapes se lisent comme
        un fil d'ariane au-dessus du contenu, dans l'ordre du parcours des
        donnees. Pas de bouton de reduction dedie : le bouton natif de la
        fenetre suffit (l'ancien detournement vers la zone de notification
        a ete aboli -- un clic sur "reduire" doit reduire)."""
        header = tk.Frame(parent, bg=BG_HEADER)
        header.pack(fill="x")
        inner_h = tk.Frame(header, bg=BG_HEADER)
        inner_h.pack(fill="x", padx=16, pady=(9, 7))

        # La roue dentee (acces aux Parametres) -- empaquetee AVANT le bloc
        # de titre extensible : le packer Tk reserve les cavites gauche/
        # droite dans l'ordre des appels, sinon le titre engloutirait toute
        # la largeur et la roue disparaitrait.
        self.settings_btn = tk.Button(
            inner_h, text="\u2699", font=("Segoe UI Symbol", 15), relief="flat", bd=0,
            bg=BG_HEADER, fg=FG_LABEL_DIM, activebackground=BG_NAV,
            activeforeground=FG_DIGIT, cursor="hand2", padx=10, pady=2,
            command=self._open_settings)
        self.settings_btn.pack(side="right", padx=(8, 0))

        # Bouton COMPTEUR, a cote de la roue dentee : raccourci permanent
        # vers l'ecran d'acquisition, c'est-a-dire vers les mesures en
        # direct. Depuis n'importe ou dans l'application, un clic ramene
        # sous les yeux ce que le bord est en train de recevoir -- que l'on
        # enregistre ou non.
        self.live_btn = tk.Button(
            inner_h, text="⏱", font=("Segoe UI Symbol", 15), relief="flat", bd=0,
            bg=BG_HEADER, fg=FG_LABEL_DIM, activebackground=BG_NAV,
            activeforeground=FG_DIGIT, cursor="hand2", padx=10, pady=2,
            command=lambda: self._show_step("acquisition"))
        self.live_btn.pack(side="right")

        title_box = tk.Frame(inner_h, bg=BG_HEADER)
        title_box.pack(side="left", fill="x", expand=True)
        line1 = tk.Frame(title_box, bg=BG_HEADER)
        line1.pack(anchor="w")
        tk.Label(line1, text=f"{pcfg.APP_NAME} v{pcfg.APP_VERSION}", bg=BG_HEADER,
                 fg=FG_DIGIT, font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Label(line1, text=f"  \u2014  {pcfg.APP_TAGLINE}", bg=BG_HEADER, fg=FG_LABEL,
                 font=("Segoe UI", 11)).pack(side="left", pady=(3, 0))
        tk.Label(title_box, text=f"by {pcfg.APP_CREDIT}", bg=BG_HEADER, fg=FG_LABEL_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w")

        # Filet de separation, puis bandeau des etapes.
        tk.Frame(parent, bg=COLOR_BORDER, height=1).pack(fill="x")

        steps_row = tk.Frame(parent, bg=BG_NAV)
        steps_row.pack(fill="x")
        # Conserve pour pouvoir MASQUER le bandeau entier quand la page
        # Parametres est ouverte (voir _show_step) : les etapes n'ont rien a
        # faire a l'ecran pendant qu'on regle l'application, et leur absence
        # dit clairement "vous etes dans les parametres, pas dans le parcours".
        self._steps_row = steps_row
        self._nav_buttons = {}
        for key, num, title in STEPS:
            btn = tk.Button(steps_row, text=f"{num}.  {title}", relief="flat",
                             bd=0, padx=18, pady=9, font=FONT_NAV, bg=BG_NAV, fg=FG_LABEL,
                             activebackground=BG_NAV_ACTIVE, activeforeground=FG_ON_ACCENT,
                             cursor="hand2",
                             command=lambda k=key: self._show_step(k))
            btn.pack(side="left", fill="x", expand=True)
            self._nav_buttons[key] = btn

    def _build_acq_bar(self, parent):
        """Barre d'acquisition, visible sur toutes les pages : l'etat de la
        prise, son chrono, la configuration enregistree, les voies actives,
        et l'unique bouton demarrer/arreter."""
        bar = tk.Frame(parent, bg=BG_PANEL)
        bar.pack(fill="x")
        self._acq_bar_frame = bar
        self._acq_bar_border = tk.Frame(parent, bg=COLOR_BORDER, height=1)
        self._acq_bar_border.pack(fill="x")
        inner = tk.Frame(bar, bg=BG_PANEL)
        inner.pack(fill="x", padx=12, pady=7)
        self.record_btn = tk.Button(inner, text="Demarrer l'acquisition",
                                     command=self._toggle_recording,
                                     bg=COLOR_STARBOARD, fg="#ffffff", font=FONT_LABEL_BOLD,
                                     padx=14, pady=4, cursor="hand2")
        self.record_btn.pack(side="left")
        self.acq_state_lbl = tk.Label(inner, text="", bg=BG_PANEL, fg=FG_LABEL,
                                       font=FONT_LABEL_BOLD, anchor="w")
        self.acq_state_lbl.pack(side="left", fill="x", expand=True, padx=(14, 0))
        # Etat + chrono + configuration : une longue voilure (beaucoup de
        # codes) passe a la ligne au lieu d'etre coupee au bord droit.
        follow_width(self.acq_state_lbl, margin=4)

        # Bandeau d'alerte manoeuvre, SOUS la barre d'acquisition et comme
        # elle visible sur toutes les pages : il survit a la fermeture de la
        # boite de dialogue (on peut cliquer la croix de la boite, changer de
        # page, et repondre plus tard depuis le bandeau). Cache au repos.
        self._man_banner = tk.Frame(parent, bg=COLOR_PORT)
        man_inner = tk.Frame(self._man_banner, bg=COLOR_PORT)
        man_inner.pack(fill="x", padx=12, pady=6)
        # Boutons poses AVANT le libelle, cote droit : le packer sert les
        # widgets dans l'ordre, et le message pose le premier ne laissait
        # plus de place aux boutons a 150 % ("Non, fausse alerte" rogne en
        # "ausse"). C'est le message qui passe a la ligne, jamais les boutons
        # -- seul moyen de repondre une fois la boite fermee -- qui disparaissent.
        tk.Button(man_inner, text="Non, fausse alerte",
                  command=lambda: self._resolve_maneuver(False),
                  bg="#ffffff", fg=FG_LABEL, font=FONT_LABEL,
                  padx=10, pady=2, cursor="hand2").pack(side="right")
        tk.Button(man_inner, text="Oui, j'ai manoeuvre",
                  command=lambda: self._resolve_maneuver(True),
                  bg="#ffffff", fg=COLOR_PORT, font=FONT_LABEL_BOLD,
                  padx=10, pady=2, cursor="hand2").pack(side="right", padx=(10, 6))
        self._man_banner_lbl = tk.Label(man_inner, text="", bg=COLOR_PORT, fg="#ffffff",
                                         font=FONT_LABEL_BOLD, anchor="w")
        self._man_banner_lbl.pack(side="left", fill="x", expand=True)
        follow_width(self._man_banner_lbl, margin=4)
        # (non packe : _show_maneuver_alert le fait apparaitre)

    def _refresh_acq_bar(self, now=None):
        """Tient la barre a jour -- appelee a chaque tick, quelle que soit la
        page affichee (contrairement au suivi en direct, qui ne se rafraichit
        que lorsqu'il est a l'ecran)."""
        if getattr(self, "acq_state_lbl", None) is None:
            return
        now = time.time() if now is None else now
        # Barre volontairement SOBRE : elle doit repondre d'un coup d'oeil a
        # "est-ce que ca enregistre, depuis quand, sur quelle voilure" -- et a
        # rien d'autre. Le detail (voies actives, qualite, reception) vit sur
        # l'ecran d'acquisition, ou l'on va justement pour le lire.
        if self.recording_active and self.recording_started_at is not None:
            cfg = "+".join(self._selected_sails() + self._selected_engines()) or "-"
            elapsed = int(now - self.recording_started_at)
            txt = f"\u25cf  Acquisition en cours   {elapsed // 60:02d}:{elapsed % 60:02d}   ({cfg})"
            if self.recording_deadline is not None:
                left = int(max(0.0, self.recording_deadline - now))
                txt += f"   reste {left // 60:02d}:{left % 60:02d}"
            self.acq_state_lbl.configure(text=txt, fg=COLOR_PORT)
        else:
            self.acq_state_lbl.configure(text="Aucune acquisition en cours", fg=FG_LABEL)

    # ---------------------------------------------------------------
    # Arret automatique sur manoeuvre : le guetteur (ManeuverWatch, cote
    # moteur) leve un evenement ; l'application sonne, affiche un bandeau
    # permanent ET une boite non bloquante qui posent la meme question.
    # Le premier des deux qui repond gagne ; fermer la boite a la croix ne
    # repond PAS (le bandeau reste, on peut repondre plus tard). "Oui" =
    # la prise s'arrete et tout ce qui a ete mesure APRES l'instant de
    # detection est jete (la manoeuvre et l'attente de la reponse
    # n'appartiennent pas a la polaire). "Non" = la prise continue, le
    # guetteur repart pour un tour d'anti-rebond.
    # ---------------------------------------------------------------
    def _poll_maneuver_watch(self, now):
        """Appele a chaque tick PENDANT une prise. Ne detecte rien tant
        qu'une question est deja posee (repondre a deux alertes n'aurait
        aucun sens : c'est la meme prise qui s'arreterait)."""
        if self._man_event is not None:
            return
        if not self.config_data.get("maneuver_stop_enabled", False):
            return
        with self._engine_lock:
            watch = self.engine.maneuver_watch
            evt = watch.check(now) if watch is not None else None
        if evt is not None:
            self._show_maneuver_alert(evt)

    def _show_maneuver_alert(self, evt):
        self._man_event = evt
        cause = ("la route fond a tourne" if evt.get("cause") == "route"
                 else "la vitesse fond a change")
        heure = time.strftime("%H:%M:%S", time.localtime(evt["t"]))
        msg = "\u26a0  " + f"Manoeuvre detectee a {heure} ({cause}) -- avez-vous manoeuvre ?"
        # Sonnerie x3 : en mer, un seul bip se perd dans le bruit du bateau.
        # Les identifiants d'after sont gardes pour pouvoir ANNULER les bips
        # encore en attente si l'alerte est rangee avant qu'ils ne partent
        # (sinon un after orphelin peut sonner apres la fermeture).
        self._man_bells = []
        for delay in (0, 350, 700):
            try:
                self._man_bells.append(self.after(delay, self.bell))
            except tk.TclError:
                pass
        self._man_banner_lbl.configure(text=msg)
        self._man_banner.pack(fill="x", after=self._acq_bar_border)

        dlg = tk.Toplevel(self)
        self._man_dialog = dlg
        dlg.title("Manoeuvre detectee")
        dlg.configure(bg=BG_APP)
        dlg.resizable(False, False)
        dlg.transient(self)
        tk.Label(dlg, text=f"Manoeuvre detectee a {heure} : {cause} au-dela du seuil.",
                 bg=BG_APP, fg=FG_LABEL, font=FONT_LABEL_BOLD,
                 padx=18).pack(anchor="w", pady=(14, 4))
        tk.Label(dlg, text="Oui = la prise s'arrete, et les mesures faites depuis cet instant\n"
                            "sont jetees (la session part propre dans l'entrepot).\n"
                            "Non = fausse alerte, la prise continue.\n\n"
                            "Fermer cette boite ne repond pas : le bandeau rouge reste\n"
                            "affiche, vous pourrez repondre plus tard.",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=FONT_LABEL, justify="left",
                 padx=18).pack(anchor="w")
        btns = tk.Frame(dlg, bg=BG_APP)
        btns.pack(fill="x", padx=18, pady=14)
        tk.Button(btns, text="Oui, j'ai manoeuvre",
                  command=lambda: self._resolve_maneuver(True),
                  bg=COLOR_PORT, fg="#ffffff", font=FONT_LABEL_BOLD,
                  padx=12, pady=4, cursor="hand2").pack(side="left")
        tk.Button(btns, text="Non, fausse alerte",
                  command=lambda: self._resolve_maneuver(False),
                  bg=BTN_BG, fg=FG_LABEL, font=FONT_LABEL,
                  padx=12, pady=4, cursor="hand2").pack(side="left", padx=(10, 0))
        # La croix ne fait que ranger la boite : la question reste posee
        # (bandeau), la detection reste suspendue jusqu'a la reponse.
        dlg.protocol("WM_DELETE_WINDOW", self._close_maneuver_dialog)
        # Theme sombre : meme rattrapage que les autres fenetres secondaires,
        # pour qu'aucun widget aux couleurs systeme n'y naisse clair.
        self._theme_new_window(dlg)
        self._center_on_parent(dlg)

    def _cancel_maneuver_bells(self):
        for aid in getattr(self, "_man_bells", []):
            try:
                self.after_cancel(aid)
            except (tk.TclError, ValueError):
                pass
        self._man_bells = []

    def _close_maneuver_dialog(self):
        if self._man_dialog is not None:
            try:
                self._man_dialog.destroy()
            except tk.TclError:
                pass
            self._man_dialog = None

    def _dismiss_maneuver_alert(self):
        """Range bandeau et boite SANS repondre -- utilise quand la question
        n'a plus d'objet (nouvelle prise, arret manuel de la prise)."""
        self._man_event = None
        self._cancel_maneuver_bells()
        self._close_maneuver_dialog()
        if getattr(self, "_man_banner", None) is not None:
            self._man_banner.pack_forget()

    def _resolve_maneuver(self, confirmed):
        """Reponse a la question -- premier arrive (bandeau ou boite), seul
        servi : l'evenement est consomme atomiquement dans le thread Tk."""
        evt = self._man_event
        if evt is None:
            return
        self._man_event = None
        self._cancel_maneuver_bells()
        self._close_maneuver_dialog()
        self._man_banner.pack_forget()
        if not confirmed:
            # Fausse alerte : la prise continue. L'anti-rebond repart de la
            # REPONSE (et non de la detection) : si l'equipage a mis deux
            # minutes a repondre, inutile de redemander dans la seconde.
            with self._engine_lock:
                watch = self.engine.maneuver_watch
                if watch is not None:
                    watch.snooze(time.time()
                                 + self.config_data.get("maneuver_stop_cooldown_s", 120.0))
            return
        if not self.recording_active:
            return
        t_det = evt["t"]
        with self._engine_lock:
            dropped = self.engine.store.drop_after(t_det)
            # Le segment de configuration se referme a l'instant de la
            # detection : la plage annoncee dans l'entrepot s'arrete la ou
            # les mesures s'arretent (l'appel de _stop_recording, plus
            # tard, trouvera le segment deja clos et ne touchera a rien).
            self.engine.journal.close_live_segment(t_det)
        self._process_session()
        extra = (f"\n\n{dropped} echantillon(s) mesures apres la detection ont ete jetes."
                 if dropped else "")
        messagebox.showinfo(
            "Manoeuvre confirmee",
            "La prise a ete arretee a l'instant de la detection et la session "
            f"envoyee a l'entrepot.{extra}")

    def _open_settings(self):
        """La roue dentee est une bascule : un clic ouvre les Parametres, un
        second clic (ou "Enregistrer") ramene a l'etape d'ou l'on venait."""
        if self._current_step == "parametres":
            back = self._settings_return_step or "acquisition"
            if back not in self._step_frames or back == "parametres":
                back = "acquisition"
            self._show_step(back)
        else:
            self._show_step("parametres")

    # ---------------------------------------------------------------
    # Theme clair / sombre -- voir les palettes en tete de module. Le mode
    # "auto" suit le soleil : clair entre le lever et le coucher calcules
    # pour la derniere position GPS connue (allure_engine.sun_times).
    # ---------------------------------------------------------------
    # Options de couleur reecrites lors d'un changement de theme, separees
    # fond/encre : une meme valeur claire (le blanc, typiquement) peut etre
    # un FOND de panneau ici et une ENCRE sur bouton d'accent la, avec deux
    # destins differents en sombre.
    _BG_OPTIONS = ("background", "activebackground", "highlightbackground",
                   "selectcolor", "disabledbackground", "readonlybackground",
                   "troughcolor", "selectbackground", "buttonbackground")
    _FG_OPTIONS = ("foreground", "activeforeground", "disabledforeground",
                   "insertbackground", "selectforeground", "highlightcolor")
    _BG_KEYS = ("BG_APP", "BG_PANEL", "BG_FIELD", "BG_HEADER", "BG_NAV",
                "BG_NAV_ACTIVE", "COLOR_STARBOARD", "COLOR_PORT", "COLOR_TICK",
                "COLOR_ACCENT", "COLOR_BORDER",
                "BTN_BG", "BTN_BG_WIN", "BTN_ACTIVE_BG", "ENTRY_BG_NAME",
                "ENTRY_BG_WIN", "TROUGH_BG")
    _FG_KEYS = ("FG_LABEL", "FG_LABEL_DIM", "FG_DIGIT", "FG_ON_ACCENT",
                "COLOR_STARBOARD", "COLOR_PORT", "COLOR_TICK", "COLOR_ACCENT",
                "COLOR_BORDER",
                "FG_BLACK_HEX", "FG_BLACK_NAME", "FG_WIN_BTN", "FG_WIN_TEXT")

    def _desired_theme_name(self, now=None):
        """Nom du theme voulu A CET INSTANT selon le reglage : le mode manuel
        repond directement, le mode auto interroge le soleil. Sans position
        GPS (ni fraiche du moteur, ni memorisee d'un lancement precedent),
        l'auto retombe sur "clair" -- comportement historique, previsible."""
        mode = self.config_data.get("theme_mode", "clair")
        if mode in ("clair", "sombre"):
            return mode
        now = time.time() if now is None else now
        pos = None
        engine = getattr(self, "engine", None)
        if engine is not None:
            with self._engine_lock:
                last = engine.last_position
            # Une position GPS de moins de 6 h reste largement assez juste
            # pour un lever/coucher de soleil (meme a 10 noeuds, 60 milles
            # ne decalent le soleil que de quelques minutes).
            if last is not None and now - last["t"] < 6 * 3600.0:
                pos = (last["lat"], last["lon"])
        if pos is None:
            saved = self.ui_state.get("last_gps")
            if isinstance(saved, (list, tuple)) and len(saved) == 2:
                try:
                    pos = (float(saved[0]), float(saved[1]))
                except (TypeError, ValueError):
                    pos = None
        if pos is None:
            return "clair"
        return "clair" if pe.is_daytime(pos[0], pos[1], now) else "sombre"

    def _apply_ttk_style(self):
        """Styles ttk (Treeview, OptionMenu, Scrollbar...) alignes sur la
        palette COURANTE -- appele a la construction et a chaque changement
        de theme (les widgets ttk ignorent les options de couleur tk et ne
        connaissent que leur Style)."""
        style = ttk.Style(self)
        style.configure(".", background=BG_APP, foreground=FG_LABEL,
                        fieldbackground=BG_FIELD)
        style.configure("Treeview", background=BG_FIELD, fieldbackground=BG_FIELD,
                        foreground=FG_LABEL, bordercolor=COLOR_BORDER)
        style.map("Treeview",
                  background=[("selected", BG_NAV_ACTIVE)],
                  foreground=[("selected", FG_ON_ACCENT)])
        # Hauteur de ligne recalculee sur la police COURANTE : le theme la
        # fige a son chargement, et une ligne plus basse que son texte le
        # rogne (lettres coupees en haut et en bas, echelle d'ecran changee).
        try:
            self.tk.call("ttk::setTreeviewRowHeight")
        except tk.TclError:
            lines = tkfont.nametofont("TkDefaultFont").metrics("linespace")
            style.configure("Treeview", rowheight=lines + 2)
        style.configure("Treeview.Heading", background=BG_NAV, foreground=FG_LABEL,
                        bordercolor=COLOR_BORDER)
        style.map("Treeview.Heading", background=[("active", BG_NAV)])
        style.configure("TMenubutton", background=BG_FIELD, foreground=FG_LABEL,
                        arrowcolor=FG_LABEL)
        style.configure("TScrollbar", background=BG_NAV, troughcolor=BG_APP,
                        bordercolor=COLOR_BORDER, arrowcolor=FG_LABEL)

    def _retheme_widget_tree(self, widget, bg_map, fg_map):
        """Reecrit recursivement les couleurs d'un widget et de toute sa
        descendance, par correspondance ancienne valeur -> nouvelle valeur.
        Les options inconnues du widget (cget leve TclError) sont simplement
        sautees -- c'est ce qui permet de traiter uniformement tk et ttk."""
        for opts, mapping in ((self._BG_OPTIONS, bg_map), (self._FG_OPTIONS, fg_map)):
            for opt in opts:
                try:
                    cur = str(widget.cget(opt)).lower()
                except tk.TclError:
                    continue
                new = mapping.get(cur)
                if new:
                    try:
                        widget.configure({opt: new})
                    except tk.TclError:
                        pass
        for child in widget.winfo_children():
            self._retheme_widget_tree(child, bg_map, fg_map)

    def _set_theme(self, name):
        """Bascule l'application ENTIERE sur le theme 'name', a chaud : les
        constantes de module d'abord (_apply_palette), puis chaque widget
        deja construit, les styles ttk, le decor du cadran et la figure
        matplotlib. Aucun etat n'est touche -- on peut changer de theme en
        pleine prise sans rien perdre."""
        if name not in THEMES or name == ACTIVE_THEME:
            return
        old_name = ACTIVE_THEME
        _apply_palette(name)
        self._retheme_all(old_name, name)

    def _theme_new_window(self, win):
        """Fenetre secondaire construite APRES la bascule en sombre : ses
        widgets aux couleurs systeme (boutons, ascenseurs, champs) naissent
        clairs -- meme rattrapage qu'a la bascule, limite a cette fenetre. A
        appeler en fin de construction de chaque fenetre secondaire."""
        if ACTIVE_THEME == "clair":
            return
        old, new = THEMES["clair"], THEMES[ACTIVE_THEME]
        self._retheme_widget_tree(
            win, {str(old[k]).lower(): new[k] for k in self._BG_KEYS},
            {str(old[k]).lower(): new[k] for k in self._FG_KEYS})

    def _retheme_all(self, old_name, new_name):
        """Reecrit l'interface complete de la palette old_name vers new_name.
        Sert au changement de theme a chaud (_set_theme), et une fois au
        demarrage quand l'application s'ouvre directement en sombre : les
        widgets aux couleurs SYSTEME par defaut naissent clairs quelle que
        soit la palette active, ce passage les rattrape."""
        old = THEMES[old_name]
        new = THEMES[new_name]
        bg_map = {str(old[k]).lower(): new[k] for k in self._BG_KEYS}
        fg_map = {str(old[k]).lower(): new[k] for k in self._FG_KEYS}
        self.configure(bg=BG_APP)
        self._retheme_widget_tree(self, bg_map, fg_map)
        self._apply_ttk_style()
        if hasattr(self, "live_twa_canvas"):
            # Redessine le decor complet (les moities teintees n'ont pas de
            # correspondance simple valeur a valeur) ; les aiguilles
            # reviennent au prochain tick de _update_instruments.
            self._live_twa_geom = _draw_twa_gauge_background(self.live_twa_canvas, self._live_twa_size)
        if hasattr(self, "_max_fig"):
            self._max_fig.set_facecolor(BG_APP)
            self._max_ax.set_facecolor(BG_PANEL)
            self._max_canvas.get_tk_widget().configure(bg=BG_APP)
            # Retrace complet : graduations et legende portent leurs couleurs.
            if self._max_result is not None:
                self._max_polar_redraw(bool(self.config_data["symmetric_port_starboard"]))
            else:
                self._max_canvas.draw_idle()

    def _theme_tick(self):
        """Controle periodique (environ une fois par minute, cadence par
        _update_once) : memorise la derniere position GPS pour les prochains
        lancements, et applique le theme voulu s'il a change -- c'est ici que
        le mode auto bascule tout seul au lever et au coucher du soleil."""
        engine = getattr(self, "engine", None)
        if engine is not None:
            with self._engine_lock:
                last = engine.last_position
            if last is not None:
                # Arrondi a ~10 m : la valeur ne sert qu'au soleil, et le
                # fichier d'etat n'a pas a tracer une position precise.
                self.ui_state["last_gps"] = [round(last["lat"], 4), round(last["lon"], 4)]
        want = self._desired_theme_name()
        if want != ACTIVE_THEME:
            self._set_theme(want)

    def _show_step(self, key):
        if key not in self._step_frames:
            return
        if key == "parametres" and self._current_step not in (None, "parametres"):
            # Retenir d'ou l'on vient : "Enregistrer les parametres" y
            # ramenera (voir _save_params) -- comportement attendu d'une
            # roue dentee, on ne se retrouve pas teleporte ailleurs.
            self._settings_return_step = self._current_step
        for k, frame in self._step_frames.items():
            frame.pack_forget()
        self._step_frames[key].pack(fill="both", expand=True)
        in_settings = (key == "parametres")
        for k, btn in self._nav_buttons.items():
            active = (k == key)
            btn.configure(bg=BG_NAV_ACTIVE if active else BG_NAV,
                          fg=FG_ON_ACCENT if active else FG_LABEL,
                          font=FONT_NAV_BOLD if active else FONT_NAV)
        # Quand la page Parametres est ouverte, c'est la roue dentee qui
        # joue le role d'onglet actif : elle s'allume, et le bandeau des
        # etapes disparait entierement -- on est dans les reglages, pas dans
        # le parcours des donnees. Il revient a sa place exacte en sortant
        # (voir plus bas).
        if hasattr(self, "settings_btn"):
            self.settings_btn.configure(
                bg=BG_NAV_ACTIVE if in_settings else BG_HEADER,
                fg=FG_ON_ACCENT if in_settings else FG_LABEL_DIM)
        if hasattr(self, "live_btn"):
            # Le compteur s'allume quand on est sur l'ecran des mesures --
            # meme grammaire que la roue dentee pour les Parametres.
            on_live = (key == "acquisition")
            self.live_btn.configure(
                bg=BG_NAV_ACTIVE if on_live else BG_HEADER,
                fg=FG_ON_ACCENT if on_live else FG_LABEL_DIM)
        if hasattr(self, "_steps_row"):
            if in_settings:
                self._steps_row.pack_forget()
            elif not self._steps_row.winfo_manager():
                # Sa place exacte : AU-DESSUS de la barre d'acquisition (voir
                # _build_ui). Repose "avant le contenu", il revenait SOUS
                # elle -- l'ordre des bandeaux changeait selon qu'on etait
                # passe ou non par les Parametres.
                self._steps_row.pack(fill="x", before=self._acq_bar_frame)
        self._current_step = key
        # Memorise la derniere etape visitee (voir __init__) -- ecriture
        # automatique a chaque navigation, fichier separe et minuscule.
        self.ui_state["last_step"] = key
        pcfg.save_ui_state(self.ui_state)
        # Rafraichissements ponctuels a l'entree de certaines etapes, pour
        # rester a jour meme si l'utilisateur a modifie autre chose entre
        # deux visites.
        if key == "polaires":
            # Trace automatique : arriver sur l'etape suffit, sans bouton
            # "Tracer" (on vient forcement ici POUR voir la polaire).
            # Silencieux si l'entrepot est vide : un message d'accueil
            # remplace le trace.
            self._refresh_config_choices()
        elif key == "entrepot":
            self._refresh_store_size_label()
            self._refresh_sessions_list()
            self._refresh_trash_list()
        elif key == "acquisition":
            self._refresh_ports_summary()
        elif key == "parametres":
            self._refresh_buffer_state()

    # ---------------------------------------------------------------
    # Etape 2 : Enregistrement
    # ---------------------------------------------------------------
    def _build_step_acquisition(self, parent):
        """Etape 1 -- ACQUISITION. Son contenu est le suivi en direct, EN
        PERMANENCE : qu'on enregistre ou non, on voit ce qui rentre et si
        c'est exploitable. C'est l'ecran qu'on laisse ouvert en passerelle.

        Il n'y a plus de formulaire prealable a remplir : le choix de la
        source (UDP) vit dans les Parametres, l'import d'un fichier vit dans
        l'Entrepot, et la configuration voiles/moteurs est demandee au
        moment ou l'on demarre -- la ou la question se pose reellement."""
        self._build_live_view(parent)
        self._refresh_ports_summary()
        self._apply_recording_lock()

    # ---------------------------------------------------------------
    # Dialogue de configuration -- pose la question voiles/moteurs
    # AU MOMENT OU ELLE SE POSE (on demarre, ou on importe un fichier),
    # plutot que d'imposer un formulaire a remplir d'avance. Les variables
    # elles-memes (sail_vars/engine_vars) vivent dans
    # l'application : la selection precedente est donc reproposee, ce qui
    # rend le cas courant -- meme voilure qu'a la prise d'avant -- immediat.
    # ---------------------------------------------------------------
    def _ask_config(self, title, intro, with_duration=False, with_range=False,
                    ok_text="Demarrer"):
        """Modale de configuration. Retourne True si elle a ete validee (les
        variables de l'application portent alors le choix), False si elle a
        ete annulee ou fermee."""
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.configure(bg=BG_APP)
        dlg.transient(self)
        dlg.resizable(False, False)
        result = {"ok": False}
        # Largeur de retour a la ligne a l'echelle de l'ecran : en pixels
        # fixes, le texte (qui grossit avec l'echelle) s'etirait en hauteur.
        wrap = self._px(470)

        tk.Label(dlg, text=intro, bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                 justify="left", wraplength=wrap).pack(anchor="w", padx=14, pady=(12, 8))

        body = tk.Frame(dlg, bg=BG_APP)
        body.pack(fill="x", padx=14)
        self.sails_frame = tk.Frame(body, bg=BG_APP)
        self.sails_frame.pack(fill="x", pady=(0, 4))
        self.engines_frame = tk.Frame(body, bg=BG_APP)
        self.engines_frame.pack(fill="x", pady=(0, 4))
        self._rebuild_sail_engine_checkboxes()

        if with_duration:
            dur_row = tk.Frame(body, bg=BG_APP)
            dur_row.pack(fill="x", pady=(0, 6))
            tk.Label(dur_row, text="Duree max :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
            # buttonbackground explicite : le rattrapage du theme sombre ne
            # connait pas cette option, et les fleches restaient claires.
            self.duration_spinbox = tk.Spinbox(dur_row, from_=0, to=1440, increment=1, width=6,
                                                textvariable=self.duration_var,
                                                buttonbackground=BTN_BG_WIN)
            self.duration_spinbox.pack(side="left", padx=(4, 6))
            tk.Label(dur_row, text="minutes (0 = illimitee)", bg=BG_APP,
                     fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(side="left")
        if with_range:
            rng_row = tk.Frame(body, bg=BG_APP)
            rng_row.pack(fill="x", pady=(0, 6))
            tk.Label(rng_row, text="Plage :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
            tk.Label(rng_row, text="de", bg=BG_APP, fg=FG_LABEL).pack(side="left")
            # 17 caracteres : 'J+10 HH:MM:SS.mmm' (fichier de plusieurs jours,
            # voir _format_t) doit se lire en entier sans faire defiler le champ.
            tk.Entry(rng_row, width=17, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
            tk.Label(rng_row, text="a", bg=BG_APP, fg=FG_LABEL).pack(side="left")
            tk.Entry(rng_row, width=17, textvariable=self.seg_end_var).pack(side="left", padx=(4, 0))

        err_lbl = tk.Label(dlg, text="", bg=BG_APP, fg=COLOR_PORT,
                           font=("Segoe UI", 8, "bold"), justify="left", wraplength=wrap)
        err_lbl.pack(anchor="w", padx=14)

        def _validate():
            # La configuration est OBLIGATOIRE : une prise sans elle ne
            # produirait que des echantillons rejetes. On le dit DANS la
            # boite, sans la fermer -- il n'y a qu'a cocher et recommencer.
            if not self._selected_sails() and not self._selected_engines():
                err_lbl.configure(text="Cochez au moins une voile ou un moteur : sans configuration, "
                                        "les mesures ne peuvent etre rattachees a aucune polaire.")
                return
            result["ok"] = True
            dlg.destroy()

        btn_row = tk.Frame(dlg, bg=BG_APP)
        btn_row.pack(fill="x", padx=14, pady=(8, 12))
        tk.Button(btn_row, text=ok_text, command=_validate, bg=COLOR_STARBOARD, fg="#ffffff",
                  font=FONT_LABEL_BOLD, padx=14, pady=3).pack(side="right")
        tk.Button(btn_row, text="Annuler", command=dlg.destroy).pack(side="right", padx=(0, 8))

        dlg.protocol("WM_DELETE_WINDOW", dlg.destroy)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # Les cases a cocher de la modale ne doivent pas survivre a sa
        # fermeture : _apply_recording_lock les parcourt, et un widget
        # detruit y leverait une exception.
        dlg.bind("<Destroy>", lambda _e: self._forget_config_widgets(dlg))
        # Theme sombre : "Annuler" et les champs (couleurs systeme)
        # naissaient clairs dans une boite sombre.
        self._theme_new_window(dlg)
        self._center_on_parent(dlg)
        try:
            dlg.grab_set()
        except tk.TclError:
            pass
        self.wait_window(dlg)
        return result["ok"]

    def _forget_config_widgets(self, dlg=None):
        """Oublie les widgets de la modale une fois celle-ci fermee (les
        VARIABLES, elles, restent : c'est ce qui fait que la configuration
        precedente est reproposee au prochain demarrage)."""
        if dlg is not None and dlg.winfo_exists():
            return
        self.sail_checkbuttons = {}
        self.engine_checkbuttons = {}
        self.duration_spinbox = None
        self.sails_frame = None
        self.engines_frame = None

    def _center_on_parent(self, dlg):
        dlg.update_idletasks()
        try:
            x = self.winfo_rootx() + max(0, (self.winfo_width() - dlg.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - dlg.winfo_height()) // 3)
            # Jamais hors de l'ecran : une boite dont le bouton de validation
            # deborde (fenetre principale pres d'un bord, ecran zoome) ne
            # pourrait plus etre validee.
            x = max(0, min(x, dlg.winfo_screenwidth() - dlg.winfo_width()))
            y = max(0, min(y, dlg.winfo_screenheight() - dlg.winfo_height() - 48))
            dlg.geometry(f"+{x}+{y}")
        except tk.TclError:
            pass

    def _px(self, n):
        """Largeur en pixels a l'echelle de l'ecran (voir _ui_scale) : les
        polices grandissent avec elle, les largeurs en pixels doivent suivre."""
        return int(round(n * self._ui_scale()))

    def _fit_window(self, win, width, height, min_width=None, min_height=None):
        """Taille d'une fenetre secondaire : la taille voulue (en pixels a
        100 %) mise a l'echelle de l'ecran, BORNEE par l'ecran -- une fenetre
        plus grande que l'ecran cache son bas, ou vivent justement ses
        boutons."""
        try:
            scr_w, scr_h = win.winfo_screenwidth(), win.winfo_screenheight()
        except tk.TclError:
            scr_w, scr_h = 1280, 800
        w = min(self._px(width), scr_w - 40)
        h = min(self._px(height), scr_h - 100)
        win.geometry(f"{w}x{h}")
        if min_width is not None and min_height is not None:
            win.minsize(min(self._px(min_width), w), min(self._px(min_height), h))
        return w, h

    def _place_main_window(self):
        """Taille de la fenetre principale. Celle de la derniere fermeture si
        elle tient encore dans l'ecran (et l'etat agrandi), sinon 1240x800 a
        l'echelle de l'ecran. Dans tous les cas BORNEE par l'ecran, taille
        minimale comprise : un 1366x768, ou un ecran zoome a 125/150 % (qui
        ne laisse voir a l'application qu'un bureau de 1093x614 a 1280x720),
        ne doit jamais recevoir une fenetre plus grande que lui -- son bas,
        ou vivent les exports et "Enregistrer", passait sous la barre des
        taches."""
        scr_w, scr_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.minsize(min(self._px(1040), scr_w - 40), min(self._px(700), scr_h - 100))
        w, h = min(self._px(1240), scr_w - 40), min(self._px(800), scr_h - 100)
        geom = f"{w}x{h}"
        saved = self.ui_state.get("window")
        if isinstance(saved, dict) and isinstance(saved.get("geometry"), str):
            try:
                size, _sep, pos = saved["geometry"].partition("+")
                sw, sh = (int(v) for v in size.split("x"))
                sx, sy = (int(v) for v in pos.split("+"))
                if (sw <= scr_w and sh <= scr_h and -20 <= sx <= scr_w - 100
                        and -20 <= sy <= scr_h - 100):
                    geom = saved["geometry"]
            except (ValueError, TypeError):
                pass
        self.geometry(geom)
        if isinstance(saved, dict) and saved.get("zoomed"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

    def _remember_main_window(self):
        """Taille et etat agrandi, retenus pour le prochain lancement (voir
        _place_main_window)."""
        try:
            zoomed = self.state() == "zoomed"
            win = dict(self.ui_state.get("window") or {})
            win["zoomed"] = zoomed
            if not zoomed and self.state() == "normal":
                win["geometry"] = self.geometry()
            self.ui_state["window"] = win
        except tk.TclError:
            pass

    def _sync_config_vars(self):
        """Aligne les variables voiles/moteurs sur les listes reglees dans les
        Parametres : ajoute les codes nouveaux, retire ceux qui n'existent
        plus, et CONSERVE la selection des codes qui restent. C'est ce qui
        permet de modifier ses listes sans perdre ce qui etait coche."""
        for attr, list_key in (("sail_vars", "sail_list"), ("engine_vars", "engine_list")):
            current = getattr(self, attr, None) or {}
            kept = {}
            for code in self.config_data[list_key]:
                kept[code] = current.get(code) or tk.BooleanVar(value=False)
            setattr(self, attr, kept)

    def _rebuild_sail_engine_checkboxes(self):
        """(Re)construit les cases a cocher DANS LA MODALE de configuration
        (voir _ask_config). Hors modale, il n'existe aucune case : la
        question n'est posee qu'au moment ou elle se pose."""
        self._sync_config_vars()
        if getattr(self, "sails_frame", None) is None:
            return
        for w in self.sails_frame.winfo_children():
            w.destroy()
        for w in self.engines_frame.winfo_children():
            w.destroy()
        # Au plus 8 cases par rangee, en grille : sur une seule rangee,
        # une garde-robe de course (16 voiles) etirait la boite -- qui n'est
        # pas redimensionnable -- jusqu'a pousser "Demarrer" hors de l'ecran
        # a 150 %. La grille garde aussi les codes alignes d'une rangee a
        # l'autre.
        per_row = 8
        for frame, title, vars_, attr in (
                (self.sails_frame, "Voiles :", self.sail_vars, "sail_checkbuttons"),
                (self.engines_frame, "Moteurs :", self.engine_vars, "engine_checkbuttons")):
            tk.Label(frame, text=title, bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left", anchor="n")
            grid_f = tk.Frame(frame, bg=BG_APP)
            grid_f.pack(side="left", fill="x")
            boxes = {}
            for k, (code, var) in enumerate(vars_.items()):
                cb = tk.Checkbutton(grid_f, text=code, variable=var, bg=BG_APP, fg=FG_LABEL,
                                    selectcolor=BG_PANEL, activebackground=BG_APP,
                                    activeforeground=FG_LABEL,
                                    command=self._on_config_checkbox_changed)
                cb.grid(row=k // per_row, column=k % per_row, sticky="w", padx=4)
                boxes[code] = cb
            setattr(self, attr, boxes)

    def _selected_sails(self):
        return [code for code, var in self.sail_vars.items() if var.get()]

    def _selected_engines(self):
        return [code for code, var in self.engine_vars.items() if var.get()]

    def _on_config_checkbox_changed(self):
        if self.mode.get() == "live" and self.recording_active:
            self._apply_live_config()

    def _apply_live_config(self):
        sails, engines = self._selected_sails(), self._selected_engines()
        with self._engine_lock:
            self.engine.set_live_config(time.time(), sails, engines)

    # ---------- Bascule de mode ----------
    def _apply_recording_lock(self):
        """Met l'ecran d'acquisition en accord avec l'etat de la prise : le
        libelle et la couleur du bouton, et l'etat affiche a cote.

        Il n'y a plus grand-chose a verrouiller : la configuration ne se
        saisit plus dans un formulaire permanent mais dans une modale ouverte
        au demarrage (voir _ask_config), et l'import a quitte cet ecran pour
        l'Entrepot. Ce qui reste ferme pendant une prise l'est la ou ca se
        decide -- les voies UDP et les sources dans les Parametres (voir
        _save_params)."""
        locked = self.recording_active
        if getattr(self, "record_btn", None) is not None:
            self.record_btn.configure(
                text="Arreter l'acquisition" if locked else "Demarrer l'acquisition",
                bg=COLOR_PORT if locked else COLOR_STARBOARD)
        # Etat pose des maintenant (et non au premier rafraichissement) : au
        # lancement, la barre ne doit pas rester une seconde muette sur la
        # seule question qui compte -- est-ce que ca enregistre ?
        self._refresh_acq_bar()
        # Les widgets de la modale n'existent que tant qu'elle est ouverte,
        # et on ne peut pas demarrer pendant qu'elle l'est : rien a y faire.
        # Ceux qui subsistent (import de l'Entrepot) sont geres ici.
        for widget in (getattr(self, "choose_import_btn", None),
                       getattr(self, "process_import_btn", None)):
            if widget is not None:
                widget.configure(state="disabled" if locked else "normal")
        if getattr(self, "import_lock_lbl", None) is not None:
            self.import_lock_lbl.configure(
                text="Acquisition en cours : l'import est momentanement indisponible."
                if locked else "")

    # Au-dela de ce silence, une voie ouverte qui n'a JAMAIS rien recu n'est
    # plus une voie qui attend : c'est une voie qu'on ne lui livre pas.
    NET_SILENCE_ALERT_S = 20.0

    def _refresh_ports_state(self):
        """Etat vivant des voies d'ecoute, et diagnostic du silence.

        Le cas qui a motive tout ceci : sous Windows, les regles de
        pare-feu portent sur l'EXECUTABLE. Lance par pythonw.exe (raccourci
        sans console), le programme est un executable NOUVEAU aux yeux du
        pare-feu, meme si python.exe avait ete autorise depuis toujours. Le
        socket s'ouvre normalement -- aucune erreur, aucune boite -- et les
        datagrammes sont jetes en silence avant d'arriver. Impossible a
        deviner sans le dire."""
        if getattr(self, "ports_state_lbl", None) is None:
            return
        now = time.time()
        lines, mutes, refused = [], [], []
        names = {key: f"{self.port_label(key, with_key=False)} "
                      f"({self.config_data[key]['ip'] or '*'}:{self.config_data[key]['port']})"
                 for key in pcfg.PORT_KEYS if self.config_data[key].get("enabled")}
        # Colonne a la largeur du plus long nom (40 caracteres + adresse
        # possibles) : figee a 34, elle decalait les colonnes des noms longs.
        name_w = max([34] + [len(n) for n in names.values()])
        for key, name in names.items():
            li = self.listeners.get(key)
            if li is None:
                lines.append(f"  {name:<{name_w}s} fermee")
                continue
            if li.bound is False:
                # Detail de l'erreur systeme dans le bandeau rouge (qui passe
                # a la ligne) et non dans le tableau : un message Windows
                # depasse la largeur de la fenetre et y etait coupe.
                lines.append(f"  {name:<{name_w}s} REFUSEE (detail ci-dessous)")
                refused.append((key, li.bind_error))
                continue
            if li.bound is None:
                lines.append(f"  {name:<{name_w}s} ouverture...")
                continue
            if li.frames == 0:
                waited = now - li.started_at
                lines.append(f"  {name:<{name_w}s} a l'ecoute, AUCUNE trame "
                             f"(depuis {waited:.0f} s)")
                if waited >= self.NET_SILENCE_ALERT_S:
                    mutes.append(key)
            else:
                age = (now - li.last_frame_at) if li.last_frame_at else 0.0
                lines.append(f"  {name:<{name_w}s} {li.frames} trame(s), "
                             + ("derniere en direct" if age < 2 else
                                f"derniere il y a {age:.0f} s"))
        # Changement d'heure du bord encaisse (fuseau, heure d'hiver, remise
        # a l'heure) : le dire, sinon l'utilisateur qui SAIT avoir change
        # l'heure se demande ce que ses mesures sont devenues. Reponse :
        # rien de fausse -- les fenetres de mesure ont ete reamorcees.
        with self._engine_lock:
            jumps = self.engine.stats.get("clock_jumps", 0)
        if jumps:
            lines.append(f"  Changement d'heure detecte ({jumps} fois) : fenetres de "
                         "mesure reamorcees, rien n'est fausse.")
        self.ports_state_lbl.configure(
            text="\n".join(lines) if lines else "  (aucune voie ouverte)")

        warns = []
        if refused:
            # En tete : une voie refusee n'a plus son detail dans le tableau,
            # il doit rester lisible meme quand une autre voie est muette.
            warns.append("\n".join(f"{self.port_label(k, with_key=False)} n'a pas pu "
                                   f"s'ouvrir : {err}" for k, err in refused)
                         + "\nLe port est probablement deja pris par un autre programme.")
        if mutes:
            exe = os.path.basename(sys.executable or "python")
            warns.append("Le port est bien ouvert, mais AUCUNE trame n'arrive sur : "
                         + ", ".join(self.port_label(k, with_key=False) for k in mutes)
                         + f".\nSous Windows, le pare-feu autorise un EXECUTABLE, pas un "
                           f"programme : lance par {exe}, Allure lui est inconnu meme si une "
                           "autre version de Python avait ete autorisee. Le socket s'ouvre "
                           "quand meme, et les trames sont jetees avant d'arriver.\n"
                           "Verifiez : Parametres Windows > Reseau > Pare-feu > Autoriser une "
                           f"application, et cochez {exe} en reseau PRIVE. Verifiez aussi que "
                           "l'emetteur envoie bien vers cet ordinateur (adresse et port).")
        self.net_warn_lbl.configure(text="\n\n".join(warns))

    def _open_error_log(self):
        """Ouvre journaux/erreurs.log avec l'application par defaut du
        systeme. Sans console, c'est le seul endroit ou une panne laisse une
        trace : il faut pouvoir y arriver sans fouiller les dossiers."""
        path = error_log_path()
        if not os.path.exists(path):
            messagebox.showinfo("Journal des erreurs",
                                "Aucune erreur n'a ete consignee : le fichier n'existe "
                                f"pas encore.\n\nIl sera cree ici le cas echeant :\n{path}")
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)      # Windows
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception:
            messagebox.showinfo("Journal des erreurs", f"Le journal se trouve ici :\n{path}")

    def _write_boot_log(self):
        """Ecrit journaux/demarrage.log (ecrase a chaque lancement : c'est
        le DERNIER demarrage qui interesse) et garde le total sous la main
        pour le Diagnostic de l'installation."""
        total = time.time() - _BOOT_T0
        self._boot_total_s = total
        lines = [f"{pcfg.APP_NAME} v{pcfg.APP_VERSION} -- demarrage du "
                 f"{time.strftime('%d/%m/%Y %H:%M:%S')}",
                 f"Interprete : {sys.executable}", ""]
        for label, _t, dt in _BOOT_PHASES:
            lines.append(f"  {dt:7.2f} s  {label}")
        lines.append(f"  {total:7.2f} s  TOTAL (invite de commande -> fenetre)")
        libs = _BOOT_PHASES[0][2] if _BOOT_PHASES else 0.0
        if libs > 15.0:
            lines += ["",
                      "La quasi-totalite du temps est partie dans le chargement des",
                      "bibliotheques, AVANT la premiere ligne d'Allure. Deux causes",
                      "classiques sous Windows :",
                      "  - premier lancement apres une installation ou mise a jour de",
                      "    matplotlib : son cache de polices se reconstruit (une fois) ;",
                      "  - antivirus qui inspecte les fichiers de matplotlib/numpy a",
                      "    chaque lancement : ajoutez le dossier de Python (ou Allure.exe)",
                      "    aux exclusions de l'antivirus pour retrouver un demarrage court."]
        try:
            pcfg.ensure_dirs()
            with open(os.path.join(pcfg.LOGS_DIR, "demarrage.log"), "w",
                      encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def diagnostic_text(self):
        """Ce qu'il faut savoir de l'installation quand quelque chose cloche :
        quel interprete tourne (donc quel executable le pare-feu voit), avec
        ou sans console, et ou sont les journaux."""
        exe = sys.executable or "?"
        frozen = bool(getattr(sys, "frozen", False))
        interp = f"{exe}" + ("   (exe autonome PyInstaller)" if frozen else "")
        return (f"{pcfg.APP_NAME} v{pcfg.APP_VERSION}\n"
                f"Interprete : {interp}\n"
                f"Console : {'aucune' if running_windowless() else 'ouverte'}\n"
                f"Journal des erreurs : {error_log_path()}\n"
                f"Dossier de travail : {pcfg.APP_DIR}\n"
                + (f"Dernier demarrage : {self._boot_total_s:.1f} s "
                   f"(detail phase par phase : journaux/demarrage.log)"
                   if getattr(self, "_boot_total_s", None) is not None else
                   "Dernier demarrage : (en cours)"))

    def _refresh_ports_summary(self):
        active = []
        for key in pcfg.PORT_KEYS:
            pc = self.config_data[key]
            if pc.get("enabled"):
                active.append(f"{self.port_label(key, with_key=False)} "
                               f"({pc['ip'] or '*'}:{pc['port']})")
        text = ("Voies UDP actives : " + ", ".join(active)) if active else \
            "Aucune voie UDP active -- configurez-les a l'etape Parametres."
        if getattr(self, "ports_summary_lbl", None) is not None:
            self.ports_summary_lbl.configure(text=text)

    # ---------- Mode direct : enregistrement ----------
    def _toggle_recording(self):
        if self.recording_active:
            # L'arret TRAITE la session dans la foulee : elle part directement
            # dans l'entrepot, sans etape intermediaire (_process_session
            # commence par arreter la prise).
            self._process_session()
        else:
            self._start_recording()

    def _start_recording(self, config_ready=False):
        """Demarre une prise. config_ready=True saute la modale : la
        configuration est deja posee dans les variables de l'application.
        C'est ce que fait un redemarrage automatise ou un test ; l'interface,
        elle, passe toujours par la question."""
        # La configuration se demande ICI, au moment ou l'on demarre : elle
        # s'applique a toute la prise, et une prise sans elle ne produirait
        # que des echantillons rejetes. La modale repropose la selection
        # precedente -- repartir sur la meme voilure est donc immediat.
        if config_ready:
            if not self._selected_sails() and not self._selected_engines():
                messagebox.showwarning(
                    "Demarrer l'acquisition",
                    "Aucune configuration active : cochez au moins une voile ou un moteur.")
                return
        elif not self._ask_config(
                "Demarrer l'acquisition",
                "Configuration active pour cette prise. Elle s'applique a toute la duree et "
                "part telle quelle dans l'entrepot a l'arret ; une annotation fausse se "
                "corrige apres coup dans l'Entrepot.",
                with_duration=True, ok_text="Demarrer l'acquisition"):
            return
        any_started = self._open_udp_listeners()
        if not any_started:
            messagebox.showwarning("Enregistrement", "Aucune voie UDP active : verifiez la configuration.")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        pcfg.ensure_dirs()
        self.session_log_path = os.path.join(pcfg.LOGS_DIR, f"polar_session_{stamp}.log")
        try:
            self.session_log_fh = open(self.session_log_path, "a", encoding="utf-8")
        except OSError as e:
            messagebox.showerror("Enregistrement", f"Impossible de creer le fichier de log :\n{e}")
            self.session_log_fh = None
            self.session_log_path = None
            return

        try:
            minutes = float(self.duration_var.get())
        except (tk.TclError, ValueError):
            minutes = 0.0
        minutes = max(0.0, minutes)
        self.recording_started_at = time.time()
        lt = time.localtime(self.recording_started_at)
        self.session_epoch_base = time.mktime(
            (lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        self.recording_deadline = (self.recording_started_at + minutes * 60.0) if minutes > 0 else None

        self.recording_active = True
        with self._engine_lock:
            # Le moteur ne produit des echantillons QUE pendant la prise :
            # les voies restent ouvertes ensuite pour le tampon glissant, et
            # sans ce drapeau elles continueraient d'alimenter la passe
            # suivante avec des mesures qui ne lui appartiennent pas.
            self.engine.collecting = True
            # Guetteur d'arret automatique NEUF a chaque prise : la fenetre
            # repart vide (les premieres secondes ne declenchent rien tant
            # qu'elle n'est pas assez remplie), et un eventuel snooze herite
            # d'une fausse alerte de la prise precedente est oublie.
            self.engine.maneuver_watch = self._make_maneuver_watch(self.config_data)
        self._dismiss_maneuver_alert()
        self._apply_recording_lock()
        if self._selected_sails() or self._selected_engines():
            self._apply_live_config()
        # Le Suivi en direct remplace le contenu de l'etape Enregistrement --
        # la configuration voiles/moteurs est desormais verrouillee
        # pour toute la duree de la prise (voir _apply_recording_lock), il n'y
        # a donc plus rien a regler sur le formulaire : autant montrer
        # directement les lectures en direct, au meme endroit.
        self._show_step("acquisition")

    def _stop_recording(self):
        # Les voies UDP ne sont PLUS fermees ici : elles alimentent aussi le
        # tampon glissant, qui doit continuer de tourner entre deux prises
        # (c'est tout son interet -- voir allure_buffer.py). Leur cycle de vie
        # est desormais celui du tampon, gere par _sync_listeners().
        self.recording_active = False
        self.recording_deadline = None
        self.recording_started_at = None
        # Referme le segment de configuration reste ouvert : l'enregistrement
        # s'arrete, donc la plage couverte par cette configuration s'arrete
        # aussi. Sans cela le segment restait "en cours" indefiniment, et
        # l'appli refusait de le modifier ou de le restaurer -- tout en
        # conseillant justement d'arreter l'enregistrement pour le faire.
        with self._engine_lock:
            self.engine.collecting = False
            self.engine.journal.close_live_segment(time.time())
        # Une question "avez-vous manoeuvre ?" encore ouverte n'a plus
        # d'objet une fois la prise arretee : on la range sans y repondre.
        self._dismiss_maneuver_alert()
        if self.session_log_fh:
            try:
                self.session_log_fh.close()
            except OSError:
                pass
            self.session_log_fh = None
        self._apply_recording_lock()
        # Le Suivi en direct s'efface avec l'arret : le formulaire reprend
        # le contenu de l'etape Enregistrement (les voies UDP, elles, restent
        # ouvertes tant que le tampon glissant tourne -- _sync_listeners
        # decide).
        self._sync_listeners()

    def _open_udp_listeners(self):
        """Ouvre les voies UDP en utilisant la configuration enregistree a
        l'etape Parametres (source unique -- pas de duplication de reglages).
        Idempotent : une voie deja ouverte sur le meme couple ip/port est
        laissee telle quelle, sans quoi reappeler cette methode (a chaque
        changement de reglage, au demarrage d'une prise...) couperait puis
        rouvrirait le socket et creerait un trou dans le tampon."""
        wanted = {}
        for key in pcfg.PORT_KEYS:
            pc = self.config_data[key]
            if pc.get("enabled"):
                # Normalise comme le fait UdpListener lui-meme, sinon la
                # comparaison d'idempotence ci-dessous verrait une
                # difference la ou il n'y en a pas (espaces autour de l'IP).
                wanted[key] = ((pc["ip"] or "").strip(), pc["port"])

        for key, listener in list(self.listeners.items()):
            if wanted.get(key) != (listener.source_filter_ip, listener.port):
                listener.stop()
                del self.listeners[key]

        for key, (ip, port) in wanted.items():
            if key in self.listeners:
                continue
            listener = UdpListener(key, ip, port, self._on_udp_line,
                                    on_error=self._listener_error)
            listener.start()
            self.listeners[key] = listener
        return bool(self.listeners)

    def _close_udp_listeners(self):
        for listener in self.listeners.values():
            listener.stop()
        self.listeners = {}

    def _sync_listeners(self):
        """Aligne l'ecoute UDP sur ce qui la justifie actuellement : le
        tampon glissant (qui tourne en permanence quand il est actif) ou un
        enregistrement en cours. Point d'entree unique -- appele au
        demarrage, apres un changement de reglages, et a chaque debut/fin
        de prise."""
        if self.config_data.get("buffer_enabled") or self.recording_active:
            return self._open_udp_listeners()
        self._close_udp_listeners()
        return False

    def _listener_error(self, port_name, message):
        self.after(0, lambda: messagebox.showwarning(
            "Erreur reseau", f"{port_name} : impossible d'ouvrir le port.\n\n{message}"))

    def _on_udp_line(self, port_name, raw):
        """Appele depuis un thread d'ecoute UDP -- doit rester rapide."""
        now = time.time()
        # Tampon glissant EN PREMIER : c'est le filet de securite, il doit
        # capter la trame meme si le reste echoue. NmeaBuffer.write() avale
        # ses propres erreurs et ne leve jamais (voir allure_buffer.py).
        if self.buffer is not None and self.config_data.get("buffer_enabled"):
            self.buffer.write(now, port_name, raw)
        if self.session_log_fh:
            ts = time.strftime("%H:%M:%S", time.localtime()) + f".{int(time.time() * 1000) % 1000:03d}"
            try:
                self.session_log_fh.write(f"{ts} [{port_name}] {raw}\n")
                self.session_log_fh.flush()
            except OSError:
                pass
        with self._engine_lock:
            # MEME horodatage que celui ecrit dans le tampon ci-dessus : une
            # seconde lecture de l'horloge donnerait a la meme trame deux
            # instants differents selon qu'on la relit du tampon ou qu'on la
            # recoit en direct.
            self.engine.ingest_line(now, raw, port=port_name)
        with self._feed_lock:
            self._feed_queue.append((port_name, raw))

    # ---------- Apercu des trames (Text widget partage live/import) ----------
    def _clear_feed(self):
        with self._feed_lock:
            self._feed_queue.clear()
        self.feed_text.configure(state="normal")
        self.feed_text.delete("1.0", "end")
        self.feed_text.configure(state="disabled")
        self._feed_line_count = 0

    def _drain_feed(self):
        """Vide la file d'attente alimentee par _on_udp_line (thread UDP) dans
        le widget Text du Suivi en direct, en tronquant a FEED_MAX_LINES pour
        rester leger meme apres des heures d'enregistrement."""
        with self._feed_lock:
            batch = list(self._feed_queue)
            self._feed_queue.clear()
        if not batch:
            return
        self.feed_text.configure(state="normal")
        for port_name, raw in batch:
            self.feed_text.insert("end", f"[{port_name}] {raw}\n")
        self._feed_line_count += len(batch)
        if self._feed_line_count > self.FEED_MAX_LINES:
            excess = self._feed_line_count - self.FEED_MAX_LINES
            self.feed_text.delete("1.0", f"{excess + 1}.0")
            self._feed_line_count = self.FEED_MAX_LINES
        self.feed_text.see("end")
        self.feed_text.configure(state="disabled")

    # ---------- Mode import ----------
    def _import_log_file(self):
        """Import d'un fichier .log : un seul geste, depuis l'Entrepot --
        choisir le fichier, puis confirmer la configuration et la plage dans
        la meme boite. L'import n'est pas un "mode" de l'application (on n'y
        bascule plus, on ne peut plus l'oublier allume) mais un apport a
        l'entrepot, au meme titre qu'une prise en direct."""
        if self.recording_active:
            messagebox.showwarning("Importer", "Une acquisition est en cours : arretez-la avant "
                                                "d'importer un fichier.")
            return
        if not self._choose_import_file():
            return
        if not self._ask_config(
                "Importer un fichier .log",
                f"Fichier : {os.path.basename(self.import_path)}\n"
                f"{self.import_count} lignes, de {_format_t(self.import_t0)} a "
                f"{_format_t(self.import_t1)}.\n\n"
                "Configuration utilisee pendant ce fichier, et plage a traiter (pre-remplie sur "
                "tout le fichier). Un fichier couvrant plusieurs voilures se traite en plusieurs "
                "imports, une plage par voilure.",
                with_range=True, ok_text="Traiter -> Entrepot"):
            self.import_path = None
            self.import_t0 = self.import_t1 = None
            self.import_count = 0
            return
        self._process_session(mode="import")

    def _choose_import_file(self):
        path = filedialog.askopenfilename(
            title="Choisir un fichier .log",
            filetypes=[("Fichiers log NMEA", "*.log"), ("Tous les fichiers", "*.*")])
        if not path:
            return False
        count, t0, t1 = 0, None, None
        try:
            for t, port, raw in pe.replay_log_lines(path):
                if t0 is None:
                    t0 = t
                t1 = t
                count += 1
        except OSError as e:
            messagebox.showerror("Import", f"Impossible de lire le fichier :\n{e}")
            return False
        if count == 0:
            messagebox.showwarning("Import", "Aucune ligne au format attendu "
                                              "('HH:MM:SS.mmm [portX] $TRAME') n'a ete trouvee.")
            return False
        self.import_path = path
        self.import_t0, self.import_t1, self.import_count = t0, t1, count
        self.seg_start_var.set(_format_t(t0))
        self.seg_end_var.set(_format_t(t1))
        if getattr(self, "import_info_lbl", None) is not None:
            self.import_info_lbl.configure(
                text=f"{os.path.basename(path)} -- {count} lignes, de {_format_t(t0)} a {_format_t(t1)}")
        return True

    # ---------- Traitement (fusion dans l'entrepot cumulatif) ----------
    def _process_session(self, mode=None):
        """Traite la session courante et fusionne le resultat dans l'entrepot.

        mode="live" (defaut) : appele par le bouton d'arret de l'ecran
        Acquisition -- arrete la prise puis traite IMMEDIATEMENT, la session
        part dans l'entrepot sans etape intermediaire.
        mode="import" : appele par "Importer un fichier .log" (Entrepot) --
        construit le segment d'annotation a partir de la plage et de la
        configuration saisies dans la modale, puis rejoue le fichier.

        Le mode est passe EXPLICITEMENT et non lu dans un etat de
        l'interface : il n'existe plus de "mode courant" dans lequel
        l'application pourrait se trouver a son insu."""
        mode = mode or "live"
        self.mode.set(mode if mode == "import" else "live")
        if mode == "live":
            if self.recording_active:
                self._stop_recording()
        else:
            if not self.import_path:
                messagebox.showwarning("Traiter", "Choisissez d'abord un fichier a importer.")
                return
            sails, engines = self._selected_sails(), self._selected_engines()
            if not sails and not engines:
                messagebox.showwarning(
                    "Traiter", "Cochez d'abord la configuration (voiles et/ou moteurs) utilisee "
                               "pendant ce fichier -- sans elle, les echantillons ne peuvent etre "
                               "rattaches a aucune polaire.")
                return
            try:
                seg_start = _parse_t(self.seg_start_var.get())
                seg_end = _parse_t(self.seg_end_var.get())
            except ValueError as e:
                messagebox.showerror("Traiter", f"Plage horaire invalide :\n{e}")
                return
            if seg_start is None:
                seg_start = self.import_t0
            with self._engine_lock:
                # Un import COLLECTE par definition : il traite une plage
                # choisie, du debut a la fin. Le drapeau du moteur, lui, est
                # a False entre deux prises directes (voir _new_session_engine)
                # -- sans ce reveil explicite, le rejeu ne produirait aucun
                # echantillon et l'import semblerait ne rien faire.
                self.engine.collecting = True
                # Repart d'un journal vierge puis pose l'UNIQUE segment de ce
                # traitement -- un fichier couvrant plusieurs voilures se
                # traite en plusieurs imports, une plage par voilure.
                self.engine.journal.load_list([])
                try:
                    self.engine.add_manual_segment(seg_start, seg_end, sails, engines)
                except ValueError as e:
                    messagebox.showerror("Traiter", f"Plage horaire invalide :\n{e}")
                    return
            for t, port, raw in pe.replay_log_lines(self.import_path):
                with self._engine_lock:
                    self.engine.ingest_line(t, raw, port=port)
            with self._engine_lock:
                self.engine.tick(self.import_t1 if self.import_t1 is not None else 0.0)

        raw_path = self.session_log_path if self.mode.get() == "live" else self.import_path
        mode_now = self.mode.get()

        with self._engine_lock:
            n_new = len(self.engine.store)
            stats = dict(self.engine.stats)
            has_segments_now = bool(self.engine.journal.all_segments())
            if n_new > 0:
                session_id = f"s{int(time.time() * 1000)}"
                self.engine.store.set_session_id(session_id)
                self.persistent_store.extend(self.engine.store)

        if n_new == 0:
            # Diagnostic cible : "rien recu", "recu mais aucune config voiles/moteurs
            # active", "recu + config active mais jamais stable assez longtemps" sont
            # trois causes tres differentes -- on les distingue explicitement plutot
            # que d'afficher un message generique.
            if stats["lines"] == 0:
                reason = ("Aucune trame NMEA n'a ete recue du tout pendant cette session -- "
                           "verifiez la reception reseau (voies UDP actives a l'etape "
                           "Parametres, cablage/reseau des instruments).")
            elif not has_segments_now:
                reason = ("Aucune configuration voiles/moteurs n'a ete appliquee pendant la "
                           "session (aucune case cochee avant ou pendant l'enregistrement) -- "
                           "meme si les trames NMEA etaient bien recues, aucun echantillon ne "
                           "peut etre attribue a une configuration inconnue.")
            elif stats["samples_rejected_no_config"] > 0:
                reason = (f"{stats['samples_rejected_no_config']} echantillon(s) lisse(s) ont bien "
                           "ete calcules a partir des trames recues, mais a des instants ou aucune "
                           "configuration voiles/moteurs n'etait encore active (typiquement : les "
                           "toutes premieres secondes, avant la premiere case cochee).")
            else:
                min_span = self.config_data["smoothing_window_s"] * self.config_data["min_fill_frac"]
                reason = (f"Des trames NMEA ont ete recues et une configuration etait active, mais "
                           f"aucune fenetre stable d'au moins {min_span:.0f}s n'a ete trouvee "
                           "(vitesse/vent trop instables -- virements, accelerations -- ou session "
                           f"trop courte : {self.config_data['smoothing_window_s']:.0f}s minimum "
                           "requises avant le tout premier echantillon possible). Si vos sessions de "
                           "test sont courtes, reduisez la fenetre de lissage a l'etape Parametres.")
            detail = (f"Lignes NMEA recues : {stats['lines']}  |  Checksums invalides : "
                       f"{stats['bad_checksum']}  |  Non reconnues : {stats['unparsed']}  |  "
                       f"Rejetees faute de configuration active : {stats['samples_rejected_no_config']}")
            raw_note = ""
            if raw_path and os.path.exists(raw_path):
                raw_note = (f"\n\nLe fichier brut de cette session a ete CONSERVE (rien n'est perdu) "
                             f":\n{raw_path}\n\nVous pouvez le reimporter (Entrepot, "
                             "'Importer un fichier .log...') apres avoir ajuste les reglages ou corrige la "
                             "configuration cochee, sans avoir a re-enregistrer en mer.")
            messagebox.showwarning(
                "Aucun echantillon produit",
                "Cette session n'a produit aucun echantillon exploitable pour la polaire.\n\n"
                f"{reason}\n\n{detail}{raw_note}")
            # Pas de proposition de suppression ici : le fichier brut est la seule
            # facon de recuperer cette session apres correction des reglages.
            self.session_log_path = None
            self.import_path = None
            self.import_t0 = self.import_t1 = None
            self.import_count = 0
            self.import_info_lbl.configure(text="Aucun fichier selectionne.")
            self._new_session_engine()
            self._clear_feed()
            return

        pcfg.save_store_data(self.persistent_store.to_list())

        if mode_now == "live":
            label = f"Direct -- {time.strftime('%Y-%m-%d %H:%M')}"
        else:
            label = f"Import {os.path.basename(raw_path) if raw_path else '?'} -- {time.strftime('%Y-%m-%d %H:%M')}"
        # --- Tri automatique a l'entree, si l'utilisateur l'a demande ---
        # La passe est entreposee dans tous les cas : on ne fait que
        # pre-positionner sa case "Incluse". Rien n'est perdu, rien n'est
        # cache, et le compte rendu ci-dessous dit ce qui a ete decide et
        # pourquoi -- une exclusion silencieuse serait pire que pas
        # d'exclusion du tout.
        included, auto_msg = True, ""
        if self.config_data.get("auto_exclude_low_confidence", False):
            seuil = int(self.config_data.get("auto_exclude_threshold", 40))
            cinfo = self.persistent_store.session_confidence(
                session_id, twa_bin_deg=self.config_data["twa_bin_deg"],
                tws_bin_kn=self.config_data["tws_bin_kn"],
                symmetric=self.config_data["symmetric_port_starboard"])
            if cinfo["score"] < seuil:
                included = False
                auto_msg = (f"\n\nCette passe N'EST PAS INCLUSE dans le calcul : sa confiance "
                            f"est de {cinfo['score']}/100 ({cinfo['level']} {cinfo['word']}), "
                            f"sous le seuil de {seuil} que vous avez regle.\n"
                            f"Raison : {cinfo['why']}.\n"
                            "Elle est bien entreposee et rien n'est perdu -- recochez sa case "
                            "Incluse dans l'Entrepot pour la faire compter.")
            else:
                auto_msg = (f"\n\nConfiance de la passe : {cinfo['score']}/100 "
                            f"({cinfo['level']} {cinfo['word']}) -- au-dessus du seuil de "
                            f"{seuil}, elle est incluse dans le calcul.")
        self.sessions_index.append({
            "id": session_id, "label": label, "created_at": time.time(),
            "mode": "direct" if mode_now == "live" else "import",
            "source": raw_path or "", "sample_count": n_new, "included": included,
            "app_version": pcfg.APP_VERSION,
        })
        pcfg.save_sessions_index(self.sessions_index)

        # Sort du fichier brut de la session -- PLUS AUCUNE QUESTION posee ici.
        # Le journal d'une prise directe fait doublon avec le tampon glissant,
        # qui a capte exactement les memes trames et les garde pendant toute
        # la duree conservee : le supprimer ne perd donc rien, et laisser des
        # polar_session_*.log s'accumuler a cote du programme n'avait aucun
        # interet. Deux garde-fous : un fichier IMPORTE appartient a
        # l'utilisateur et n'est jamais touche, et si le tampon est desactive
        # le journal est conserve (il redevient alors la seule trace brute).
        # A noter : AWA/AWS restent de toute facon reconstituables a partir de
        # TWA/TWS/STW conserves dans l'entrepot (voir _show_archive_detail).
        raw_msg = ""
        if mode_now == "live" and raw_path and os.path.exists(raw_path):
            if self.config_data.get("buffer_enabled", True):
                try:
                    os.remove(raw_path)
                    raw_msg = ("\n\nLe journal brut de la prise a ete efface : le tampon glissant "
                               "en conserve les memes trames.")
                except OSError as e:
                    raw_msg = f"\n\n(Le journal brut n'a pas pu etre efface : {e})"
            else:
                raw_msg = ("\n\nLe tampon glissant etant desactive, le journal brut de la prise a "
                           f"ete conserve :\n{raw_path}")
        messagebox.showinfo(
            "Traitement termine",
            f"Session traitee : {n_new} echantillon(s) ajoute(s) a l'entrepot cumulatif "
            f"(total : {len(self.persistent_store)}).\n\n"
            f"Lignes lues : {stats['lines']}  |  Checksums invalides : {stats['bad_checksum']}  |  "
            f"Non reconnues : {stats['unparsed']}  |  Rejetees (pas de config) : "
            f"{stats['samples_rejected_no_config']}" + auto_msg + raw_msg)

        # Repart sur une session vierge
        self.session_log_path = None
        self.import_path = None
        self.import_t0 = self.import_t1 = None
        self.import_count = 0
        self.import_info_lbl.configure(text="Aucun fichier selectionne.")
        self._new_session_engine()
        self._clear_feed()
        self._refresh_config_choices()
        self._refresh_sessions_list()
        if mode_now == "live":
            # Fin d'une prise directe : on RESTE sur l'ecran d'acquisition,
            # qui continue d'afficher les mesures -- pret a repartir.
            self._show_step("acquisition")
        else:
            # Import : direction l'entrepot, pour verifier la passe qui vient
            # d'y arriver (voiles utilisees, nombre d'echantillons...).
            self._show_step("entrepot")

    # ---------------------------------------------------------------
    # Etape 1 : Parametres
    # ---------------------------------------------------------------
    # Categories de la page Parametres -- (cle, libelle), dans l'ordre du
    # bandeau, place a GAUCHE de la page :
    # chaque bouton affiche sa categorie, tous les widgets de toutes les
    # categories existent en permanence (seule la visibilite change), et
    # "Enregistrer les parametres" applique donc TOUT d'un coup, pas
    # seulement la categorie affichee.
    # Regroupees par affinite plutot qu'une page par reglage : l'acquisition
    # (ce qui ENTRE -- voies UDP + tampon glissant), le traitement (ce qui
    # se CALCULE -- lissage + table polaire), le materiel du bord, et les
    # sauvegardes.
    # Ordre de LECTURE voulu : d'abord ce qui decrit le bateau et ce qui
    # entre (materiel, sources), puis ce que l'application en fait
    # (manoeuvres, traitement), enfin ce qui entoure (affichage,
    # sauvegardes). Chaque categorie tient en un mot-cle.
    SETTINGS_CATEGORIES = [
        ("materiel", "Voiles & moteurs"),
        ("sources", "Sources & tampon"),
        ("manoeuvre", "Manoeuvres"),
        ("traitement", "Lissage & table"),
        ("affichage", "Affichage & fenetre"),
        ("sauvegarde", "Sauvegardes"),
    ]

    def _build_step_parametres(self, parent):
        """Page Parametres (ouverte par la roue dentee) : bandeau de
        categories a gauche, contenu de la categorie active a droite, barre
        d'action fixe en bas (Enregistrer + messages d'etat) commune a
        toutes les categories."""
        # Barre du bas d'abord : pack(side="bottom") reserve sa bande avant
        # que le corps ne prenne tout le reste.
        bottom = tk.Frame(parent, bg=BG_PANEL)
        bottom.pack(side="bottom", fill="x")
        tk.Frame(parent, bg=COLOR_BORDER, height=1).pack(side="bottom", fill="x")
        inner_b = tk.Frame(bottom, bg=BG_PANEL)
        inner_b.pack(fill="x", padx=14, pady=8)
        self.save_params_btn = tk.Button(inner_b, text="Enregistrer les parametres",
                                          command=self._save_params,
                                          bg=COLOR_STARBOARD, fg="#ffffff", font=FONT_LABEL_BOLD,
                                          padx=14, pady=4, cursor="hand2")
        self.save_params_btn.pack(side="right")
        self.params_pending_row = tk.Frame(inner_b, bg=BG_PANEL)
        self.params_pending_row.pack(side="left", fill="x", expand=True)
        # Le message prend la place REELLEMENT laissee par les boutons et
        # passe a la ligne a cette largeur : 680 px fixes ecrasaient le
        # bouton 'Aller a l'etape Acquisition...' contre Enregistrer a la
        # largeur minimale (ce bouton est servi AVANT lui, voir _update_once).
        self.params_pending_lbl = tk.Label(
            self.params_pending_row, text="", bg=BG_PANEL, fg=COLOR_PORT,
            font=("Segoe UI", 8, "bold"), wraplength=self._px(400))
        self.params_pending_lbl.pack(side="left", fill="x", expand=True)
        follow_width(self.params_pending_lbl)
        # Raccourci affiche uniquement quand pertinent (voir _update_once) :
        # va directement traiter/vider la session en attente.
        self.goto_session_btn = tk.Button(
            self.params_pending_row, text="Aller a l'etape Acquisition...",
            command=lambda: self._show_step("acquisition"))

        # Bandeau de categories, a gauche -- empaquete avant le corps pour
        # reserver sa colonne (meme regle du packer que pour la roue dentee).
        # Sa largeur suit le libelle le plus long EN GRAS (celui de la
        # categorie active), marges comprises : 200 px fixes suffisaient a
        # 100 %, mais a 150 % le texte touchait le bord, et au-dela il aurait
        # ete coupe (pack_propagate(False) fige la largeur donnee ici).
        bold = tkfont.Font(root=self, font=FONT_LABEL_BOLD)
        cat_w = (max(bold.measure(lbl) for _k, lbl in self.SETTINGS_CATEGORIES)
                 + 2 * 14 + self._px(16))
        catbar = tk.Frame(parent, bg=BG_NAV, width=max(200, cat_w))
        catbar.pack(side="left", fill="y")
        catbar.pack_propagate(False)
        tk.Label(catbar, text="PARAMETRES", bg=BG_NAV, fg=FG_LABEL_DIM,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(14, 6))
        # Filet vertical entre le bandeau et le contenu.
        tk.Frame(parent, bg=COLOR_BORDER, width=1).pack(side="left", fill="y")

        body = tk.Frame(parent, bg=BG_APP)
        body.pack(side="left", fill="both", expand=True)

        self._settings_frames = {}
        self._settings_buttons = {}
        for key, label in self.SETTINGS_CATEGORIES:
            btn = tk.Button(catbar, text=label, relief="flat", bd=0, anchor="w",
                             padx=14, pady=8, font=FONT_LABEL, bg=BG_NAV, fg=FG_LABEL,
                             activebackground=BG_NAV_ACTIVE, activeforeground=FG_ON_ACCENT,
                             cursor="hand2",
                             command=lambda k=key: self._show_settings_category(k))
            btn.pack(fill="x")
            self._settings_buttons[key] = btn
            self._settings_frames[key] = tk.Frame(body, bg=BG_APP)

        self._build_settings_materiel(self._settings_frames["materiel"])
        self._build_settings_sources(self._settings_frames["sources"])
        self._build_settings_traitement(self._settings_frames["traitement"])
        self._build_settings_manoeuvre(self._settings_frames["manoeuvre"])
        self._build_settings_affichage(self._settings_frames["affichage"])
        self._build_settings_sauvegarde(self._settings_frames["sauvegarde"])

        self._current_settings_category = None
        self._show_settings_category(self.SETTINGS_CATEGORIES[0][0])

    def _show_settings_category(self, key):
        if key not in self._settings_frames:
            return
        for k, frame in self._settings_frames.items():
            frame.pack_forget()
        self._settings_frames[key].pack(fill="both", expand=True)
        for k, btn in self._settings_buttons.items():
            active = (k == key)
            btn.configure(bg=BG_NAV_ACTIVE if active else BG_NAV,
                          fg=FG_ON_ACCENT if active else FG_LABEL,
                          font=FONT_LABEL_BOLD if active else FONT_LABEL)
        self._current_settings_category = key

    # ---------- Categorie : voiles & moteurs ----------
    def _build_settings_materiel(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)
        f_lists = section(inner, "Voiles et moteurs disponibles")
        note(f_lists, "Ces listes definissent les cases a cocher proposees au demarrage "
                      "d'une acquisition et a l'import d'un .log. Chaque code moteur donne "
                      "aussi, d'office, sa propulsion 'Voiles + <code>' a l'etape Polaires "
                      "(grisee tant qu'aucune passe ne l'a mesuree). "
                       "Modifiable librement -- les valeurs par defaut correspondent au greement "
                       "habituel (J0, J1A, MSA, J1F, MSF / 1ME, 2ME, 1SB, 2SB).")
        lists_row = tk.Frame(f_lists, bg=BG_APP)
        lists_row.pack(fill="x", padx=6, pady=(0, 8))

        # La grille TimeZero suit la liste des voiles a chaque + / -.
        self.sail_list_box, self.sail_list_entry = self._build_editable_list(
            lists_row, "Voiles", self.config_data["sail_list"],
            on_change=self._refresh_timezero_grid)
        self.engine_list_box, self.engine_list_entry = self._build_editable_list(
            lists_row, "Moteurs", self.config_data["engine_list"])

        self._build_section_timezero(inner)

    TZ_MAP_NONE = "(non exporte)"

    def _build_section_timezero(self, inner):
        """Correspondance des voiles du bord vers le vocabulaire TimeZero.
        Elle ne peut pas se deviner -- "MSF" ne veut rien dire pour
        TimeZero, et seul l'equipage sait s'il s'agit d'une grand-voile a
        deux ris ou d'un solent de gros temps."""
        f_tz = section(inner, "Export TimeZero : correspondance des voiles")
        note(f_tz, "TimeZero ne connait pas vos codes de voile : il raisonne en grand-voile "
                    "(GV, 1 a 3 ris, ou affalee) et en voile d'avant classee par force et par "
                    "allure. Dites ici ce qu'est chacune des votres, et l'export TimeZero "
                    "produira non seulement la polaire de vitesse, mais aussi le fichier de "
                    "VOILURE : quelle toile porter pour chaque cap et chaque vent, calcule "
                    "d'apres ce que votre bateau a reellement fait. Une voile laissee sans "
                    "correspondance est simplement absente du fichier de voilure -- la "
                    "polaire de vitesse, elle, n'en depend pas.", wraplength="auto")
        self.tz_map_vars = {}
        # Choix faits a l'ecran et pas encore enregistres, par code : une
        # voile retiree puis remise retrouve sa correspondance.
        self._tz_memory = {}
        self.tz_grid = tk.Frame(f_tz, bg=BG_APP)
        self.tz_grid.pack(fill="x", padx=6, pady=(0, 8))
        self._refresh_timezero_grid()

    def _refresh_timezero_grid(self):
        """(Re)construit la grille de correspondance TimeZero d'apres la
        liste des voiles TELLE QU'ELLE EST A L'ECRAN. Batie une seule fois au
        demarrage, elle ignorait une voile ajoutee (impossible de lui donner
        une correspondance sans relancer l'application) et gardait une voile
        retiree. Les choix deja faits sont repris."""
        grid = getattr(self, "tz_grid", None)
        if grid is None:
            return
        box = getattr(self, "sail_list_box", None)
        codes = (list(box.get(0, "end")) if box is not None
                 else list(self.config_data["sail_list"]))
        self._tz_memory.update({code: var.get() for code, var in self.tz_map_vars.items()})
        saved = dict(self.config_data.get("timezero_sail_map") or {})
        choices = [self.TZ_MAP_NONE] + list(pe.TZ_MAINSAILS) + list(pe.TZ_FRONTSAILS)
        for child in grid.winfo_children():
            child.destroy()
        self.tz_map_vars = {}
        for i, code in enumerate(codes):
            r, c = i // 2, (i % 2) * 2
            # Sans largeur fixe en caracteres : la colonne de la grille
            # s'aligne d'elle-meme sur le code le plus long, qui n'est plus
            # coupe au-dela de six lettres.
            tk.Label(grid, text=f"{code} :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, anchor="e").grid(
                row=r, column=c, sticky="e", padx=(0, 4), pady=2)
            if code in self._tz_memory:
                value = self._tz_memory[code]
            else:
                value = saved.get(code) or self.TZ_MAP_NONE
            var = tk.StringVar(value=value)
            menu = ttk.OptionMenu(grid, var, var.get(), *choices)
            menu.configure(width=18)
            menu.grid(row=r, column=c + 1, sticky="w", padx=(0, 20), pady=2)
            self.tz_map_vars[code] = var
        # Reconstruite apres une bascule en sombre, la grille naitrait avec
        # des menus aux couleurs systeme claires.
        self._theme_new_window(grid)

    # ---------- Categorie : sources (type d'acquisition, voies, tampon) ----------
    def _build_settings_sources(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)
        self._build_section_acq_type(inner)
        self._build_section_ports(inner)
        self._build_section_tampon(inner)

    # Modes d'acquisition disponibles. La liste est volontairement une
    # STRUCTURE et non un booleen : l'ecoute UDP est le seul mode
    # aujourd'hui, mais une liaison serie ou un fichier suivi en continu
    # viendront s'y ajouter sans avoir a redessiner cette page.
    ACQUISITION_TYPES = (
        ("udp", "Ecoute reseau UDP", "Les trames arrivent par le reseau du bord, sur les voies "
                                       "reglees ci-dessous. C'est le montage habituel derriere une "
                                       "passerelle NMEA."),
    )

    def _build_section_acq_type(self, inner):
        f_type = section(inner, "Type d'acquisition")
        self.acq_type_var = tk.StringVar(value=self.config_data.get("acquisition_type", "udp"))
        for value, label, desc in self.ACQUISITION_TYPES:
            row = tk.Frame(f_type, bg=BG_APP)
            row.pack(fill="x", padx=6, pady=(2, 0))
            tk.Radiobutton(row, text=label, variable=self.acq_type_var, value=value,
                           bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                           activebackground=BG_APP, activeforeground=FG_LABEL,
                           font=FONT_LABEL_BOLD).pack(side="left")
            # Retour a la ligne a la largeur reelle, comme les notes : 620 px
            # fixes figeaient ce texte dans une colonne etroite.
            desc_lbl = tk.Label(f_type, text=desc, bg=BG_APP, fg=FG_LABEL_DIM,
                                font=("Segoe UI", 8))
            desc_lbl.pack(fill="x", padx=28, pady=(0, 4))
            follow_width(desc_lbl)
        note(f_type, "Traiter un fichier .log deja enregistre n'est pas un mode d'acquisition "
                      "mais un apport a l'entrepot : cela se fait depuis l'etape Entrepot "
                      "(bouton 'Importer un fichier .log').", pady=(0, 8))

    def _build_section_ports(self, inner):
        f_ports = section(inner, "Voies d'ecoute UDP (mode direct, 4 disponibles)")
        note(f_ports, "Les voies actives sont toutes ecoutees simultanement. Donnez a chacune "
                       "le nom de l'equipement qui s'y trouve (Station nav, Meteo France, GPS "
                       "passerelle...) : c'est ce nom qui s'affichera partout ailleurs, un "
                       "numero de port UDP ne se retenant pas. L'ordre de priorite ne sert plus "
                       "que de REPLI : le choix des sources se fait juste en dessous, grandeur "
                       "par grandeur.", wraplength="auto")
        self.params_port_widgets = {}
        self.priority_vars = {}
        ports_grid = tk.Frame(f_ports, bg=BG_APP)
        ports_grid.pack(fill="x", padx=6, pady=(0, 8))
        hdr = {"bg": BG_APP, "fg": FG_LABEL, "font": FONT_LABEL_BOLD}
        tk.Label(ports_grid, text="Actif", **hdr).grid(row=0, column=0, padx=4)
        tk.Label(ports_grid, text="Voie", **hdr).grid(row=0, column=1, padx=4)
        tk.Label(ports_grid, text="Nom de l'equipement", **hdr).grid(row=0, column=2, padx=4)
        tk.Label(ports_grid, text="IP source (filtre)", **hdr).grid(row=0, column=3, padx=4)
        tk.Label(ports_grid, text="Port", **hdr).grid(row=0, column=4, padx=4)
        priority_order = self.config_data.get("priority") or list(pcfg.PORT_KEYS)
        for i, key in enumerate(pcfg.PORT_KEYS):
            pc = self.config_data[key]
            r = i + 1
            enabled_var = tk.BooleanVar(value=pc["enabled"])
            tk.Checkbutton(ports_grid, variable=enabled_var, bg=BG_APP, selectcolor=BG_PANEL).grid(
                row=r, column=0, pady=2)
            tk.Label(ports_grid, text=key, bg=BG_APP, fg=FG_LABEL_DIM,
                     font=FONT_MONO).grid(row=r, column=1, sticky="w", padx=6)
            name_var = tk.StringVar(value=pc.get("name", ""))
            name_entry = tk.Entry(ports_grid, width=22, textvariable=name_var)
            name_entry.grid(row=r, column=2, padx=4, pady=2)
            # Les menus de source suivent la frappe : renommer une voie doit
            # se voir tout de suite la ou on la choisit, sans avoir a
            # enregistrer pour verifier qu'on a bien nomme la bonne.
            name_entry.bind("<KeyRelease>", lambda _e: self._refresh_source_menus())
            ip_var = tk.StringVar(value=pc["ip"])
            tk.Entry(ports_grid, width=15, textvariable=ip_var).grid(row=r, column=3, padx=4, pady=2)
            port_var = tk.StringVar(value=str(pc["port"]))
            tk.Entry(ports_grid, width=8, textvariable=port_var).grid(row=r, column=4, padx=4, pady=2)
            # L'ordre de preference n'est plus un attribut de la VOIE : il se
            # regle mesure par mesure, juste en dessous. La variable est
            # conservee (l'ancien ordre reste dans le fichier de reglages et
            # sert encore de repli general) mais n'a plus de widget.
            rank = (priority_order.index(key) + 1) if key in priority_order else r
            self.params_port_widgets[key] = {"enabled": enabled_var, "ip": ip_var,
                                              "port": port_var, "name": name_var}
            self.priority_vars[key] = tk.IntVar(value=rank)

        self._build_section_scan(inner)

    def port_label(self, key, with_key=True, live=False):
        """Nom lisible d'une voie : l'equipement quand il a ete nomme, la
        cle technique sinon. Utilise partout ou une voie est citee a
        l'ecran -- personne ne se souvient de ce qu'il y a sur 'port3'.

        live=True lit le nom EN COURS DE SAISIE plutot que celui enregistre :
        reserve a la page Acquisition elle-meme, ou les menus de source
        doivent suivre la frappe. Ailleurs, c'est le nom enregistre qui fait
        foi -- un nom a moitie tape n'a rien a faire dans le suivi en direct."""
        name = ""
        if live:
            w = getattr(self, "params_port_widgets", {}).get(key)
            if w is not None:
                try:
                    name = w["name"].get().strip()
                except tk.TclError:
                    name = ""
        if not name:
            name = (self.config_data.get(key) or {}).get("name", "").strip()
        if not name:
            return key
        return f"{name} ({key})" if with_key else name

    def _build_section_scan(self, inner):
        """Inventaire de ce que portent REELLEMENT les voies, et choix de la
        source de chaque grandeur. Remplace le classement par priorite comme
        reglage principal : sur une passerelle reelle, deux voies portent le
        meme type de trame avec des contenus differents, et c'est un choix
        qu'on veut exprimer, pas un ordre de preference."""
        f_scan = section(inner, "Ce que portent les voies (analyse)")
        note(f_scan, "L'analyse relit le TAMPON GLISSANT -- rien a brancher, rien a attendre : "
                      "tout ce qui est passe par les voies ces dernieres heures est deja "
                      "enregistre. Pour chaque voie et chaque type de trame, elle donne la "
                      "cadence, l'unite reellement employee et un exemple de valeur. C'est ainsi "
                      "qu'on decouvre qu'une girouette emet en noeuds et l'autre en metres par "
                      "seconde, ou qu'une voie rafraichit deux fois plus vite que sa voisine.",
             wraplength="auto")
        scan_row = tk.Frame(f_scan, bg=BG_APP)
        scan_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Button(scan_row, text="Analyser les voies", command=self._scan_sources,
                  font=FONT_LABEL_BOLD).pack(side="left")
        tk.Label(scan_row, text="sur les dernieres", bg=BG_APP, fg=FG_LABEL).pack(side="left", padx=(12, 4))
        self.scan_minutes_var = tk.IntVar(value=30)
        tk.Spinbox(scan_row, from_=1, to=2880, increment=10, width=6,
                   textvariable=self.scan_minutes_var).pack(side="left")
        tk.Label(scan_row, text="minutes de tampon", bg=BG_APP, fg=FG_LABEL).pack(side="left", padx=(4, 0))
        # Message d'etat sur SA ligne, sous les commandes : en bout de
        # rangee, il etait coupe des deux cotes des que la fenetre se
        # retrecissait -- alors qu'il confirme l'action (voie designee...).
        self.scan_status_lbl = tk.Label(f_scan, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                         font=("Segoe UI", 8))
        self.scan_status_lbl.pack(fill="x", padx=6, pady=(0, 2))
        follow_width(self.scan_status_lbl)

        scan_tree_row = tk.Frame(f_scan, bg=BG_APP)
        scan_tree_row.pack(fill="x", padx=6, pady=(2, 0))
        scols = ("voie", "trame", "role", "cadence", "unite", "valeur", "source")
        self.scan_tree = ttk.Treeview(scan_tree_row, columns=scols, show="headings",
                                       height=8, selectmode="browse")
        # Tri par clic sur l'en-tete, comme le tableau des passes. Utile ici
        # pour rassembler les lignes d'un meme type de trame -- c'est ainsi
        # qu'on VOIT que deux voies portent le meme vent, et qu'on les compare.
        self._scan_sort = ("voie", False)
        for c, label in self.SCAN_HEADINGS:
            anchor = "w" if c in ("voie", "role", "valeur") else "center"
            self.scan_tree.heading(c, text=label, command=lambda k=c: self._sort_scan_by(k))
            self.scan_tree.column(c, anchor=anchor)
        # Largeurs calees sur le contenu, voir _fit_scan_columns -- recalees
        # aussi au retour sur la page, ou le tableau a pu changer de largeur.
        self._fit_scan_columns()
        self.scan_tree.bind("<Map>", lambda _e: self._fit_scan_columns(), add="+")
        # Ascenseurs vertical ET horizontal, en grille : sur une fenetre
        # etroite, le tableau defile au lieu de comprimer ses colonnes
        # jusqu'a en couper le texte. La colonne du tableau est seule
        # extensible : les ascenseurs gardent toujours leur place.
        scan_vsb = ttk.Scrollbar(scan_tree_row, orient="vertical", command=self.scan_tree.yview)
        scan_hsb = ttk.Scrollbar(scan_tree_row, orient="horizontal", command=self.scan_tree.xview)
        self.scan_tree.configure(yscrollcommand=scan_vsb.set, xscrollcommand=scan_hsb.set)
        self.scan_tree.grid(row=0, column=0, sticky="nsew")
        scan_vsb.grid(row=0, column=1, sticky="ns")
        scan_hsb.grid(row=1, column=0, sticky="ew")
        scan_tree_row.grid_columnconfigure(0, weight=1)
        self._scan_rows = []
        # Le tableau n'est pas qu'un rapport : un double-clic sur une ligne
        # exploitable DESIGNE cette voie comme source de la grandeur. Voir ce
        # que porte une voie et le choisir sont le meme geste -- sinon il
        # faut retenir la ligne, descendre, retrouver la voie dans un menu.
        self.scan_tree.bind("<Double-1>", lambda _e: self._use_scan_row_as_source())
        # Detail COMPLET de la ligne selectionnee, sous le tableau : le role
        # d'une station meteo et ce qu'elle alimente ne tiennent dans aucune
        # colonne raisonnable -- ici, le texte passe a la ligne.
        self.scan_detail_lbl = tk.Label(f_scan, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                         font=("Segoe UI", 8))
        self.scan_detail_lbl.pack(fill="x", padx=6, pady=(2, 2))
        follow_width(self.scan_detail_lbl)
        self.scan_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_scan_detail(), add="+")
        self._show_scan_detail()
        act_row = tk.Frame(f_scan, bg=BG_APP)
        act_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Button(act_row, text="Utiliser cette voie comme source",
                  command=self._use_scan_row_as_source).pack(side="left")
        tk.Label(act_row, text="(ou double-clic sur la ligne ; l'ancienne source devient le repli)",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(side="left", padx=(8, 0))

        f_src = section(inner, "Source de chaque grandeur")
        note(f_src, "Pour chacune des trois grandeurs qui font une polaire, designez la voie qui "
                     "fait foi, et celle qui prend le relais si la premiere se tait. L'ordre est "
                     "propre a CHAQUE MESURE : une passerelle peut tres bien porter la meilleure "
                     "girouette et le plus mauvais loch, classer les voies en bloc n'aurait alors "
                     "aucun sens. 'Automatique' laisse l'application prendre ce qui arrive. Ce "
                     "choix ne s'applique qu'aux prises A VENIR -- pour refaire une passe deja "
                     "entreposee avec la bonne source, voir 'Recalculer depuis le tampon' dans "
                     "l'Entrepot.", wraplength="auto")
        self.source_vars = {}
        src_grid = tk.Frame(f_src, bg=BG_APP)
        src_grid.pack(fill="x", padx=6, pady=(0, 4))
        hdr2 = {"bg": BG_APP, "fg": FG_LABEL, "font": FONT_LABEL_BOLD}
        tk.Label(src_grid, text="Grandeur", **hdr2).grid(row=0, column=0, sticky="w", padx=(0, 12))
        tk.Label(src_grid, text="Source principale", **hdr2).grid(row=0, column=1, sticky="w", padx=6)
        tk.Label(src_grid, text="Si elle se tait", **hdr2).grid(row=0, column=2, sticky="w", padx=6)
        chosen = self.config_data.get("sources") or {}
        for i, (styp, label) in enumerate(pe.MEASUREMENT_SOURCES):
            chain = chosen.get(styp) or []
            if isinstance(chain, str):
                chain = [chain]
            tk.Label(src_grid, text=f"{label} ({styp})", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL).grid(row=i + 1, column=0, sticky="w", pady=3)
            entry = {}
            for rank, col in ((0, 1), (1, 2)):
                var = tk.StringVar()
                menu = ttk.OptionMenu(src_grid, var, "", "")
                menu.configure(width=24)
                menu.grid(row=i + 1, column=col, sticky="w", padx=6, pady=3)
                # "key" est la valeur qui FAIT FOI (cle de voie, ou "" pour
                # Automatique/aucun) ; "var" n'en est que l'affichage. Tant
                # que la selection etait retenue par LIBELLE, renommer une
                # voie la faisait disparaitre : le libelle enregistre dans
                # le menu ne correspondait plus a aucune voie connue, et
                # toutes les sources retombaient silencieusement sur
                # "Automatique" -- alors que le tableau d'inventaire, lui,
                # continuait d'afficher "principale".
                entry[rank] = {"var": var, "menu": menu,
                               "key": chain[rank] if rank < len(chain) else ""}
            self.source_vars[styp] = entry
        self._refresh_source_menus()

        fb_row = tk.Frame(f_src, bg=BG_APP)
        fb_row.pack(fill="x", padx=6, pady=(0, 8))
        self.source_fallback_var = tk.BooleanVar(
            value=self.config_data.get("source_fallback", True))
        tk.Checkbutton(fb_row, text="Basculer sur une autre voie si la source choisie se tait",
                       variable=self.source_fallback_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL).pack(side="left")
        tk.Label(fb_row, text=f"(apres {pe.PolarEngine.PRIORITY_STALE_S:.0f} s de silence)",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

    # Libelles des choix "pas de source imposee" / "pas de repli".
    AUTO_SOURCE = "Automatique"
    NO_SOURCE = "(aucun)"

    def _refresh_source_menus(self):
        """Reecrit les libelles des menus de source pour qu'ils portent le NOM
        des equipements. Appele a la construction et apres chaque renommage,
        sinon les menus continueraient d'afficher 'port2' alors que la voie
        s'appelle desormais 'Station nav'.

        La selection est preservee parce qu'elle est retenue par CLE
        (slot["key"]) et non par libelle : c'est le libelle qui suit la
        cle, jamais l'inverse. Le tableau d'inventaire est re-rendu dans la
        foulee, pour que sa colonne Source ne puisse pas raconter autre
        chose que ces menus."""
        if not hasattr(self, "source_vars"):
            return
        self._source_label_to_key = {self.AUTO_SOURCE: "", self.NO_SOURCE: ""}
        labels_main = [self.AUTO_SOURCE]
        labels_fb = [self.NO_SOURCE]
        for key in pcfg.PORT_KEYS:
            lbl = self.port_label(key, live=True)
            self._source_label_to_key[lbl] = key
            labels_main.append(lbl)
            labels_fb.append(lbl)
        for styp, entry in self.source_vars.items():
            for rank, slot in entry.items():
                key = slot.get("key") or ""
                empty = self.AUTO_SOURCE if rank == 0 else self.NO_SOURCE
                new_label = self.port_label(key, live=True) if key in pcfg.PORT_KEYS else empty
                menu = slot["menu"]["menu"]
                menu.delete(0, "end")
                for lbl in (labels_main if rank == 0 else labels_fb):
                    menu.add_command(
                        label=lbl,
                        command=lambda v=lbl, s=slot: self._set_source_slot(s, v))
                slot["var"].set(new_label)
        if hasattr(self, "scan_tree") and getattr(self, "_scan_rows", None):
            self._render_scan_rows()

    def _set_source_slot(self, slot, label):
        """Choix fait dans un menu de source : la CLE est ce qu'on retient,
        le libelle n'est que ce qu'on montre."""
        slot["key"] = getattr(self, "_source_label_to_key", {}).get(label, "") or ""
        slot["var"].set(label)
        if hasattr(self, "scan_tree") and getattr(self, "_scan_rows", None):
            self._render_scan_rows()

    def _selected_sources(self):
        """{grandeur: [voie principale, repli...]} d'apres les menus.
        Une grandeur laissee en 'Automatique' est simplement absente ; un
        repli identique a la source principale, ou pose sans source
        principale, est ignore -- il ne voudrait rien dire."""
        out = {}
        for styp, entry in getattr(self, "source_vars", {}).items():
            chain = []
            for rank in sorted(entry):
                key = entry[rank].get("key") or ""
                if key in pcfg.PORT_KEYS and key not in chain:
                    chain.append(key)
            if chain:
                out[styp] = chain
        return out

    def _scan_sources(self):
        """Relit le tampon glissant sur la plage demandee et remplit le
        tableau d'inventaire. Aucune ecoute reseau supplementaire n'est
        necessaire : le tampon tourne en permanence, c'est precisement ce
        qui rend cette analyse instantanee."""
        self._scan_rows = []
        for row in self.scan_tree.get_children(""):
            self.scan_tree.delete(row)
        # Le detail ne doit pas survivre a la ligne qu'il decrivait.
        self._show_scan_detail()
        if getattr(self, "buffer", None) is None:
            self.scan_status_lbl.configure(text="tampon indisponible")
            return
        try:
            minutes = max(1, int(self.scan_minutes_var.get()))
        except (tk.TclError, ValueError):
            minutes = 30
        now = time.time()
        try:
            found = pe.scan_nmea_sources(self.buffer.iter_range(now - minutes * 60.0, now))
        except Exception as e:
            self.scan_status_lbl.configure(text=f"analyse impossible : {e}")
            return
        if not found:
            span = self.buffer.span()
            if span is None:
                self.scan_status_lbl.configure(
                    text="tampon vide -- laissez-le tourner quelques minutes, voies actives")
            else:
                self.scan_status_lbl.configure(
                    text=f"rien sur cette plage ; le tampon couvre "
                         f"{time.strftime('%d/%m %H:%M', time.localtime(span[0]))} -> "
                         f"{time.strftime('%d/%m %H:%M', time.localtime(span[1]))}")
            return

        total = 0
        for (port, styp), e in found.items():
            total += e["count"]
            # Cadence EXPLOITABLE de preference : c'est elle qui decide du
            # nombre d'echantillons qu'une source produira. Une voie qui
            # emet du MWV deux fois par seconde mais n'y met du vent
            # apparent qu'une fois sur deux est deux fois plus lente qu'elle
            # n'en a l'air, et l'ecart est signale explicitement.
            hz = e.get("usable_hz") or e["hz"]
            if not hz:
                cadence = "-"
            elif hz >= 1.0:
                cadence = f"{hz:.1f} /s"
            else:
                cadence = f"1 / {1.0 / hz:.1f} s"
            if e.get("usable_hz") and e["hz"] and e["hz"] > e["usable_hz"] * 1.3:
                cadence += " utile"
            unite = ", ".join(e["units"]) or "-"
            if e["refs"]:
                # R = vent apparent, T = pretendu vent vrai. Sur les
                # passerelles rencontrees, le champ 'T' porte en realite une
                # direction au compas : l'application ne l'exploite jamais,
                # et le dit ici plutot que de laisser croire le contraire.
                unite += "  [" + "/".join("R apparent" if r == "R" else "T non exploite"
                                           for r in e["refs"]) + "]"
            # Ce que la trame apporte REELLEMENT, en deux temps : son role,
            # puis ce qu'elle alimente. L'ancien verdict unique
            # "non exploitee par l'application" etait faux pour tout ce qui
            # nourrit la fiche d'une minute archivee (XDR, MTW, station
            # meteo...) -- une voie qui porte la seule pression du bord ne
            # merite pas d'etre annoncee comme inutile.
            role = e["role"] or "non exploitee par l'application"
            usages = []
            if e.get("measure"):
                usages.append("polaire : "
                              + dict(pe.MEASUREMENT_SOURCES)[e["measure"]].lower())
            if e.get("ambient"):
                usages.append("fiche : " + ", ".join(
                    pe.AMBIENT_LABELS[k][0].lower()
                    for k in e["ambient"] if k in pe.AMBIENT_LABELS))
            if usages:
                role += "   [" + " | ".join(usages) + "]"
            self._scan_rows.append({
                "port": port, "styp": styp,
                "role": role, "ambient": list(e.get("ambient") or []),
                "measure": e.get("measure"),
                "cadence": cadence, "unite": unite, "valeur": e["value"] or "",
                # Cle de tri NUMERIQUE pour la cadence : trier "1 / 10.7 s"
                # comme du texte placerait 1/2 s apres 1/10 s.
                "hz": hz or 0.0, "count": e["count"],
            })
        self._render_scan_rows()
        self.scan_status_lbl.configure(
            text=f"{total} trames analysees sur {minutes} min, "
                 f"{len({r['port'] for r in self._scan_rows})} voie(s)")

    def _source_role_of(self, port, styp):
        """Role de cette voie pour ce type de trame d'apres les menus :
        "principale", "repli", ou "" -- c'est ce que montre la colonne
        Source du tableau d'inventaire.

        L'arbitrage porte sur la GRANDEUR et non sur le nom de la trame
        (voir pe.measure_of) -- la distinction ne coute rien et servira le
        jour ou deux trames normalisees porteront la meme mesure."""
        chain = self._selected_sources().get(pe.measure_of(styp)) or []
        if not chain:
            return ""
        if chain[0] == port:
            return "\u25b8 principale"
        return "repli" if port in chain else ""

    SCAN_HEADINGS = (("voie", "Voie"), ("trame", "Trame"), ("role", "Ce qu'elle apporte"),
                      ("cadence", "Cadence"), ("unite", "Unite / reference"),
                      ("valeur", "Derniere valeur lue"), ("source", "Source"))

    def _render_scan_rows(self):
        """(Re)remplit le tableau d'inventaire dans l'ordre de tri courant.
        Repart des donnees deja collectees : trier ne relit jamais le tampon.
        Chaque ligne a pour identifiant 'voie|trame' : la ligne selectionnee
        le reste apres un tri ou une designation, et son detail aussi."""
        sel = self.scan_tree.selection()
        keep = sel[0] if sel else None
        for row in self.scan_tree.get_children(""):
            self.scan_tree.delete(row)
        sort_col, descending = getattr(self, "_scan_sort", ("voie", False))
        def _key(r):
            if sort_col == "cadence":
                return (r["hz"], r["port"], r["styp"])
            if sort_col == "voie":
                return (self.port_label(r["port"], live=True).lower(), -r["hz"])
            if sort_col == "source":
                # Les sources designees remontent : principale, puis repli.
                order = {"\u25b8 principale": 0, "repli": 1, "": 2}
                return (order.get(self._source_role_of(r["port"], r["styp"]), 2),
                        r["port"], r["styp"])
            # La cle de COLONNE n'est pas la cle de DONNEE ("trame" porte
            # styp, "voie" porte port) : sans cette table, un tri sur une
            # colonne inconnue retombait silencieusement sur le meme ordre
            # pour tout le monde.
            field = {"trame": "styp", "role": "role", "unite": "unite",
                     "valeur": "valeur"}.get(sort_col)
            if field is None:
                return (self.port_label(r["port"], live=True).lower(), -r["hz"])
            return (str(r.get(field, "")).lower(), r["port"], -r["hz"])
        for r in sorted(self._scan_rows, key=_key, reverse=descending):
            self.scan_tree.insert("", "end", iid=f"{r['port']}|{r['styp']}", values=(
                self.port_label(r["port"], live=True), r["styp"], r["role"],
                r["cadence"], r["unite"], r["valeur"],
                self._source_role_of(r["port"], r["styp"])))
        for key, label in self.SCAN_HEADINGS:
            mark = ("  \u25bc" if descending else "  \u25b2") if key == sort_col else ""
            self.scan_tree.heading(key, text=f"{label}{mark}")
        if keep and self.scan_tree.exists(keep):
            self.scan_tree.selection_set(keep)
            self.scan_tree.see(keep)
        self._fit_scan_columns()
        self._show_scan_detail()

    def _fit_scan_columns(self):
        """Largeur de chaque colonne du tableau d'inventaire calee sur son
        contenu et sur son en-tete (marque de tri comprise), mesures dans la
        police REELLE -- donc a l'echelle de l'ecran. Les largeurs fixes
        coupaient la voie, l'unite ou la valeur des 100 %, et donnaient en
        grand ecran autant de place a 'Trame' (5 lettres) qu'au reste.
        Seule 'Ce qu'elle apporte' est plafonnee et etirable : elle recoit la
        place en plus, et son texte complet s'affiche sous le tableau."""
        style = ttk.Style(self)
        try:
            body = tkfont.Font(root=self, font=style.lookup("Treeview", "font")
                               or "TkDefaultFont")
            head = tkfont.Font(root=self, font=style.lookup("Treeview.Heading", "font")
                               or "TkHeadingFont")
        except tk.TclError:
            body = head = tkfont.nametofont("TkDefaultFont")
        pad = self._px(16)
        rows = [self.scan_tree.item(i, "values") for i in self.scan_tree.get_children("")]
        needs = {}
        for n, (key, label) in enumerate(self.SCAN_HEADINGS):
            need = head.measure(label + "  \u25bc") + pad
            for v in rows:
                if n < len(v):
                    need = max(need, body.measure(str(v[n])) + pad)
            needs[key] = need
        for key, need in needs.items():
            if key != "role":
                self.scan_tree.column(key, width=need, minwidth=need, stretch=False)
        # 'role' prend ce qui reste de la largeur REELLE du tableau, sans
        # descendre sous un minimum lisible (au-dela, l'ascenseur horizontal
        # prend le relais). Tk n'ajuste les colonnes etirables qu'au
        # redimensionnement : sans ce calcul, la largeur dependait de ce qui
        # s'etait passe avant.
        role_min = min(needs["role"], self._px(220))
        tree_w = self.scan_tree.winfo_width()
        if self.scan_tree.winfo_ismapped() and tree_w > 1:
            rest = tree_w - 4 - sum(w for k, w in needs.items() if k != "role")
            role_w = max(role_min, rest)
        else:
            role_w = min(needs["role"], self._px(420))
        self.scan_tree.column("role", width=role_w, minwidth=role_min, stretch=True)

    # Invite affichee sous le tableau tant qu'aucune ligne n'est choisie.
    SCAN_DETAIL_HINT = ("Selectionnez une ligne : tout ce qu'elle apporte s'affiche ici en "
                        "entier, meme quand la colonne est trop etroite.")

    def _show_scan_detail(self):
        """Texte complet de la ligne selectionnee du tableau d'inventaire."""
        lbl = getattr(self, "scan_detail_lbl", None)
        if lbl is None:
            return
        sel = self.scan_tree.selection()
        if not sel or not self.scan_tree.exists(sel[0]):
            lbl.configure(text=self.SCAN_DETAIL_HINT if self._scan_rows else "",
                          fg=FG_LABEL_DIM)
            return
        voie, trame, role, cadence, unite, valeur, source = (
            list(self.scan_tree.item(sel[0], "values")) + [""] * 7)[:7]
        text = (f"{voie} -- {trame} : {role}\n"
                f"cadence {cadence}  |  unite : {unite}  |  derniere valeur : {valeur or '-'}")
        source = str(source).replace("\u25b8", "").strip()
        if source:
            text += f"  |  source {source}"
        lbl.configure(text=text, fg=FG_LABEL)

    def _sort_scan_by(self, column):
        sort_col, descending = getattr(self, "_scan_sort", ("voie", False))
        if column == sort_col:
            descending = not descending
        else:
            # Cadence : la plus rapide d'abord, c'est la question qu'on se
            # pose en la triant. Le reste part en ordre croissant.
            descending = (column == "cadence")
        self._scan_sort = (column, descending)
        self._render_scan_rows()

    def _use_scan_row_as_source(self):
        """Designe la voie de la ligne selectionnee comme source PRINCIPALE
        de la grandeur qu'elle porte ; l'ancienne principale devient le
        repli. Ne s'applique qu'aux trames que l'application exploite --
        designer une trame de temperature comme source de vent n'aurait
        aucun sens, et on le dit plutot que de l'ignorer."""
        sel = self.scan_tree.selection()
        if not sel:
            messagebox.showinfo("Sources", "Selectionnez d'abord une ligne du tableau.")
            return
        vals = self.scan_tree.item(sel[0], "values")
        styp = str(vals[1])
        label = str(vals[0])
        port = getattr(self, "_source_label_to_key", {}).get(label)
        if port is None:
            port = next((r["port"] for r in self._scan_rows
                         if self.port_label(r["port"], live=True) == label), None)
        if port is None:
            return
        measure = pe.measure_of(styp)
        if measure not in {k for k, _lbl in pe.MEASUREMENT_SOURCES}:
            usable = ", ".join(f"{k} ({lbl.lower()})" for k, lbl in pe.MEASUREMENT_SOURCES)
            # Ne pas exploiter une trame POUR LA POLAIRE ne veut pas dire ne
            # pas l'exploiter du tout : la temperature, l'humidite ou la
            # pression nourrissent la fiche d'une minute archivee. Le dire
            # ici evite de laisser croire que la trame part a la poubelle.
            row = next((r for r in self._scan_rows
                        if r["port"] == port and r["styp"] == styp), None)
            amb = (row or {}).get("ambient") or []
            if amb:
                where = ", ".join(pe.AMBIENT_LABELS[k][0].lower()
                                  for k in amb if k in pe.AMBIENT_LABELS)
                extra = ("\n\nElle n'est pas perdue pour autant : elle alimente la fiche "
                         f"d'une minute archivee ({where}). Choisissez ce qui doit y "
                         "figurer dans la roue dentee > Lissage & table.")
            else:
                extra = ""
            messagebox.showinfo(
                "Sources",
                f"La trame {styp} n'entre pas dans le calcul d'une polaire : elle ne peut pas "
                f"etre designee comme source.\n\nGrandeurs choisissables : {usable}.{extra}")
            return
        entry = self.source_vars.get(measure)
        if entry is None:
            return
        previous = entry[0].get("key") or ""
        self._set_source_slot(entry[0], self.port_label(port, live=True))
        entry[0]["key"] = port
        # L'ancienne principale devient le repli -- sauf si c'etait deja
        # celle-ci : on ne se met pas en repli de soi-meme.
        if previous and previous != port:
            entry[1]["key"] = previous
            entry[1]["var"].set(self.port_label(previous, live=True))
        self._render_scan_rows()
        label_measure = dict(pe.MEASUREMENT_SOURCES).get(measure, measure)
        via = f" (via {styp})" if measure != styp else ""
        self.scan_status_lbl.configure(
            text=f"{self.port_label(port, live=True)} designee pour "
                 f"{label_measure.lower()}{via} -- enregistrez les parametres pour l'appliquer")
    def _build_settings_traitement(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)
        self._build_section_lissage(inner)
        self._build_section_table(inner)
        self._build_section_archive_detail(inner)

    def _build_section_archive_detail(self, inner):
        """Choix des grandeurs d'ambiance montrees dans la fiche d'une minute
        archivee. La liste est confrontee a ce que l'analyse des voies a
        REELLEMENT trouve a bord : proposer une profondeur a qui n'a pas de
        sondeur ne servirait qu'a promettre une case qui restera vide."""
        f_det = section(inner, "Fiche d'une minute archivee (double-clic dans la recherche)")
        note(f_det, "Grandeurs ajoutees a la fiche detaillee d'une minute, en plus des mesures de "
                     "la polaire. Elles sont moyennees sur une fenetre centree sur cette minute et "
                     "relues dans le tampon glissant -- de quoi remplir un journal de passerelle a "
                     "posteriori. 'Analyser les voies' (Sources & tampon) coche d'un trait ce qui "
                     "existe reellement a bord.", wraplength="auto")
        win_row = tk.Frame(f_det, bg=BG_APP)
        win_row.pack(fill="x", padx=6, pady=(0, 6))
        tk.Label(win_row, text="Moyenner sur (minutes) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.archive_window_var = tk.IntVar(
            value=self.config_data.get("archive_detail_window_min", 10))
        tk.Spinbox(win_row, from_=1, to=120, increment=1, width=5,
                   textvariable=self.archive_window_var).pack(side="left", padx=(4, 16))
        tk.Button(win_row, text="Cocher ce qui existe a bord",
                  command=self._detect_archive_fields).pack(side="left")
        # Sur SA ligne et avec retour a la ligne : la liste de ce qui a ete
        # trouve peut etre longue, et en bout de rangee elle etait coupee des
        # deux cotes ('trouve :' disparaissait, le message n'avait plus de sens).
        self.archive_detect_lbl = tk.Label(f_det, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                            font=("Segoe UI", 8))
        self.archive_detect_lbl.pack(fill="x", padx=6, pady=(0, 4))
        follow_width(self.archive_detect_lbl)

        chosen = set(self.config_data.get("archive_detail_fields") or [])
        self.archive_field_vars = {}
        grid = tk.Frame(f_det, bg=BG_APP)
        grid.pack(fill="x", padx=6, pady=(0, 8))
        for i, (key, label, unit, _dec) in enumerate(pe.AMBIENT_FIELDS):
            var = tk.BooleanVar(value=key in chosen)
            tk.Checkbutton(grid, text=f"{label} ({unit})", variable=var, bg=BG_APP, fg=FG_LABEL,
                           selectcolor=BG_PANEL, activebackground=BG_APP,
                           activeforeground=FG_LABEL, anchor="w").grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 24))
            self.archive_field_vars[key] = var

    def _detect_archive_fields(self):
        """Coche les grandeurs effectivement presentes dans le tampon, et
        DECOCHE celles qu'on n'y trouve pas : une case cochee doit promettre
        une valeur, pas une ligne vide."""
        if getattr(self, "buffer", None) is None:
            self.archive_detect_lbl.configure(text="tampon indisponible")
            return
        now = time.time()
        try:
            found = pe.ambient_over_range(self.buffer.iter_range(now - 3600.0, now))
        except Exception as e:
            self.archive_detect_lbl.configure(text=f"analyse impossible : {e}")
            return
        if not found:
            self.archive_detect_lbl.configure(
                text="rien trouve sur la derniere heure de tampon (laissez-le tourner)")
            return
        for key, var in self.archive_field_vars.items():
            var.set(key in found)
        labels = ", ".join(pe.AMBIENT_LABELS[k][0].lower() for k in sorted(found)
                           if k in pe.AMBIENT_LABELS)
        self.archive_detect_lbl.configure(text=f"trouve : {labels}")

    def _build_section_lissage(self, inner):
        f_smooth = section(inner, "Lissage temporel")
        note(f_smooth, "Les mesures sont moyennees sur la fenetre de lissage, et un echantillon "
                        "est produit a chaque periode d'echantillonnage. Les seuils qui decident "
                        "si la fenetre est 'stable' (pas de manoeuvre en cours) ont leur propre "
                        "categorie : Manoeuvres, dans le bandeau de gauche.")
        sm_row1 = tk.Frame(f_smooth, bg=BG_APP)
        sm_row1.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(sm_row1, text="Fenetre de lissage (s) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.window_s_var = tk.DoubleVar(value=self.config_data["smoothing_window_s"])
        tk.Spinbox(sm_row1, from_=3, to=300, increment=1, width=6,
                   textvariable=self.window_s_var).pack(side="left", padx=(4, 16))
        tk.Label(sm_row1, text="Periode d'echantillonnage (s) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.sample_period_var = tk.DoubleVar(value=self.config_data["sample_period_s"])
        tk.Spinbox(sm_row1, from_=1, to=300, increment=1, width=6,
                   textvariable=self.sample_period_var).pack(side="left", padx=(4, 0))

    # ---------- Categorie : detection de manoeuvre ----------
    def _build_settings_manoeuvre(self, parent):
        """Categorie A PART (et non plus noyee dans le lissage) : c'est le
        reglage le plus 'metier' de l'application, celui qu'on ajuste en
        fonction de son bateau -- et la future detection de manoeuvre avec
        arret automatique de l'enregistrement viendra naturellement s'y
        ranger aussi."""
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)
        f_man = section(inner, "Detection de manoeuvre")
        note(f_man, "Une manoeuvre (virement, empannage, acceleration/deceleration) est "
                     "detectee quand le TWA ou le STW varient plus que ces seuils sur la "
                     "fenetre de lissage (reglee dans Lissage & table). Les mesures de la fenetre "
                     "sont alors ECARTEES plutot que faussees : c'est ce filtre qui garantit "
                     "que la polaire n'est construite que sur du bateau etabli. Le Suivi en "
                     "direct affiche en continu ou en sont ces variations par rapport aux "
                     "seuils -- le meilleur moyen de les regler finement pour VOTRE bateau.",
             wraplength="auto")
        sm_row2 = tk.Frame(f_man, bg=BG_APP)
        sm_row2.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(sm_row2, text="Manoeuvre si TWA varie de plus de (deg) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_twa_var = tk.DoubleVar(value=self.config_data["maneuver_twa_deg"])
        tk.Spinbox(sm_row2, from_=2, to=90, increment=1, width=5,
                   textvariable=self.maneuver_twa_var).pack(side="left", padx=(4, 16))
        tk.Label(sm_row2, text="ou STW varie de plus de (%) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_stw_var = tk.DoubleVar(value=self.config_data["maneuver_stw_frac"] * 100.0)
        tk.Spinbox(sm_row2, from_=2, to=200, increment=1, width=5,
                   textvariable=self.maneuver_stw_var).pack(side="left", padx=(4, 0))

        f_stop = section(inner, "Arret automatique sur manoeuvre")
        note(f_stop, "A ne pas confondre avec le filtre ci-dessus : ici il ne s'agit plus "
                      "d'ecarter quelques mesures, mais de DETECTER que vous avez vire de "
                      "bord ou change d'allure -- sur la route fond (COG) et la vitesse fond "
                      "(SOG) des trames VTG, donc sans dependre du girouette-anemometre. "
                      "Quand c'est le cas, Allure sonne, affiche un bandeau et pose la "
                      "question : 'Avez-vous manoeuvre ?'. Si oui, l'enregistrement s'arrete "
                      "et les mesures posterieures a l'instant de detection sont jetees (la "
                      "passe reste propre) ; si non, il continue comme si de rien n'etait.",
             wraplength="auto")
        st_row0 = tk.Frame(f_stop, bg=BG_APP)
        st_row0.pack(fill="x", padx=6, pady=(0, 4))
        self.maneuver_stop_enabled_var = tk.BooleanVar(
            value=bool(self.config_data.get("maneuver_stop_enabled", False)))
        tk.Checkbutton(st_row0, text="Proposer l'arret automatique quand une manoeuvre est detectee",
                       variable=self.maneuver_stop_enabled_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL_BOLD).pack(side="left")
        st_row1 = tk.Frame(f_stop, bg=BG_APP)
        st_row1.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(st_row1, text="Observer sur une fenetre de (secondes) :",
                 bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_stop_window_var = tk.DoubleVar(
            value=self.config_data.get("maneuver_stop_window_s", 45.0))
        tk.Spinbox(st_row1, from_=10, to=600, increment=5, width=5,
                   textvariable=self.maneuver_stop_window_var).pack(side="left", padx=(4, 16))
        tk.Label(st_row1, text="Anti-rebond apres detection (secondes) :",
                 bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_stop_cooldown_var = tk.DoubleVar(
            value=self.config_data.get("maneuver_stop_cooldown_s", 120.0))
        tk.Spinbox(st_row1, from_=10, to=3600, increment=10, width=6,
                   textvariable=self.maneuver_stop_cooldown_var).pack(side="left", padx=(4, 0))
        st_row2 = tk.Frame(f_stop, bg=BG_APP)
        st_row2.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(st_row2, text="Manoeuvre si la route fond (COG) balaie plus de (deg) :",
                 bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_stop_cog_var = tk.DoubleVar(
            value=self.config_data.get("maneuver_stop_cog_deg", 30.0))
        tk.Spinbox(st_row2, from_=5, to=180, increment=1, width=5,
                   textvariable=self.maneuver_stop_cog_var).pack(side="left", padx=(4, 16))
        tk.Label(st_row2, text="ou si la vitesse fond (SOG) varie de plus de (%) :",
                 bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.maneuver_stop_sog_var = tk.DoubleVar(
            value=self.config_data.get("maneuver_stop_sog_frac", 0.35) * 100.0)
        tk.Spinbox(st_row2, from_=5, to=200, increment=1, width=5,
                   textvariable=self.maneuver_stop_sog_var).pack(side="left", padx=(4, 0))
        note(f_stop, "Ces seuils se changent a chaud, meme en pleine acquisition. La route "
                      "n'est prise en compte qu'au-dessus de 1,5 nd de SOG (en dessous, le "
                      "COG d'un GPS ne veut plus rien dire).", wraplength="auto")

    # ---------- Categorie : affichage & fenetre ----------
    def _build_settings_affichage(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)

        f_damp = section(inner, "Amortissement des valeurs en direct")
        note(f_damp, "Les grands chiffres et les fleches du suivi en direct sont moyennes sur "
                      "cette duree : sans elle, ils sautent a chaque trame et deviennent "
                      "illisibles en mer. 0 = valeurs brutes. Cela ne touche QUE l'affichage : "
                      "ni les echantillons enregistres, ni la detection de manoeuvre, ni la "
                      "polaire n'en dependent -- le lissage de la mesure, lui, se regle dans "
                      "Lissage & table.", wraplength="auto")
        damp_row = tk.Frame(f_damp, bg=BG_APP)
        damp_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(damp_row, text="Moyenner l'affichage sur (secondes) :",
                 bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.display_damping_var = tk.DoubleVar(
            value=self.config_data.get("display_damping_s", 5.0))
        tk.Spinbox(damp_row, from_=0, to=60, increment=1, width=5,
                   textvariable=self.display_damping_var).pack(side="left", padx=(4, 0))

        f_theme = section(inner, "Theme de l'interface")
        note(f_theme, "Le mode automatique suit le soleil : theme clair du lever au coucher, "
                       "sombre la nuit -- calcules pour la derniere position GPS recue sur les "
                       "voies d'ecoute (GGA/RMC). Sans position connue, l'automatique reste en "
                       "clair. Le changement se fait a chaud, meme pendant un enregistrement.",
             wraplength="auto")
        theme_row = tk.Frame(f_theme, bg=BG_APP)
        theme_row.pack(fill="x", padx=6, pady=(0, 8))
        self.theme_mode_var = tk.StringVar(value=self.config_data.get("theme_mode", "clair"))
        for value, label in (("clair", "Clair"), ("sombre", "Sombre"),
                              ("auto", "Automatique (soleil)")):
            tk.Radiobutton(theme_row, text=label, variable=self.theme_mode_var, value=value,
                           bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                           activebackground=BG_APP, activeforeground=FG_LABEL,
                           font=FONT_LABEL).pack(side="left", padx=(0, 18))

        f_tray = section(inner, "Fermeture de la fenetre")
        note(f_tray, "Quand cette option est active, la croix de la fenetre ne QUITTE pas : "
                      "l'application se range dans la zone de notification (a cote de "
                      "l'horloge) et continue de tourner -- tampon glissant et enregistrement "
                      "compris. Pour l'eteindre reellement : clic sur son icone de "
                      "notification, puis Quitter. Necessite la bibliotheque facultative "
                      "pystray (voir LISEZ-MOI) ; sans elle, la croix ferme normalement.",
             wraplength="auto")
        tray_row = tk.Frame(f_tray, bg=BG_APP)
        tray_row.pack(fill="x", padx=6, pady=(0, 2))
        self.close_to_tray_var = tk.BooleanVar(value=self.config_data.get("close_to_tray", True))
        tk.Checkbutton(tray_row, text="La croix range dans la zone de notification",
                       variable=self.close_to_tray_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL_BOLD).pack(side="left")
        # Sous la case, avec retour a la ligne : en bout de rangee, la fin
        # de la commande d'installation -- justement la partie a recopier --
        # etait coupee sur une fenetre etroite.
        self.tray_avail_lbl = tk.Label(f_tray, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                        font=("Segoe UI", 8))
        self.tray_avail_lbl.pack(fill="x", padx=(30, 6), pady=(0, 8))
        follow_width(self.tray_avail_lbl)
        # Exception large et non ImportError seul : pystray peut aussi
        # echouer a l'import en environnement sans zone de notification
        # (backend indisponible), ce qui revient au meme pour nous.
        try:
            import pystray  # noqa: F401 -- test de presence uniquement
            self.tray_avail_lbl.configure(text="(pystray detecte : option operationnelle)")
        except Exception:
            self.tray_avail_lbl.configure(
                text="(pystray absent : la croix fermera normalement -- "
                     "python -m pip install pystray pillow)")

    def _build_section_tampon(self, inner):
        f_buffer = section(inner, "Tampon d'enregistrement glissant")
        note(f_buffer, "Enregistre le flux NMEA en PERMANENCE et conserve toujours les dernieres "
                        "heures, les plus anciennes etant purgees automatiquement. On ne peut donc "
                        "plus rater un bord faute d'avoir pense a lancer l'enregistrement : tout est "
                        "deja capte, il ne reste qu'a decouper apres coup. C'est aussi la source du "
                        "journal passerelle. Au debit d'une passerelle reelle (environ 2,6 trames/s), "
                        "48 h representent de l'ordre de 32 Mo.", wraplength="auto")
        buf_row = tk.Frame(f_buffer, bg=BG_APP)
        buf_row.pack(fill="x", padx=6, pady=(0, 4))
        self.buffer_enabled_var = tk.BooleanVar(value=self.config_data.get("buffer_enabled", True))
        tk.Checkbutton(buf_row, text="Tampon actif", variable=self.buffer_enabled_var, bg=BG_APP,
                        fg=FG_LABEL, selectcolor=BG_PANEL, activebackground=BG_APP,
                        font=FONT_LABEL_BOLD).pack(side="left", padx=(0, 20))
        tk.Label(buf_row, text="Duree conservee (heures) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.buffer_retention_var = tk.IntVar(value=self.config_data.get("buffer_retention_h", 48))
        tk.Spinbox(buf_row, from_=pbuf.MIN_RETENTION_H, to=pbuf.MAX_RETENTION_H, increment=1, width=6,
                   textvariable=self.buffer_retention_var).pack(side="left", padx=(4, 0))

        buf_state_row = tk.Frame(f_buffer, bg=BG_APP)
        buf_state_row.pack(fill="x", padx=6, pady=(0, 8))
        self.buffer_state_lbl = tk.Label(buf_state_row, text="", bg=BG_APP, fg=FG_LABEL,
                                          font=FONT_MONO, anchor="w", justify="left")
        self.buffer_state_lbl.pack(side="left")
        tk.Button(buf_state_row, text="Vider le tampon",
                  command=self._clear_buffer).pack(side="right")
        self._refresh_buffer_state()

    def _build_section_table(self, inner):
        f_bins = section(inner, "Table polaire (case, symetrie, statistique)")
        note(f_bins, "Finesse de la grille (case TWA/TWS), demi-cercle unique (symetrie "
                      "babord/tribord), valeur retenue par case (statistique, p90 = "
                      "quasi-vitesse-max). S'applique au calcul de la table SANS toucher a "
                      "l'Entrepot : les echantillons restent stockes un par un, changer ces "
                      "reglages ne perd jamais rien -- la polaire est simplement retracee "
                      "avec la nouvelle grille des l'enregistrement des parametres.",
             wraplength="auto")
        # Deux rangees (la grille, puis symetrie et statistique) : sur une
        # seule, le menu Statistique, servi en dernier, etait ecrase a 150 %.
        bins_row = tk.Frame(f_bins, bg=BG_APP)
        bins_row.pack(fill="x", padx=6, pady=(0, 4))
        bins_row2 = tk.Frame(f_bins, bg=BG_APP)
        bins_row2.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(bins_row, text="Case TWA (deg) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.twa_bin_var = tk.DoubleVar(value=self.config_data["twa_bin_deg"])
        tk.Spinbox(bins_row, from_=1, to=45, increment=1, width=5,
                   textvariable=self.twa_bin_var).pack(side="left", padx=(4, 16))
        tk.Label(bins_row, text="Case TWS (noeuds) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.tws_bin_var = tk.DoubleVar(value=self.config_data["tws_bin_kn"])
        tk.Spinbox(bins_row, from_=0.5, to=20, increment=0.5, width=5,
                   textvariable=self.tws_bin_var).pack(side="left", padx=(4, 0))
        tk.Label(bins_row2, text="Symetrie babord/tribord :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.symmetric_var = tk.BooleanVar(value=self.config_data["symmetric_port_starboard"])
        tk.Checkbutton(bins_row2, variable=self.symmetric_var, bg=BG_APP, selectcolor=BG_PANEL).pack(
            side="left", padx=(4, 16))
        tk.Label(bins_row2, text="Statistique :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.stat_var = tk.StringVar(value=self.config_data["aggregation_stat"])
        self.stat_menu = ttk.OptionMenu(bins_row2, self.stat_var, self.stat_var.get(), *pe.STATISTICS)
        self.stat_menu.configure(width=10)
        self.stat_menu.pack(side="left", padx=(4, 0))

        f_maxp = section(inner, "Polaire max (routage)")
        note(f_maxp, "Sur un cargo a voile ET moteur, le moteur fait partie de la "
                      "voilure : chaque cas de vent (force, orientation) appelle un JEU "
                      "voiles/moteur, pas une voile seule. Cochee, cette option ouvre "
                      "l'etape Polaires avec TOUTES les propulsions cochees : voiles et "
                      "moteurs concourent ensemble dans la polaire max -- et le guide de "
                      "voilure dit donc aussi QUAND passer au moteur. Decochee (voilier "
                      "pur), seule \"Voiles seules\" est cochee. Le choix fait ensuite sur "
                      "l'etape elle-meme est retenu d'une fois sur l'autre ; modifier ce "
                      "reglage le remplace.",
             wraplength="auto")
        maxp_row = tk.Frame(f_maxp, bg=BG_APP)
        maxp_row.pack(fill="x", padx=6, pady=(0, 8))
        self.max_allure_engine_var = tk.BooleanVar(
            value=bool(self.config_data.get("max_polar_include_engine", True)))
        tk.Checkbutton(maxp_row, text="Le moteur fait partie de la voilure "
                                       "(toutes les propulsions cochees aux Polaires)",
                       variable=self.max_allure_engine_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL_BOLD).pack(side="left")

        f_auto = section(inner, "Tri automatique a l'entree de l'entrepot")
        note(f_auto, "Une passe dont l'indice de confiance est trop faible peut entrer dans "
                      "l'entrepot DECOCHEE, plutot qu'incluse d'office dans le calcul. Rien "
                      "n'est supprime ni cache : la passe est entreposee normalement, sa "
                      "polaire reste consultable (double-clic), et sa case Incluse se recoche "
                      "d'un clic. C'est un pre-positionnement, pas une censure -- et le compte "
                      "rendu de fin de prise vous dit toujours ce qui a ete decide, et "
                      "pourquoi.", wraplength="auto")
        auto_row = tk.Frame(f_auto, bg=BG_APP)
        auto_row.pack(fill="x", padx=6, pady=(0, 4))
        self.auto_exclude_var = tk.BooleanVar(
            value=bool(self.config_data.get("auto_exclude_low_confidence", False)))
        tk.Checkbutton(auto_row, text="Ne pas inclure une passe dont la confiance est inferieure a",
                       variable=self.auto_exclude_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL_BOLD).pack(side="left")
        self.auto_exclude_threshold_var = tk.IntVar(
            value=int(self.config_data.get("auto_exclude_threshold", 40)))
        tk.Spinbox(auto_row, from_=0, to=100, increment=5, width=5,
                   textvariable=self.auto_exclude_threshold_var).pack(side="left", padx=(6, 4))
        tk.Label(auto_row, text="/ 100", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        # Le seuil CONSEILLE est affiche juste sous le champ, et il est
        # motive : un nombre sans justification ne se regle qu'au hasard. Sous
        # la case et non a cote : sur la meme rangee, il etait le premier
        # coupe ('conseille :' disparaissait, et '40' seul se lisait comme la
        # valeur saisie). Decale sous le texte de la case.
        adv_lbl = tk.Label(f_auto,
                           text=f"conseille : {pe.CONF_ADVISED_THRESHOLD}  "
                                f"(frontiere entre C indicative et D fragile)",
                           bg=BG_APP, fg=COLOR_ACCENT, font=FONT_LABEL_BOLD)
        adv_lbl.pack(fill="x", padx=(30, 6), pady=(0, 4))
        follow_width(adv_lbl)
        note(f_auto, "Plus haut (60, frontiere B/C), vous ne gardez que les passes bien "
                      "reparties -- utile quand l'entrepot est deja fourni. Plus bas (20), "
                      "vous n'ecartez que les passes vraiment maigres. A 0, rien n'est jamais "
                      "ecarte.", wraplength="auto")

    # ---------- Categorie : sauvegardes ----------
    def _build_settings_sauvegarde(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)

        f_diag = section(inner, "Diagnostic de l'installation")
        note(f_diag, "A lire d'abord quand quelque chose cloche. L'interprete indique quel "
                      "EXECUTABLE tourne reellement -- c'est lui que le pare-feu Windows "
                      "autorise ou bloque, et c'est lui qui decide de la presence d'une "
                      "console. Pour ouvrir Allure SANS fenetre noire, creez un raccourci "
                      "dont la cible est \"...\\pythonw.exe\" \"...\\allure.py\" : c'est "
                      "l'executable, et non l'extension du fichier, qui decide.",
             wraplength="auto")
        # Text en lecture seule plutot que Label : un chemin long (dossier
        # OneDrive, exe autonome) passe a la ligne au lieu d'etre coupe a
        # droite -- la ou sont le dossier et le fichier, la partie utile -- et
        # on peut le selectionner pour le copier. Sa hauteur suit le nombre
        # de lignes affichees (voir _set_diag_text).
        self.diag_txt = tk.Text(f_diag, height=6, width=40, wrap="word", bg=BG_APP, fg=FG_LABEL,
                                font=FONT_MONO, relief="flat", bd=0, highlightthickness=0,
                                padx=0, pady=0, takefocus=0)
        self.diag_txt.pack(fill="x", padx=6, pady=(0, 4))
        self.diag_txt.bind("<Configure>", lambda _e: self._fit_diag_height(), add="+")
        self._set_diag_text(self.diagnostic_text())
        diag_row = tk.Frame(f_diag, bg=BG_APP)
        diag_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Button(diag_row, text="Ouvrir le journal des erreurs",
                  command=self._open_error_log).pack(side="left")

        f_backup = section(inner, "Sauvegarde de l'entrepot (export / import)")
        note(f_backup, "Un seul fichier .json regroupant l'entrepot cumulatif ET la liste des "
                        "passes -- VOS DONNEES DE MER. Pratique pour une sauvegarde manuelle, ou "
                        "pour les recuperer apres une mise a jour ou sur un autre poste. L'import "
                        "propose soit de FUSIONNER (ajoute uniquement les passes pas deja "
                        "connues, sans doublon si vous importez deux fois le meme fichier), soit "
                        "de REMPLACER entierement l'entrepot actuel.", wraplength="auto")
        backup_row = tk.Frame(f_backup, bg=BG_APP)
        backup_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Button(backup_row, text="Exporter une sauvegarde...",
                  command=self._export_store_backup).pack(side="left", padx=(0, 8))
        tk.Button(backup_row, text="Importer une sauvegarde...",
                  command=self._import_store_backup).pack(side="left")

        f_settings_backup = section(inner, "Reglages (export / import)")
        note(f_settings_backup, "Un seul fichier .json regroupant tous les reglages de cette page "
                                  "(voies UDP + priorite, listes voiles/moteurs, lissage, tampon, "
                                  "table polaire) -- pratique pour changer de version ou "
                                  "d'ordinateur sans tout re-saisir. INDEPENDANT de la sauvegarde "
                                  "de l'entrepot ci-dessus : celle-la porte vos donnees de mer, "
                                  "celle-ci vos reglages -- les deux s'exportent separement.",
             wraplength="auto")
        settings_backup_row = tk.Frame(f_settings_backup, bg=BG_APP)
        settings_backup_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Button(settings_backup_row, text="Exporter les reglages...",
                  command=self._export_settings).pack(side="left", padx=(0, 8))
        tk.Button(settings_backup_row, text="Importer des reglages...",
                  command=self._import_settings).pack(side="left")

        tk.Label(inner, text=f"{pcfg.APP_NAME} v{pcfg.APP_VERSION} -- by {pcfg.APP_CREDIT}",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(
            anchor="w", padx=16, pady=(10, 12))

    def _set_diag_text(self, text):
        """Remplit le diagnostic (Text en lecture seule) et ajuste sa hauteur."""
        t = self.diag_txt
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text)
        # 'disabled' interdit la frappe, pas la selection ni la copie.
        t.configure(state="disabled")
        self._fit_diag_height()

    def _fit_diag_height(self):
        """Hauteur du diagnostic = nombre de lignes AFFICHEES (chemins
        replies compris) : ni ligne vide en bas, ni derniere ligne cachee --
        et, tout etant visible, la molette continue de faire defiler la page."""
        t = getattr(self, "diag_txt", None)
        if t is None:
            return
        try:
            lines = int(t.tk.call(t._w, "count", "-update", "-displaylines", "1.0", "end-1c")) + 1
        except (tk.TclError, ValueError):
            return
        if lines != int(t.cget("height")):
            t.configure(height=max(1, lines))

    def _build_editable_list(self, parent, title, items, on_change=None):
        """Liste editable (+ / -). on_change est appele apres chaque ajout ou
        retrait : la grille TimeZero suit ainsi la liste des voiles sans
        attendre un redemarrage."""
        col = tk.Frame(parent, bg=BG_APP)
        col.pack(side="left", padx=(0, 24), fill="y")
        tk.Label(col, text=title, bg=BG_APP, fg=FG_LABEL, font=FONT_LABEL_BOLD).pack(anchor="w")
        # Ascenseur vertical : au-dela de 6 elements, les suivants etaient
        # invisibles, sans indice que la liste continuait -- et ne pouvaient
        # etre retires qu'a l'aveugle. Pose AVANT la liste (le packer sert
        # dans l'ordre : il ne peut plus etre prive de place).
        box_row = tk.Frame(col, bg=BG_APP)
        box_row.pack(pady=(4, 4))
        vsb = ttk.Scrollbar(box_row, orient="vertical")
        listbox = tk.Listbox(box_row, height=6, width=14, bg=BG_FIELD, fg=FG_DIGIT,
                              selectbackground=COLOR_TICK, font=FONT_MONO,
                              yscrollcommand=vsb.set)
        vsb.configure(command=listbox.yview)
        for item in items:
            listbox.insert("end", item)
        vsb.pack(side="right", fill="y")
        listbox.pack(side="left")
        row = tk.Frame(col, bg=BG_APP)
        row.pack()
        entry = tk.Entry(row, width=8)
        entry.pack(side="left")

        def add_item():
            val = entry.get().strip().upper()
            if val and val not in listbox.get(0, "end"):
                listbox.insert("end", val)
                # L'ajout se fait en bas de la liste : le montrer.
                listbox.see("end")
                entry.delete(0, "end")
                if on_change is not None:
                    on_change()

        def remove_item():
            sel = listbox.curselection()
            if sel:
                listbox.delete(sel[0])
                if on_change is not None:
                    on_change()

        tk.Button(row, text="+", width=2, command=add_item).pack(side="left", padx=2)
        tk.Button(row, text="-", width=2, command=remove_item).pack(side="left")
        return listbox, entry

    def _has_pending_session(self):
        """True si une session est en cours d'enregistrement OU contient deja
        des segments/echantillons non traites.

        Ne bloque plus la sauvegarde des parametres a elle seule : seuls les
        reglages de LISSAGE entrent en conflit avec une telle session (voir
        SMOOTHING_KEYS et _save_params), et meme dans ce cas une re-analyse
        est proposee plutot qu'un refus."""
        if self.recording_active:
            return True
        with self._engine_lock:
            return len(self.engine.store) > 0 or bool(self.engine.journal.all_segments())

    def _save_params(self):
        # Les reglages sont lus et valides AVANT tout verrouillage : on ne
        # peut savoir si la modification pose reellement probleme qu'une fois
        # qu'on sait ce qui a change (voir SMOOTHING_KEYS plus bas).
        new_cfg = dict(self.config_data)
        for key in pcfg.PORT_KEYS:
            w = self.params_port_widgets[key]
            try:
                port_num = int(w["port"].get())
            except (tk.TclError, ValueError):
                messagebox.showerror("Parametrage", f"Port invalide pour {key}.")
                return
            new_cfg[key] = {"ip": w["ip"].get().strip() or "0.0.0.0", "port": port_num,
                             "enabled": bool(w["enabled"].get()),
                             "name": w["name"].get().strip()}
        new_cfg["priority"] = sorted(pcfg.PORT_KEYS, key=lambda k: self.priority_vars[k].get())
        new_cfg["sources"] = self._selected_sources()
        new_cfg["source_fallback"] = bool(self.source_fallback_var.get())

        sails = list(self.sail_list_box.get(0, "end"))
        engines = list(self.engine_list_box.get(0, "end"))
        if not sails:
            messagebox.showerror("Parametrage", "La liste des voiles ne peut pas etre vide.")
            return
        new_cfg["sail_list"] = sails
        new_cfg["engine_list"] = engines

        new_cfg["twa_bin_deg"] = float(self.twa_bin_var.get())
        new_cfg["tws_bin_kn"] = float(self.tws_bin_var.get())
        new_cfg["symmetric_port_starboard"] = bool(self.symmetric_var.get())
        new_cfg["aggregation_stat"] = self.stat_var.get()
        new_cfg["max_polar_include_engine"] = bool(self.max_allure_engine_var.get())
        try:
            new_cfg["auto_exclude_low_confidence"] = bool(self.auto_exclude_var.get())
            new_cfg["auto_exclude_threshold"] = int(self.auto_exclude_threshold_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parametrage", "Seuil de confiance invalide.")
            return
        # Seules les voiles encore dans la liste : une voile retiree ne doit
        # pas garder une correspondance enregistree.
        new_cfg["timezero_sail_map"] = {
            code: var.get() for code, var in getattr(self, "tz_map_vars", {}).items()
            if code in sails and var.get() and var.get() != self.TZ_MAP_NONE}

        new_cfg["smoothing_window_s"] = float(self.window_s_var.get())
        new_cfg["sample_period_s"] = float(self.sample_period_var.get())
        new_cfg["maneuver_twa_deg"] = float(self.maneuver_twa_var.get())
        new_cfg["maneuver_stw_frac"] = float(self.maneuver_stw_var.get()) / 100.0

        # Arret automatique sur manoeuvre : volontairement HORS de
        # SMOOTHING_KEYS. Ces seuils ne touchent pas les echantillons deja
        # calcules (ils ne font que declencher une question), on peut donc
        # les ajuster en pleine prise sans moteur neuf ni re-analyse.
        try:
            new_cfg["maneuver_stop_enabled"] = bool(self.maneuver_stop_enabled_var.get())
            new_cfg["maneuver_stop_window_s"] = float(self.maneuver_stop_window_var.get())
            new_cfg["maneuver_stop_cog_deg"] = float(self.maneuver_stop_cog_var.get())
            new_cfg["maneuver_stop_sog_frac"] = float(self.maneuver_stop_sog_var.get()) / 100.0
            new_cfg["maneuver_stop_cooldown_s"] = float(self.maneuver_stop_cooldown_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parametrage", "Seuils d'arret automatique invalides.")
            return

        try:
            new_cfg["buffer_enabled"] = bool(self.buffer_enabled_var.get())
            new_cfg["buffer_retention_h"] = int(self.buffer_retention_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parametrage", "Duree de conservation du tampon invalide.")
            return

        new_cfg["archive_detail_fields"] = [
            k for k, var in getattr(self, "archive_field_vars", {}).items() if var.get()]
        try:
            new_cfg["archive_detail_window_min"] = int(self.archive_window_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parametrage", "Duree de moyennage de la fiche invalide.")
            return

        try:
            new_cfg["display_damping_s"] = float(self.display_damping_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Parametrage", "Amortissement de l'affichage invalide.")
            return

        new_cfg["theme_mode"] = self.theme_mode_var.get()
        new_cfg["close_to_tray"] = bool(self.close_to_tray_var.get())

        new_cfg = pcfg.fixup(new_cfg)
        sail_list_before = list(self.config_data["sail_list"])
        engine_list_before = list(self.config_data["engine_list"])

        # --- Ce changement entre-t-il en conflit avec une session en attente ? ---
        # Seuls les reglages de LISSAGE sont concernes : ils sont figes dans
        # le SteadyStateSmoother a sa creation, donc les appliquer impose un
        # moteur neuf, ce qui jetterait les echantillons deja calcules. Tout
        # le reste (voies UDP, listes voiles/moteurs, tampon, reglages de la
        # table polaire, duree par defaut) n'a aucune prise sur une session
        # en cours d'annotation et n'a donc jamais eu de raison d'etre
        # bloque -- c'etait une restriction trop large.
        changed_smoothing = [k for k in SMOOTHING_KEYS
                             if new_cfg.get(k) != self.config_data.get(k)]

        # Pendant un enregistrement EN COURS, seuls les reglages qui touchent
        # la chaine d'acquisition elle-meme restent interdits : les changer en
        # route couperait les voies UDP, reinitialiserait les cases a cocher
        # ou modifierait le filtrage au milieu du flux. Le tampon, la table
        # polaire et la duree par defaut, eux, se reglent meme en pleine prise.
        if self.recording_active:
            blocked = []
            if changed_smoothing:
                blocked.append("le lissage et la detection de manoeuvre")
            if (new_cfg["sail_list"] != self.config_data["sail_list"]
                    or new_cfg["engine_list"] != self.config_data["engine_list"]):
                blocked.append("les listes voiles / moteurs")
            # Le NOM d'une voie est purement decoratif : le renommer en pleine
            # prise ne change rien a l'acquisition, on ne le compte donc pas
            # comme un changement bloquant (comparaison sans le nom).
            def _wire(cfg_, k):
                return {kk: vv for kk, vv in cfg_[k].items() if kk != "name"}
            if (new_cfg["priority"] != self.config_data["priority"]
                    or new_cfg.get("sources") != self.config_data.get("sources")
                    or new_cfg.get("source_fallback") != self.config_data.get("source_fallback")
                    or any(_wire(new_cfg, k) != _wire(self.config_data, k) for k in pcfg.PORT_KEYS)):
                blocked.append("les voies UDP et le choix des sources")
            if blocked:
                messagebox.showwarning(
                    "Enregistrement en cours",
                    "Un enregistrement est en cours : " + ", ".join(blocked) +
                    " ne peuvent pas changer en route sans fausser ou interrompre la prise.\n\n"
                    "Arretez l'enregistrement -- vous pourrez alors les modifier, et meme "
                    "re-analyser la session avec les nouveaux reglages sans rien perdre.\n\n"
                    "Les autres parametres (tampon, table polaire, duree par defaut) restent "
                    "modifiables des maintenant.")
                return

        reanalyse = False
        if changed_smoothing and self._has_pending_session():
            raw_path = self._pending_raw_path()
            if raw_path is None:
                if not messagebox.askyesno(
                        "Reglages de lissage",
                        "Les nouveaux reglages de lissage imposent de recalculer la session "
                        "en attente, mais son fichier brut n'est plus disponible : les "
                        "echantillons deja calcules seraient PERDUS.\n\n"
                        "Enregistrer quand meme les parametres (et abandonner cette session) ?"):
                    return
            else:
                choice = messagebox.askyesnocancel(
                    "Reglages de lissage",
                    "Une session est en attente de traitement, et ses echantillons ont ete "
                    "calcules avec les anciens reglages de lissage.\n\n"
                    "Oui = enregistrer les reglages ET RE-ANALYSER la session avec les "
                    "nouveaux (les segments d'annotation deja definis sont conserves).\n"
                    "Non = enregistrer les reglages et repartir d'une session vide.\n"
                    "Annuler = ne rien changer.")
                if choice is None:
                    return
                reanalyse = bool(choice)

        pcfg.save_config(new_cfg)
        self.config_data = new_cfg

        # Le theme suit immediatement le nouveau reglage -- a chaud, sans
        # rien perdre (voir _set_theme). En mode auto, c'est le soleil qui
        # decide a partir de maintenant (voir _theme_tick).
        self._set_theme(self._desired_theme_name())

        # Le choix des sources s'applique au moteur EN PLACE, sans le
        # reconstruire : ces deux reglages ne sont que des attributs de
        # lecture (voir PolarEngine._resolve_source), alors qu'un moteur neuf
        # jetterait les echantillons d'une session en attente. Sans cela, la
        # nouvelle source n'aurait pris effet qu'au prochain changement de
        # lissage -- un piege silencieux.
        with self._engine_lock:
            self.engine.source_by_type = dict(new_cfg.get("sources") or {})
            self.engine.allow_fallback = bool(new_cfg.get("source_fallback", True))
            self.engine.port_priority = list(new_cfg.get("priority") or []) or None
            # Le guetteur de manoeuvre suit les nouveaux seuils A CHAUD :
            # un guetteur neuf repart fenetre vide (quelques dizaines de
            # secondes de re-armement), ce qui est le comportement sain
            # apres un changement de seuils.
            self.engine.maneuver_watch = self._make_maneuver_watch(new_cfg)
        self._refresh_source_menus()

        # Le tampon suit immediatement les nouveaux reglages : reduire la
        # duree conservee libere la place tout de suite, et
        # activer/desactiver ouvre ou ferme les voies UDP sans redemarrage.
        self.buffer.set_retention(new_cfg["buffer_retention_h"])
        self._sync_listeners()
        self._refresh_buffer_state()

        # Les variables voiles/moteurs suivent les nouvelles listes SANS
        # perdre ce qui etait coche (voir _sync_config_vars) : on peut donc
        # ajuster ses listes en pleine prise sans que le suivi en direct se
        # mette a afficher "Voiles : -" alors que la prise continue.
        if (new_cfg["sail_list"] != sail_list_before
                or new_cfg["engine_list"] != engine_list_before):
            self._sync_config_vars()
        self._apply_recording_lock()
        self._refresh_ports_summary()

        result_msg = "Parametres enregistres."
        if reanalyse:
            result_msg = self._reanalyse_pending_session()
        elif changed_smoothing:
            # Nouveau lissage a appliquer : moteur neuf. Sans session en
            # attente, cela ne coute rien (c'est le cas courant).
            self._new_session_engine()
        # Sinon : le moteur et les segments de la session en cours sont
        # laisses STRICTEMENT intacts -- c'est ce qui permet desormais de
        # regler le tampon ou la table polaire sans rien perdre.

        messagebox.showinfo("Parametrage", result_msg)
        # Retour la ou l'on etait avant d'ouvrir les Parametres (comportement
        # attendu d'une roue dentee : regler, puis reprendre son travail --
        # pas etre teleporte ailleurs). Si le retour vise Polaires, le trace
        # se refait automatiquement avec les nouveaux reglages de table via
        # _show_step. On ne navigue que si l'on est effectivement SUR la
        # page Parametres : un appel programmatique a _save_params depuis
        # une autre etape ne doit pas deplacer l'utilisateur.
        if self._current_step == "parametres":
            back = self._settings_return_step or "acquisition"
            if back not in self._step_frames or back == "parametres":
                back = "acquisition"
            self._show_step(back)

    def _pending_raw_path(self):
        """Fichier brut de la session en attente, s'il est encore la --
        c'est lui qui rend une re-analyse possible. None sinon."""
        raw = self.session_log_path if self.mode.get() == "live" else self.import_path
        return raw if raw and os.path.exists(raw) else None

    def _reanalyse_pending_session(self):
        """Recalcule les echantillons de la session en attente a partir de
        son fichier brut, avec les reglages de lissage courants, en
        CONSERVANT les segments d'annotation deja saisis (ils ne dependent
        pas du lissage : ce sont des plages horaires annotees a la main).

        C'est ce qui permet de regler la detection de manoeuvre par
        essais successifs sur une vraie session : on change un seuil, on
        re-analyse, on compare le nombre d'echantillons retenus -- sans
        jamais avoir a retourner en mer ni a polluer l'entrepot."""
        raw_path = self._pending_raw_path()
        if raw_path is None:
            self._new_session_engine()
            return "Parametres enregistres (session abandonnee : fichier brut absent)."

        with self._engine_lock:
            segments = self.engine.journal.to_list()
            before = len(self.engine.store)

        # Base de temps du fichier brut : un journal ecrit en direct ne porte
        # que des heures (HH:MM:SS), alors que ses segments ont ete horodates
        # en temps absolu par set_live_config(). Il faut donc replacer le
        # rejeu sur la meme base, sinon aucun echantillon ne retomberait dans
        # un segment. En mode import, les deux sont deja exprimes depuis
        # minuit : aucun decalage a appliquer.
        base = self.session_epoch_base if self.mode.get() == "live" else 0.0
        if base is None:
            base = 0.0

        self._new_session_engine()
        last_t = 0.0
        try:
            with self._engine_lock:
                # Rejeu d'un fichier : on collecte du debut a la fin.
                self.engine.collecting = True
                self.engine.journal.load_list(segments)
                for t, port, line in pe.replay_log_lines(raw_path):
                    last_t = base + t
                    self.engine.ingest_line(last_t, line, port=port)
                self.engine.tick(last_t)
                after = len(self.engine.store)
        except OSError as e:
            return f"Parametres enregistres, mais la re-analyse a echoue :\n{e}"

        return (f"Parametres enregistres et session re-analysee avec les nouveaux reglages.\n\n"
                f"Echantillons retenus : {after} (contre {before} avec les reglages precedents).\n"
                f"Segments d'annotation conserves : {len(segments)}.")

    # ---------- Tampon glissant ----------
    def _refresh_buffer_state(self):
        """Etat du tampon en clair : ce qu'il couvre reellement et ce qu'il
        occupe. Sans cela, "48 h de retention" reste une promesse abstraite --
        l'utilisateur ne saurait pas s'il peut vraiment aller rechercher le
        bord d'hier soir."""
        if not hasattr(self, "buffer_state_lbl"):
            return
        if not self.config_data.get("buffer_enabled"):
            self.buffer_state_lbl.configure(text="Tampon desactive -- rien n'est enregistre en continu.")
            return
        span = self.buffer.span()
        size_mo = self.buffer.size_bytes() / 1e6
        if span is None:
            self.buffer_state_lbl.configure(
                text="Tampon actif, encore vide (aucune trame recue pour l'instant).")
            return
        t0, t1 = span
        self.buffer_state_lbl.configure(
            text=f"Donnees disponibles du {time.strftime('%d/%m %H:%M', time.localtime(t0))} "
                 f"au {time.strftime('%d/%m %H:%M', time.localtime(t1))}"
                 f"   ({(t1 - t0) / 3600.0:.1f} h, {size_mo:.1f} Mo)")

    def _clear_buffer(self):
        if not messagebox.askyesno(
                "Tampon", "Supprimer TOUT le contenu du tampon glissant ?\n\n"
                          "Les trames brutes deja enregistrees seront perdues (l'entrepot de "
                          "polaires et les passes deja traitees, eux, ne sont pas concernes). "
                          "Le tampon recommencera immediatement a se remplir s'il est actif."):
            return
        n = self.buffer.clear()
        self._refresh_buffer_state()
        messagebox.showinfo("Tampon", f"Tampon vide ({n} fichier(s) horaire(s) supprime(s)).")

    # ---------- Export / import des reglages seuls (voir allure_config.
    # export_settings/import_settings) -- distinct de la sauvegarde
    # portable de l'entrepot (etape Entrepot, _export_store_backup/
    # _import_store_backup) ----------
    def _export_settings(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("Reglages Allure", "*.json")],
            initialfile=f"allure_reglages_{time.strftime('%Y%m%d_%H%M')}.json")
        if not path:
            return
        pcfg.export_settings(path, self.config_data)
        messagebox.showinfo("Export", f"Reglages exportes :\n{path}")

    def _import_settings(self):
        if self._has_pending_session():
            messagebox.showwarning(
                "Parametrage verrouille",
                "Impossible d'importer des reglages : une acquisition est en cours, "
                "ou ses mesures n'ont pas encore ete envoyees a l'entrepot.\n\n"
                "Arretez l'acquisition (etape Acquisition) : l'arret envoie la session "
                "a l'entrepot. Vous pourrez ensuite importer des reglages.")
            return
        path = filedialog.askopenfilename(
            title="Choisir un export de reglages Allure (.json)",
            filetypes=[("Reglages Allure", "*.json"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        try:
            new_cfg = pcfg.import_settings(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Import", f"Impossible d'importer ce fichier :\n{e}")
            return
        if not messagebox.askyesno(
                "Import", "Remplacer les reglages actuels par ceux de ce fichier ?\n\n"
                          "Voies UDP, listes voiles/moteurs, lissage et reglages de la table "
                          "polaire seront tous ecrases. L'entrepot cumulatif (vos donnees de "
                          "mer, vos passes) n'est PAS concerne par cet import."):
            return
        pcfg.save_config(new_cfg)
        self.config_data = new_cfg
        # Contrairement a _save_params() (qui LIT les widgets pour construire
        # new_cfg, donc reste deja synchronise), importer change config_data
        # SANS passer par les widgets -- il faut donc reconstruire l'etape
        # Parametres de toutes pieces pour qu'elle reflete le fichier importe
        # (listes voiles/moteurs, voies UDP, priorite, lissage...), plutot
        # que de laisser les anciens widgets afficher des valeurs perimees.
        for w in self.tab_parametres.winfo_children():
            w.destroy()
        self._build_step_parametres(self.tab_parametres)
        self._sync_config_vars()
        self._apply_recording_lock()
        self._refresh_ports_summary()
        self._new_session_engine()
        messagebox.showinfo("Import", f"Reglages importes depuis :\n{path}")

    # ---------------------------------------------------------------
    # Etape 3 : Entrepot
    # ---------------------------------------------------------------
    def _build_step_entrepot(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)

        f_archive = section(inner, "Interroger les archives")
        note(f_archive, "Indiquez une date (JJ/MM/AAAA) et un horaire (HH:MM) : le tableau montre "
                         "une ligne par minute mesuree autour de cet instant -- dans tout "
                         "l'entrepot, ET dans le tampon glissant pour les minutes qu'aucune passe "
                         "ne couvre (lignes '(tampon)', reconstruites des trames brutes). La ligne "
                         "la plus proche est pre-selectionnee, cliquez-en une autre pour voir son "
                         "detail (AWA/AWS/SOG compris). Pour une passe importee d'un fichier .log "
                         "(qui ne contient que des heures, jamais de date), la date est supposee "
                         "etre celle du traitement.", wraplength="auto")
        archive_row = tk.Frame(f_archive, bg=BG_APP)
        archive_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(archive_row, text="Date :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.archive_date_var = tk.StringVar(value=time.strftime("%d/%m/%Y"))
        self.archive_date_entry = tk.Entry(archive_row, width=11, textvariable=self.archive_date_var)
        self.archive_date_entry.pack(side="left", padx=(4, 16))
        tk.Label(archive_row, text="Heure :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.archive_time_var = tk.StringVar()
        self.archive_time_entry = tk.Entry(archive_row, width=7, textvariable=self.archive_time_var)
        self.archive_time_entry.pack(side="left", padx=(4, 12))
        # Les separateurs se posent tout seuls pendant la frappe : on tape
        # 21082026 et 1435, l'application ecrit 21/08/2026 et 14:35. Une
        # date se saisit ainsi d'une seule main, sans chercher la barre
        # oblique ni les deux-points.
        attach_mask(self.archive_date_entry, self.archive_date_var, (2, 2, 4), "/")
        attach_mask(self.archive_time_entry, self.archive_time_var, (2, 2), ":")
        # Entree valide la recherche depuis l'un ou l'autre champ : on tape
        # une heure, on appuie, c'est cherche. Aller chercher le bouton a la
        # souris pour une saisie faite au clavier n'a aucun sens.
        for _e in (self.archive_date_entry, self.archive_time_entry):
            _e.bind("<Return>", lambda _ev: self._archive_lookup())
            _e.bind("<KP_Enter>", lambda _ev: self._archive_lookup())
        tk.Button(archive_row, text="Rechercher", command=self._archive_lookup).pack(side="left")
        self.archive_status_lbl = tk.Label(archive_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                            font=("Segoe UI", 8))
        self.archive_status_lbl.pack(side="left", padx=(12, 0))

        # Largeur d'une colonne de tableau : la largeur voulue a 100 % mise a
        # l'echelle de l'ecran, et JAMAIS moins que son en-tete (marque de tri
        # comprise) ni que le texte type qu'elle doit montrer en entier (une
        # date, un nom de journal...). Des pixels fixes coupaient ces cellules
        # sur une fenetre etroite ou a 125/150 %.
        head_font = tkfont.nametofont("TkHeadingFont")
        cell_font = tkfont.nametofont("TkDefaultFont")

        def _col_width(base, heading, sample=""):
            return max(self._px(base),
                       head_font.measure(heading + "  ▼") + self._px(14),
                       cell_font.measure(sample) + self._px(14) if sample else 0)

        def _tree_with_scrollbars(row, tree):
            """Tableau + ascenseur vertical + ascenseur horizontal, ce dernier
            affiche seulement quand les colonnes depassent : les colonnes ne
            se compriment plus sous leur largeur utile, on fait defiler."""
            vsb = ttk.Scrollbar(row, orient="vertical", command=tree.yview)
            hsb = ttk.Scrollbar(row, orient="horizontal", command=tree.xview)

            def _xset(lo, hi):
                if float(lo) <= 0.0 and float(hi) >= 1.0:
                    hsb.grid_remove()
                else:
                    hsb.grid()
                hsb.set(lo, hi)
            tree.configure(yscrollcommand=vsb.set, xscrollcommand=_xset)
            tree.grid(row=0, column=0, sticky="ew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            hsb.grid_remove()
            row.columnconfigure(0, weight=1)

        archive_tree_row = tk.Frame(f_archive, bg=BG_APP)
        archive_tree_row.pack(fill="x", padx=6, pady=(2, 2))
        acols = ("heure", "passe", "voiles", "moteurs", "stw", "twa", "tws")
        self.archive_tree = ttk.Treeview(archive_tree_row, columns=acols, show="headings",
                                          height=7, selectmode="browse")
        # Colonnes de VALEURS centrees (stw, twa, tws) : ce sont des
        # grandeurs courtes et de largeur homogene, que l'oeil compare d'une
        # ligne a l'autre -- centrees, elles forment des colonnes nettes,
        # alors qu'un alignement a droite ou a gauche les laissait flotter
        # loin de leur en-tete.
        # Seule la colonne de TEXTE (le libelle de la passe) recoit la largeur
        # en plus : une heure ou une vitesse n'en a que faire.
        for c, label, w, anchor, sample in (
                ("heure", "Heure", 60, "center", "00:00"),
                ("passe", "Passe", 220, "w", ""),
                ("voiles", "Voiles", 90, "w", ""), ("moteurs", "Moteurs", 80, "w", ""),
                ("stw", "STW (kn)", 70, "center", "00.00"),
                ("twa", "TWA (deg)", 75, "center", "+000"),
                ("tws", "TWS (kn)", 70, "center", "00.00")):
            width = _col_width(w, label, sample)
            self.archive_tree.heading(c, text=label)
            self.archive_tree.column(c, width=width, minwidth=width, anchor=anchor,
                                     stretch=(c == "passe"))
        _tree_with_scrollbars(archive_tree_row, self.archive_tree)
        self._archive_tree_rows = {}  # iid -> (epoch, sample)
        self.archive_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_archive_detail())
        # Double-clic : la fiche complete de la minute, grandeurs d'ambiance
        # comprises (pression, temperatures, humidite...) telles que reglees
        # dans les Parametres. La selection simple garde son resume d'une
        # ligne -- on ne relit pas le tampon a chaque coup de fleche.
        self.archive_tree.bind("<Double-1>", lambda _e: self._show_archive_sheet())
        dbl_hint = tk.Label(f_archive, text="Double-cliquez une ligne pour la fiche complete de la "
                                             "minute (grandeurs a choisir dans la roue dentee > "
                                             "Lissage & table).",
                            bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8))
        dbl_hint.pack(anchor="w", padx=6, fill="x")
        follow_width(dbl_hint)

        # Detail a chasse fixe, dont la 1re ligne porte le libelle de la passe
        # (un nom de fichier .log peut etre long) : il suit la largeur reelle
        # et passe a la ligne au lieu d'etre coupe a droite.
        self.archive_detail_lbl = tk.Label(f_archive, text="", bg=BG_APP, fg=FG_LABEL, font=FONT_MONO,
                                            justify="left", anchor="w")
        self.archive_detail_lbl.pack(anchor="w", padx=6, pady=(4, 8), fill="x")
        follow_width(self.archive_detail_lbl)

        f_import = section(inner, "Importer un fichier .log")
        note(f_import, "Ajoute a l'entrepot une passe issue d'un enregistrement deja sur disque "
                        "(journal d'une prise conservee, extraction faite ailleurs...). Le fichier "
                        "est choisi, puis une seule boite demande la configuration utilisee et la "
                        "plage a traiter. Ce n'est pas un mode dans lequel l'application "
                        "basculerait : c'est un apport ponctuel a l'entrepot.", wraplength="auto")
        imp_row = tk.Frame(f_import, bg=BG_APP)
        imp_row.pack(fill="x", padx=6, pady=(0, 8))
        self.choose_import_btn = tk.Button(imp_row, text="Importer un fichier .log...",
                                            command=self._import_log_file, font=FONT_LABEL_BOLD)
        self.choose_import_btn.pack(side="left")
        self.import_info_lbl = tk.Label(imp_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                         font=("Segoe UI", 8))
        self.import_info_lbl.pack(side="left", padx=(12, 0))
        self.import_lock_lbl = tk.Label(imp_row, text="", bg=BG_APP, fg=COLOR_PORT,
                                         font=("Segoe UI", 8, "bold"))
        self.import_lock_lbl.pack(side="left", padx=(12, 0))

        f_store = section(inner, "Entrepot cumulatif : passes enregistrees")
        note(f_store, "Chaque ligne correspond a une session traitee, avec la configuration "
                       "reellement utilisee pendant cette passe (voiles, moteurs) ; une "
                       "passe qui a change de voilure en route en affiche plusieurs, separees "
                       "par des virgules et dominante en tete. Un clic sur un en-tete trie le "
                       "tableau (un second clic inverse le sens). Un clic sur la case de la "
                       "colonne 'Incluse' inclut/"
                       "exclut directement la passe du calcul de polaire, sans la supprimer -- "
                       "pratique pour ecarter une sortie douteuse. La colonne 'Echantillons' "
                       "compte ce qui est RETENU ; quand des troncons ont ete decoches "
                       "(double-clic sur la passe), elle affiche 'retenus / contenus' -- rien "
                       "n'est perdu, la difference dort simplement dans l'entrepot. "
                       "'Modifier' permet de corriger "
                       "a posteriori les voiles/moteurs d'une passe deja entreposee -- "
                       "utile quand on s'apercoit longtemps apres que l'enregistrement portait la "
                       "mauvaise etiquette. 'Recalculer depuis le tampon' va plus loin : il "
                       "rejoue les trames BRUTES de la passe et refait tous les calculs avec les "
                       "reglages du moment -- c'est ce qui repare une passe enregistree sur la "
                       "mauvaise girouette, sans rien estimer, tant qu'elle reste couverte par le "
                       "tampon glissant. La suppression, elle, passe par la Corbeille.")
        # fill="x" + suivi de largeur : ce releve d'une seule longue ligne
        # demandait plus que la fenetre (des qu'une passe est ecartee) et,
        # centre dans un libelle trop etroit, etait coupe des DEUX cotes.
        self.store_size_lbl = tk.Label(f_store, text="", bg=BG_APP, fg=FG_LABEL, font=FONT_MONO)
        self.store_size_lbl.pack(anchor="w", fill="x", padx=6, pady=(0, 6))
        follow_width(self.store_size_lbl)

        tree_row = tk.Frame(f_store, bg=BG_APP)
        tree_row.pack(fill="x", padx=6, pady=(0, 4))
        # La passe est decrite par sa configuration COMPLETE -- voiles ET
        # moteurs : afficher les seules voiles laissait invisible la moitie
        # de ce qui distingue deux polaires (un bord au moteur n'a rien a
        # voir avec le meme bord a la voile seule).
        cols = ("date", "mode", "source", "voiles", "moteurs", "ech",
                "conf", "incluse")
        self.sessions_tree = ttk.Treeview(tree_row, columns=cols, show="headings",
                                           height=6, selectmode="browse")
        # Tri par clic sur l'en-tete : un clic trie sur cette colonne, un
        # second inverse le sens (voir _sort_sessions_by). Colonnes courtes
        # (mode, echantillons) centrees comme la case Incluse.
        # Ordre par defaut : la date croissante, c'est-a-dire l'ordre dans
        # lequel les passes sont arrivees -- l'ordre historique du tableau.
        self._sessions_sort = ("date", False)  # (colonne, decroissant)
        # Largeurs mises a l'echelle, place reservee a la marque de tri (voir
        # _col_width) ; seules les colonnes de TEXTE (source, configuration)
        # s'etirent -- une date ou une case a cocher n'a que faire de la
        # largeur en plus, que les colonnes courtes prenaient a egalite.
        for c, label, w, anchor, sample in (
                ("date", "Date", 115, "w", "00/00/0000 00:00"),
                ("mode", "Mode", 60, "center", "import"),
                # Source : de quoi montrer en entier le nom de journal que
                # l'application ecrit elle-meme (polar_session_<date>_<heure>.log).
                ("source", "Source", 200, "w", "polar_session_00000000_000000.log"),
                ("voiles", "Voiles", 100, "w", ""),
                ("moteurs", "Moteurs", 70, "center", ""),
                ("ech", "Echantillons", 90, "center", "0000 / 0000"),
                ("conf", "Confiance", 100, "center", "C  100  indicative"),
                ("incluse", "Incluse", 60, "center", "")):
            width = _col_width(w, label, sample)
            self.sessions_tree.heading(c, text=label,
                                        command=lambda k=c: self._sort_sessions_by(k))
            self.sessions_tree.column(c, width=width, minwidth=width, anchor=anchor,
                                      stretch=c in ("source", "voiles", "moteurs"))
        _tree_with_scrollbars(tree_row, self.sessions_tree)
        self._sessions_tree_rows = {}  # iid -> session_id
        # La colonne "Incluse" est une VRAIE case a cocher : un clic dessus
        # bascule l'inclusion de la ligne, directement dans le tableau --
        # plus de bouton a part ni d'aller-retour selection puis action.
        self.sessions_tree.bind("<Button-1>", self._on_sessions_tree_click)
        self.sessions_tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_session_conf_lbl())
        # Double-clic : la polaire de CETTE passe, et d'elle seule. C'est la
        # question qu'on se pose en rentrant -- "qu'est-ce que ma sortie
        # d'aujourd'hui a donne ?" -- et l'entrepot cumulatif, par
        # construction, ne sait pas y repondre : il melange tout.
        self.sessions_tree.bind("<Double-1>", self._on_sessions_tree_double_click)

        # Explication de l'indice, et detail de la passe selectionnee. Une
        # note qui reste ABSTRAITE ne sert a personne : c'est en lisant
        # "toute la passe tient dans 2 cases" qu'on comprend pourquoi une
        # sortie de trois heures vaut moins qu'une sortie d'une heure.
        note(f_store, "Confiance : ce que vaut chaque enregistrement -- A solide, B bonne, "
                       "C indicative, D fragile. Elle recompense la REPARTITION plus que le "
                       "nombre : des mesures prises a des moments differents, sur des cases "
                       "differentes, valent bien mieux que deux heures passees dans le meme "
                       "bord. Elle ne porte que sur les mesures RETENUES : decocher des "
                       "troncons la fait bouger. Purement informative pour autant -- elle "
                       "n'ecarte rien d'elle-meme : ce sont la case Incluse et les troncons "
                       "decoches qui decident de ce qui entre dans le calcul.",
             wraplength="auto")
        self.session_conf_lbl = tk.Label(f_store, text="", bg=BG_APP, fg=FG_LABEL,
                                          font=FONT_MONO, anchor="w", justify="left")
        self.session_conf_lbl.pack(fill="x", padx=6, pady=(0, 4))
        # Une longue passe (duree, dispersion en fin de ligne) depassait la
        # largeur d'une fenetre etroite : la fin passe desormais a la ligne.
        follow_width(self.session_conf_lbl)

        # Case d'ENSEMBLE : tout cocher / tout decocher d'un geste. Trier un
        # entrepot de trente passes case par case est un travail de copiste,
        # et l'on veut souvent repartir de zero pour n'en garder que
        # quelques-unes.
        all_row = tk.Frame(f_store, bg=BG_APP)
        all_row.pack(fill="x", padx=6, pady=(0, 6))
        self.sessions_all_var = tk.BooleanVar(value=True)
        tk.Checkbutton(all_row, text="Tout inclure / tout exclure",
                       variable=self.sessions_all_var, command=self._toggle_all_sessions,
                       bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                       activebackground=BG_APP, font=FONT_LABEL_BOLD).pack(side="left")
        self.sessions_all_lbl = tk.Label(all_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                          font=("Segoe UI", 8))
        self.sessions_all_lbl.pack(side="left", padx=(12, 0))

        sess_btn_row = tk.Frame(f_store, bg=BG_APP)
        sess_btn_row.pack(anchor="w", padx=6, pady=(0, 8))
        tk.Button(sess_btn_row, text="Modifier l'annotation...",
                  command=self._edit_selected_session).pack(side="left")
        tk.Button(sess_btn_row, text="Recalculer depuis le tampon...",
                  command=self._recompute_selected_session).pack(side="left", padx=(8, 0))
        tk.Button(sess_btn_row, text="Supprimer la passe", bg=COLOR_PORT, fg="#ffffff",
                  command=self._delete_selected_session).pack(side="left", padx=(8, 0))

        # Panneau de correction d'une passe -- non empaquete tant qu'aucune
        # passe n'est en cours de modification (voir _start_edit_session).
        self._editing_session = None
        self.session_edit_frame = tk.Frame(f_store, bg=BG_PANEL, relief="solid", borderwidth=1)
        self.session_edit_title = tk.Label(self.session_edit_frame, text="", bg=BG_PANEL,
                                            fg=FG_LABEL, font=FONT_LABEL_BOLD, anchor="w")
        self.session_edit_title.pack(fill="x", padx=8, pady=(6, 0))
        # Retour a la ligne a la largeur reelle (et non 880 px figes, qui ne
        # laissaient presque aucune marge a 1040 px de large).
        follow_width(self.session_edit_title)
        edit_note = tk.Label(
            self.session_edit_frame, bg=BG_PANEL, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
            text="Une passe peut contenir plusieurs configurations (changement de voilure en "
                 "cours de route) : chacune se corrige separement, pour ne pas ecraser un "
                 "changement legitime. Seules les etiquettes changent -- les mesures "
                 "(vitesse, angle et force du vent) ne sont jamais recalculees.")
        edit_note.pack(fill="x", padx=8, pady=(0, 4))
        follow_width(edit_note)
        self.session_edit_rows = tk.Frame(self.session_edit_frame, bg=BG_PANEL)
        self.session_edit_rows.pack(fill="x", padx=8, pady=(0, 4))
        tk.Button(self.session_edit_frame, text="Fermer",
                  command=self._cancel_edit_session).pack(anchor="w", padx=8, pady=(0, 8))

        tk.Button(f_store, text="Reinitialiser tout l'entrepot cumulatif (irreversible)",
                  command=self._reset_store, bg=COLOR_PORT, fg="#ffffff").pack(anchor="w", padx=6, pady=(0, 8))

        f_trash = section(inner, "Corbeille (passes supprimees)")
        note(f_trash, "Une passe supprimee ('Supprimer' ci-dessus, ou 'Reinitialiser tout l'entrepot') "
                       "atterrit ici plutot que de disparaitre definitivement -- 'Restaurer' la remet "
                       "dans l'entrepot cumulatif. Les " + str(TRASH_MAX_ENTRIES) + " suppressions les "
                       "plus recentes seulement sont conservees ; au-dela, les plus anciennes sont "
                       "evincees automatiquement.", wraplength="auto")
        self.trash_list_frame = tk.Frame(f_trash, bg=BG_APP)
        self.trash_list_frame.pack(fill="x", padx=6, pady=(0, 8))
        self.empty_trash_btn = tk.Button(f_trash, text="Vider la corbeille", command=self._empty_trash)
        self.empty_trash_btn.pack(anchor="w", padx=6, pady=(0, 8))

        # (La sauvegarde portable -- export/import de l'entrepot en un seul
        # fichier -- vit desormais dans les Parametres, categorie
        # "Sauvegardes", avec l'export/import des reglages : tout ce qui
        # touche a "migrer ou proteger mes donnees" est au meme endroit.)

        # PAS de remplissage ici : _show_step("entrepot") s'en charge a
        # chaque arrivee sur l'etape -- remplir aussi a la construction
        # faisait tout calculer DEUX fois a chaque demarrage, sur un ecran
        # que personne ne regardait encore.

    def _refresh_store_size_label(self):
        """Ce que l'entrepot CONTIENT, et ce qu'il EXPLOITE reellement.

        Annoncer le seul total brut juste au-dessus d'un tableau ou l'on
        vient de trier etait la principale raison de croire que le tri ne
        servait a rien : la polaire, elle, ecartait bien ce qu'il fallait,
        mais le bandeau continuait de compter tout. Les deux nombres sont
        desormais cote a cote, et le second est celui qui part dans le
        calcul."""
        sids = self._included_session_ids()
        n = len(self.persistent_store)
        n_kept = self.persistent_store.count_included(session_ids=sids)
        n_cfg = len(self.persistent_store.configs(session_ids=sids))
        if n_kept == n:
            txt = (f"{n} echantillon(s) accumule(s), tous retenus, "
                   f"repartis sur {n_cfg} configuration(s) voiles/moteurs.")
        else:
            txt = (f"{n_kept} echantillon(s) RETENUS sur {n} accumule(s) "
                   f"({n - n_kept} ecarte(s) : passes non incluses ou troncons decoches), "
                   f"repartis sur {n_cfg} configuration(s) voiles/moteurs.")
        self.store_size_lbl.configure(text=txt)

    def _reset_store(self):
        if len(self.persistent_store) == 0 and not self.sessions_index:
            messagebox.showinfo("Reinitialiser", "L'entrepot cumulatif est deja vide.")
            return
        if not messagebox.askyesno("Reinitialiser", "Supprimer TOUT l'entrepot cumulatif de polaires "
                                                       "(toutes les passes) ? Une copie complete part "
                                                       "dans la Corbeille (voir plus bas) -- vous "
                                                       "pourrez tout restaurer d'un clic en cas d'erreur."):
            return
        self._push_trash_entry({
            "kind": "store_reset", "deleted_at": time.time(),
            "sessions_index": list(self.sessions_index), "samples": self.persistent_store.to_list(),
        })
        self.persistent_store = pe.PolarSampleStore()
        self.sessions_index = []
        pcfg.save_store_data([])
        pcfg.save_sessions_index([])
        self._refresh_store_size_label()
        self._refresh_sessions_list()
        self._refresh_config_choices()

    # ---------- Corbeille (passes supprimees) ----------
    def _push_trash_entry(self, entry):
        """Empile une entree de corbeille et persiste, en evincant les plus
        anciennes au-dela de TRASH_MAX_ENTRIES (voir la note en tete de ce
        fichier) -- toujours appele AVANT la suppression effective, pour ne
        jamais risquer de perdre des donnees si l'ecriture disque echoue en
        cours de route."""
        self.trash.append(entry)
        if len(self.trash) > TRASH_MAX_ENTRIES:
            self.trash = self.trash[-TRASH_MAX_ENTRIES:]
        pcfg.save_trash(self.trash)
        self._refresh_trash_list()

    def _refresh_trash_list(self):
        if not hasattr(self, "trash_list_frame"):
            return
        for w in self.trash_list_frame.winfo_children():
            w.destroy()
        self.trash_list_frame.columnconfigure(2, weight=1)

        headers = ("Supprime le", "Type", "Detail", "", "")
        for col, text in enumerate(headers):
            tk.Label(self.trash_list_frame, text=text, bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, anchor="w").grid(
                row=0, column=col, sticky="w", padx=(0, 10), pady=(0, 2))

        if not self.trash:
            tk.Label(self.trash_list_frame, text="(corbeille vide)", bg=BG_APP, fg=FG_LABEL_DIM,
                     font=("Segoe UI", 8)).grid(row=1, column=0, columnspan=len(headers), sticky="w")
            return

        # Plus recent en premier -- c'est generalement celui qu'on vient de
        # supprimer par erreur et qu'on veut restaurer tout de suite.
        for i, entry in enumerate(reversed(self.trash), start=1):
            real_idx = len(self.trash) - i
            date_txt = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry["deleted_at"]))
            if entry["kind"] == "session":
                kind_txt = "Passe (avant recalcul)" if entry.get("replace") else "Passe"
                rec = entry["session_record"]
                detail = f"{rec.get('label', '?')} ({len(entry.get('samples', []))} echantillon(s))"
            else:
                kind_txt = "Entrepot entier"
                detail = (f"{len(entry.get('sessions_index', []))} passe(s), "
                          f"{len(entry.get('samples', []))} echantillon(s)")
            tk.Label(self.trash_list_frame, text=date_txt, bg=BG_APP, fg=FG_LABEL,
                     font=("Segoe UI", 8), anchor="w").grid(row=i, column=0, sticky="w", padx=(0, 10))
            tk.Label(self.trash_list_frame, text=kind_txt, bg=BG_APP, fg=FG_LABEL,
                     font=("Segoe UI", 8), anchor="w").grid(row=i, column=1, sticky="w", padx=(0, 10))
            # Detail dans la seule colonne extensible, retour a la ligne a sa
            # largeur REELLE (et non 280 px figes) : un libelle de passe long
            # s'etale sur grand ecran et se replie sur un petit, sans pousser
            # les boutons hors de la fenetre.
            follow_width(tk.Label(self.trash_list_frame, text=detail, bg=BG_APP, fg=FG_LABEL,
                                  font=("Segoe UI", 8))).grid(row=i, column=2, sticky="ew",
                                                              padx=(0, 10))
            tk.Button(self.trash_list_frame, text="Restaurer", bg=COLOR_STARBOARD, fg="#ffffff",
                      command=lambda idx=real_idx: self._restore_trash_item(idx)).grid(
                row=i, column=3, sticky="w", padx=(0, 6))
            tk.Button(self.trash_list_frame, text="Supprimer definitivement", bg=COLOR_PORT, fg="#ffffff",
                      command=lambda idx=real_idx: self._purge_trash_item(idx)).grid(row=i, column=4, sticky="w")

    def _restore_trash_item(self, idx):
        if idx < 0 or idx >= len(self.trash):
            return
        entry = self.trash[idx]
        if entry["kind"] == "session":
            existing_ids = {rec["id"] for rec in self.sessions_index}
            rec = entry["session_record"]
            if rec["id"] in existing_ids and entry.get("replace"):
                # Entree posee par un RECALCUL : la passe existe toujours,
                # mais avec des echantillons refaits. Restaurer signifie ici
                # revenir en arriere -- remettre la version d'avant a la
                # place de la version recalculee, et non renoncer.
                current = self.persistent_store.sample_count_for_session(rec["id"]) \
                    if hasattr(self.persistent_store, "sample_count_for_session") else None
                current_txt = f" ({current} echantillon(s))" if current else ""
                if not messagebox.askyesno(
                        "Restaurer",
                        f"Revenir a la version d'avant recalcul de la passe "
                        f"'{rec.get('label', '?')}' ?\n\n"
                        f"La version actuelle{current_txt} sera remplacee par les "
                        f"{len(entry.get('samples', []))} echantillon(s) sauvegardes."):
                    return
                self.persistent_store.remove_sessions({rec["id"]})
                restored = pe.PolarSampleStore.from_list(entry.get("samples", []))
                self.persistent_store.extend(restored)
                for i_rec, existing in enumerate(self.sessions_index):
                    if existing["id"] == rec["id"]:
                        self.sessions_index[i_rec] = dict(rec)
                        break
                pcfg.save_store_data(self.persistent_store.to_list())
                pcfg.save_sessions_index(self.sessions_index)
                messagebox.showinfo("Restaurer", f"Passe '{rec.get('label', '?')}' revenue a sa "
                                                   f"version d'avant recalcul "
                                                   f"({len(restored)} echantillon(s)).")
            elif rec["id"] in existing_ids:
                messagebox.showinfo("Restaurer", "Cette passe est deja presente dans l'entrepot "
                                                   "cumulatif (rien a restaurer) -- l'entree de "
                                                   "corbeille correspondante va etre retiree.")
            else:
                restored = pe.PolarSampleStore.from_list(entry.get("samples", []))
                self.persistent_store.extend(restored)
                self.sessions_index.append(rec)
                pcfg.save_store_data(self.persistent_store.to_list())
                pcfg.save_sessions_index(self.sessions_index)
                messagebox.showinfo("Restaurer", f"Passe '{rec.get('label', '?')}' restauree "
                                                   f"({len(restored)} echantillon(s)).")
        else:
            msg = self._merge_sessions_and_samples(entry.get("samples", []), entry.get("sessions_index", []))
            messagebox.showinfo("Restaurer", msg)
        del self.trash[idx]
        pcfg.save_trash(self.trash)
        self._refresh_trash_list()
        self._refresh_sessions_list()
        self._refresh_config_choices()

    def _purge_trash_item(self, idx):
        if idx < 0 or idx >= len(self.trash):
            return
        if not messagebox.askyesno("Corbeille", "Supprimer definitivement cette entree de la "
                                                   "corbeille ? Elle ne sera alors plus restaurable."):
            return
        del self.trash[idx]
        pcfg.save_trash(self.trash)
        self._refresh_trash_list()

    def _empty_trash(self):
        if not self.trash:
            return
        if not messagebox.askyesno("Corbeille", f"Vider definitivement la corbeille "
                                                   f"({len(self.trash)} entree(s)) ?"):
            return
        self.trash = []
        pcfg.save_trash(self.trash)
        self._refresh_trash_list()

    def _merge_sessions_and_samples(self, samples, sessions):
        """Fusionne (sans doublon, par id de passe) des echantillons+passes
        dans l'entrepot cumulatif actuel -- factorise la logique partagee par
        'Importer une sauvegarde' (fusion) et 'Restaurer' une entree de
        corbeille de type 'Entrepot entier' : dans les deux cas, on ne veut
        PAS ecraser ce que l'utilisateur a fait depuis (nouvelles passes
        enregistrees apres coup), seulement reintegrer ce qui manque."""
        existing_ids = {rec["id"] for rec in self.sessions_index}
        new_sessions = [rec for rec in sessions if rec["id"] not in existing_ids]
        if not new_sessions:
            return ("Aucune passe a reintegrer : toutes celles de cette sauvegarde sont deja "
                     "presentes dans l'entrepot cumulatif.")
        import_ids = {rec["id"] for rec in new_sessions}
        # isinstance() avant .get() : une sauvegarde d'une autre version (ou
        # retouchee a la main) peut contenir autre chose que des dicts dans
        # la liste d'echantillons -- on filtre ici plutot que de laisser
        # remonter une AttributeError. from_list() ecarte ensuite ceux qui
        # restent inexploitables (voir allure_engine.fixup_sample).
        imported_store = pe.PolarSampleStore.from_list(
            [s for s in samples if isinstance(s, dict) and s.get("session_id") in import_ids])
        self.persistent_store.extend(imported_store)
        self.sessions_index.extend(new_sessions)
        pcfg.save_store_data(self.persistent_store.to_list())
        pcfg.save_sessions_index(self.sessions_index)
        return (f"{len(new_sessions)} passe(s) reintegree(s) ({len(imported_store)} echantillon(s)). "
                f"Total de l'entrepot : {len(self.persistent_store)} echantillon(s).")

    # ---------- Sauvegarde portable (export / import) ----------
    def _export_store_backup(self):
        if len(self.persistent_store) == 0:
            messagebox.showinfo("Export", "L'entrepot cumulatif est vide : rien a exporter.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("Sauvegarde Allure", "*.json")],
            initialfile=f"allure_sauvegarde_{time.strftime('%Y%m%d_%H%M')}.json")
        if not path:
            return
        pcfg.save_backup_bundle(path, self.persistent_store.to_list(), self.sessions_index)
        messagebox.showinfo(
            "Export",
            f"Sauvegarde exportee : {len(self.persistent_store)} echantillon(s), "
            f"{len(self.sessions_index)} passe(s).\n\n{path}")

    def _import_store_backup(self):
        path = filedialog.askopenfilename(
            title="Choisir une sauvegarde Allure (.json)",
            filetypes=[("Sauvegarde Allure", "*.json"), ("Tous les fichiers", "*.*")])
        if not path:
            return
        try:
            samples, sessions = pcfg.load_backup_bundle(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Import", f"Impossible de lire cette sauvegarde :\n{e}")
            return
        if not samples and not sessions:
            messagebox.showinfo("Import", "Cette sauvegarde est vide (aucun echantillon, aucune passe).")
            return

        n_untagged = sum(1 for s in samples if s.get("session_id") is None)
        untagged_note = (f"\n\nAttention : {n_untagged} echantillon(s) de cette sauvegarde ne sont "
                          "rattaches a aucune passe (donnees anciennes) -- une FUSION les ignorera "
                          "(pas de moyen fiable d'eviter les doublons pour ces echantillons-la) ; "
                          "seul un REMPLACEMENT les recupere." if n_untagged else "")
        origin = pcfg.backup_bundle_origin(path)
        origin_note = f" (produite par Allure {origin})" if origin != "inconnue" else \
            " (version d'origine inconnue -- sauvegarde anterieure a la numerotation)"
        choice = messagebox.askyesnocancel(
            "Importer la sauvegarde",
            f"Sauvegarde choisie{origin_note} : {len(samples)} echantillon(s), "
            f"{len(sessions)} passe(s).\n\n"
            f"Oui = FUSIONNER avec l'entrepot actuel ({len(self.persistent_store)} echantillon(s) "
            "actuellement) -- ajoute uniquement les passes pas deja connues.\n"
            f"Non = REMPLACER entierement l'entrepot actuel par cette sauvegarde "
            f"({len(self.persistent_store)} echantillon(s) actuels iraient dans la Corbeille, "
            "restaurables en cas d'erreur).\n"
            "Annuler = ne rien faire." + untagged_note)
        if choice is None:
            return

        if choice:
            result_msg = self._merge_sessions_and_samples(samples, sessions)
        else:
            if len(self.persistent_store) > 0 or self.sessions_index:
                self._push_trash_entry({
                    "kind": "store_reset", "deleted_at": time.time(),
                    "sessions_index": list(self.sessions_index), "samples": self.persistent_store.to_list(),
                })
            self.persistent_store = pe.PolarSampleStore.from_list(samples)
            self.sessions_index = sessions
            pcfg.save_store_data(self.persistent_store.to_list())
            pcfg.save_sessions_index(self.sessions_index)
            result_msg = (f"Entrepot remplace : {len(self.persistent_store)} echantillon(s), "
                          f"{len(self.sessions_index)} passe(s).")

        self._refresh_sessions_list()
        self._refresh_config_choices()
        messagebox.showinfo("Import", result_msg)

    # ---------- Gestion des passes (sessions traitees) ----------
    def _session_config_texts(self, session_id, configs=None):
        """(voiles, moteurs) d'une passe, en texte, pour le tableau
        de l'entrepot. Une passe peut porter PLUSIEURS configurations (un
        changement de voilure en cours de route en cree une nouvelle) : les
        valeurs distinctes sont alors listees separees par des virgules,
        dans l'ordre decroissant du nombre d'echantillons -- la
        configuration dominante de la passe se lit donc en premier.

        Les deux colonnes sont construites d'un seul tenant, a partir de la
        meme source (configs_for_session) : impossible qu'elles se
        desynchronisent.

        configs : le resultat deja regroupe de session_overview(), quand
        l'appelant affiche PLUSIEURS passes -- sans lui, chaque ligne du
        tableau reparcourait l'entrepot entier."""
        if configs is None:
            configs = self.persistent_store.configs_for_session(session_id)
        if not configs:
            return "-", "-"

        def _distinct(render):
            out = []
            for cfg, _n in configs:  # deja trie par nombre d'echantillons
                txt = render(cfg)
                if txt not in out:
                    out.append(txt)
            return ", ".join(out) or "-"

        return (_distinct(lambda c: "+".join(c[0]) if c[0] else "-"),
                _distinct(lambda c: "+".join(c[1]) if c[1] else "-"))

    def _refresh_sessions_list(self):
        """(Re)construit le tableau des passes (une ligne par session
        traitee) avec les voiles reellement utilisees, calculees depuis
        l'entrepot (PolarSampleStore.sails_for_session). Les actions
        (modifier, supprimer) portent sur la ligne selectionnee ; la case
        de la colonne "Incluse" se clique directement. L'ordre d'affichage
        suit _sessions_sort (clic sur un en-tete), sans jamais toucher a
        l'ordre de sessions_index lui-meme : trier est un geste de LECTURE,
        il ne doit rien reecrire sur disque."""
        if not hasattr(self, "sessions_tree"):
            return
        selected_sid = self._selected_session_id()
        for row in self.sessions_tree.get_children(""):
            self.sessions_tree.delete(row)
        self._sessions_tree_rows = {}

        # Realignement systematique, AVANT de compter : sessions_index
        # change par bien des chemins (restauration de corbeille, import de
        # sauvegarde, tri automatique...), et l'entrepot doit suivre a tous
        # les coups. En tete de methode parce que le releve ci-dessous
        # decrit desormais ce qui est RETENU : le compter avant d'avoir
        # repercute les troncons ecartes afficherait l'etat d'avant.
        self._apply_excluded_ranges()

        # UN SEUL parcours de l'entrepot pour toutes les lignes (voir
        # session_overview) : configurations et confiance par passe. Avant,
        # chaque ligne reparcourait l'entrepot deux fois -- au demarrage,
        # c'etait la premiere cause de lenteur.
        overview = self.persistent_store.session_overview(
            twa_bin_deg=self.config_data["twa_bin_deg"],
            tws_bin_kn=self.config_data["tws_bin_kn"],
            symmetric=self.config_data["symmetric_port_starboard"])
        rows = []
        for i, rec in enumerate(self.sessions_index):
            session_id = rec["id"]
            date_txt = time.strftime("%d/%m/%Y %H:%M", time.localtime(rec["created_at"])) \
                if rec.get("created_at") else "?"
            ov = overview.get(session_id)
            # Configurations decrites d'apres les mesures RETENUES. Repli sur
            # la passe entiere quand il n'en reste aucune : une ligne dont
            # tous les troncons sont ecartes doit continuer a dire ce
            # qu'elle etait, sinon l'utilisateur ne sait plus ce qu'il a mis
            # de cote (la colonne Echantillons, elle, affiche bien 0 / n).
            cfg_src = (ov["configs"] if ov else [])
            if ov and not cfg_src and ov["n_total"]:
                cfg_src = self.persistent_store.configs_for_session(session_id)
            voiles_txt, moteurs_txt = self._session_config_texts(
                session_id, configs=cfg_src)
            # Indice de confiance de la passe : ce que vaut CET
            # enregistrement -- purement informatif, il n'exclut rien et ne
            # change aucune vitesse (ce sont la case "Incluse" et les
            # troncons decoches qui decident de ce qui entre dans le
            # calcul). Il est calcule sur les seules mesures RETENUES : une
            # note batie sur des troncons que l'utilisateur vient d'ecarter
            # noterait une passe qui n'existe plus.
            cinfo = ov["confidence"] if ov else self.persistent_store.session_confidence(
                session_id,
                twa_bin_deg=self.config_data["twa_bin_deg"],
                tws_bin_kn=self.config_data["tws_bin_kn"],
                symmetric=self.config_data["symmetric_port_starboard"])
            conf_txt = f"{cinfo['level']}  {cinfo['score']:3d}  {cinfo['word']}"
            # Echantillons : ce qui COMPTE, suivi du total contenu quand des
            # troncons ont ete decoches. L'ancienne colonne
            # affichait le compte fige au traitement (rec["sample_count"]),
            # qui ne bougeait jamais d'un pouce quand on ecartait la moitie
            # d'une passe -- de quoi croire que le tri restait sans effet.
            n_total = ov["n_total"] if ov else rec.get("sample_count", 0)
            n_kept = ov["n_kept"] if ov else n_total
            ech_txt = f"{n_kept}" if n_kept == n_total else f"{n_kept} / {n_total}"
            rows.append({
                "i": i, "id": session_id,
                "values": (date_txt, rec.get("mode", "?"),
                           os.path.basename(rec.get("source", "")) or "-",
                           voiles_txt, moteurs_txt, ech_txt,
                           conf_txt,
                           "☑" if rec.get("included", True) else "☐"),
                # Cles de tri prises sur la DONNEE et non sur le texte
                # affiche : une date se trie chronologiquement (et non
                # "01/12" avant "02/01"), un nombre d'echantillons
                # numeriquement (et non "1000" avant "9").
                "keys": {"date": rec.get("created_at") or 0.0,
                         "mode": str(rec.get("mode", "")),
                         "source": os.path.basename(rec.get("source", "")).lower(),
                         "voiles": voiles_txt.lower(),
                         "moteurs": moteurs_txt.lower(),
                         "ech": n_kept,
                         "conf": cinfo["score"],
                         "incluse": bool(rec.get("included", True))},
                "conf": cinfo,
            })

        sort_col, descending = getattr(self, "_sessions_sort", ("date", False))
        # Index d'origine en cle secondaire : deux passes de meme date (ou de
        # meme mode) gardent un ordre stable et previsible d'un tri a l'autre.
        rows.sort(key=lambda r: (r["keys"].get(sort_col, 0), r["i"]), reverse=descending)

        for r in rows:
            iid = f"sess{r['i']}"
            self._sessions_tree_rows[iid] = r["id"]
            self.sessions_tree.insert("", "end", iid=iid, values=r["values"])
            if r["id"] == selected_sid:
                self.sessions_tree.selection_set(iid)
        self._refresh_sessions_headings()
        self._refresh_sessions_all_checkbox()

        if hasattr(self, "store_size_lbl"):
            self._refresh_store_size_label()
        # Referme le panneau de correction si la passe editee a disparu entre
        # temps (suppression, reinitialisation, import remplacant l'entrepot) :
        # il proposerait sinon de corriger une passe qui n'existe plus.
        if getattr(self, "_editing_session", None) is not None and \
                self._session_record(self._editing_session) is None:
            self._cancel_edit_session()

    SESSIONS_HEADINGS = (("date", "Date"), ("mode", "Mode"), ("source", "Source"),
                          ("voiles", "Voiles"), ("moteurs", "Moteurs"),
                          ("ech", "Echantillons"), ("conf", "Confiance"),
                          ("incluse", "Incluse"))

    def _refresh_session_conf_lbl(self):
        """Detail de la confiance de la passe selectionnee, en clair."""
        if not hasattr(self, "session_conf_lbl"):
            return
        sid = self._selected_session_id()
        if sid is None:
            self.session_conf_lbl.configure(text="")
            return
        c = self.persistent_store.session_confidence(
            sid, twa_bin_deg=self.config_data["twa_bin_deg"],
            tws_bin_kn=self.config_data["tws_bin_kn"],
            symmetric=self.config_data["symmetric_port_starboard"])
        mins = int(c.get("duration_s", 0) // 60)
        disp = ("-" if c["spread"] is None else f"{100.0 * c['spread']:.0f} %")
        # c['n'] ne compte que les mesures RETENUES : quand des troncons ont
        # ete decoches, le total contenu est rappele a cote -- sans lui, la
        # ligne semblerait avoir perdu des mesures en chemin.
        n_total = self.persistent_store.sample_count_for_session(sid)
        ech_txt = (f"{c['n']} echantillon(s)" if c["n"] == n_total
                   else f"{c['n']} echantillon(s) retenu(s) sur {n_total}")
        self.session_conf_lbl.configure(
            text=f"Passe selectionnee : {c['level']} {c['score']}/100 ({c['word']})   "
                 f"{ech_txt}, {c['blocks']} moment(s) independant(s), "
                 f"{c['cells']} case(s) couverte(s), {mins} min, dispersion {disp}\n"
                 f"   -> {c['why']}")

    def _refresh_sessions_headings(self):
        """Rappelle dans l'en-tete quelle colonne trie le tableau et dans
        quel sens -- sans ce reperage, un tableau trie sur une colonne autre
        que la date ressemble a un tableau en desordre."""
        sort_col, descending = getattr(self, "_sessions_sort", ("date", False))
        for key, label in self.SESSIONS_HEADINGS:
            mark = ("  ▼" if descending else "  ▲") if key == sort_col else ""
            self.sessions_tree.heading(key, text=f"{label}{mark}")

    def _sort_sessions_by(self, column):
        """Clic sur un en-tete : trie sur cette colonne ; un second clic sur
        la meme colonne inverse le sens."""
        sort_col, descending = getattr(self, "_sessions_sort", ("date", False))
        if column == sort_col:
            descending = not descending
        else:
            # Nouvelle colonne : croissant, sauf pour les colonnes ou la
            # question posee est presque toujours "les plus grosses/recentes
            # d'abord" (date, nombre d'echantillons).
            descending = column in ("date", "ech")
        self._sessions_sort = (column, descending)
        self._refresh_sessions_list()

    def _selected_session_id(self):
        if not hasattr(self, "sessions_tree"):
            return None
        sel = self.sessions_tree.selection()
        return self._sessions_tree_rows.get(sel[0]) if sel else None

    def _require_selected_session(self):
        sid = self._selected_session_id()
        if sid is None:
            messagebox.showinfo("Passes", "Selectionnez d'abord une passe dans le tableau.")
        return sid

    def _on_sessions_tree_click(self, event):
        """Clic dans le tableau des passes : si le clic tombe sur la colonne
        'Incluse', il bascule la case de CETTE ligne (sans avoir a la
        selectionner d'abord) ; partout ailleurs, comportement normal du
        tableau (selection)."""
        region = self.sessions_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        # Numero de colonne calcule, jamais code en dur : ajouter une colonne
        # au tableau ne doit pas silencieusement deplacer la case a cocher
        # sur une autre colonne.
        cols = list(self.sessions_tree["columns"])
        want = f"#{cols.index('incluse') + 1}" if "incluse" in cols else None
        if self.sessions_tree.identify_column(event.x) != want:
            return
        iid = self.sessions_tree.identify_row(event.y)
        sid = self._sessions_tree_rows.get(iid)
        if sid is None:
            return "break"
        rec = self._session_record(sid)
        if rec is not None:
            self._set_session_included(sid, not rec.get("included", True))
        # "break" : le clic a ete consomme par la case, il ne doit pas en
        # plus deplacer la selection.
        return "break"

    def _on_sessions_tree_double_click(self, event):
        """Double-clic sur une passe : sa polaire a elle. Le double-clic sur
        la colonne Incluse est laisse au simple clic (qui la bascule deja),
        sinon un double-clic la rebasculerait aussitot dans l'autre sens."""
        cols = list(self.sessions_tree["columns"])
        if ("incluse" in cols
                and self.sessions_tree.identify_column(event.x)
                == f"#{cols.index('incluse') + 1}"):
            return "break"
        iid = self.sessions_tree.identify_row(event.y)
        sid = self._sessions_tree_rows.get(iid)
        if sid is not None:
            self._show_session_polar(sid)
        return "break"

    def _show_session_polar(self, session_id):
        """Fenetre : la polaire issue de CETTE passe uniquement.

        L'etape Polaires trace l'entrepot CUMULATIF -- des mois de mesures
        confondus. Elle ne peut donc pas repondre a "qu'est-ce que ma sortie
        d'aujourd'hui a donne ?", qui est pourtant la premiere question
        qu'on se pose en rentrant. C'est le role de cette fenetre : meme
        traitement, meme reglages de table, mais une seule passe -- et sa
        confiance, qui dit tout de suite si la sortie valait quelque chose.
        """
        rec = self._session_record(session_id)
        if rec is None:
            return
        twa_bin = float(self.config_data["twa_bin_deg"])
        tws_bin = float(self.config_data["tws_bin_kn"])
        symmetric = bool(self.config_data["symmetric_port_starboard"])
        stat = self.config_data["aggregation_stat"]
        # Filtre sur CETTE passe seule (et non sur les passes incluses) :
        # une passe exclue du calcul cumulatif reste parfaitement
        # consultable -- c'est meme souvent pour decider de l'exclure ou de
        # la reintegrer qu'on vient la regarder.
        only = {session_id}
        configs = self.persistent_store.configs_for_session(session_id)
        if not configs:
            messagebox.showinfo(
                "Polaire de la passe",
                "Cette passe ne porte aucun echantillon : il n'y a rien a tracer.")
            return

        dlg = tk.Toplevel(self)
        dlg.title(f"Polaire de la passe -- {rec.get('label') or session_id}")
        dlg.configure(bg=BG_APP)
        dlg.transient(self)
        # Taille a l'echelle de l'ecran ET bornee par lui (voir _fit_window) :
        # 1120x760 figes ne suivaient pas l'echelle 125/150 %, ou le contenu
        # demande bien plus -- le pied et son bouton Fermer disparaissaient.
        win_w, _win_h = self._fit_window(dlg, 1120, 760, 900, 540)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        date_txt = (time.strftime("%d/%m/%Y %H:%M", time.localtime(rec["created_at"]))
                    if rec.get("created_at") else "?")
        cinfo = self.persistent_store.session_confidence(
            session_id, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric)
        head = tk.Frame(dlg, bg=BG_APP)
        head.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(head, text=f"Passe du {date_txt}", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL_BOLD, anchor="w").pack(fill="x")
        follow_width(tk.Label(head, text=f"Confiance {cinfo['level']} {cinfo['score']}/100 "
                                         f"({cinfo['word']}) -- {cinfo['n']} echantillon(s), "
                                         f"{cinfo['cells']} case(s), "
                                         f"{int(cinfo['duration_s'] // 60)} min"
                                         f"\n{cinfo['why']}",
                              bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8))).pack(fill="x")

        # Pied pose EN PREMIER, en bas : Tk sert les bords avant le centre, le
        # bouton Fermer ne peut plus etre chasse hors de la fenetre par le
        # trace (dont la taille demandee grandit avec l'echelle d'ecran).
        foot = tk.Frame(dlg, bg=BG_APP)
        foot.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        tk.Button(foot, text="Fermer", command=dlg.destroy, padx=14, pady=3).pack(side="right")
        follow_width(tk.Label(foot, text="Cette polaire ne montre QUE cette passe -- l'etape "
                                         "Polaires, elle, trace l'entrepot cumulatif.",
                              bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8))).pack(
            side="left", fill="x", expand=True)

        # --- Panneau des TRONCONS, a droite ---
        # La passe est un contenant, pas une unite de mesure. On la decoupe
        # en bords homogenes (voir pe.split_into_legs) pour pouvoir ecarter
        # le quart d'heure ou le loch s'est encrasse sans jeter les deux
        # heures qui vont avec.
        body = tk.Frame(dlg, bg=BG_APP)
        body.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        # Largeur a l'echelle de l'ecran (ses textes grandissent avec elle),
        # sans jamais prendre plus des deux cinquiemes de la fenetre : le
        # trace doit garder la place d'un cercle lisible.
        right = tk.Frame(body, bg=BG_APP, width=min(self._px(360), int(win_w * 0.4)))
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        left = tk.Frame(body, bg=BG_APP)
        left.pack(side="left", fill="both", expand=True)

        # Taille demandee modeste : le canvas s'etend de toute facon a la
        # place offerte (fill="both"), et une grande figure imposait sa
        # hauteur a 125/150 %, au detriment du pied.
        fig = Figure(figsize=(4.0, 3.2), dpi=100, facecolor=BG_APP)
        ax = fig.add_subplot(111, projection="polar", facecolor=BG_PANEL)
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.tick_params(colors=FG_LABEL, labelsize=8)
        ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)

        canvas = FigureCanvasTkAgg(fig, master=left)
        canvas.get_tk_widget().configure(bg=BG_APP, highlightthickness=0)
        # Message d'absence de courbe pose AVANT le trace, en bas : le trace
        # extensible ne peut plus le repousser hors de vue.
        empty_lbl = tk.Label(left, text="", bg=BG_APP, fg=COLOR_PORT, font=FONT_LABEL)
        empty_lbl.pack(side="bottom", fill="x")
        follow_width(empty_lbl)
        canvas.get_tk_widget().pack(fill="both", expand=True)

        # Entrees des deux legendes, posees par _redraw, placees par _layout.
        legend_entries = {"winds": [], "configs": []}
        # Configuration = style de trait (et, au-dela de quatre, forme des
        # points) ; la couleur reste au vent.
        cfg_styles = ("-", "--", ":", "-.")
        cfg_markers = ("o", "s", "^", "D")

        def _layout(_event=None):
            """Legendes A DROITE du cercle, ancrees a la FIGURE (comme l'etape
            Polaires), et cercle carre dans la place qu'elles laissent.
            Posee sous le cercle, la legende sortait du cadre des qu'une
            passe portait plus de quelques courbes. Le nombre de colonnes et
            la taille du texte sont choisis pour qu'elles TIENNENT en
            hauteur ; recalcule a chaque retrace et a chaque
            redimensionnement."""
            for old in list(fig.legends):
                old.remove()
            fw, fh = fig.bbox.width, fig.bbox.height
            if fw < 50 or fh < 50:
                return
            renderer = canvas.get_renderer()
            pad = 5.0 * fig.dpi / 72.0       # 5 pt entre legendes et bords
            leg_w = 0.0
            winds, cfgs = legend_entries["winds"], legend_entries["configs"]
            if winds:
                for ncol_w, ncol_c, size in ((1, 1, 7), (2, 1, 7), (2, 2, 7), (3, 2, 6),
                                             (4, 3, 6)):
                    for old in list(fig.legends):
                        old.remove()
                    built = []
                    top = 1.0 - pad / fh
                    for entries, ncol, title in ((winds, ncol_w, "Vent reel"),
                                                 (cfgs, ncol_c, "Configuration")):
                        if not entries:
                            continue
                        lg = fig.legend([e[0] for e in entries], [e[1] for e in entries],
                                        loc="upper right", bbox_to_anchor=(1.0 - pad / fw, top),
                                        ncol=min(ncol, len(entries)), title=title,
                                        fontsize=size, title_fontsize=size,
                                        facecolor=BG_PANEL, edgecolor=COLOR_TICK,
                                        framealpha=1.0)
                        for txt in lg.get_texts():
                            txt.set_color(FG_LABEL)
                        lg.get_title().set_color(FG_LABEL)
                        bb = lg.get_window_extent(renderer)
                        built.append(bb)
                        top = (bb.y0 - pad) / fh
                    leg_w = max(bb.width for bb in built)
                    if min(bb.y0 for bb in built) >= pad:
                        break
            avail_w = max(fw - leg_w - 2.0 * pad, 1.0)
            # Marge FIXE autour du cercle (0,34 pouce, comme l'etape Polaires)
            # pour les graduations d'angle qui debordent du cercle.
            margin = 0.34 * fig.dpi
            side = max(min(avail_w, fh) - 2.0 * margin, 40.0)
            ax.set_position([(avail_w - side) / 2.0 / fw, (fh - side) / 2.0 / fh,
                             side / fw, side / fh])
            if _event is not None:
                canvas.draw_idle()

        canvas.mpl_connect("resize_event", _layout)

        def _redraw():
            """Retrace la polaire de la passe avec les troncons ACTUELLEMENT
            retenus. Appelee a l'ouverture et a chaque case cochee : voir
            immediatement l'effet de ce qu'on ecarte est tout l'interet du
            decoupage."""
            ax.clear()
            ax.set_theta_zero_location("N")
            ax.set_theta_direction(-1)
            ax.tick_params(colors=FG_LABEL, labelsize=8)
            ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)
            ax.set_facecolor(BG_PANEL)
            max_radius = 0.0
            # Une passe peut porter PLUSIEURS configurations (il suffit
            # d'avoir change de voile en route) : chacune a sa courbe, sinon
            # on melangerait des voilures differentes sur un meme trace.
            # Tables d'abord, trace ensuite : la COULEUR dit le vent (une
            # meme force, une meme couleur d'une configuration a l'autre,
            # comme dans l'etape Polaires) et le STYLE DE TRAIT la
            # configuration. L'ancien indice (configuration + vent) donnait
            # la meme teinte a deux couples differents.
            series, cfg_used = [], {}
            for ci, ((sails, engines), _n) in enumerate(
                    self.persistent_store.configs_for_session(session_id)):
                twa_v, tws_v, grid, _cnt = self.persistent_store.table(
                    sails, engines, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                    symmetric=symmetric, stat=stat, session_ids=only)
                cfg_txt = f"{'+'.join(sails) or '-'}/{'+'.join(engines) or '-'}"
                for j, tws in enumerate(tws_v):
                    pts = sorted((twa_v[i], grid[i][j]) for i in range(len(twa_v))
                                 if grid[i][j] is not None)
                    if len(pts) >= 2:
                        series.append((ci, tws, pts))
                        cfg_used[ci] = cfg_txt
            winds = sorted({tws for _ci, tws, _pts in series})
            palette = tws_colors()
            for ci, tws, pts in series:
                color = palette[winds.index(tws) % len(palette)]
                style = cfg_styles[ci % len(cfg_styles)]
                marker = cfg_markers[(ci // len(cfg_styles)) % len(cfg_markers)]
                th = [math.radians(a) for a, _v in pts]
                ra = [v for _a, v in pts]
                max_radius = max(max_radius, max(ra))
                if symmetric:
                    ax.plot([-t for t in reversed(th)], list(reversed(ra)),
                            linewidth=1.2, color=color, linestyle=style)
                ax.plot(th, ra, linewidth=1.2, color=color, linestyle=style)
                # Au-dela de 10 vents la palette se repete : le trait disant
                # deja la configuration, le second tour a des points CREUX.
                second = winds.index(tws) >= len(palette)
                ax.scatter(th, ra, s=14, marker=marker, zorder=3,
                           facecolors=("none" if second else color),
                           edgecolors=(color if second else "none"))
            legend_entries["winds"] = [
                (Line2D([], [], color=palette[k % len(palette)], linewidth=2.0,
                        marker="o", markersize=4,
                        markerfacecolor=("none" if k >= len(palette) else palette[k % len(palette)])),
                 f"{tws:g} nds")
                for k, tws in enumerate(winds)]
            legend_entries["configs"] = [
                (Line2D([], [], color=FG_LABEL, linewidth=1.2,
                        linestyle=cfg_styles[ci % len(cfg_styles)],
                        marker=cfg_markers[(ci // len(cfg_styles)) % len(cfg_markers)],
                        markersize=4), txt)
                for ci, txt in sorted(cfg_used.items())]
            ticks = [i * math.pi / 4.0 for i in range(8)]
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [f"{abs(int(round(math.degrees(((t + math.pi) % (2 * math.pi)) - math.pi))))}°"
                 for t in ticks])
            if max_radius > 0:
                ax.set_rmax(max_radius + 0.5)
            _layout()
            empty_lbl.configure(
                text=("" if series else
                      "Aucune courbe tracable avec les troncons retenus : il faut au moins "
                      "deux angles mesures pour une meme force de vent."))
            canvas.draw_idle()

        self._build_session_legs_panel(right, session_id, _redraw)
        _redraw()

        self._center_on_parent(dlg)
        # Theme sombre : boutons, ascenseurs et cases naissent clairs.
        self._theme_new_window(dlg)
        self._session_polar_win = dlg
        return dlg

    def _build_session_legs_panel(self, parent, session_id, on_change):
        """Liste des TRONCONS d'une passe, chacun avec sa confiance et sa
        case a cocher.

        Le decoupage est CALCULE a la volee, jamais stocke : ce sont les
        plages horaires ecartees qui sont enregistrees (voir
        _set_session_excluded_ranges), si bien qu'affiner un jour le
        decoupage ne deplacera aucune exclusion existante."""
        samples = [s for s in self.persistent_store.to_list()
                   if s.get("session_id") == session_id]
        # Les plages DEJA ecartees decoupent elles aussi : sans cela, un
        # decoupage devenu plus grossier (regles affinees, configurations
        # reunies) fondrait une partie ecartee dans un troncon coche, et le
        # panneau contredirait l'entrepot.
        excluded = self._session_excluded_ranges(session_id)
        legs = pe.split_into_legs(samples, excluded_ranges=excluded)
        tk.Label(parent, text=f"Troncons de la passe ({len(legs)})", bg=BG_APP,
                 fg=FG_LABEL, font=FONT_LABEL_BOLD, anchor="w").pack(fill="x")
        note_txt = ("Une passe de plusieurs heures n'est pas un bloc : elle se decoupe "
                    "toute seule aux manoeuvres, aux changements de voilure et aux "
                    "interruptions. Decochez un troncon pour l'ecarter du calcul -- "
                    "rien n'est supprime, la polaire se retrace aussitot, et le "
                    "tableau de l'Entrepot suit (colonnes Echantillons et Confiance). "
                    "Un eventuel troncon 'sans horodatage' regroupe des mesures venues "
                    "d'un entrepot ancien : il s'ecarte comme les autres.")
        # Textes replies a la largeur REELLE de la colonne (340 px figes ne
        # suivaient ni l'echelle d'ecran ni la largeur de la colonne).
        follow_width(tk.Label(parent, text=note_txt, bg=BG_APP, fg=FG_LABEL_DIM,
                              font=("Segoe UI", 8)), margin=4).pack(fill="x", pady=(0, 6))
        # Boutons puis bilan poses EN BAS avant la liste extensible : servie
        # en premier, celle-ci ne laissait plus que des miettes a "Tout
        # retenir / Tout ecarter", coupes a moitie a 125/150 %.
        btn_row = tk.Frame(parent, bg=BG_APP)
        btn_row.pack(side="bottom", fill="x", pady=(6, 0))
        summary = follow_width(tk.Label(parent, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=("Segoe UI", 8)), margin=4)
        summary.pack(side="bottom", fill="x", pady=(6, 0))
        box_outer, box = make_scrollable(parent, height=1)
        box_outer.pack(fill="both", expand=True)

        rec = self._session_record(session_id)
        undated_out = bool((rec or {}).get("exclude_undated"))

        def _is_excluded(leg):
            if leg["start"] is None:
                # Troncon sans horodatage : son sort est porte par le
                # drapeau de la passe, pas par une plage horaire.
                return undated_out
            mid = (leg["start"] + leg["end"]) / 2.0
            return any(a <= mid <= b for a, b in excluded)

        vars_by_leg = []

        def _refresh_summary():
            kept = sum(lg["n"] for lg, v in vars_by_leg if v.get())
            total = sum(lg["n"] for lg, _v in vars_by_leg)
            out = sum(1 for _lg, v in vars_by_leg if not v.get())
            summary.configure(
                text=(f"{kept} mesure(s) retenue(s) sur {total}"
                      + (f"   --   {out} troncon(s) ecarte(s)" if out else "")))

        def _apply():
            ranges, undated = [], False
            for lg, var in vars_by_leg:
                if var.get():
                    continue
                if lg["start"] is None:
                    # Pas d'heure, donc pas de plage possible : ce troncon
                    # s'ecarte par le drapeau de la passe.
                    undated = True
                    continue
                # Bornes elargies d'une demi-seconde : un echantillon
                # pile sur la frontiere doit tomber du bon cote, sans
                # dependre d'un arrondi de flottant.
                ranges.append((lg["start"] - 0.5, lg["end"] + 0.5))
            self._set_session_excluded_ranges(session_id, ranges,
                                              exclude_undated=undated)
            _refresh_summary()
            on_change()

        for lg in legs:
            conf = pe.leg_confidence(lg)
            var = tk.BooleanVar(value=not _is_excluded(lg))
            vars_by_leg.append((lg, var))
            when = (time.strftime("%H:%M", time.localtime(lg["start"]))
                    if lg["start"] is not None else "?")
            dur = ((lg["end"] - lg["start"]) / 60.0
                   if lg["start"] is not None else 0.0)
            twas = [s["twa"] for s in lg["samples"] if s.get("twa") is not None]
            twa_txt = (f"TWA {min(twas):+.0f} a {max(twas):+.0f}" if twas else "TWA ?")
            cfg_txt = "+".join(lg["sails"]) or "-"
            if lg["engines"]:
                cfg_txt += " / " + "+".join(lg["engines"])
            # Un troncon sans horodatage n'a ni heure de debut ni duree :
            # afficher "?  0 min" laissait croire a une mesure vide alors
            # qu'il porte de vraies mesures -- il se decrit par ce qu'il
            # est, pas par ce qui lui manque.
            head = (f"{when}  {dur:.0f} min  {lg['n']} mes." if lg["start"] is not None
                    else f"sans horodatage  --  {lg['n']} mes.")
            txt = (f"{head}\n"
                   f"    {twa_txt}  |  {pe.LEG_TACK_LABELS[lg['tack']]}\n"
                   f"    {cfg_txt}  |  confiance {conf['level']} {conf['score']}\n"
                   f"    depuis : {lg['cause']}")
            tk.Checkbutton(box, text=txt, variable=var, command=_apply,
                           bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                           activebackground=BG_APP, font=("Segoe UI", 8),
                           anchor="w", justify="left").pack(fill="x", pady=(0, 4))
        _refresh_summary()

        def _all(value):
            for _lg, v in vars_by_leg:
                v.set(value)
            _apply()

        tk.Button(btn_row, text="Tout retenir", command=lambda: _all(True),
                  padx=8).pack(side="left")
        tk.Button(btn_row, text="Tout ecarter", command=lambda: _all(False),
                  padx=8).pack(side="left", padx=(6, 0))
        return vars_by_leg

    def _toggle_selected_session_included(self):
        """Bascule l'inclusion de la passe selectionnee -- conserve comme
        action programmable (tests, raccourcis eventuels) meme si l'interface
        passe desormais par la case a cocher du tableau."""
        sid = self._require_selected_session()
        if sid is None:
            return
        rec = self._session_record(sid)
        if rec is None:
            return
        self._set_session_included(sid, not rec.get("included", True))

    def _apply_excluded_ranges(self):
        """Repercute sur l'entrepot les plages ecartees a l'interieur des
        passes. Un seul point d'entree : toute modification de
        sessions_index passe par ici, sinon l'entrepot et le tableau
        finiraient par raconter deux histoires differentes."""
        self.persistent_store.set_excluded_ranges({
            rec["id"]: [tuple(r) for r in (rec.get("excluded_ranges") or [])]
            for rec in self.sessions_index})
        # Second canal, pour les echantillons qu'aucune plage horaire ne
        # peut designer -- ceux qui n'ont pas d'heure (voir
        # PolarSampleStore.set_excluded_undated).
        self.persistent_store.set_excluded_undated(
            {rec["id"] for rec in self.sessions_index if rec.get("exclude_undated")})

    def _session_excluded_ranges(self, session_id):
        rec = self._session_record(session_id)
        return [list(r) for r in ((rec or {}).get("excluded_ranges") or [])]

    def _set_session_excluded_ranges(self, session_id, ranges, exclude_undated=None):
        """Enregistre les plages ecartees d'une passe, persiste et retrace.
        exclude_undated (True/False) traite a part les echantillons sans
        horodatage, qu'aucune plage ne peut designer ; None = ne pas y
        toucher.

        Rien n'est supprime : les echantillons restent dans l'entrepot et
        redeviennent comptables des que la plage est levee."""
        rec = self._session_record(session_id)
        if rec is None:
            return
        rec["excluded_ranges"] = [[float(a), float(b)] for a, b in ranges]
        if exclude_undated is not None:
            rec["exclude_undated"] = bool(exclude_undated)
        pcfg.save_sessions_index(self.sessions_index)
        self._apply_excluded_ranges()
        self._refresh_sessions_list()
        self._refresh_config_choices()

    def _set_session_included(self, session_id, included):
        """Inclut/exclut une passe du calcul de polaire, persiste, et
        repercute sur le tableau et les choix de configuration."""
        rec = self._session_record(session_id)
        if rec is None:
            return
        rec["included"] = bool(included)
        pcfg.save_sessions_index(self.sessions_index)
        self._refresh_sessions_list()
        self._refresh_config_choices()

    def _set_all_sessions_included(self, included):
        """Coche ou decoche TOUTES les passes d'un coup.

        Le geste courant quand l'entrepot grossit : repartir de zero pour ne
        garder que quelques passes, ou tout reintegrer apres un tri. Le
        faire ligne par ligne sur trente passes est un travail de copiste.
        Aucune donnee n'est touchee -- seule la case Incluse change, et elle
        se rebascule aussi vite."""
        if not self.sessions_index:
            messagebox.showinfo("Passes", "L'entrepot ne contient aucune passe.")
            return
        n = sum(1 for r in self.sessions_index
                if bool(r.get("included", True)) != bool(included))
        if not n:
            self._refresh_sessions_list()
            return
        for rec in self.sessions_index:
            rec["included"] = bool(included)
        pcfg.save_sessions_index(self.sessions_index)
        self._refresh_sessions_list()
        self._refresh_config_choices()
        self.store_size_lbl.configure(text=self.store_size_lbl.cget("text"))

    def _toggle_all_sessions(self):
        """Bascule d'ensemble : si tout est deja coche, on decoche tout ;
        sinon on coche tout. Une seule case pour les deux gestes -- et son
        libelle dit toujours ce que le prochain clic fera."""
        if not self.sessions_index:
            messagebox.showinfo("Passes", "L'entrepot ne contient aucune passe.")
            self.sessions_all_var.set(False)
            return
        self._set_all_sessions_included(bool(self.sessions_all_var.get()))

    def _refresh_sessions_all_checkbox(self):
        """Tient la case d'ensemble d'accord avec le tableau : cochee quand
        tout est inclus, decochee sinon. Sans cela, elle annoncerait un etat
        qui n'est pas celui des passes."""
        if not hasattr(self, "sessions_all_var"):
            return
        recs = self.sessions_index
        n_in = sum(1 for r in recs if r.get("included", True))
        self.sessions_all_var.set(bool(recs) and n_in == len(recs))
        if hasattr(self, "sessions_all_lbl"):
            self.sessions_all_lbl.configure(
                text=(f"{n_in} passe(s) incluse(s) sur {len(recs)}" if recs
                      else "aucune passe"))

    def _edit_selected_session(self):
        sid = self._require_selected_session()
        if sid is not None:
            self._start_edit_session(sid)

    def _delete_selected_session(self):
        sid = self._require_selected_session()
        if sid is not None:
            self._delete_session(sid)

    # ---------- Correction a posteriori d'une passe entreposee ----------
    def _start_edit_session(self, session_id):
        """Ouvre le panneau de correction d'une passe deja entreposee : une
        ligne par configuration presente, chacune corrigeable separement."""
        rec = self._session_record(session_id)
        if rec is None:
            return
        configs = self.persistent_store.configs_for_session(session_id)
        if not configs:
            messagebox.showinfo("Modifier la passe",
                                "Cette passe ne contient aucun echantillon a re-annoter.")
            return
        self._editing_session = session_id
        self.session_edit_title.configure(
            text=f"Corriger l'annotation de : {rec.get('label', session_id)}")

        for w in self.session_edit_rows.winfo_children():
            w.destroy()
        self._session_edit_vars = []
        for idx, ((sails, engines), count) in enumerate(configs):
            # Deux lignes par configuration : l'etat actuel + le bouton en
            # haut, les cases a cocher en dessous. Tout sur une seule ligne
            # deborderait de la fenetre (voiles + moteurs + bouton)
            # et le bouton "Appliquer" se retrouvait hors champ.
            block = tk.Frame(self.session_edit_rows, bg=BG_PANEL, relief="groove", borderwidth=1)
            block.pack(fill="x", pady=3)

            head = tk.Frame(block, bg=BG_PANEL)
            head.pack(fill="x", padx=6, pady=(4, 0))
            # Bouton empaquete AVANT le libelle extensible : le packer Tk
            # reserve les cavites dans l'ordre des appels (meme raison que
            # pour le credit du bandeau, voir _build_nav).
            tk.Button(head, text="Appliquer cette correction", bg=COLOR_STARBOARD, fg="#ffffff",
                      command=lambda i=idx: self._apply_session_config_edit(i)).pack(side="right")
            # Passe a la ligne plutot que d'etre coupe par le bouton, sur une
            # fenetre etroite ou a 150 %.
            follow_width(tk.Label(head, text=f"{count} echantillon(s)   -   actuellement : "
                                             f"{'+'.join(sails) or '-'} / {'+'.join(engines) or '-'}",
                                  bg=BG_PANEL, fg=FG_LABEL, font=FONT_LABEL_BOLD)).pack(
                side="left", fill="x", expand=True)

            # Voiles et moteurs sur DEUX lignes, et les cases d'une famille
            # reparties en colonnes selon la largeur reelle : sur une seule
            # ligne, une liste de voiles un peu longue (ou l'echelle 150 %)
            # poussait les moteurs hors du cadre.
            sail_vars, engine_vars = {}, {}

            def _family_row(title, pady=0):
                row = tk.Frame(block, bg=BG_PANEL)
                row.pack(fill="x", padx=6, pady=pady)
                tk.Label(row, text=title, bg=BG_PANEL, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                         width=9, anchor="w").pack(side="left", anchor="n", pady=(3, 0))
                cells = tk.Frame(row, bg=BG_PANEL)
                cells.pack(side="left", fill="x", expand=True)
                return cells

            def _flow(cells):
                """Regrille les cases de cells sur autant de colonnes egales
                que la largeur en loge -- au moins une."""
                def _regrid(_e=None):
                    kids = cells.winfo_children()
                    if not kids:
                        return
                    cell_w = max(k.winfo_reqwidth() for k in kids) + 4
                    ncol = max(1, cells.winfo_width() // cell_w)
                    for k_i, kid in enumerate(kids):
                        kid.grid(row=k_i // ncol, column=k_i % ncol, sticky="w")
                cells.bind("<Configure>", _regrid, add="+")
                _regrid()

            for title, codes, chosen, store, pady in (
                    ("Voiles :", self.config_data["sail_list"], sails, sail_vars, (2, 0)),
                    ("Moteurs :", self.config_data["engine_list"], engines, engine_vars, (0, 5))):
                cells = _family_row(title, pady)
                for code in codes:
                    v = tk.BooleanVar(value=code in chosen)
                    tk.Checkbutton(cells, text=code, variable=v, bg=BG_PANEL, fg=FG_LABEL,
                                   selectcolor=BG_APP, activebackground=BG_PANEL)
                    store[code] = v
                _flow(cells)
            self._session_edit_vars.append({
                "old": (sails, engines), "count": count,
                "sails": sail_vars, "engines": engine_vars,
            })

        if not self.session_edit_frame.winfo_ismapped():
            self.session_edit_frame.pack(fill="x", padx=6, pady=(0, 8))

    def _cancel_edit_session(self):
        self._editing_session = None
        if self.session_edit_frame.winfo_ismapped():
            self.session_edit_frame.pack_forget()

    def _apply_session_config_edit(self, idx):
        session_id = self._editing_session
        if session_id is None or idx >= len(self._session_edit_vars):
            return
        entry = self._session_edit_vars[idx]
        sails = [c for c, v in entry["sails"].items() if v.get()]
        engines = [c for c, v in entry["engines"].items() if v.get()]
        old = entry["old"]
        if (tuple(sorted(sails)), tuple(sorted(engines))) == \
                (tuple(sorted(old[0])), tuple(sorted(old[1]))):
            messagebox.showinfo("Modifier la passe", "Cette configuration est deja celle indiquee.")
            return
        if not sails and not engines:
            if not messagebox.askyesno("Modifier la passe",
                                        "Aucune voile ni moteur coche -- enregistrer quand meme "
                                        "cette configuration comme 'a nu' ?"):
                return
        n = self.persistent_store.relabel_session_config(session_id, old, sails, engines)
        if n == 0:
            messagebox.showinfo("Modifier la passe", "Aucun echantillon correspondant : la "
                                                       "configuration a peut-etre deja ete modifiee.")
            return
        pcfg.save_store_data(self.persistent_store.to_list())
        self._refresh_sessions_list()
        self._refresh_config_choices()
        # La correction est faite : le formulaire se REFERME (le recapitulatif
        # ci-dessous dit ce qui a change). Pour corriger autre chose sur la
        # meme passe, un clic sur "Modifier..." rouvre le panneau, reconstruit
        # sur les configurations a jour.
        self._cancel_edit_session()
        messagebox.showinfo(
            "Modifier la passe",
            f"{n} echantillon(s) re-annote(s) :\n\n"
            f"{'+'.join(old[0]) or '-'} / {'+'.join(old[1]) or '-'}\n"
            f"->  {'+'.join(sorted(sails)) or '-'} / {'+'.join(sorted(engines)) or '-'}")

    def _session_record(self, session_id):
        for rec in self.sessions_index:
            if rec["id"] == session_id:
                return rec
        return None

    def _delete_session(self, session_id):
        rec = self._session_record(session_id)
        if rec is None:
            return
        if not messagebox.askyesno(
                "Supprimer la passe",
                f"Supprimer la passe '{rec['label']}' "
                f"({rec.get('sample_count', 0)} echantillon(s)) de l'entrepot cumulatif ?\n\n"
                "Une copie part dans la Corbeille (voir plus bas) -- vous pourrez la restaurer "
                "d'un clic en cas d'erreur."):
            return
        samples = [s for s in self.persistent_store.to_list() if s.get("session_id") == session_id]
        self._push_trash_entry({
            "kind": "session", "deleted_at": time.time(), "session_record": dict(rec), "samples": samples,
        })
        self.persistent_store.remove_sessions({session_id})
        self.sessions_index = [r for r in self.sessions_index if r["id"] != session_id]
        pcfg.save_store_data(self.persistent_store.to_list())
        pcfg.save_sessions_index(self.sessions_index)
        self._refresh_sessions_list()
        self._refresh_config_choices()

    # ---------- Recalcul d'une passe depuis le tampon glissant ----------
    def _recompute_selected_session(self):
        sid = self._require_selected_session()
        if sid is not None:
            self._recompute_session(sid)

    def _session_segments_from_samples(self, samples):
        """Reconstitue les plages d'annotation d'une passe a partir de ses
        echantillons : chaque echantillon porte deja sa configuration, il
        suffit de regrouper les instants consecutifs qui partagent la meme.
        Les bornes sont posees a MI-CHEMIN entre le dernier echantillon d'une
        configuration et le premier de la suivante -- un changement de voile
        s'est produit quelque part entre les deux, et le milieu est le seul
        choix qui ne privilegie aucune des deux.

        Retourne [(debut, fin, voiles, moteurs)], bornes en epoch."""
        pts = sorted((s for s in samples if s.get("t") is not None), key=lambda s: s["t"])
        if not pts:
            return []
        groups = []
        for s in pts:
            cfg = (tuple(s.get("sails") or ()), tuple(s.get("engines") or ()))
            if groups and groups[-1][0] == cfg:
                groups[-1][2] = s["t"]
            else:
                groups.append([cfg, s["t"], s["t"]])
        out = []
        for i, (cfg, first, last) in enumerate(groups):
            # Marge avant le premier echantillon : un echantillon lisse est
            # date de la FIN de sa fenetre, la configuration couvrait donc
            # deja la fenetre qui l'a precede.
            start = first - self.config_data["smoothing_window_s"] if i == 0 else out[-1][1]
            if i + 1 < len(groups):
                end = (last + groups[i + 1][1]) / 2.0
            else:
                end = last + self.config_data["smoothing_window_s"]
            out.append((start, end, list(cfg[0]), list(cfg[1])))
        return out

    def _recompute_session(self, session_id):
        """Recalcule une passe A PARTIR DES TRAMES BRUTES du tampon glissant,
        avec les reglages COURANTS (source de chaque grandeur, lissage,
        detection de manoeuvre) -- et non a partir des valeurs deja
        calculees, qu'aucune correction a posteriori ne pourrait rattraper
        honnetement.

        C'est la reponse propre au probleme "j'ai enregistre des jours de
        mesures sur la mauvaise girouette" : tant que la passe est dans la
        fenetre conservee par le tampon, rien n'est perdu, il suffit de tout
        rejouer. L'annotation (voiles/moteurs) est preservee, la passe
        garde son identite, et l'ancienne version part dans la Corbeille."""
        rec = self._session_record(session_id)
        if rec is None:
            return
        if self.recording_active:
            messagebox.showwarning("Recalculer", "Impossible pendant un enregistrement : "
                                                  "arretez la prise d'abord.")
            return
        old_samples = [s for s in self.persistent_store.to_list()
                       if s.get("session_id") == session_id]
        segments = self._session_segments_from_samples(old_samples)
        if not segments:
            messagebox.showwarning("Recalculer", "Cette passe ne porte aucun echantillon horodate : "
                                                  "impossible de retrouver la plage a rejouer.")
            return
        # Couverture jugee sur la plage des ECHANTILLONS eux-memes, pas sur
        # les bornes elargies des segments : celles-ci debordent volontairement
        # d'une fenetre de lissage de part et d'autre, et ce debordement ne
        # doit pas faire declarer "hors tampon" une passe qui commence tout
        # juste au debut de la fenetre conservee. Le rejeu, lui, se contente
        # de ce que le tampon a reellement (bornes rabotees plus bas).
        sample_ts = [s["t"] for s in old_samples if s.get("t") is not None]
        first_t, last_t = min(sample_ts), max(sample_ts)
        span = self.buffer.span() if getattr(self, "buffer", None) is not None else None
        if span is None:
            messagebox.showwarning(
                "Recalculer", "Le tampon glissant est vide : il n'y a aucune trame brute a "
                              "rejouer. Le recalcul n'est possible que pour les passes encore "
                              "couvertes par le tampon.")
            return
        t_from = max(segments[0][0], span[0])
        t_to = min(segments[-1][1], span[1])
        if first_t < span[0] or last_t > span[1]:
            messagebox.showwarning(
                "Recalculer",
                "Cette passe n'est plus entierement couverte par le tampon glissant.\n\n"
                f"Passe   : {time.strftime('%d/%m %H:%M', time.localtime(first_t))} -> "
                f"{time.strftime('%d/%m %H:%M', time.localtime(last_t))}\n"
                f"Tampon : {time.strftime('%d/%m %H:%M', time.localtime(span[0]))} -> "
                f"{time.strftime('%d/%m %H:%M', time.localtime(span[1]))}\n\n"
                "Les trames brutes correspondantes ont ete purgees : la passe ne peut plus etre "
                "recalculee, seulement supprimee ou re-annotee.")
            return

        srcs = self.config_data.get("sources") or {}
        src_txt = ", ".join(
            f"{styp} : " + " puis ".join(self.port_label(p) for p in
                                          ([srcs[styp]] if isinstance(srcs[styp], str) else srcs[styp]))
            for styp, _lbl in pe.MEASUREMENT_SOURCES if srcs.get(styp)) or "automatique"
        if not messagebox.askyesno(
                "Recalculer la passe",
                f"Rejouer les trames brutes de cette passe depuis le tampon glissant et "
                f"recalculer ses echantillons ?\n\n"
                f"Plage : {time.strftime('%d/%m %H:%M', time.localtime(t_from))} -> "
                f"{time.strftime('%d/%m %H:%M', time.localtime(t_to))}\n"
                f"Sources : {src_txt}\n"
                f"Annotation : {len(segments)} configuration(s), conservee(s) telle(s) quelle(s).\n\n"
                f"Les {len(old_samples)} echantillon(s) actuels seront remplaces ; une copie part "
                "dans la Corbeille, restaurable d'un clic."):
            return

        cfg = self.config_data
        engine = pe.PolarEngine(
            smoother=pe.SteadyStateSmoother(
                window_s=cfg["smoothing_window_s"], sample_period_s=cfg["sample_period_s"],
                maneuver_twa_deg=cfg["maneuver_twa_deg"], maneuver_stw_frac=cfg["maneuver_stw_frac"],
                min_fill_frac=cfg["min_fill_frac"]),
            port_priority=cfg.get("priority"), source_by_type=cfg.get("sources"),
            allow_fallback=cfg.get("source_fallback", True))
        for start, end, sails, engines in segments:
            try:
                engine.add_manual_segment(start, end, sails, engines)
            except ValueError:
                continue
        n_lines = 0
        try:
            for t, port, raw in self.buffer.iter_range(t_from, t_to):
                engine.ingest_line(t, raw, port=port)
                n_lines += 1
            engine.tick(t_to)
        except Exception as e:
            messagebox.showerror("Recalculer", f"Lecture du tampon impossible :\n{e}")
            return

        n_new = len(engine.store)
        if n_new == 0:
            messagebox.showwarning(
                "Recalculer",
                f"{n_lines} trame(s) rejouee(s), mais aucun echantillon exploitable n'en est "
                "sorti -- la passe n'a PAS ete modifiee.\n\nCause probable : la source choisie "
                "pour le vent ou la vitesse surface n'emettait pas sur cette plage. Verifiez "
                "l'analyse des voies (roue dentee > Acquisition).")
            return

        # Filet de securite AVANT de toucher a l'entrepot : la version
        # actuelle de la passe part dans la Corbeille, d'ou elle se restaure
        # d'un clic si le resultat ne convient pas.
        self._push_trash_entry({
            "kind": "session", "deleted_at": time.time(),
            "session_record": dict(rec), "samples": old_samples,
            # Marque l'entree comme un RETOUR EN ARRIERE possible : la passe
            # n'a pas ete supprimee, elle a ete refaite. "Restaurer" doit
            # donc remplacer la version recalculee, pas refuser d'agir sous
            # pretexte que la passe existe encore (voir _restore_trash_item).
            "replace": True,
        })
        engine.store.set_session_id(session_id)
        self.persistent_store.remove_sessions({session_id})
        self.persistent_store.extend(engine.store)
        rec["sample_count"] = n_new
        rec["app_version"] = pcfg.APP_VERSION
        rec["recomputed_at"] = time.time()
        pcfg.save_store_data(self.persistent_store.to_list())
        pcfg.save_sessions_index(self.sessions_index)
        self._refresh_sessions_list()
        self._refresh_config_choices()

        # Ce qui a change, chiffre : c'est la seule facon de juger si le
        # recalcul a corrige quelque chose ou n'a rien fait.
        def _median_abs_twa(samples):
            vals = sorted(abs(s["twa"]) for s in samples if s.get("twa") is not None)
            return vals[len(vals) // 2] if vals else None
        old_med, new_med = _median_abs_twa(old_samples), _median_abs_twa(engine.store.to_list())
        delta = ""
        if old_med is not None and new_med is not None:
            delta = (f"\n\nTWA median (valeur absolue) : {old_med:.1f} deg  ->  {new_med:.1f} deg "
                     f"({new_med - old_med:+.1f} deg)")
        messagebox.showinfo(
            "Recalculer la passe",
            f"Passe recalculee depuis {n_lines} trame(s) brutes.\n\n"
            f"Echantillons : {len(old_samples)}  ->  {n_new}{delta}\n\n"
            "L'ancienne version est dans la Corbeille si le resultat ne convient pas.")

    def _included_session_ids(self):
        """Ensemble des session_id actuellement inclus (case a case) -- passe a
        PolarSampleStore.table()/configs() pour ne calculer la polaire qu'a
        partir des passes selectionnees. None si aucune passe n'est encore
        connue (entrepot cree avant cette fonctionnalite, ou tout juste
        reinitialise) : dans ce cas, pas de filtre -- tout est inclus."""
        if not self.sessions_index:
            return None
        return {rec["id"] for rec in self.sessions_index if rec.get("included", True)}

    # ---------- Interroger les archives ----------
    def _replay_instant(self, path, t_query):
        """Rejoue le fichier brut d'une passe JUSQU'A t_query (inclus) et
        retourne (awa, aws, sog, t) d'apres les DERNIERES trames MWV (vent
        apparent, ref='R') / VTG (vitesse fond) rencontrees a ou avant cet
        instant -- t est l'horodatage de la plus recente de ces deux trames
        (None si aucune trouvee). Ces grandeurs ne sont JAMAIS conservees
        dans l'entrepot cumulatif (voir PolarSampleStore : seul le vent VRAI
        lisse y est stocke), d'ou la necessite de rejouer le fichier brut
        pour les retrouver -- utilise uniquement par _archive_lookup()."""
        last_awa = last_aws = last_sog = None
        last_t = None
        try:
            for t, _port, raw in pe.replay_log_lines(path):
                if t > t_query:
                    break
                parsed = pe.parse_sentence(raw)
                if parsed is None:
                    continue
                _talker, styp, fields, ck = parsed
                if ck is False:
                    continue
                if styp == "MWV":
                    mwv = pe.extract_mwv(fields)
                    if mwv is not None and mwv[0] == "R":
                        last_awa, last_aws = mwv[1], mwv[2]
                        last_t = t
                elif styp == "VTG":
                    sog = pe.extract_sog(fields)
                    if sog is not None:
                        last_sog = sog
                        last_t = t
        except OSError:
            return None, None, None, None
        return last_awa, last_aws, last_sog, last_t

    def _sample_absolute_epoch(self, sample):
        """Meilleure estimation de l'horodatage ABSOLU (epoch Unix) d'un
        echantillon de l'entrepot, quelle que soit la passe d'origine --
        necessaire pour _archive_lookup(), qui compare desormais des
        echantillons venant de passes DIFFERENTES (donc potentiellement
        d'unites de temps differentes, voir ci-dessous), None si
        indeterminable.

        - passe en mode DIRECT : sample['t'] est deja un epoch absolu
          (time.time() au moment de la reception), utilise tel quel.
        - passe en mode IMPORT : sample['t'] est un nombre de secondes
          depuis MINUIT tel qu'ecrit dans le fichier .log source (voir
          allure_engine.replay_log_lines) -- ce fichier ne contient JAMAIS
          de date. Faute de mieux, la date du TRAITEMENT de la session
          ('Traiter la session', rec['created_at']) sert d'ancrage -- une
          approximation, signalee a l'utilisateur dans la note de
          l'interface, correcte tant que l'import a lieu le jour meme ou
          peu apres l'enregistrement."""
        t = sample.get("t")
        if t is None:
            return None
        rec = self._session_record(sample.get("session_id"))
        if rec is not None and rec.get("mode") == "import":
            anchor = rec.get("created_at")
            if anchor is None:
                return None
            midnight = time.mktime(time.localtime(anchor)[:3] + (0, 0, 0, 0, 0, -1))
            return midnight + (t % 86400.0)
        return t  # mode direct (ou passe sans 'mode' connu, ex. anciennes donnees) : deja absolu

    # Fenetre de recherche des archives : minutes affichees de part et
    # d'autre de l'horaire demande. +/-10 min = 21 lignes au plus, assez
    # pour choisir sans noyer le tableau.
    ARCHIVE_WINDOW_MIN = 10

    def _buffer_minutes(self, t_from, t_to):
        """Reconstruit depuis le TAMPON GLISSANT une lecture par minute sur
        la plage donnee : {minute: {"t","twa","tws","stw","awa","aws","sog"}}.

        La recherche d'archives interroge ainsi TOUT ce que le bord a capte,
        pas seulement ce qui a ete traite en passes : une minute couverte par
        le tampon mais par aucune passe reste consultable. Le rejeu passe par
        un PolarEngine jetable, avec le meme arbitrage de priorite des voies
        que le direct -- memes trames, memes calculs, memes valeurs. Quelques
        milliers de lignes pour ±10 min : instantane a l'echelle d'un clic."""
        if getattr(self, "buffer", None) is None:
            return {}
        cfg = self.config_data
        engine = pe.PolarEngine(
            smoother=pe.SteadyStateSmoother(
                window_s=cfg["smoothing_window_s"], sample_period_s=cfg["sample_period_s"],
                maneuver_twa_deg=cfg["maneuver_twa_deg"], maneuver_stw_frac=cfg["maneuver_stw_frac"],
                min_fill_frac=cfg["min_fill_frac"]),
            port_priority=cfg.get("priority"),
            source_by_type=cfg.get("sources"),
            allow_fallback=cfg.get("source_fallback", True))
        out = {}
        try:
            lines = self.buffer.iter_range(t_from, t_to)
        except Exception:
            return {}
        for t, port, raw in lines:
            engine.ingest_line(t, raw, port=port)
            inst = engine.last_instant
            # Seule une ligne qui vient de produire une lecture complete
            # (MWV apparent + STW connue) alimente le tableau.
            if inst is None or inst.get("t") != t:
                continue
            minute = int(t // 60)
            mid = minute * 60 + 30
            cur = out.get(minute)
            if cur is None or abs(t - mid) < abs(cur["t"] - mid):
                entry = {"t": t, "twa": inst["twa"], "tws": inst["tws"], "stw": inst["stw"],
                         "awa": None, "aws": None, "sog": None}
                ap = engine.last_apparent
                if ap is not None:
                    entry["awa"], entry["aws"] = ap["awa"], ap["aws"]
                sog = engine.last_sog_reading
                if sog is not None and t - sog["t"] <= 30.0:
                    entry["sog"] = sog["sog"]
                out[minute] = entry
        return out

    def _archive_lookup(self):
        """Remplit le tableau des archives : une ligne par MINUTE mesuree
        dans la fenetre autour de la date/heure demandee (chaque ligne porte
        l'echantillon le plus representatif de sa minute), la plus proche de
        l'instant demande pre-selectionnee. Le detail (AWA/AWS/SOG compris)
        s'affiche sous le tableau pour la ligne selectionnee."""
        try:
            date_epoch = _parse_date(self.archive_date_var.get())
            time_of_day = _parse_hms(self.archive_time_var.get())
        except ValueError as e:
            messagebox.showerror("Archives", str(e))
            return
        target_epoch = date_epoch + time_of_day

        for row in self.archive_tree.get_children(""):
            self.archive_tree.delete(row)
        self._archive_tree_rows = {}
        self.archive_detail_lbl.configure(text="")

        samples = [s for s in self.persistent_store.to_list() if s.get("t") is not None]
        scored = [(self._sample_absolute_epoch(s), s) for s in samples]
        scored = [(e, s) for e, s in scored if e is not None]

        half = self.ARCHIVE_WINDOW_MIN * 60.0
        in_window = [(e, s) for e, s in scored if abs(e - target_epoch) <= half]

        # Une ligne par minute : l'echantillon retenu pour chaque minute est
        # celui le plus proche du milieu de la minute (representatif, stable
        # d'une recherche a l'autre).
        by_minute = {}
        for e, smp in in_window:
            minute = int(e // 60)
            mid = minute * 60 + 30
            cur = by_minute.get(minute)
            if cur is None or abs(e - mid) < abs(cur[0] - mid):
                by_minute[minute] = (e, smp)

        # Le TAMPON GLISSANT complete l'entrepot : les minutes que le tampon
        # couvre mais qu'aucune passe ne mesure deviennent des lignes
        # "(tampon)" -- tout ce que le bord a capte est consultable, meme
        # jamais traite. L'entrepot garde la main sur les minutes communes
        # (ses valeurs sont lissees et rattachees a une passe annotee).
        buffer_minutes = self._buffer_minutes(target_epoch - half, target_epoch + half)
        buffer_only = {m: v for m, v in buffer_minutes.items() if m not in by_minute}

        if not by_minute and not buffer_only:
            # Rien nulle part : dire ou se trouve la mesure la plus proche et
            # ce que couvre le tampon, pour relancer au bon endroit.
            details = [f"Aucune mesure a moins de {self.ARCHIVE_WINDOW_MIN} min de l'horaire "
                       "demande (ni dans l'entrepot, ni dans le tampon glissant)."]
            if scored:
                e_near, _s_near = min(scored, key=lambda pair: abs(pair[0] - target_epoch))
                details.append(
                    f"Mesure d'entrepot la plus proche : "
                    f"{time.strftime('%d/%m/%Y a %H:%M', time.localtime(e_near))} -- relancez la "
                    f"recherche sur cet horaire pour l'explorer.")
            span = self.buffer.span() if getattr(self, "buffer", None) is not None else None
            if span is not None:
                details.append(
                    f"Tampon glissant : du {time.strftime('%d/%m/%Y %H:%M', time.localtime(span[0]))} "
                    f"au {time.strftime('%d/%m/%Y %H:%M', time.localtime(span[1]))}.")
            self.archive_status_lbl.configure(text="aucune mesure dans cette fenetre")
            self.archive_detail_lbl.configure(text="\n".join(details))
            return

        target_minute = int(target_epoch // 60)
        best_iid = None
        best_gap = None
        all_minutes = sorted(set(by_minute) | set(buffer_only))
        for i, minute in enumerate(all_minutes):
            iid = f"arch{i}"
            if minute in by_minute:
                e, smp = by_minute[minute]
                self._archive_tree_rows[iid] = ("entrepot", e, smp)
                rec = self._session_record(smp.get("session_id"))
                passe_txt = rec.get("label", smp.get("session_id")) if rec else (smp.get("session_id") or "?")
                values = (
                    time.strftime("%H:%M", time.localtime(minute * 60)),
                    passe_txt,
                    "+".join(smp.get("sails") or []) or "-",
                    "+".join(smp.get("engines") or []) or "-",
                    f"{smp['stw']:.2f}", f"{smp['twa']:+.0f}", f"{smp['tws']:.2f}",
                )
            else:
                entry = buffer_only[minute]
                self._archive_tree_rows[iid] = ("tampon", entry["t"], entry)
                values = (
                    time.strftime("%H:%M", time.localtime(minute * 60)),
                    "(tampon)", "-", "-",
                    f"{entry['stw']:.2f}", f"{entry['twa']:+.0f}", f"{entry['tws']:.2f}",
                )
            self.archive_tree.insert("", "end", iid=iid, values=values)
            gap = abs(minute - target_minute)
            if best_gap is None or gap < best_gap:
                best_gap, best_iid = gap, iid

        status = (f"{len(all_minutes)} minute(s) dans la fenetre de "
                  f"\u00b1{self.ARCHIVE_WINDOW_MIN} min")
        if buffer_only:
            status += f", dont {len(buffer_only)} du tampon glissant"
        self.archive_status_lbl.configure(text=status)
        if best_iid is not None:
            self.archive_tree.selection_set(best_iid)
            self.archive_tree.see(best_iid)
        # La selection declenche _show_archive_detail via <<TreeviewSelect>>.

    def _show_archive_detail(self):
        """Detail complet de la ligne selectionnee du tableau des archives --
        y compris AWA/AWS/SOG, retrouves en rejouant le fichier brut de la
        passe s'il est encore present (ces grandeurs ne sont jamais stockees
        dans l'entrepot). Le rejeu ne se fait qu'A LA SELECTION : inutile de
        relire un fichier entier pour des lignes qu'on ne regarde pas."""
        sel = self.archive_tree.selection()
        if not sel or sel[0] not in self._archive_tree_rows:
            return
        kind, epoch, smp = self._archive_tree_rows[sel[0]]
        if kind == "tampon":
            # Ligne reconstruite depuis le tampon glissant : tout est deja
            # calcule (y compris AWA/AWS/SOG), aucun rejeu supplementaire.
            awa_txt = f"{smp['awa']:+.0f} deg" if smp.get("awa") is not None else "-"
            aws_txt = f"{smp['aws']:.2f} kn" if smp.get("aws") is not None else "-"
            sog_txt = f"{smp['sog']:.2f} kn" if smp.get("sog") is not None else "-"
            self.archive_detail_lbl.configure(text="\n".join([
                f"Mesure du {time.strftime('%d/%m/%Y a %H:%M:%S', time.localtime(epoch))}   "
                "(source : tampon glissant -- minute couverte par aucune passe)",
                f"STW={smp['stw']:.2f} kn   TWA={smp['twa']:+.0f} deg   TWS={smp['tws']:.2f} kn"
                "   (valeurs instantanees reconstruites des trames brutes)",
                f"AWA={awa_txt}   AWS={aws_txt}   SOG={sog_txt}",
            ]))
            return
        rec = self._session_record(smp.get("session_id"))
        passe_txt = rec.get("label", smp.get("session_id")) if rec else (smp.get("session_id") or "?")
        lines = [
            f"Mesure du {time.strftime('%d/%m/%Y a %H:%M:%S', time.localtime(epoch))}   "
            f"(passe : {passe_txt})",
            f"Configuration : voiles={'+'.join(smp.get('sails') or []) or '-'}   "
            f"moteurs={'+'.join(smp.get('engines') or []) or '-'}",
            f"STW={smp['stw']:.2f} kn   TWA={smp['twa']:+.0f} deg   TWS={smp['tws']:.2f} kn"
            "   (valeurs lissees issues de l'entrepot)",
        ]
        raw_path = rec.get("source") if rec else None
        if raw_path and os.path.exists(raw_path):
            awa, aws, sog, raw_t = self._replay_instant(raw_path, smp["t"])
            if raw_t is None:
                lines.append("AWA/AWS/SOG : aucune trame exploitable trouvee dans le fichier brut "
                              "avant cet horaire.")
            else:
                awa_txt = f"{awa:+.0f} deg" if awa is not None else "-"
                aws_txt = f"{aws:.2f} kn" if aws is not None else "-"
                sog_txt = f"{sog:.2f} kn" if sog is not None else "-"
                lines.append(f"D'apres le fichier brut : AWA={awa_txt}   AWS={aws_txt}   SOG={sog_txt}")
        else:
            # Pas de fichier brut (efface apres traitement, le tampon glissant
            # ayant les memes trames) : le vent APPARENT se recalcule malgre
            # tout, exactement, a partir du TWA/TWS/STW conserves dans
            # l'entrepot -- c'est le meme triangle des vents, parcouru dans
            # l'autre sens. Seule la vitesse fond, qui ne s'en deduit pas,
            # reste indisponible.
            awa_r, aws_r = pe.true_to_apparent(smp["twa"], smp["tws"], smp["stw"])
            if awa_r is not None:
                lines.append(f"Vent apparent recalcule : AWA={awa_r:+.0f} deg   AWS={aws_r:.2f} kn"
                              "   (SOG indisponible : le journal brut de cette passe n'est plus la)")
            else:
                lines.append("AWA/AWS/SOG indisponibles : le journal brut de cette passe n'est plus "
                              "present sur disque.")
        self.archive_detail_lbl.configure(text="\n".join(lines))

    def _show_archive_sheet(self):
        """Fiche complete d'une minute archivee, ouverte au double-clic.

        Reprend le resume affiche sous le tableau, et y ajoute les grandeurs
        d'ambiance choisies dans les Parametres -- moyennees sur une fenetre
        centree (10 min par defaut) relue dans le TAMPON GLISSANT. C'est ce
        qui permet de remplir un journal de passerelle a posteriori : la
        pression et la temperature d'il y a deux heures ne se lisent nulle
        part ailleurs."""
        sel = self.archive_tree.selection()
        if not sel or sel[0] not in self._archive_tree_rows:
            return
        kind, epoch, smp = self._archive_tree_rows[sel[0]]

        dlg = tk.Toplevel(self)
        dlg.title("Detail de la minute")
        dlg.configure(bg=BG_APP)
        dlg.transient(self)
        tk.Label(dlg, text=time.strftime("%d/%m/%Y a %H:%M", time.localtime(epoch)),
                 bg=BG_APP, fg=FG_DIGIT, font=("Segoe UI", 13, "bold")).pack(
            anchor="w", padx=16, pady=(12, 0))
        source_txt = ("reconstruite des trames brutes du tampon" if kind == "tampon"
                      else "mesure de l'entrepot")
        tk.Label(dlg, text=source_txt, bg=BG_APP, fg=FG_LABEL_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16)

        f_pol = section(dlg, "Grandeurs de la polaire")
        tk.Label(f_pol, text=f"STW = {smp['stw']:.2f} kn      TWA = {smp['twa']:+.0f} deg"
                              f"      TWS = {smp['tws']:.2f} kn",
                 bg=BG_APP, fg=FG_LABEL, font=FONT_MONO, anchor="w").pack(
            fill="x", padx=6, pady=(2, 6))
        if kind != "tampon":
            tk.Label(f_pol, text=f"Voiles : {'+'.join(smp.get('sails') or []) or '-'}      "
                                  f"Moteurs : {'+'.join(smp.get('engines') or []) or '-'}",
                     bg=BG_APP, fg=FG_LABEL, font=FONT_MONO, anchor="w").pack(
                fill="x", padx=6, pady=(0, 6))

        keys = [k for k in (self.config_data.get("archive_detail_fields") or [])
                if k in pe.AMBIENT_LABELS]
        win_min = self.config_data.get("archive_detail_window_min", 10)
        f_amb = section(dlg, f"Conditions (moyenne sur {win_min} min)")
        if not keys:
            note(f_amb, "Aucune grandeur d'ambiance selectionnee. Choisissez-les dans la roue "
                        "dentee > Lissage & table -- la liste ne propose que ce que l'analyse des "
                        "voies a reellement trouve a bord.", wraplength="auto")
        else:
            half = win_min * 30.0     # demi-fenetre, en secondes
            found = {}
            if getattr(self, "buffer", None) is not None:
                try:
                    found = pe.ambient_over_range(
                        self.buffer.iter_range(epoch - half, epoch + half), keys=keys)
                except Exception:
                    found = {}
            for key in keys:
                if key in found:
                    txt, color = pe.format_ambient(key, found[key]), FG_LABEL
                else:
                    label = pe.AMBIENT_LABELS[key][0]
                    txt = f"{label} : non trouvee dans le tampon sur cette plage"
                    color = FG_LABEL_DIM
                tk.Label(f_amb, text=txt, bg=BG_APP, fg=color, font=FONT_MONO,
                         anchor="w").pack(fill="x", padx=6, pady=1)
            tk.Frame(f_amb, bg=BG_APP, height=4).pack()

        f_wind = section(dlg, "Vent apparent")
        awa_txt = aws_txt = sog_txt = "-"
        if kind == "tampon":
            if smp.get("awa") is not None:
                awa_txt = f"{smp['awa']:+.0f} deg"
                aws_txt = f"{smp['aws']:.2f} kn"
            if smp.get("sog") is not None:
                sog_txt = f"{smp['sog']:.2f} kn"
        else:
            awa_r, aws_r = pe.true_to_apparent(smp["twa"], smp["tws"], smp["stw"])
            if awa_r is not None:
                awa_txt, aws_txt = f"{awa_r:+.0f} deg", f"{aws_r:.2f} kn"
        tk.Label(f_wind, text=f"AWA = {awa_txt}      AWS = {aws_txt}      SOG = {sog_txt}",
                 bg=BG_APP, fg=FG_LABEL, font=FONT_MONO, anchor="w").pack(
            fill="x", padx=6, pady=(2, 6))

        tk.Button(dlg, text="Fermer", command=dlg.destroy, padx=14, pady=3).pack(
            anchor="e", padx=16, pady=(8, 12))
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        self._center_on_parent(dlg)
        # Theme sombre : le bouton Fermer naissait clair.
        self._theme_new_window(dlg)

    # ---------------------------------------------------------------
    # Etape 3 : Polaires -- la polaire MAX exploitable, directement.
    # Pour chaque case cap/vent, la meilleure vitesse parmi les
    # configurations cochees l'emporte -- et l'on retient LAQUELLE, ce qui
    # donne en prime le guide de voilure ("pour ce cap et ce vent, portez
    # ca"). Une seule configuration cochee, et c'est SA polaire : la vue par
    # configuration n'est qu'un cas particulier de la polaire max, elle n'a
    # plus d'ecran a part. Les trous interieurs sont bouches par
    # interpolation le long du TWA (jamais d'extrapolation au-dela des
    # mesures).
    #
    # Le trace est AUTOMATIQUE : arriver sur l'etape, cocher une
    # configuration ou enregistrer de nouveaux parametres retrace aussitot.
    # Les reglages de la table (case, symetrie, statistique) vivent dans les
    # Parametres, categorie "Lissage & table" : on les fixe une fois, ce ne
    # sont pas des commandes de trace.
    #
    # LISIBILITE D'ABORD : le trace dispose de toute la hauteur de la page.
    # Rien dont la taille depend du nombre de configurations (leur liste,
    # leurs codes) ou du nombre de vents (la legende) ne s'empile au-dessus
    # ou au-dessous de lui : la liste vit dans la colonne de gauche avec sa
    # propre barre de defilement, la legende des vents a droite du cercle,
    # le guide de voilure dans une vue voisine, A LA PLACE du trace. Un
    # bateau a vingt configurations a donc la meme polaire, a la meme
    # taille, qu'un bateau a deux.
    # ---------------------------------------------------------------

    # Largeur de la colonne de gauche, en pixels : assez pour lire une
    # configuration sur une ou deux lignes, pas au point de manger le trace.
    MAX_LEFT_WIDTH = 300

    def _build_step_polaires(self, parent):
        self._max_cfg_vars = []       # [(BooleanVar, (voiles, moteurs), libelle)]
        self._max_cfg_code = {}       # (voiles, moteurs) -> "C1", "C2"...
        self._max_checked = {}        # coches memorisees d'une reconstruction a l'autre
        self._max_all_configs = []    # [((voiles, moteurs), effectif)] de l'entrepot
        self._max_families = {}       # propulsion -> cochee (voir _max_family_of)
        self._max_fam_vars = {}
        self._max_fam_labels = []     # [(propulsion, libelle, nombre de voilures)]
        self._max_codes = []          # codes des configurations de la table courante
        self._max_result = None       # (twa, tws, grid, counts, winners, mask, labels, clipped)
        self._max_hidden_tws = set()  # vents masques d'un clic dans la legende
        self._max_legend = None
        self._max_legend_map = {}     # artiste de la legende -> vent qu'il designe
        self._max_legend_px = 0       # largeur reservee a droite pour la legende
        self._max_legend_click = False
        self._max_pick_mouse = None
        self._max_view = None         # None = ajuste a la page
        self._max_drag = None
        self._max_press_xy = None
        self._max_detail_win = None
        self._max_detail_cell = None
        self._max_conf = []           # confiance de chaque case (gagnante), ou None

        # --- Pied de page : les EXPORTS, sur toute la largeur ---
        # Pose EN PREMIER et en side="bottom" : Tk sert les bords avant le
        # centre, ces boutons ne peuvent donc pas etre repousses hors de la
        # page par quoi que ce soit au-dessus.
        footer = tk.Frame(parent, bg=BG_PANEL)
        footer.pack(side="bottom", fill="x")
        tk.Frame(parent, bg=COLOR_BORDER, height=1).pack(side="bottom", fill="x")
        foot_in = tk.Frame(footer, bg=BG_PANEL)
        foot_in.pack(fill="x", padx=12, pady=8)
        # Une rangee par ligne de boutons (voir _layout_exports), creees
        # AVANT les boutons : un cadre cree apres eux passerait au-dessus
        # d'eux et les masquerait.
        foot_rows = [tk.Frame(foot_in, bg=BG_PANEL) for _k in range(4)]
        exports = [
            tk.Button(foot_in, text="Exporter .pol (routage)",
                      command=self._max_polar_export_pol, bg=COLOR_ACCENT, fg=FG_ON_ACCENT,
                      font=FONT_LABEL_BOLD, padx=14, pady=4, cursor="hand2"),
            tk.Button(foot_in, text="Exporter .csv (vitesses + voilure gagnante)",
                      command=self._max_polar_export_csv, padx=14, pady=4,
                      cursor="hand2"),
            tk.Button(foot_in, text="Exporter TimeZero (2 fichiers .xml)",
                      command=self._max_polar_export_timezero, padx=14, pady=4,
                      cursor="hand2"),
            # A part, a droite : un export pour le tableur, une polaire par
            # configuration avec son indice de confiance -- pas une table de
            # routage.
            tk.Button(foot_in, text="Exporter .csv (une polaire par configuration)",
                      command=self._export_csv_all, padx=14, pady=4,
                      cursor="hand2"),
        ]
        # Les quatre sur UNE ligne quand elles tiennent, sinon sur autant de
        # lignes qu'il faut : sur une seule ligne coute que coute, la
        # derniere etait rognee a 125/150 % ou en fenetre etroite ("ter .csv
        # (une polaire par configur"). Chaque ligne est un cadre ou les
        # boutons sont poses (pack in_) dans l'ordre.
        foot_layout = {"key": None}

        def _layout_exports(_e=None):
            avail = foot_in.winfo_width()
            if avail <= 1:
                return
            gap = 8
            rows, used = [[]], 0
            for b in exports:
                need = b.winfo_reqwidth() + (gap if rows[-1] else 0)
                if rows[-1] and used + need > avail:
                    rows.append([])
                    need = b.winfo_reqwidth()
                    used = 0
                rows[-1].append(b)
                used += need
            key = tuple(len(r) for r in rows)
            if key == foot_layout["key"]:
                return
            foot_layout["key"] = key
            for f in foot_rows:
                f.pack_forget()
            for b in exports:
                b.pack_forget()
            for f, row in zip(foot_rows, rows):
                f.pack(side="top", fill="x", pady=(0 if f is foot_rows[0] else 6, 0))
                for k, b in enumerate(row):
                    if len(rows) == 1 and b is exports[-1]:
                        # Tout tient : le dernier reste a part, a droite.
                        b.pack(in_=f, side="right")
                    else:
                        b.pack(in_=f, side="left", padx=(0 if k == 0 else gap, 0))

        foot_in.bind("<Configure>", _layout_exports, add="+")
        foot_rows[0].pack(side="top", fill="x")
        for b in exports[:-1]:
            b.pack(in_=foot_rows[0], side="left", padx=(0 if b is exports[0] else 8, 0))
        exports[-1].pack(in_=foot_rows[0], side="right")

        # Colonne de gauche et trace, separes par une POIGNEE qu'on tire a la
        # souris. La largeur utile a la colonne depend des noms de voilure,
        # des codes moteur et de l'ecran : aucune valeur figee ne convient a
        # tous -- une colonne de largeur fixe rognait ce qui depassait, quelle
        # que soit la taille de la fenetre. La largeur proposee suit l'echelle
        # de l'ecran ; celle qu'on choisit est retenue.
        scale = self._ui_scale()
        paned = tk.PanedWindow(parent, orient="horizontal", bg=COLOR_BORDER, bd=0,
                               sashwidth=max(5, int(5 * scale)), sashrelief="flat",
                               showhandle=False, opaqueresize=True)
        paned.pack(fill="both", expand=True, padx=10)
        self._max_left_pane = tk.Frame(paned, bg=BG_APP)
        right_pane = tk.Frame(paned, bg=BG_APP)
        min_left = int(220 * scale)
        try:
            width = int(self.ui_state.get("polar_left_width") or 0)
        except (TypeError, ValueError):
            width = 0
        if width < min_left:
            width = int(self.MAX_LEFT_WIDTH * scale)
        paned.add(self._max_left_pane, minsize=min_left, width=width, stretch="never")
        paned.add(right_pane, minsize=int(380 * scale), stretch="always")
        paned.bind("<ButtonRelease-1>", lambda _e: self._max_remember_left_width(),
                   add="+")
        # La colonne entiere DEFILE quand la fenetre est trop basse pour elle
        # (ecran zoome, 150 %, bandeau manoeuvre) : sinon le packer ecrasait
        # la liste des configurations, posee en dernier, jusqu'a la faire
        # disparaitre sous les deux blocs du bas. Assez haute, elle remplit
        # la hauteur et c'est la liste qui s'etire.
        left_outer, left = make_scrollable(self._max_left_pane, height=1, fill_height=True)
        left_outer.pack(fill="both", expand=True, pady=(0, 6))
        right = tk.Frame(right_pane, bg=BG_APP)
        right.pack(fill="both", expand=True, padx=(6, 0), pady=(10, 6))

        # Colonne de gauche : les deux blocs du BAS sont poses en premier, en
        # side="bottom" -- ils gardent leur place quoi qu'il arrive, et c'est
        # la liste des configurations, seule partie dont la hauteur depend
        # des donnees, qui prend le reste et defile au besoin. Pas de notes
        # explicatives ici (le README les porte, et la fiche d'un point dit
        # ce qu'interpolation et ecretage ont fait de sa case) : chaque ligne
        # prise a ces blocs l'est a la liste, et sur un portable elle n'avait
        # plus la place d'afficher une seule configuration.
        wrap = int(self.MAX_LEFT_WIDTH * scale) - 50
        f_ask = section(left, "Quelle voilure pour ce cap / ce vent ?", side="bottom")
        f_opt = section(left, "Trous et pointes", side="bottom")
        # Le choix des configurations : UNE liste defilante de cases. Chaque
        # PROPULSION (voiles seules, voiles + 1ME, voiles + 1SB...) a sa case,
        # et ses voilures suivent en retrait quand elle est cochee -- on
        # combine les propulsions a volonte, puis on affine voilure par
        # voilure. Une seule colonne, des libelles qui passent a la ligne :
        # rien ne peut deborder ni etre rogne, a aucune largeur.
        f_cfg = section(left, "Configurations en concurrence", expand=True)

        btn_row = tk.Frame(f_cfg, bg=BG_APP)
        btn_row.pack(fill="x", padx=6, pady=(2, 0))
        tk.Button(btn_row, text="Tout cocher", command=lambda: self._max_check_all(True),
                  padx=6, cursor="hand2").pack(side="left")
        tk.Button(btn_row, text="Tout decocher", command=lambda: self._max_check_all(False),
                  padx=6, cursor="hand2").pack(side="left", padx=(6, 0))
        note(f_cfg, "Cochez les propulsions a comparer, puis leurs voilures. "
                    "Double-clic sur une voilure : elle seule.", pady=(2, 2),
             wraplength="auto")
        # La liste garde au moins quelques lignes (hauteur DEMANDEE) : si la
        # colonne n'a pas la place, c'est elle qui defile en entier -- la
        # liste ne peut plus disparaitre sous les deux blocs du bas.
        row_h = tkfont.Font(font=FONT_LABEL).metrics("linespace") + self._px(10)
        cfg_outer, self._max_cfg_box = make_scrollable(f_cfg, height=4 * row_h)
        cfg_outer.pack(fill="both", expand=True, padx=(4, 2), pady=(0, 4))
        self._max_cfg_box.bind("<Configure>",
                               lambda e: self._max_rewrap_list(e.width), add="+")

        self._max_fill_var = tk.BooleanVar(value=True)
        self._max_fill_cb = tk.Checkbutton(
            f_opt, text="Boucher les trous (interpolation)",
            variable=self._max_fill_var, bg=BG_APP, fg=FG_LABEL,
            selectcolor=BG_PANEL, activebackground=BG_APP, font=FONT_LABEL,
            anchor="w", justify="left", wraplength=wrap,
            command=self._max_polar_recompute)
        self._max_fill_cb.pack(fill="x", padx=6)

        clip_row = tk.Frame(f_opt, bg=BG_APP)
        clip_row.pack(fill="x", padx=6, pady=(0, 4))
        self._max_clip_var = tk.BooleanVar(
            value=bool(self.config_data.get("max_polar_clip_enabled", False)))
        tk.Checkbutton(clip_row, text="Ecreter au-dela de",
                       variable=self._max_clip_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP, font=FONT_LABEL,
                       command=self._max_polar_recompute).pack(side="left")
        self._max_clip_slope_var = tk.DoubleVar(
            value=float(self.config_data.get("max_polar_clip_slope", 1.5)))
        sp = tk.Spinbox(clip_row, from_=0.1, to=10.0, increment=0.1, width=4,
                        textvariable=self._max_clip_slope_var,
                        command=self._max_polar_recompute)
        sp.pack(side="left", padx=(4, 4))
        sp.bind("<Return>", lambda _e: self._max_polar_recompute())
        sp.bind("<FocusOut>", lambda _e: self._max_polar_recompute())
        tk.Label(clip_row, text="nd / 10°", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        # Compte rendu de l'ecretage : affiche seulement quand il a quelque
        # chose a dire (voir _max_polar_recompute) -- vide, il prendrait une
        # ligne a la liste pour rien.
        self._max_clip_lbl = tk.Label(f_opt, text="", bg=BG_APP, fg=COLOR_ACCENT,
                                       font=("Segoe UI", 8), anchor="w", wraplength=wrap,
                                       justify="left")

        ask_row = tk.Frame(f_ask, bg=BG_APP)
        ask_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(ask_row, text="TWA :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self._max_ask_twa = tk.Entry(ask_row, width=5)
        self._max_ask_twa.pack(side="left", padx=(4, 8))
        tk.Label(ask_row, text="TWS :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self._max_ask_tws = tk.Entry(ask_row, width=5)
        self._max_ask_tws.pack(side="left", padx=(4, 8))
        tk.Button(ask_row, text="Chercher", command=self._max_polar_lookup,
                  padx=8).pack(side="left")
        for w in (self._max_ask_twa, self._max_ask_tws):
            w.bind("<Return>", lambda _e: self._max_polar_lookup())
        self._max_ask_result = tk.Label(f_ask, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_LABEL_BOLD, wraplength=wrap,
                                         justify="left", anchor="w")
        # Pose a la premiere reponse (voir _max_ask_say) : vide, il prendrait
        # une ligne a la liste des voilures pour rien.
        # La case a cocher retire en plus son indicateur et ses marges (pres
        # de 30 px a 100 %, qui grandissent avec l'echelle) : a la meme marge
        # que les libelles, "Boucher les trous (interpolation)" etait rogne.
        rewrap = (self._max_clip_lbl, self._max_ask_result)

        def _rewrap_left(e):
            for w in rewrap:
                w.configure(wraplength=max(120, e.width - 50))
            self._max_fill_cb.configure(wraplength=max(120, e.width - 40 - self._px(30)))

        left.bind("<Configure>", _rewrap_left, add="+")

        # Largeur minimale de la colonne = celle de ses rangees fixes (TWA /
        # TWS / Chercher, Ecreter au-dela de..., les deux boutons, les titres
        # des blocs), et non une valeur devinee : en dessous, "Chercher" et
        # "nd / 10" etaient ecrases. Mesuree une fois les rangees calculees.
        def _left_min_width():
            title_font = tkfont.Font(font=FONT_LABEL_BOLD)
            need = max([r.winfo_reqwidth() + 12 for r in (ask_row, clip_row, btn_row)]
                       + [title_font.measure(f.cget("text")) + 24
                          for f in (f_ask, f_opt, f_cfg)])
            # Cadres des blocs (marges de 10 px et bordure) et ascenseur de
            # la colonne, quand elle defile.
            need = max(min_left, need + 24 + self._px(18))
            try:
                paned.paneconfigure(self._max_left_pane, minsize=need)
                if int(paned.panecget(self._max_left_pane, "width") or 0) < need:
                    paned.paneconfigure(self._max_left_pane, width=need)
            except (tk.TclError, ValueError):
                pass

        self.after_idle(_left_min_width)

        # --- Colonne de droite : un en-tete court, puis la vue ---
        head = tk.Frame(right, bg=BG_APP)
        head.pack(side="top", fill="x")
        # Trace et guide de voilure : deux vues A LA MEME PLACE plutot
        # qu'empilees -- pose sous le trace, le guide lui volait sa hauteur.
        self._max_view_btns = {}
        for key, text in (("guide", "Guide de voilure"), ("polaire", "Polaire")):
            b = tk.Button(head, text=text, relief="flat", bd=0, padx=12, pady=3,
                          cursor="hand2", command=lambda k=key: self._max_show_view(k))
            b.pack(side="right", padx=(4, 0))
            self._max_view_btns[key] = b
        self._max_title_lbl = tk.Label(head, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=FONT_LABEL_BOLD, anchor="w", justify="left")
        self._max_title_lbl.pack(side="left", fill="x", expand=True)

        def _rewrap_title(_e=None):
            # La largeur REELLE des boutons de vue, et non 250 px fixes : a
            # 150 % ils en font plus de 300, et le titre perdait sa fin.
            # Rappelee au changement de vue (le bouton actif passe en gras).
            if head.winfo_width() <= 1:
                return
            btns = sum(b.winfo_reqwidth() + 4 for b in self._max_view_btns.values())
            self._max_title_lbl.configure(
                wraplength=max(120, head.winfo_width() - btns - 12))

        self._max_title_rewrap = _rewrap_title
        head.bind("<Configure>", _rewrap_title)
        # Aide du TRACE : posee par _max_show_view dans la vue Polaire
        # seulement.
        self._max_zoom_lbl = tk.Label(right, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                       font=("Segoe UI", 8), anchor="w", justify="left")
        right.bind("<Configure>", lambda e: self._max_zoom_lbl.configure(
            wraplength=max(200, e.width - 12)))
        views = tk.Frame(right, bg=BG_APP)
        views.pack(side="top", fill="both", expand=True)
        self._max_views_frame = views

        # Vue 1 : le trace.
        plot_frame = tk.Frame(views, bg=BG_APP)
        self._max_fig = Figure(figsize=(7, 5), dpi=100, facecolor=BG_APP)
        self._max_ax = self._max_fig.add_subplot(111, projection="polar", facecolor=BG_PANEL)
        self._max_canvas = FigureCanvasTkAgg(self._max_fig, master=plot_frame)
        self._max_canvas.get_tk_widget().configure(bg=BG_APP, highlightthickness=0)
        # La molette y zoome (voir _on_max_polar_scroll) : l'aiguilleur commun
        # ne doit rien faire defiler a sa place.
        self._max_canvas.get_tk_widget()._allure_wheel_owner = True
        self._max_canvas.get_tk_widget().pack(fill="both", expand=True)
        # Infobulle de SURVOL : une polaire porte des centaines de cases, et
        # aller chercher chacune dans un tableau casse le fil de la lecture.
        # Sous le curseur, la case dit tout ce qu'elle sait d'elle-meme --
        # vitesse, voilure gagnante, origine, confiance.
        self._max_tip = tk.Label(plot_frame, text="", bg=BG_PANEL, fg=FG_LABEL,
                                  font=("Segoe UI", 8), justify="left", anchor="w",
                                  relief="solid", borderwidth=1, padx=6, pady=4)
        self._max_canvas.mpl_connect("motion_notify_event", self._on_max_polar_hover)
        self._max_canvas.mpl_connect("figure_leave_event",
                                      lambda _e: self._max_tip.place_forget())
        # ZOOM COMME UNE IMAGE : la molette agrandit le dessin, le glisser
        # s'y promene. Une premiere version zoomait l'echelle des vitesses
        # (le rayon) -- techniquement plus simple, mais ce n'est pas ce
        # qu'on veut d'une polaire : resserrer le rayon ne grossit pas les
        # etiquettes ni l'ecart entre deux cases voisines au pres, qui est
        # justement ce qu'on cherche a lire de plus pres. Ici c'est le CADRE
        # du trace qu'on agrandit dans la figure : tout grossit ensemble, y
        # compris les graduations, et ce qui deborde est simplement rogne.
        self._max_canvas.mpl_connect("scroll_event", self._on_max_polar_scroll)
        self._max_canvas.mpl_connect("button_press_event", self._on_max_polar_press)
        self._max_canvas.mpl_connect("button_release_event", self._on_max_polar_release)
        self._max_canvas.mpl_connect("pick_event", self._on_max_legend_pick)
        self._max_canvas.get_tk_widget().bind(
            "<Double-1>", lambda _e: self._reset_max_polar_zoom())
        # Le cadre carre se deduit des proportions de la FIGURE : redimensionner
        # la fenetre les change, la vue doit donc se reposer -- et la legende
        # peut avoir a changer de nombre de colonnes. Sur le resize_event de
        # matplotlib et non sur le <Configure> de Tk : au premier affichage a
        # 125/150 %, matplotlib change la dpi de la figure SANS <Configure>,
        # et le cadre calcule avant debordait (etiquette 0 deg rognee).
        self._max_canvas.mpl_connect(
            "resize_event", lambda _e: (self._max_fit_legend(), self._apply_max_view()))

        # Vue 2 : le guide de voilure.
        guide_frame = tk.Frame(views, bg=BG_APP)
        note(guide_frame, "Configuration gagnante par case -- les codes C1, C2... "
                          "sont ceux de la colonne de gauche ; ~ = case interpolee. "
                          "Cliquez une case pour la fiche detaillee du point.",
             pady=(0, 4), wraplength="auto")
        gwrap = tk.Frame(guide_frame, bg=BG_APP)
        gwrap.pack(side="top", fill="both", expand=True)
        self._max_guide = ttk.Treeview(gwrap, show="headings")
        guide_sb_y = ttk.Scrollbar(gwrap, orient="vertical", command=self._max_guide.yview)
        guide_sb_x = ttk.Scrollbar(gwrap, orient="horizontal", command=self._max_guide.xview)
        self._max_guide.configure(yscrollcommand=guide_sb_y.set,
                                  xscrollcommand=guide_sb_x.set)
        guide_sb_y.pack(side="right", fill="y")
        guide_sb_x.pack(side="bottom", fill="x")
        self._max_guide.pack(side="left", fill="both", expand=True)
        # CLIC sur une case du guide (ou du trace) : la fiche complete du
        # point s'ouvre -- voir _open_max_cell_details.
        self._max_guide.bind("<Button-1>", self._on_max_guide_click)

        self._max_views = {"polaire": plot_frame, "guide": guide_frame}
        self._max_show_view("polaire")
        self._refresh_config_choices()

    def _max_show_view(self, which):
        """Trace ou guide de voilure, a la meme place : la vue choisie prend
        toute la hauteur disponible."""
        for key, frame in self._max_views.items():
            if key == which:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        # L'aide du trace (molette, glisser, legende...) ne vaut que pour le
        # trace : au-dessus du guide, elle lui prenait 2 a 3 lignes a 150 %.
        if which == "polaire":
            self._max_zoom_lbl.pack(side="top", fill="x", pady=(2, 4),
                                    before=self._max_views_frame)
        else:
            self._max_zoom_lbl.pack_forget()
        for key, btn in self._max_view_btns.items():
            on = (key == which)
            btn.configure(bg=BG_NAV_ACTIVE if on else BG_NAV,
                          fg=FG_ON_ACCENT if on else FG_LABEL,
                          activebackground=BG_NAV_ACTIVE if on else BG_NAV,
                          font=FONT_LABEL_BOLD if on else FONT_LABEL)
        # Le bouton actif passe en gras, donc s'elargit : le titre a cote
        # se replie en consequence.
        rewrap = getattr(self, "_max_title_rewrap", None)
        if rewrap is not None:
            rewrap()

    # Propulsions de l'etape Polaires : "Voiles seules", puis "Voiles + X"
    # pour CHAQUE configuration moteur declaree dans les Parametres (Voiles
    # & moteurs) -- proposees d'office, dans leur ordre, rien n'est fige ici.
    # Ce sont des CASES : on en coche autant qu'on veut (voiles seules ET
    # voiles + 1SB, par exemple) ; toutes cochees, voiles et moteurs
    # concourent ensemble -- le moteur fait alors partie de la voilure.
    MAX_FAMILY_PURE = "Voiles seules"

    def _max_family_of(self, engines):
        """Propulsion d'une configuration, d'apres ses moteurs : "Voiles
        seules", "Voiles + 1ME", ou "Voiles + 1ME + 1SB" pour une combinaison
        (codes dans l'ordre des Parametres)."""
        if not engines:
            return self.MAX_FAMILY_PURE
        order = [str(c) for c in self.config_data.get("engine_list") or []]
        codes = sorted(engines, key=lambda c: (order.index(c) if c in order else len(order), c))
        return "Voiles + " + " + ".join(codes)

    def _max_family_options(self):
        """[(propulsion, libelle, nombre de voilures)] : "Voiles seules",
        puis une propulsion par configuration moteur des Parametres, dans
        leur ordre et MEME sans mesure encore (elle s'affiche alors grisee) ;
        enfin les combinaisons de moteurs et les codes que seules d'anciennes
        passes portent encore -- une voilure mesuree ne doit jamais rester
        sans case."""
        engine_list = [str(c) for c in self.config_data.get("engine_list") or []]
        count_of = {}
        for cfg, _n in self._max_all_configs:
            fam = self._max_family_of(cfg[1])
            count_of[fam] = count_of.get(fam, 0) + 1
        order = [self.MAX_FAMILY_PURE]
        for code in engine_list:
            fam = self._max_family_of((code,))
            if fam not in order:
                order.append(fam)
        order += sorted((f for f in count_of if f not in order),
                        key=lambda f: (f.count("+"), f))
        return [(fam, f"{fam} ({count_of.get(fam, 0)})", count_of.get(fam, 0))
                for fam in order]

    def _refresh_config_choices(self):
        """Reconstruit la liste des propulsions et de leurs voilures --
        l'entrepot ou les passes incluses ont change -- en gardant ce qui
        etait coche, puis retrace si l'etape est sous les yeux. Sinon le
        trace attendra qu'on y arrive (voir _show_step) : inutile de calculer
        une polaire que personne ne regarde."""
        if hasattr(self, "store_size_lbl"):
            self._refresh_store_size_label()
        if not hasattr(self, "_max_cfg_box"):
            return
        self._max_all_configs = self.persistent_store.configs(
            session_ids=self._included_session_ids())
        # Code STABLE : le rang de la configuration dans tout l'entrepot, et
        # non parmi les cochees -- decocher C2 ou une propulsion ne renomme
        # pas C3, et le guide, l'infobulle et la fiche d'un point parlent la
        # meme langue que la liste.
        self._max_cfg_code = {cfg: f"C{k + 1}"
                              for k, (cfg, _n) in enumerate(self._max_all_configs)}
        self._max_fam_labels = self._max_family_options()
        families = [fam for fam, _lbl, n in self._max_fam_labels if n]
        # Propulsions cochees : le dernier choix fait ici, sinon le reglage
        # "le moteur fait partie de la voilure" (Parametres > Lissage &
        # table) -- toutes s'il est coche, "Voiles seules" sinon. Le choix est
        # memorise AVEC le reglage sous lequel il a ete fait : modifier ce
        # reglage le remplace, meme si l'on quitte sans repasser par ici.
        engine_in = bool(self.config_data.get("max_polar_include_engine", True))
        if self.ui_state.get("polar_propulsion_rule", engine_in) != engine_in:
            for key in ("polar_propulsions", "polar_propulsions_off", "polar_propulsion",
                        "polar_propulsion_rule"):
                self.ui_state.pop(key, None)
        remembered = self.ui_state.get("polar_propulsions")
        off = self.ui_state.get("polar_propulsions_off")
        old = self.ui_state.pop("polar_propulsion", None)
        if not isinstance(remembered, list) and isinstance(old, str):
            # Choix unique memorise par la 2.1b (boutons radio) : repris, et
            # reecrit sous la forme d'aujourd'hui.
            remembered = list(families) if old == "Tout confondu" else [old]
            off = [f for f in families if f not in remembered]
            self.ui_state["polar_propulsions"] = remembered
            self.ui_state["polar_propulsions_off"] = off
            self.ui_state["polar_propulsion_rule"] = engine_in
        def default_on(fam):
            return engine_in or self.MAX_FAMILY_PURE not in families or fam == self.MAX_FAMILY_PURE

        if isinstance(remembered, list) and isinstance(off, list):
            # Cochees ET decochees sont retenues : une propulsion qui n'est ni
            # l'une ni l'autre est NOUVELLE (premiere passe sur un code moteur,
            # nouvelle combinaison) et prend le defaut du reglage -- elle ne
            # doit pas sortir du calcul en silence.
            self._max_families = {f: (f in remembered) or (f not in off and default_on(f))
                                  for f in families}
        elif isinstance(remembered, list) and any(f in families for f in remembered):
            # Memoire d'une version anterieure (cochees seules). Celle qui ne
            # designe plus AUCUNE propulsion actuelle ouvrirait l'etape sur une
            # liste toute decochee : le defaut reprend alors la main.
            self._max_families = {f: f in remembered for f in families}
        elif engine_in or self.MAX_FAMILY_PURE not in families:
            self._max_families = {f: True for f in families}
        else:
            self._max_families = {f: f == self.MAX_FAMILY_PURE for f in families}
        # Une propulsion cochee dont aucune voilure ne l'est n'apporterait
        # rien : ses voilures reviennent cochees (meme regle qu'au clic).
        for var, cfg, _lbl in self._max_cfg_vars:
            self._max_checked[cfg] = bool(var.get())
        for fam, on in self._max_families.items():
            mine = [c for c, _n in self._max_all_configs if self._max_family_of(c[1]) == fam]
            if on and mine and not any(self._max_checked.get(c, True) for c in mine):
                for c in mine:
                    self._max_checked[c] = True
        self._max_cfg_vars = []
        self._max_rebuild_cfg_list()

    def _max_save_families(self):
        """Les propulsions cochees sont retenues d'un lancement a l'autre,
        avec le reglage sous lequel elles l'ont ete."""
        self.ui_state["polar_propulsions"] = [f for f, on in self._max_families.items() if on]
        self.ui_state["polar_propulsions_off"] = [f for f, on in self._max_families.items()
                                                  if not on]
        self.ui_state["polar_propulsion_rule"] = bool(
            self.config_data.get("max_polar_include_engine", True))
        try:
            pcfg.save_ui_state(self.ui_state)
        except OSError:
            pass

    def _max_toggle_family(self, family):
        """Case d'une propulsion : cochee, ses voilures apparaissent sous elle
        et concourent ; decochee, elles se replient et sortent du calcul."""
        on = bool(self._max_fam_vars[family].get())
        self._max_families[family] = on
        for var, cfg, _lbl in self._max_cfg_vars:
            self._max_checked[cfg] = bool(var.get())
        if on:
            # Une propulsion cochee dont aucune voilure ne l'est n'apporterait
            # rien : on les coche toutes.
            mine = [cfg for cfg, _n in self._max_all_configs
                    if self._max_family_of(cfg[1]) == family]
            if not any(self._max_checked.get(cfg, True) for cfg in mine):
                for cfg in mine:
                    self._max_checked[cfg] = True
        self._max_save_families()
        self._max_rebuild_cfg_list()

    def _max_rebuild_cfg_list(self):
        """UNE liste defilante de cases : chaque propulsion, puis -- si elle
        est cochee -- ses voilures, en retrait, cochees comme on les avait
        laissees (toutes la premiere fois). Une seule colonne, chaque libelle
        passe a la ligne plutot que d'etre coupe : rien ne peut deborder de la
        largeur, quelle qu'elle soit."""
        for var, cfg, _lbl in self._max_cfg_vars:
            self._max_checked[cfg] = bool(var.get())
        box = self._max_cfg_box
        for child in box.winfo_children():
            child.destroy()
        self._max_cfg_vars = []
        self._max_fam_vars = {}
        for k, (fam, text, n_cfg) in enumerate(self._max_fam_labels):
            fvar = tk.BooleanVar(value=bool(n_cfg) and self._max_families.get(fam, False))
            self._max_fam_vars[fam] = fvar
            # Une configuration moteur des Parametres sans aucune passe encore
            # garde sa case, grisee : la liste dit ce que le bord PEUT
            # comparer, pas seulement ce qu'il a deja mesure.
            head = tk.Checkbutton(box, text=text, variable=fvar,
                                  command=lambda f=fam: self._max_toggle_family(f),
                                  bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                                  activebackground=BG_APP, font=FONT_LABEL_BOLD,
                                  anchor="w", justify="left",
                                  state=("normal" if n_cfg else "disabled"))
            head.pack(fill="x", padx=2, pady=((0 if k == 0 else 6), 0))
            head._wrap_margin = 40
            if not fvar.get():
                continue
            for cfg, count in self._max_all_configs:
                if self._max_family_of(cfg[1]) != fam:
                    continue
                sails, engines = cfg
                label = f"{'+'.join(sails) or '-'} / {'+'.join(engines) or '-'}  (n={count})"
                var = tk.BooleanVar(value=self._max_checked.get(cfg, True))
                cb = tk.Checkbutton(box, text=f"{self._max_cfg_code[cfg]}  {label}",
                                    variable=var, bg=BG_APP, fg=FG_LABEL,
                                    selectcolor=BG_PANEL, activebackground=BG_APP,
                                    font=FONT_LABEL, anchor="w", justify="left",
                                    command=self._max_polar_recompute)
                cb.pack(fill="x", padx=(22, 2))
                cb._wrap_margin = 64
                # "break" : le second clic du double-clic ne doit pas
                # re-basculer la case que le double-clic vient de laisser
                # seule cochee.
                cb.bind("<Double-Button-1>",
                        lambda _e, c=cfg: (self._max_solo(c), "break")[1])
                self._max_cfg_vars.append((var, cfg, label))
        self._max_list_width = None
        self._max_rewrap_list()
        if getattr(self, "_current_step", None) == "polaires":
            self._max_polar_recompute()

    def _max_rewrap_list(self, width=None):
        """Retour a la ligne des libelles de la liste, a la largeur REELLE de
        la colonne -- qu'on peut elargir a la souris (voir la poignee)."""
        box = self._max_cfg_box
        if width is None:
            width = box.winfo_width()
        if width <= 1:
            width = int(self.MAX_LEFT_WIDTH * self._ui_scale()) - 40
        if width == getattr(self, "_max_list_width", None):
            return
        self._max_list_width = width
        # La case a cocher et ses marges grandissent avec l'echelle de
        # l'ecran : la marge laissee au texte aussi.
        scale = self._ui_scale()
        for w in box.winfo_children():
            margin = getattr(w, "_wrap_margin", None)
            if margin is not None:
                w.configure(wraplength=max(80, width - int(margin * scale)))

    def _max_check_all(self, value):
        """Tout cocher : toutes les propulsions, toutes leurs voilures. Tout
        decocher : la liste se replie sur les propulsions, et l'on recoche
        celle qu'on veut -- ses voilures reviennent toutes cochees."""
        for var, _cfg, _lbl in self._max_cfg_vars:
            var.set(value)
        for cfg, _n in self._max_all_configs:
            self._max_checked[cfg] = value
        for fam in self._max_families:
            self._max_families[fam] = value
        self._max_save_families()
        self._max_rebuild_cfg_list()

    def _max_solo(self, cfg):
        """Double-clic sur une voilure : elle seule -- sa polaire a elle,
        sans concurrence. Les autres propulsions se replient."""
        fam = self._max_family_of(cfg[1])
        for var, c, _lbl in self._max_cfg_vars:
            var.set(c == cfg)
        for c, _n in self._max_all_configs:
            if self._max_family_of(c[1]) == fam:
                self._max_checked[c] = (c == cfg)
        for f in self._max_families:
            self._max_families[f] = (f == fam)
        self._max_save_families()
        self._max_rebuild_cfg_list()

    def _ui_scale(self):
        """Echelle de l'ecran : 1.0 a 100 %, 1.5 a 150 %... Les polices
        grandissent avec elle, les largeurs donnees en pixels non : celles-ci
        doivent donc la suivre, sinon le texte deborde de son cadre."""
        try:
            return max(1.0, float(self.tk.call("tk", "scaling")) / (96.0 / 72.0))
        except (tk.TclError, ValueError):
            return 1.0

    def _max_remember_left_width(self):
        """Largeur de la colonne, retenue d'un lancement a l'autre une fois la
        poignee relachee."""
        try:
            width = int(self._max_left_pane.winfo_width())
        except tk.TclError:
            return
        if width > 1 and self.ui_state.get("polar_left_width") != width:
            self.ui_state["polar_left_width"] = width
            try:
                pcfg.save_ui_state(self.ui_state)
            except OSError:
                pass

    def _export_csv_all(self):
        session_ids = self._included_session_ids()
        configs = self.persistent_store.configs(session_ids=session_ids)
        if not configs:
            messagebox.showinfo("Export", "L'entrepot cumulatif est vide (ou aucune passe incluse).",
                                parent=self)
            return
        # Les reglages ENREGISTRES, comme le trace et les autres exports de la
        # page -- et non les champs de la page Parametres, qui peuvent porter
        # une saisie pas encore enregistree (voire invalide).
        twa_bin = float(self.config_data["twa_bin_deg"])
        tws_bin = float(self.config_data["tws_bin_kn"])
        symmetric = bool(self.config_data["symmetric_port_starboard"])
        stat = self.config_data["aggregation_stat"]
        configs_tables, confidences = [], []
        for (sails, engines), _count in configs:
            twa_values, tws_values, grid, counts = self.persistent_store.table(
                sails, engines, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric,
                stat=stat, session_ids=session_ids)
            if twa_values:
                configs_tables.append((sails, engines, twa_values, tws_values, grid, counts))
                confidences.append(self.persistent_store.confidence_table(
                    sails, engines, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                    symmetric=symmetric, session_ids=session_ids)[2])
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="polaires_toutes_configs.csv", parent=self)
        if not path:
            return
        content = pe.format_csv_table(configs_tables, confidences=confidences)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        messagebox.showinfo("Export", f"Fichier exporte :\n{path}", parent=self)

    def _close_max_cell_details(self):
        if getattr(self, "_max_detail_win", None) is not None:
            try:
                self._max_detail_win.destroy()
            except tk.TclError:
                pass
            self._max_detail_win = None
            self._max_detail_cell = None

    def _max_polar_recompute(self):
        """(Re)construit la table max selon les configurations cochees et
        retrace. Appele a chaque coche/decoche : la page repond du tac au
        tac."""
        if not hasattr(self, "_max_ax"):
            return
        chosen = [(cfg, lbl) for var, cfg, lbl in self._max_cfg_vars if var.get()]
        cfg_list = [cfg for cfg, _lbl in chosen]
        labels = [lbl for _cfg, lbl in chosen]
        twa_bin = float(self.config_data["twa_bin_deg"])
        tws_bin = float(self.config_data["tws_bin_kn"])
        symmetric = bool(self.config_data["symmetric_port_starboard"])
        stat = self.config_data["aggregation_stat"]
        twa_values, tws_values, grid, counts, winners = self.persistent_store.max_table(
            cfg_list, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric,
            stat=stat, session_ids=self._included_session_ids())
        # ORDRE : ecreter d'abord, boucher ensuite. L'interpolation doit
        # partir de valeurs deja assainies, sinon une pointe aberrante
        # contaminerait les cases qu'elle sert a combler.
        clipped = [[False] * len(r) for r in grid]
        n_clip = 0
        if self._max_clip_var.get() and grid:
            try:
                slope = float(self._max_clip_slope_var.get())
            except (tk.TclError, ValueError):
                slope = 1.5
            grid, clipped, n_clip = pe.clip_polar_slope(twa_values, grid, slope)
        clip_txt = ("" if not self._max_clip_var.get() else
                    (f"{n_clip} case(s) rabaissee(s) par l'ecretage." if n_clip
                     else "Aucune pointe a ecreter a ce seuil."))
        self._max_clip_lbl.configure(text=clip_txt)
        if clip_txt:
            self._max_clip_lbl.pack(fill="x", padx=6, pady=(0, 4))
        else:
            self._max_clip_lbl.pack_forget()
        mask = [[False] * len(r) for r in grid]
        if self._max_fill_var.get() and grid:
            grid, mask = pe.fill_polar_holes(twa_values, grid)
        # Le reglage d'ecretage suit l'utilisateur d'une session a l'autre :
        # c'est un choix de methode, pas un geste a refaire chaque fois.
        try:
            self.config_data["max_polar_clip_enabled"] = bool(self._max_clip_var.get())
            self.config_data["max_polar_clip_slope"] = float(self._max_clip_slope_var.get())
            pcfg.save_config(self.config_data)
        except (tk.TclError, ValueError, OSError):
            pass
        self._max_codes = [self._max_cfg_code.get(cfg, "?") for cfg in cfg_list]
        self._max_conf = self._max_confidence_grid(
            cfg_list, twa_values, tws_values, winners, twa_bin, tws_bin, symmetric)
        self._max_result = (twa_values, tws_values, grid, counts, winners, mask,
                            labels, clipped)
        self._max_polar_refresh_title(cfg_list, labels)
        self._max_polar_redraw(symmetric)
        self._max_polar_refresh_guide()
        # Une fiche de point encore ouverte decrirait l'ANCIENNE table :
        # fermee plutot que laissee mentir.
        self._close_max_cell_details()

    def _max_confidence_grid(self, cfg_list, twa_values, tws_values, winners,
                             twa_bin, tws_bin, symmetric):
        """Indice de confiance de chaque case de la table max : celui de la
        configuration GAGNANTE, seule a l'avoir produite, calcule sur
        exactement le meme decoupage (voir PolarSampleStore._cells).
        Purement informatif : il donne leur taille aux points, il ne
        retranche ni ne modifie aucune vitesse."""
        session_ids = self._included_session_ids()
        per_cfg = {}
        for ci in {w for row in winners for w in row if w is not None}:
            sails, engines = cfg_list[ci]
            t_axis, w_axis, conf = self.persistent_store.confidence_table(
                sails, engines, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                symmetric=symmetric, session_ids=session_ids)
            # Les axes d'une configuration ne couvrent pas forcement toute la
            # grille max : la case se retrouve par sa VALEUR d'angle et de
            # vent, jamais par son indice.
            per_cfg[ci] = ({v: k for k, v in enumerate(t_axis)},
                           {v: k for k, v in enumerate(w_axis)}, conf)
        out = [[None] * len(tws_values) for _ in twa_values]
        for i, twa in enumerate(twa_values):
            for j, tws in enumerate(tws_values):
                entry = per_cfg.get(winners[i][j])
                if entry is None:
                    continue
                ii, jj = entry[0].get(twa), entry[1].get(tws)
                conf = entry[2]
                if ii is not None and jj is not None and ii < len(conf) and jj < len(conf[ii]):
                    out[i][j] = conf[ii][jj]
        return out

    def _max_code_of(self, ci):
        """Code stable (C1, C2...) de la ci-eme configuration de la table."""
        if ci is not None and 0 <= ci < len(self._max_codes):
            return self._max_codes[ci]
        return "-"

    def _max_polar_refresh_title(self, cfg_list, labels):
        """En-tete du trace : ce qu'on regarde. Une seule configuration
        cochee, c'est SA polaire -- l'en-tete dit alors sa maturite et ce
        qui n'a jamais ete mesure, c'est-a-dire ou aller chercher des
        donnees."""
        # Les propulsions qui CONCOURENT (au moins une voilure cochee), dans
        # l'ordre de la liste -- et non toutes celles qui sont cochees.
        used = {self._max_family_of(c[1]) for c in cfg_list}
        families = [f for f, _l, _n in self._max_fam_labels if f in used]
        with_data = [f for f, _l, n in self._max_fam_labels if n]
        if not self._max_all_configs:
            txt = ("Entrepot vide (ou aucune passe incluse) : traitez d'abord une "
                   "session -- la polaire se tracera ici automatiquement.")
        elif not cfg_list:
            txt = ("Aucune configuration cochee : cochez une propulsion, puis ses "
                   "voilures, a gauche.")
        elif len(cfg_list) == 1:
            sails, engines = cfg_list[0]
            mat = self.persistent_store.config_confidence(
                sails, engines,
                twa_bin_deg=float(self.config_data["twa_bin_deg"]),
                tws_bin_kn=float(self.config_data["tws_bin_kn"]),
                symmetric=bool(self.config_data["symmetric_port_starboard"]),
                session_ids=self._included_session_ids())
            txt = (f"{self._max_codes[0]} seule : {labels[0]}. Maturite "
                   f"{mat['score']}/100 ({mat['word']}) -- {mat['solid']} case(s) "
                   f"solide(s), {mat['usable']} bonne(s), {mat['weak']} fragile(s), "
                   f"sur {mat['sessions']} sortie(s).")
            if mat["missing"]:
                txt += "  Jamais mesure : " + ", ".join(mat["missing"]) + "."
        else:
            scope = ("toutes propulsions"
                     if len(families) > 1 and len(families) == len(with_data)
                     else ", ".join(families))
            txt = f"Meilleure vitesse parmi {len(cfg_list)} voilures ({scope})."
        self._max_title_lbl.configure(text=txt)

    def _max_polar_redraw(self, symmetric):
        twa_values, tws_values, grid, counts, _winners, mask, labels, clipped = self._max_result
        ax = self._max_ax
        ax.clear()
        if self._max_legend is not None:
            try:
                self._max_legend.remove()
            except (ValueError, AttributeError, NotImplementedError):
                pass
            self._max_legend = None
        self._max_legend_map = {}
        self._max_legend_px = 0
        self._max_legend_entries = ([], [], [])
        self._max_legend_key = None
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.tick_params(colors=FG_LABEL, labelsize=9)
        ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)
        ax.set_facecolor(BG_PANEL)
        if not tws_values:
            self._apply_max_view()
            self._max_canvas.draw_idle()
            return
        max_radius = 0.0
        handles, names, shown = [], [], []
        # Palette du theme COURANT : sur fond sombre, celle de jour tombait
        # sous 3:1 de contraste pour la moitie des vents.
        palette = tws_colors()
        for j, tws in enumerate(tws_values):
            pts = [(twa_values[i], grid[i][j], mask[i][j], clipped[i][j],
                    ((self._max_conf[i][j] or {}).get("score", 0)
                     if i < len(self._max_conf) else 0))
                   for i in range(len(twa_values)) if grid[i][j] is not None]
            if not pts:
                continue
            pts.sort(key=lambda p: p[0])
            color = palette[j % len(palette)]
            # Au-dela de 10 vents la palette se repete : le TRAIT distingue
            # alors 4 et 24 nds, sur la courbe comme dans la legende.
            style = ("-", "--", ":")[(j // len(palette)) % 3]
            thetas = [math.radians(p[0]) for p in pts]
            radii = [p[1] for p in pts]
            # L'echelle tient compte des vents masques : masquer une courbe
            # ne doit pas faire bondir les autres, la comparaison avant/apres
            # garde ainsi un sens.
            max_radius = max(max_radius, max(radii))
            hidden = tws in self._max_hidden_tws
            handles.append(Line2D([], [], color=color, linewidth=2.0, marker="o",
                                  markersize=4, linestyle=style,
                                  alpha=(0.25 if hidden else 1.0)))
            names.append(f"{tws:g} nds")
            shown.append(tws)
            if hidden:
                continue
            if symmetric:
                ax.plot([-t for t in reversed(thetas)], list(reversed(radii)),
                        linewidth=1.3, color=color, linestyle=style)
                ax.plot(thetas, radii, linewidth=1.3, color=color, linestyle=style)
                sides = ((thetas, pts), ([-t for t in thetas], pts))
            else:
                ax.plot(thetas, radii, linewidth=1.3, color=color, linestyle=style)
                sides = ((thetas, pts),)
            for th_list, pt_list in sides:
                meas = [(th, v, sc) for th, (_t, v, m, c, sc) in zip(th_list, pt_list)
                        if not m and not c]
                interp = [(th, v) for th, (_t, v, m, c, _sc) in zip(th_list, pt_list) if m]
                clip = [(th, v) for th, (_t, v, m, c, _sc) in zip(th_list, pt_list)
                        if c and not m]
                # La taille d'un point mesure suit l'INDICE DE CONFIANCE de sa
                # case, et non le simple nombre de mesures : 200 mesures prises
                # dans un seul bord de dix minutes ne valent pas 20 mesures
                # reparties sur huit sorties, et le trace doit le montrer
                # plutot que le masquer. 6 pt minimum pour rester visible.
                if meas:
                    ax.scatter([p[0] for p in meas], [p[1] for p in meas],
                               s=[6.0 + 36.0 * (p[2] / 100.0) for p in meas],
                               color=color, edgecolors="none", zorder=3)
                if interp:
                    ax.scatter([p[0] for p in interp], [p[1] for p in interp], s=16,
                               facecolors="none", edgecolors=color, zorder=3)
                if clip:
                    ax.scatter([p[0] for p in clip], [p[1] for p in clip], s=28,
                               marker="x", color=color, linewidths=1.0, zorder=4)
        # Graduations angulaires en ANGLE DE VENT (0-180 de chaque bord) et
        # non en 0-360 : le demi-cercle babord porte des angles negatifs
        # (-90 = travers babord), que matplotlib etiquetterait "270 deg" --
        # un cap, alors qu'une polaire ne parle jamais que d'angles au vent.
        ticks = [i * math.pi / 4.0 for i in range(8)]
        ax.set_xticks(ticks)
        ax.set_xticklabels(
            [f"{abs(int(round(math.degrees(((t + math.pi) % (2 * math.pi)) - math.pi))))}°"
             for t in ticks])
        # Un demi-noeud d'air au-dela du point le plus rapide : sans cela, un
        # point pose pile sur la valeur max se colle au trait de bordure.
        if max_radius > 0:
            ax.set_rmax(max_radius + 0.5)
        # Legende des vents A DROITE du cercle, ancree a la FIGURE et non aux
        # axes : elle ne bouge pas au zoom, et sa hauteur ne retranche rien a
        # celle du trace -- posee sous le cercle, elle grandissait avec le
        # nombre de vents et ecrasait la polaire. Cliquer un vent masque sa
        # courbe, ou la rend : une polaire a quinze courbes se lit mieux a
        # cinq. Colonnes et place reservee : voir _max_fit_legend.
        self._max_legend_entries = (handles, names, shown)
        self._max_fit_legend()
        # Le zoom/deplacement en cours SURVIT au retrace : cocher une
        # configuration ne doit pas rejeter l'utilisateur a la vue de
        # depart sans qu'il l'ait demande.
        self._apply_max_view()
        self._max_canvas.draw_idle()

    def _max_fit_legend(self):
        """(Re)pose la legende des vents pour qu'elle TIENNE dans la hauteur
        de la figure : autant de colonnes qu'il en faut, d'apres la hauteur
        disponible et non d'apres le nombre de vents -- a 150 % dans une
        fenetre basse, les derniers vents sortaient par le bas, illisibles
        et impossibles a cliquer pour les masquer. La place reservee a
        droite du cercle est sa largeur MESUREE : la dpi de la figure suit
        l'echelle de l'ecran, une largeur fixe en pixels ne suffisait plus
        et la legende mordait sur le cercle. Rappelee a chaque
        redimensionnement de la figure ; ne refait rien si sa hauteur et sa
        dpi n'ont pas change."""
        handles, names, shown = getattr(self, "_max_legend_entries", ([], [], []))
        fig = self._max_fig
        key = (round(fig.bbox.height), fig.dpi, len(handles))
        if key == getattr(self, "_max_legend_key", None) and (
                self._max_legend is not None or not handles):
            return
        self._max_legend_key = key
        if self._max_legend is not None:
            try:
                self._max_legend.remove()
            except (ValueError, AttributeError, NotImplementedError):
                pass
            self._max_legend = None
        self._max_legend_map = {}
        self._max_legend_px = 0
        if not handles:
            return
        try:
            renderer = self._max_canvas.get_renderer()
        except Exception:
            renderer = None
        ncol = 1
        while True:
            leg = fig.legend(handles, names, loc="upper right",
                             bbox_to_anchor=(0.995, 0.99), ncol=ncol,
                             title="Vent reel", fontsize=8, title_fontsize=8,
                             facecolor=BG_PANEL, edgecolor=COLOR_TICK,
                             framealpha=1.0)
            if renderer is None or ncol >= len(handles):
                break
            box = leg.get_window_extent(renderer)
            if box.y0 >= 0:
                break
            leg.remove()
            ncol += 1
        for text in leg.get_texts():
            text.set_color(FG_LABEL)
        leg.get_title().set_color(FG_LABEL)
        leg_handles = (getattr(leg, "legend_handles", None)
                       or getattr(leg, "legendHandles", None) or [])
        for handle, text, tws in zip(leg_handles, leg.get_texts(), shown):
            for artist in (handle, text):
                artist.set_picker(True)
                self._max_legend_map[artist] = tws
        self._max_legend = leg
        if renderer is not None:
            # Largeur de la legende + son retrait du bord droit (0.5 % de la
            # figure) + un peu d'air avant le cercle.
            self._max_legend_px = (leg.get_window_extent(renderer).width
                                   + 0.005 * fig.bbox.width + 6 * fig.dpi / 100.0)
        else:
            self._max_legend_px = 96 * ncol * fig.dpi / 100.0

    def _on_max_legend_pick(self, event):
        """Clic sur un vent de la legende : sa courbe se masque, ou revient.
        Le symbole et le texte d'une meme entree sont tous deux cliquables --
        un seul clic peut donc les toucher l'un et l'autre : un clic ne
        bascule qu'une fois.

        Seul un clic GAUCHE compte : matplotlib emet aussi un pick a la
        molette et aux autres boutons -- zoomer au-dessus de la legende
        masquait sinon un vent a chaque cran."""
        tws = self._max_legend_map.get(event.artist)
        me = event.mouseevent
        if (tws is None or not self._max_result
                or getattr(me, "name", "") != "button_press_event"
                or getattr(me, "button", None) != 1):
            return
        if me is self._max_pick_mouse:
            return
        # Retenu pour _on_max_polar_press, qui recoit ce MEME evenement juste
        # apres : c'est lui qui saura que ce clic visait la legende.
        self._max_pick_mouse = me
        self._max_hidden_tws ^= {tws}
        # Retrace APRES l'evenement : la legende qu'on est en train de
        # parcourir ne doit pas etre detruite sous ses pieds.
        self.after_idle(lambda: self._max_polar_redraw(
            bool(self.config_data["symmetric_port_starboard"])))

    # Facteur applique a chaque cran de molette. 1,15 : assez pour qu'un
    # cran se voie, assez peu pour viser sans depasser.
    MAX_POLAR_ZOOM_STEP = 1.15
    MAX_POLAR_ZOOM_MAX = 8.0
    # Air autour du cercle, en pouces (34 px a 100 dpi) : de quoi loger les
    # graduations d'angle qui debordent du cercle, en haut comme en bas.
    MAX_POLAR_MARGIN_IN = 0.34

    def _max_view_base(self):
        """Cadre du trace a l'echelle 1, en fractions de figure : largeur,
        hauteur, et centre (x, y) de la zone ou il se loge -- ce que la
        legende des vents laisse libre a droite.

        CARRE : une polaire garde son aspect quoi qu'il arrive, et un cadre
        rectangulaire ferait flotter le cercle a l'interieur -- le
        deplacement deviendrait imprevisible, le curseur ne suivrait plus le
        dessin."""
        w_in, h_in = self._max_fig.get_size_inches()
        w_in, h_in = max(float(w_in), 1e-6), max(float(h_in), 1e-6)
        # Place de la legende : sa largeur MESUREE (voir _max_fit_legend),
        # jusqu'a la moitie de la figure -- a 30 %, une legende a plusieurs
        # colonnes d'une figure etroite mordait sur le cercle.
        leg_in = min(self._max_legend_px / float(self._max_fig.get_dpi() or 100.0),
                     w_in * 0.5)
        avail_w = max(w_in - leg_in, 1e-6)
        # Marge FIXE, et non proportionnelle : les graduations 0 et 180 deg
        # debordent du cercle d'une hauteur de texte, qui ne grandit pas avec
        # lui -- une marge en pourcentage les rognait sur un petit ecran.
        side = max(min(avail_w, h_in) - 2.0 * self.MAX_POLAR_MARGIN_IN, 0.5)
        return side / w_in, side / h_in, (avail_w / 2.0) / w_in, 0.5

    def _apply_max_view(self):
        """Place le cadre du trace selon le zoom et le deplacement courants.

        La vue retient le point du DESSIN (en fraction du cadre) qu'on veut
        garder au centre de la figure : c'est ce qui permet de zoomer sous
        le curseur sans que l'image parte sur le cote."""
        bw, bh, xc, yc = self._max_view_base()
        view = getattr(self, "_max_view", None)
        if not view:
            self._max_ax.set_position([xc - bw / 2.0, yc - bh / 2.0, bw, bh])
        else:
            s_, cx, cy = view["scale"], view["cx"], view["cy"]
            w, h = bw * s_, bh * s_
            self._max_ax.set_position([xc - cx * w, yc - cy * h, w, h])
        if hasattr(self, "_max_zoom_lbl"):
            self._max_zoom_lbl.configure(
                text=("Points pleins : mesure (taille = confiance)  -  creux : interpolation  -  "
                      "croix : pointe ecretee.     Survol : detail du point  |  clic : sa fiche  |  "
                      "molette : zoom  |  glisser : se deplacer  |  double-clic : vue "
                      "entiere  |  clic sur un vent de la legende : le masquer"
                      + (f"   (x{view['scale']:.1f})" if view else "")))

    def _max_cursor_in_axes(self, event):
        """Position du curseur dans le CADRE du trace, en fraction (0-1) --
        y compris hors du cadre, ou elle sort de [0,1] : c'est justement ce
        qui permet de zoomer en visant un bord."""
        try:
            x0, y0, w, h = self._max_ax.get_position().bounds
            fw, fh = self._max_canvas.get_width_height()
            fx, fy = event.x / max(fw, 1), event.y / max(fh, 1)
            return (fx - x0) / max(w, 1e-9), (fy - y0) / max(h, 1e-9)
        except Exception:
            return 0.5, 0.5

    def _on_max_polar_scroll(self, event):
        """Molette : zoom SOUS LE CURSEUR. Le point vise reste ou il est --
        sans cela, chaque cran chasserait de l'ecran ce qu'on cherchait
        justement a regarder de plus pres."""
        if not self._max_result or event.x is None:
            return
        cur = (self._max_view or {}).get("scale", 1.0)
        up = (getattr(event, "button", None) == "up"
              or (getattr(event, "step", 0) or 0) > 0)
        new = cur * self.MAX_POLAR_ZOOM_STEP if up else cur / self.MAX_POLAR_ZOOM_STEP
        new = max(1.0, min(new, self.MAX_POLAR_ZOOM_MAX))
        if abs(new - cur) < 1e-9:
            return
        if new <= 1.0 + 1e-9:
            self._max_view = None       # de retour a la vue entiere
        else:
            ax_x, ax_y = self._max_cursor_in_axes(event)
            view = self._max_view or {"scale": 1.0, "cx": 0.5, "cy": 0.5}
            # Le point vise garde sa place a l'ecran : on recale le centre
            # d'apres l'ecart entre lui et le centre actuel.
            self._max_view = {
                "scale": new,
                "cx": ax_x + (view["cx"] - ax_x) * (cur / new),
                "cy": ax_y + (view["cy"] - ax_y) * (cur / new),
            }
            self._clamp_max_view()
        self._apply_max_view()
        self._max_canvas.draw_idle()

    def _clamp_max_view(self):
        """Empeche de perdre le dessin hors de l'ecran : le point tenu au
        centre reste dans le cadre, marge comprise."""
        v = self._max_view
        if not v:
            return
        m = 0.5 / max(v["scale"], 1e-9)
        v["cx"] = max(-m, min(1.0 + m, v["cx"]))
        v["cy"] = max(-m, min(1.0 + m, v["cy"]))

    def _on_max_polar_press(self, event):
        if getattr(event, "button", None) != 1:
            return
        # Le pick de la legende a vu passer ce meme evenement juste avant
        # (matplotlib le traite en premier) : ce clic-la n'est pas pour une
        # case. Recalcule a CHAQUE clic, le drapeau ne peut pas rester leve.
        self._max_legend_click = event is self._max_pick_mouse
        # Point de depart memorise dans tous les cas : au relachement, un
        # deplacement quasi nul est un CLIC (detail de la case), un vrai
        # deplacement etait un glisser (panoramique du zoom).
        self._max_press_xy = (event.x, event.y)
        if self._max_view:
            self._max_drag = (event.x, event.y, self._max_view["cx"], self._max_view["cy"])

    def _on_max_polar_release(self, event):
        self._max_drag = None
        press = getattr(self, "_max_press_xy", None)
        self._max_press_xy = None
        if self._max_legend_click:
            # Le clic visait la legende (voir _on_max_legend_pick), pas une case.
            self._max_legend_click = False
            return
        if (press is None or event.x is None
                or math.hypot(event.x - press[0], event.y - press[1]) > 5):
            return
        cell = self._max_cell_at(event)
        if cell is not None:
            self._open_max_cell_details(*cell)

    def _max_polar_drag(self, event):
        """Glisser : le dessin suit la souris, exactement comme une image
        qu'on pousse du doigt."""
        if not self._max_drag or not self._max_view:
            return False
        x0, y0, cx0, cy0 = self._max_drag
        bw, bh, _xc, _yc = self._max_view_base()
        fw, fh = self._max_canvas.get_width_height()
        s_ = self._max_view["scale"]
        self._max_view["cx"] = cx0 - (event.x - x0) / max(fw * bw * s_, 1e-9)
        self._max_view["cy"] = cy0 - (event.y - y0) / max(fh * bh * s_, 1e-9)
        self._clamp_max_view()
        self._apply_max_view()
        self._max_canvas.draw_idle()
        return True

    def _reset_max_polar_zoom(self):
        """Double-clic : la polaire entiere, recadree dans la fenetre."""
        if not self._max_result:
            return
        self._max_view = None
        self._max_drag = None
        self._apply_max_view()
        self._max_canvas.draw_idle()

    def _on_max_polar_hover(self, event):
        """Detail de la case survolee. En coordonnees polaires, event.xdata
        est l'angle en radians et ydata le rayon (la vitesse) : on cherche
        la case la plus proche, mais uniquement si le curseur en est
        VRAIMENT proche -- une infobulle qui suit la souris a travers tout
        le cercle en pointant n'importe quelle case serait pire qu'aucune
        infobulle."""
        # Un glisser en cours prime : on deplace le dessin, on ne lit pas
        # une case au passage.
        if self._max_polar_drag(event):
            tip = getattr(self, "_max_tip", None)
            if tip is not None:
                tip.place_forget()
            return
        tip = getattr(self, "_max_tip", None)
        if tip is None:
            return
        if (event.inaxes is not self._max_ax or not self._max_result
                or event.xdata is None or event.ydata is None):
            tip.place_forget()
            return
        twa_values, tws_values, grid, counts, winners, mask, labels, clipped = self._max_result
        if not tws_values:
            tip.place_forget()
            return
        cell = self._max_cell_at(event)
        if cell is None:
            tip.place_forget()
            return
        i, j = cell
        v = grid[i][j]
        origin = ("interpolee" if mask[i][j]
                  else ("mesuree, pointe ecretee" if clipped[i][j] else "mesuree"))
        ci = winners[i][j]
        who = (f"{self._max_code_of(ci)} = {labels[ci]}" if ci is not None
               else "-- (interpolation)")
        lines = [f"TWA {twa_values[i]:g}deg   TWS {tws_values[j]:g} nds",
                 f"{v:.2f} nds  ({origin})",
                 f"voilure : {who}"]
        if ci is not None and counts[i][j]:
            conf = self._max_cell_confidence(i, j)
            lines.append(f"{counts[i][j]} mesure(s)"
                         + (f"   confiance {conf['level']} {conf['score']}" if conf else ""))
        tip.configure(text="\n".join(lines))
        # Place l'infobulle a cote du curseur, dans le widget du trace, en la
        # rabattant si elle sortirait du cadre.
        w = self._max_canvas.get_tk_widget()
        x = int(event.x) + 14
        y = int(w.winfo_height() - event.y) + 14
        tip.update_idletasks()
        if x + tip.winfo_reqwidth() > w.winfo_width():
            x = max(0, int(event.x) - tip.winfo_reqwidth() - 10)
        if y + tip.winfo_reqheight() > w.winfo_height():
            y = max(0, y - tip.winfo_reqheight() - 24)
        tip.place(in_=w, x=x, y=y)

    def _max_cell_at(self, event):
        """Case (i, j) de la table max la plus proche du curseur, ou None si
        le curseur n'est VRAIMENT proche d'aucune. En coordonnees polaires,
        event.xdata est l'angle en radians et ydata le rayon (la vitesse) ;
        la distance se mesure dans le plan du dessin, jamais en unites
        melangees (comparer des degres a des noeuds n'aurait aucun sens).
        Partagee par l'infobulle de survol et par le clic de detail : les
        deux doivent designer exactement la meme case."""
        if (not self._max_result or event.inaxes is not self._max_ax
                or event.xdata is None or event.ydata is None):
            return None
        twa_values, tws_values, grid = self._max_result[0], self._max_result[1], self._max_result[2]
        if not tws_values:
            return None
        # L'angle survole, ramene en angle au vent 0-180 (le demi-cercle
        # babord est le miroir du tribord : la meme case y est decrite).
        deg = abs(math.degrees(((event.xdata + math.pi) % (2 * math.pi)) - math.pi))
        r = event.ydata
        best = None
        for i, twa in enumerate(twa_values):
            for j, tws in enumerate(tws_values):
                v = grid[i][j]
                # Un vent masque dans la legende n'a plus de points a l'ecran :
                # rien a y designer.
                if v is None or tws in self._max_hidden_tws:
                    continue
                d = math.hypot(math.radians(abs(twa) - deg) * max(r, 0.5), v - r)
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None or best[0] > max(0.6, 0.08 * max(1.0, r)):
            return None
        return best[1], best[2]

    def _max_cell_confidence(self, i, j):
        """Confiance de la case (i, j) de la polaire max : celle de la
        configuration GAGNANTE, seule a l'avoir produite (calculee au
        recalcul de la table, voir _max_confidence_grid)."""
        if not self._max_result or i >= len(self._max_conf) or j >= len(self._max_conf[i]):
            return None
        return self._max_conf[i][j]

    def _max_polar_refresh_guide(self):
        twa_values, tws_values, _grid, _counts, winners, mask, _labels, _clip = self._max_result
        tree = self._max_guide
        tree.delete(*tree.get_children())
        cols = ["twa"] + [f"tws{j}" for j in range(len(tws_values))]
        tree.configure(columns=cols)
        # Largeurs calees sur la police des EN-TETES (qui grandit avec
        # l'echelle de l'ecran) : en pixels fixes, "TWA \ TWS" et "12.5 nds"
        # etaient rognes a 125/150 %.
        hf = tkfont.nametofont("TkHeadingFont")
        tree.heading("twa", text="TWA \\ TWS")
        tree.column("twa", width=max(self._px(80), hf.measure("TWA \\ TWS") + 16),
                    anchor="center", stretch=False)
        # Largeur FIXE : au-dela de ce que la vue peut montrer, le guide
        # defile horizontalement plutot que d'ecraser ses colonnes.
        for j, tws in enumerate(tws_values):
            head = f"{tws:g} nds"
            tree.heading(f"tws{j}", text=head)
            tree.column(f"tws{j}", width=max(self._px(64), hf.measure(head) + 16),
                        anchor="center", stretch=False)
        # Les codes sont ceux de la liste de gauche, toujours visible : la
        # legende "C1 = ..." n'a plus a s'empiler sous le guide.
        for i, twa in enumerate(twa_values):
            row = [f"{twa:g}°"]
            for j in range(len(tws_values)):
                ci = winners[i][j]
                if ci is None:
                    row.append("~" if mask[i][j] else "")
                else:
                    row.append(self._max_code_of(ci))
            tree.insert("", "end", values=row)

    def _on_max_guide_click(self, event):
        """Clic dans le guide de voilure : la colonne cliquee designe le
        vent, la ligne l'angle -- exactement la case de la table max."""
        tree = self._max_guide
        if tree.identify("region", event.x, event.y) != "cell" or not self._max_result:
            return
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        i = tree.index(row_id)
        j = int(col_id.lstrip("#")) - 2   # #1 = colonne des angles
        twa_values, tws_values = self._max_result[0], self._max_result[1]
        if 0 <= i < len(twa_values) and 0 <= j < len(tws_values):
            self._open_max_cell_details(i, j)

    # Libelles humains des statistiques d'agregation (reglage
    # aggregation_stat) -- pour que la fiche dise "90e centile" et non "p90".
    STAT_LABELS = {"p90": "90e centile des mesures", "median": "mediane des mesures",
                   "mean": "moyenne des mesures", "max": "maximum des mesures"}

    def _open_max_cell_details(self, i, j):
        """La FICHE D'IDENTITE d'un point de la polaire max : d'ou vient la
        valeur retenue, sur combien de mesures, avec quelle confiance, et
        qui elle a battu. La polaire affiche une vitesse ; cette fenetre
        raconte tout le reste."""
        if not self._max_result or not self._max_result[1]:
            return
        (twa_values, tws_values, grid, counts, winners, mask,
         labels, clipped) = self._max_result
        if not (0 <= i < len(twa_values) and 0 <= j < len(tws_values)):
            return
        # Une seule fiche a la fois ; recliquer la meme case la ramene
        # simplement au premier plan.
        if (getattr(self, "_max_detail_win", None) is not None
                and getattr(self, "_max_detail_cell", None) == (i, j)):
            try:
                self._max_detail_win.lift()
                return
            except tk.TclError:
                pass
        self._close_max_cell_details()

        chosen = [cfg for var, cfg, _lbl in self._max_cfg_vars if var.get()]
        stat = self.config_data["aggregation_stat"]
        detail = self.persistent_store.max_cell_detail(
            chosen, twa_values[i], tws_values[j],
            twa_bin_deg=float(self.config_data["twa_bin_deg"]),
            tws_bin_kn=float(self.config_data["tws_bin_kn"]),
            symmetric=bool(self.config_data["symmetric_port_starboard"]),
            stat=stat, session_ids=self._included_session_ids())

        parent = self
        win = tk.Toplevel(parent)
        self._max_detail_win = win
        self._max_detail_cell = (i, j)
        win.title(f"Point {twa_values[i]:g}\u00b0 / {tws_values[j]:g} nds -- detail")
        win.configure(bg=BG_APP)
        win.transient(parent)
        # Taille : voir la fin de la methode, une fois le contenu mesure.
        win.bind("<Escape>", lambda _e: self._close_max_cell_details())
        win.protocol("WM_DELETE_WINDOW", self._close_max_cell_details)

        outer, inner = make_scrollable(win, height=1)
        outer.pack(fill="both", expand=True)

        mono = {"bg": BG_APP, "fg": FG_LABEL, "font": FONT_MONO,
                "anchor": "w", "justify": "left"}
        # Blocs a chasse fixe : la fenetre se taille sur leur ligne la plus
        # longue (voir la fin), et ils passent a la ligne si l'ecran ne la
        # laisse pas si large -- sans quoi leur fin etait coupee, sans
        # ascenseur horizontal (dates des passes importees, 150 %).
        mono_lbls = []

        def mono_label(master, text):
            lbl = tk.Label(master, text=text, **mono)
            lbl.pack(fill="x", padx=6, pady=(0, 6))
            mono_lbls.append(lbl)
            return follow_width(lbl)

        # ----- 1. La case et la valeur retenue -----
        f_cell = section(inner, "La case")
        v = grid[i][j]
        ci = winners[i][j]
        tlo, thi = detail["twa_range"]
        wlo, whi = detail["tws_range"]
        lines = [
            f"Angle au vent : {twa_values[i]:g} deg   "
            f"(mesures de {max(0.0, tlo):g} a {thi:g} deg)",
            f"Vent reel     : {tws_values[j]:g} nds   "
            f"(mesures de {max(0.0, wlo):g} a {whi:g} nds)",
        ]
        if v is None:
            lines.append("Valeur retenue : aucune -- le bateau n'a jamais ete "
                         "observe dans cette case.")
        else:
            origin = ("interpolee" if mask[i][j]
                      else ("mesuree, pointe ecretee" if clipped[i][j] else "mesuree"))
            lines.append(f"Valeur retenue : {v:.2f} nds  ({origin})")
            lines.append(f"Statistique    : {self.STAT_LABELS.get(stat, stat)}")
            if ci is not None:
                lines.append(f"Voilure gagnante : {self._max_code_of(ci)} = {labels[ci]}")
        mono_label(f_cell, "\n".join(lines))
        if v is not None and mask[i][j]:
            # Case interpolee : dire ENTRE QUOI. L'interpolation court le
            # long du TWA, dans la meme colonne de vent (fill_polar_holes).
            src = []
            for k in range(i - 1, -1, -1):
                if grid[k][j] is not None and not mask[k][j]:
                    src.append(f"{twa_values[k]:g} deg ({grid[k][j]:.2f} nds)")
                    break
            for k in range(i + 1, len(twa_values)):
                if grid[k][j] is not None and not mask[k][j]:
                    src.append(f"{twa_values[k]:g} deg ({grid[k][j]:.2f} nds)")
                    break
            note(f_cell, "Case SANS mesure, bouchee par interpolation le long de "
                         "l'angle, entre " + " et ".join(src) + ". Rien ci-dessous ne "
                         "la concerne directement : les mesures decrites sont celles "
                         "des cases voisines." if src else
                         "Case interpolee.", wraplength="auto")
        if v is not None and clipped[i][j]:
            note(f_cell, "Pointe ECRETEE : la valeur mesuree depassait la pente "
                         "maximale reglee sur l'etape Polaires ; elle a ete "
                         "rabaissee au niveau que la pente autorise. La statistique "
                         "d'origine reste visible dans le tableau ci-dessous.",
                 wraplength="auto")

        # ----- 2. Les configurations en concurrence -----
        entries = detail["entries"]
        f_comp = section(inner, "Configurations en concurrence dans cette case")
        if not entries:
            note(f_comp, "Aucune configuration cochee n'a de mesure dans cette case.",
                 wraplength="auto")
        else:
            note(f_comp, "De la plus rapide a la plus lente ; la premiere l'emporte. "
                         "'Retenue' est la statistique d'agregation, avant ecretage "
                         "eventuel.", wraplength="auto")
            cols = ("code", "n", "ret", "mini", "med", "maxi", "ecart", "conf")
            heads = ("Voilure", "n", "Retenue", "Mini", "Med", "Maxi", "Ecart", "Conf.")
            widths = (76, 44, 70, 58, 58, 58, 58, 56)
            # Au-dela de 8 concurrentes, un ascenseur : plafonne a 6 lignes
            # sans rien d'autre, le tableau cachait les suivantes sans le
            # dire.
            tv_row = tk.Frame(f_comp, bg=BG_APP)
            tv_row.pack(fill="x", padx=6, pady=(0, 6))
            tv = ttk.Treeview(tv_row, show="headings", columns=cols,
                              height=min(8, len(entries)))
            if len(entries) > 8:
                tv_sb = ttk.Scrollbar(tv_row, orient="vertical", command=tv.yview)
                tv.configure(yscrollcommand=tv_sb.set)
                tv_sb.pack(side="right", fill="y")
            # Colonnes a l'echelle de l'ecran et jamais plus etroites que leur
            # en-tete : en pixels fixes, "Retenue" devenait "Retenu" a 150 %.
            hf = tkfont.nametofont("TkHeadingFont")
            for c, h, w in zip(cols, heads, widths):
                tv.heading(c, text=h)
                tv.column(c, width=max(self._px(w), hf.measure(h) + 16),
                          anchor="center", stretch=(c == "code"))
            for e in entries:
                conf = e["confidence"]
                spread = ("-" if e["spread"] is None else f"{100 * e['spread']:.0f} %")
                tv.insert("", "end", values=(
                    self._max_code_of(e["config_index"]), e["n"],
                    f"{e['speed']:.2f}" if e["speed"] is not None else "-",
                    f"{e['min']:.2f}", f"{e['median']:.2f}", f"{e['max']:.2f}",
                    spread, f"{conf['level']} {conf['score']}"))
            tv.pack(side="left", fill="x", expand=True)

        # ----- 3. La gagnante en detail -----
        best = entries[0] if entries else None
        if best is not None:
            f_win = section(inner, "La configuration gagnante, en detail")
            conf = best["confidence"]
            wl = [
                f"Mesures   : {best['n']}   sur {conf['blocks']} moment(s) "
                f"independant(s), {conf['sessions']} sortie(s)",
                f"Vitesses  : mini {best['min']:.2f}   mediane {best['median']:.2f}   "
                f"moyenne {best['mean']:.2f}",
                f"            p90 {best['p90']:.2f}   maxi {best['max']:.2f} nds",
                "Precision : dispersion "
                + ("indeterminable (trop peu de mesures)" if best["spread"] is None
                   else f"{100 * best['spread']:.0f} % (interdecile / mediane)"),
                f"Angles    : {best['twa_min']:.1f} a {best['twa_max']:.1f} deg reellement mesures",
                f"Vents     : {best['tws_min']:.1f} a {best['tws_max']:.1f} nds reellement mesures",
                f"Bords     : {best['tacks']['tribord']} tribord / "
                f"{best['tacks']['babord']} babord",
            ]
            if best["t_first"] is not None:
                d1 = time.strftime("%d/%m/%Y %H:%M", time.localtime(best["t_first"]))
                d2 = time.strftime("%d/%m/%Y %H:%M", time.localtime(best["t_last"]))
                wl.append(f"Periode   : du {d1} au {d2}")
            wl.append(f"Confiance : {conf['level']} {conf['score']}/100 ({conf['word']})")
            wl.append(f"            {conf['why']}")
            mono_label(f_win, "\n".join(wl))

            # Les passes qui ont nourri la case, nommees comme dans l'Entrepot.
            if best["sessions"]:
                srows = []
                for se in best["sessions"]:
                    rec = self._session_record(se["session_id"]) if se["session_id"] else None
                    name = (rec.get("label") if rec else None) or se["session_id"] or "(sans passe)"
                    when = ("" if se["t_first"] is None else
                            "  " + time.strftime("%d/%m/%Y %H:%M", time.localtime(se["t_first"])))
                    srows.append(f"  {se['n']:>3d} mesure(s)  {name}{when}")
                mono_label(f_win, "Passes ayant nourri la case :\n" + "\n".join(srows))

            # ----- 4. Les valeurs elles-memes -----
            f_val = section(inner, "Les vitesses mesurees (gagnante)")
            vals = best["values"]
            shown = vals if len(vals) <= 80 else vals[:80]
            txt = "  ".join(f"{x:.2f}" for x in shown)
            if len(vals) > 80:
                txt += f"  ... (+{len(vals) - 80} autres)"
            lbl = tk.Label(f_val, text=txt, bg=BG_APP, fg=FG_LABEL_DIM,
                           font=("Consolas", 8), anchor="w", justify="left")
            lbl.pack(fill="x", padx=6, pady=(0, 8))
            lbl.bind("<Configure>", lambda e, l=lbl: l.configure(
                wraplength=max(200, e.width - 12)))

        tk.Button(inner, text="Fermer", command=self._close_max_cell_details,
                  padx=14, pady=3).pack(pady=(4, 10))
        # Largeur a la mesure de la plus longue ligne a chasse fixe (et du
        # tableau), a l'echelle de l'ecran et bornee par lui (_fit_window) :
        # 660 px fixes coupaient ces lignes a 150 %, et a 100 % les noms des
        # passes importees. Au-dela de 980 px a 100 %, elles passent a la
        # ligne plutot que d'etirer la fenetre sans fin. Marges : cadres des
        # blocs, libelles, ascenseur.
        content = [lbl.winfo_reqwidth() for lbl in mono_lbls]
        if entries:
            content.append(tv.winfo_reqwidth() + self._px(18))
        need = max(content, default=0) + 40 + self._px(24)
        width = min(980, max(660, need / self._ui_scale()))
        self._fit_window(win, int(round(width)), 680, 560, 420)
        self._theme_new_window(win)
        win.update_idletasks()
        self._center_on_parent(win)

    def _max_ask_say(self, text):
        self._max_ask_result.configure(text=text)
        if not self._max_ask_result.winfo_manager():
            self._max_ask_result.pack(fill="x", padx=6, pady=(0, 6))

    def _max_polar_lookup(self):
        if not self._max_result or not self._max_result[1]:
            self._max_ask_say("Aucune table : cochez une voilure.")
            return
        twa_values, tws_values, grid, _counts, winners, mask, labels, clipped = self._max_result
        try:
            twa = abs(float(self._max_ask_twa.get().replace(",", ".")))
            tws = float(self._max_ask_tws.get().replace(",", "."))
        except ValueError:
            self._max_ask_say("Entrez un angle et un vent numeriques.")
            return
        i = min(range(len(twa_values)), key=lambda k: abs(twa_values[k] - twa))
        j = min(range(len(tws_values)), key=lambda k: abs(tws_values[k] - tws))
        v = grid[i][j]
        if v is None:
            self._max_ask_say(
                f"Case {twa_values[i]:g}° / {tws_values[j]:g} nds : aucune mesure "
                     f"ni interpolation. Le bateau n'a jamais ete observe la.")
            return
        ci = winners[i][j]
        who = (f"{self._max_code_of(ci)} = {labels[ci]}" if ci is not None
               else "interpolation entre deux voilures mesurees")
        origin = "interpolee" if mask[i][j] else "mesuree"
        self._max_ask_say(
            f"Case {twa_values[i]:g}° / {tws_values[j]:g} nds : {v:.2f} nds "
                 f"({origin}).\nVoilure : {who}")

    def _max_export_name(self, default):
        """Nom propose a l'export : celui de la configuration quand elle est
        seule cochee -- exporter C1, C2, C3 l'une apres l'autre ne doit pas
        proposer trois fois le meme fichier."""
        chosen = [cfg for var, cfg, _lbl in self._max_cfg_vars if var.get()]
        if len(chosen) != 1:
            return default
        sails, engines = chosen[0]
        return f"polaire_{'-'.join(sails) or 'nu'}_{'-'.join(engines) or 'nu'}"

    def _max_polar_export_pol(self):
        if not self._max_result or not self._max_result[1]:
            messagebox.showinfo("Export", "Aucune table max a exporter.")
            return
        twa_values, tws_values, grid, _c, _w, _m, _l, _cl = self._max_result
        path = filedialog.asksaveasfilename(
            defaultextension=".pol", filetypes=[("Fichier polaire", "*.pol")],
            initialfile=self._max_export_name("polaire_max_routage") + ".pol", parent=self)
        if not path:
            return
        # Grille PLEINE : les cases jamais observees passent a 0 -- pour un
        # routeur, 0 = "le bateau ne marche pas la" (le vent debout,
        # typiquement), ce qui est exactement l'intention.
        full = [[(v if v is not None else 0.0) for v in row] for row in grid]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(pe.format_pol_table(twa_values, tws_values, full))
        messagebox.showinfo(
            "Export", f"Fichier exporte :\n{path}\n\nGrille PLEINE au format Expedition "
                      "(qtVlm, Adrena, OpenCPN...). Les cases sans mesure ni interpolation "
                      "y valent 0 : un routeur y lit 'le bateau ne marche pas la'.",
            parent=self)

    def _max_polar_export_timezero(self):
        """Deux fichiers TimeZero d'un seul geste : la polaire de vitesse et
        le fichier de VOILURE.

        Le second est ce qu'Allure a de plus rare a offrir : TimeZero sait
        afficher quelle toile porter par plage de cap et de vent, et c'est
        exactement ce que la polaire max calcule -- la configuration
        gagnante de chaque case. Le routage recoit donc non seulement ce
        que le bateau sait faire, mais avec quoi il le fait."""
        if not self._max_result or not self._max_result[1]:
            messagebox.showinfo("Export", "Aucune table max a exporter.")
            return
        twa_values, tws_values, grid, _c, winners, _m, labels, _cl = self._max_result
        base = filedialog.asksaveasfilename(
            defaultextension=".xml", filetypes=[("Fichier TimeZero", "*.xml")],
            initialfile="Allure_Wind_Polar.xml", parent=self)
        if not base:
            return
        root, _ext = os.path.splitext(base)
        # Le nom du second fichier se deduit du premier : TimeZero les
        # charge separement, mais ils vont par paire et doivent se
        # reconnaitre au premier coup d'oeil dans un dossier.
        if root.endswith("_Wind_Polar"):
            root_sail = root[: -len("_Wind_Polar")] + "_Sail_Set"
        else:
            root_sail = root + "_Sail_Set"
        polar_path, sail_path = root + ".xml", root_sail + ".xml"

        sail_map = self.config_data.get("timezero_sail_map") or {}
        chosen = [cfg for var, cfg, _lbl in self._max_cfg_vars if var.get()]
        sails_of_winner = {
            i: pe.timezero_sails_for_config(cfg[0], cfg[1], sail_map)
            for i, cfg in enumerate(chosen)}
        bands_by_tws = [
            pe.timezero_bands(twa_values, [winners[i][j] for i in range(len(twa_values))],
                              sails_of_winner)
            for j in range(len(tws_values))]
        try:
            with open(polar_path, "w", encoding="utf-8") as fh:
                fh.write(pe.format_timezero_polar(twa_values, tws_values, grid))
            with open(sail_path, "w", encoding="utf-8") as fh:
                fh.write(pe.format_timezero_sailset(tws_values, bands_by_tws))
        except OSError as e:
            messagebox.showerror("Export", f"Ecriture impossible :\n{e}",
                                 parent=self)
            return
        unmapped = [c for cfg in chosen for c in cfg[0] if c not in sail_map]
        warn = ""
        if unmapped:
            warn = ("\n\nVoiles sans correspondance TimeZero : "
                    + ", ".join(sorted(set(unmapped)))
                    + ".\nLe fichier de voilure les remplace par la valeur par defaut. "
                      "Reglez-les dans la roue dentee > Voiles & moteurs.")
        messagebox.showinfo(
            "Export TimeZero",
            f"Deux fichiers ecrits :\n\n{polar_path}\n(la polaire de vitesse)\n\n"
            f"{sail_path}\n(la voilure a porter, par cap et par vent)\n\n"
            "Dans TimeZero : bouton TIMEZERO > Ouvrir un fichier polaire, puis "
            "Parcourir." + warn, parent=self)

    def _max_polar_export_csv(self):
        if not self._max_result or not self._max_result[1]:
            messagebox.showinfo("Export", "Aucune table max a exporter.")
            return
        twa_values, tws_values, grid, counts, winners, mask, labels, clipped = self._max_result
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=self._max_export_name("polaire_max_routage") + ".csv", parent=self)
        if not path:
            return
        import csv as _csv
        with open(path, "w", encoding="utf-8", newline="") as fh:
            writer = _csv.writer(fh)
            writer.writerow(["twa_deg", "tws_kn", "stw_kn", "origine",
                             "voilure_gagnante", "n_echantillons"])
            for i, twa in enumerate(twa_values):
                for j, tws in enumerate(tws_values):
                    v = grid[i][j]
                    if v is None:
                        continue
                    ci = winners[i][j]
                    origine = ("interpole" if mask[i][j]
                               else ("mesure_ecrete" if clipped[i][j] else "mesure"))
                    writer.writerow([twa, tws, round(v, 2), origine,
                                     labels[ci] if ci is not None else "-",
                                     counts[i][j] if not mask[i][j] else 0])
        messagebox.showinfo("Export", f"Fichier exporte :\n{path}",
                            parent=self)

    # ---------------------------------------------------------------
    # Suivi en direct -- vit DANS l'etape Enregistrement, dont il remplace
    # le contenu de l'etape Acquisition en permanence, et
    # _update_instruments pour le rafraichissement, appele depuis
    # _update_once. Ne calcule ni ne modifie jamais rien pour le moteur de
    # polaire : strictement de la lecture.
    #
    # Choix des informations affichees -- volontairement restreint a ce qui
    # sert a construire OU a diagnostiquer une polaire, et a rien d'autre :
    #
    #   1. les trois grandeurs qui FONT la polaire : TWA, TWS, STW ;
    #   2. la qualite de l'echantillonnage : c'est l'information a plus forte
    #      valeur ajoutee, et elle n'existe dans aucun afficheur du commerce
    #      -- savoir en direct si les mesures sont retenues ou ecartees comme
    #      "manoeuvre" evite de rentrer avec deux heures d'enregistrement
    #      pour trois echantillons exploitables ;
    #   3. la configuration en cours d'enregistrement, pour attraper tout de
    #      suite le grand classique du changement de voile non repercute ;
    #   4. la reception, en diagnostic : les deux entrees du calcul (vent
    #      apparent, vitesse surface) et la vitesse fond comme recoupement.
    #
    # Ecarte a dessein (present sur un afficheur de navigation classique,
    # sans role ici) : position, pression, temperature, humidite, force
    # Beaufort, cap et direction du vent VRAI par rapport au nord. Une
    # polaire est bord-relative : le nord n'y entre jamais, et cette derniere
    # aurait de surcroit demande un cap compas que l'installation ne fournit
    # pas. Tout cela reste evidemment present dans le fichier .log brut de la
    # session, donc rien n'est perdu.
    # ---------------------------------------------------------------
    def _build_live_view(self, parent):
        # La barre d'etat/commande de l'acquisition ne vit PLUS ici : elle est
        # permanente, sous le bandeau des etapes, visible depuis n'importe
        # quelle page (voir _build_acq_bar). Une prise qui tourne doit se
        # voir meme quand on regarde ses polaires.
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)

        # --- 1. Les trois grandeurs de la polaire ---
        f_mes = section(inner, "Mesures en cours")
        mes_row = tk.Frame(f_mes, bg=BG_APP)
        mes_row.pack(fill="x", padx=6, pady=(2, 8))

        gauge_col = tk.Frame(mes_row, bg=BG_APP)
        gauge_col.pack(side="left", padx=(0, 16))
        # Cadran a l'echelle de l'ecran, comme les grands chiffres voisins :
        # en pixels fixes, il restait petit a 150 % et ses graduations se
        # serraient. _retheme_all le redessine a la meme taille.
        self._live_twa_size = self._px(240)
        self.live_twa_canvas = tk.Canvas(gauge_col, width=self._live_twa_size,
                                          height=self._live_twa_size, bg=BG_APP,
                                          highlightthickness=0)
        self.live_twa_canvas.pack()
        # Legende des DEUX fleches (et rien d'autre : les bords babord/
        # tribord se lisent deja aux teintes des moities du cadran).
        legend = tk.Frame(gauge_col, bg=BG_APP)
        legend.pack(pady=(2, 0))
        tk.Label(legend, text="▬ Vent vrai", bg=BG_APP, fg=FG_DIGIT,
                 font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 14))
        tk.Label(legend, text="▬ Vent apparent", bg=BG_APP, fg=COLOR_ACCENT,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(gauge_col, text="Les fleches soufflent vers le bateau ; leur longueur suit\n"
                                  "la force du vent (meme echelle pour les deux).",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 7), justify="center").pack(pady=(1, 0))

        values_col = tk.Frame(mes_row, bg=BG_APP)
        values_col.pack(side="left", fill="both", expand=True)
        self.live_twa_ro = ValueReadout(values_col, "TWA -- angle de vent vrai (bord-relatif)")
        self.live_twa_ro.pack(fill="x", pady=(0, 6))
        self.live_tws_ro = ValueReadout(values_col, "TWS -- vitesse de vent vrai")
        self.live_tws_ro.pack(fill="x", pady=(0, 6))
        self.live_stw_ro = ValueReadout(values_col, "STW -- vitesse surface (loch)")
        self.live_stw_ro.pack(fill="x", pady=(0, 6))
        self.live_sog_ro = ValueReadout(values_col, "SOG -- vitesse fond (GPS)")
        self.live_sog_ro.pack(fill="x")

        # --- 2. Qualite de l'echantillonnage ---
        f_qual = section(inner, "Echantillonnage")
        self.live_quality_lbl = tk.Label(f_qual, text="En attente de donnees",
                                          bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 13, "bold"))
        self.live_quality_lbl.pack(fill="x", padx=6, pady=(2, 2))
        follow_width(self.live_quality_lbl)
        # Compteur SOUS l'etat et non a sa droite : cote a cote, les deux
        # textes depassaient la largeur a 125 / 150 % et le compteur -- qui
        # dit si ca enregistre et depuis quand -- etait rogne des deux cotes.
        self.live_yield_lbl = tk.Label(f_qual, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=FONT_MONO)
        self.live_yield_lbl.pack(fill="x", padx=6, pady=(0, 2))
        follow_width(self.live_yield_lbl)
        self.live_quality_detail_lbl = tk.Label(f_qual, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                                 font=FONT_MONO, justify="left", anchor="w")
        self.live_quality_detail_lbl.pack(fill="x", padx=6, pady=(0, 2))
        follow_width(self.live_quality_detail_lbl)
        # N'apparait QUE quand le probleme se produit (voir _update_instruments) :
        # un avertissement affiche en permanence cesse d'etre lu.
        # Les bandeaux d'alerte de l'etape suivent la largeur reelle
        # (follow_width) et non 900 px figes : a 1920 de large ils
        # s'empilaient sur 4 a 6 lignes avec la moitie droite vide.
        self.live_warning_lbl = tk.Label(f_qual, text="", bg=BG_APP, fg=COLOR_PORT,
                                          font=("Segoe UI", 9, "bold"), justify="left", anchor="w")
        follow_width(self.live_warning_lbl)

        # --- 3. Configuration enregistree ---
        f_cfg = section(inner, "Configuration enregistree")
        self.live_config_lbl = tk.Label(f_cfg, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=("Segoe UI", 11, "bold"), anchor="w")
        self.live_config_lbl.pack(fill="x", padx=6, pady=(2, 0))
        follow_width(self.live_config_lbl)
        self.live_timer_lbl = tk.Label(f_cfg, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                        font=FONT_MONO, anchor="w")
        self.live_timer_lbl.pack(fill="x", padx=6, pady=(0, 6))
        follow_width(self.live_timer_lbl)

        # --- 4. Reception (diagnostic) ---
        # Le resume des voies actives vit ici, avec le reste du diagnostic --
        # et non dans la barre permanente, qui doit rester lisible d'un coup
        # d'oeil.
        f_rx = section(inner, "Reception")
        note(f_rx, "Les deux premieres lignes sont les entrees du calcul : sans elles, aucun "
                    "echantillon ne peut etre produit. La vitesse fond ne sert qu'au recoupement "
                    "-- un ecart durable avec la vitesse surface signale un loch encrasse (ou un "
                    "courant), cause classique d'une polaire faussee vers le bas.")
        self.ports_summary_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                           font=("Segoe UI", 8), anchor="w", justify="left")
        self.ports_summary_lbl.pack(fill="x", padx=6, pady=(0, 2))
        # Les lignes de cette section portent des noms de voie libres (40
        # caracteres) et des messages du systeme : dans la zone defilante, qui
        # rogne tout debordement horizontal, elles passent a la ligne a la
        # largeur reelle (follow_width) au lieu d'etre coupees a droite.
        follow_width(self.ports_summary_lbl)
        # Etat REEL de chaque voie : ouverte ? combien de trames ? depuis
        # quand ? Une voie qui s'ouvre sans rien recevoir etait jusqu'ici
        # indiscernable d'une voie qui marche.
        self.ports_state_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.ports_state_lbl.pack(fill="x", padx=6, pady=(0, 2))
        follow_width(self.ports_state_lbl)
        self.net_warn_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=COLOR_PORT,
                                      font=FONT_LABEL_BOLD, anchor="w", justify="left")
        self.net_warn_lbl.pack(fill="x", padx=6, pady=(0, 4))
        follow_width(self.net_warn_lbl)
        self.live_rx_mwv_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_mwv_lbl.pack(fill="x", padx=6)
        follow_width(self.live_rx_mwv_lbl)
        self.live_rx_vhw_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_vhw_lbl.pack(fill="x", padx=6)
        follow_width(self.live_rx_vhw_lbl)
        self.live_rx_vtg_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_vtg_lbl.pack(fill="x", padx=6, pady=(0, 8))
        follow_width(self.live_rx_vtg_lbl)
        # Diagnostic d'une mesure MUETTE. Une grandeur qui n'arrive pas est
        # ce qui coute le plus cher : sans explication, on soupconne le
        # reseau ou l'instrument alors que la cause tient le plus souvent a
        # un simple reglage de source. Ce bandeau nomme la voie fautive.
        self.live_source_warn_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=COLOR_PORT,
                                              font=FONT_LABEL_BOLD, anchor="w",
                                              justify="left")
        self.live_source_warn_lbl.pack(fill="x", padx=6, pady=(0, 8))
        follow_width(self.live_source_warn_lbl)

        # --- 5. Comparaison des girouettes ---
        # N'apparait QUE si au moins deux voies annoncent du vent apparent
        # (voir _update_instruments) : sur une installation a une seule
        # girouette, ce bloc n'aurait rien a dire.
        self.f_anemo = section(inner, "Comparaison des girouettes")
        note(self.f_anemo, "Chaque voie qui annonce du vent apparent est affichee ici avec le TWA "
                            "qu'elle DONNERAIT si elle etait prioritaire -- c'est la seule facon de "
                            "voir qu'une girouette ment. Deux capteurs qui divergent de quelques "
                            "degres produisent des polaires franchement differentes, et c'est "
                            "toujours la voie prioritaire (fleche) qui construit la votre. "
                            "L'ordre de priorite se regle dans la roue dentee > Acquisition.",
             wraplength="auto")
        self.live_anemo_lbl = tk.Label(self.f_anemo, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=FONT_MONO, anchor="w", justify="left")
        self.live_anemo_lbl.pack(fill="x", padx=6)
        follow_width(self.live_anemo_lbl)
        self.live_anemo_warn_lbl = tk.Label(self.f_anemo, text="", bg=BG_APP, fg=COLOR_PORT,
                                             font=("Segoe UI", 9, "bold"), anchor="w",
                                             justify="left")
        self.live_anemo_warn_lbl.pack(fill="x", padx=6, pady=(2, 8))
        follow_width(self.live_anemo_warn_lbl)

        # --- Flux NMEA brut -- deplace ici depuis l'etape Enregistrement :
        # c'est pendant la prise qu'il sert (verifier d'un coup d'oeil que
        # la reception vit), pas au moment d'annoter des segments.
        # Repere de position pour le bloc "Comparaison des girouettes", qui
        # apparait et disparait selon le nombre de voies entendues et doit
        # retrouver sa place exacte (voir _update_anemo_compare).
        f_feed = self._live_feed_frame = section(inner, "Trames NMEA brutes")
        note(f_feed, "Flux brut recu sur les voies UDP, tel qu'il part dans le journal de "
                      "session et le tampon glissant -- simple verification visuelle que la "
                      "reception fonctionne.")
        feed_row = tk.Frame(f_feed, bg=BG_APP)
        feed_row.pack(fill="x", padx=6, pady=(0, 8))
        self.feed_text = tk.Text(feed_row, height=7, wrap="none", font=FONT_MONO,
                                  bg=BG_FIELD, fg=FG_LABEL, state="disabled",
                                  relief="solid", borderwidth=1,
                                  highlightthickness=0)
        feed_vsb = ttk.Scrollbar(feed_row, orient="vertical", command=self.feed_text.yview)
        # Ascenseur horizontal : une trame proprietaire ($PEUMA, ~120
        # caracteres) depasse la largeur a 125 / 150 %, et wrap="none" en
        # coupait la fin -- checksum compris -- sans recours. Les ascenseurs
        # sont poses AVANT le texte, pour que ce soit lui qui cede la place.
        feed_hsb = ttk.Scrollbar(feed_row, orient="horizontal", command=self.feed_text.xview)
        self.feed_text.configure(yscrollcommand=feed_vsb.set, xscrollcommand=feed_hsb.set)
        feed_hsb.pack(side="bottom", fill="x")
        feed_vsb.pack(side="right", fill="y")
        self.feed_text.pack(side="left", fill="both", expand=True)

        # Aucun bouton en bas : la seule commande de cet ecran est celle de
        # la barre du haut, toujours visible (voir plus haut).
        self._live_twa_geom = _draw_twa_gauge_background(self.live_twa_canvas,
                                                          self._live_twa_size)

    @staticmethod
    def _format_age(t, now):
        """Age d'une lecture, en texte court. Sert a distinguer d'un coup
        d'oeil 'ca arrive en continu' de 'la derniere valeur date d'une
        minute' -- une valeur figee mais plausible est le piege classique."""
        if t is None:
            return "jamais recue"
        age = now - t
        if age < 1.5:
            return "en direct"
        if age < 90:
            return f"il y a {age:.0f} s"
        return f"il y a {age / 60.0:.0f} min"

    def _damped_readings(self, now):
        """Valeurs amorties pour l'affichage, selon le reglage courant.
        Isolee pour rester testable et pour que l'amortissement ait un seul
        point d'entree : il ne doit jamais s'appliquer par accident a une
        valeur enregistree."""
        with self._engine_lock:
            return self.engine.display_readings(
                now, self.config_data.get("display_damping_s", 5.0))

    def _update_instruments(self):
        """Rafraichit le suivi en direct -- appele depuis _update_once() a
        chaque tick (400 ms), mais ne fait rien tant que l'etape Acquisition
        n'est pas a l'ecran : inutile de recalculer et de redessiner pendant
        que l'utilisateur regarde ses polaires."""
        if not hasattr(self, "live_twa_canvas") or self._current_step != "acquisition":
            return
        now = time.time()
        with self._engine_lock:
            apparent = self.engine.last_apparent
            apparent_by_port = dict(self.engine.last_apparent_by_port)
            instant = self.engine.last_instant
            stw_reading = self.engine.last_stw_reading
            sog_reading = self.engine.last_sog_reading
            station_wind = self.engine.last_station_wind
            status = self.engine.smoother.status(now)
            active = self.engine.active_ports(now)
            n_kept = len(self.engine.store)
            rejected_no_config = self.engine.stats["samples_rejected_no_config"]
            last_smoothed = self.engine.last_smoothed

        def fresh(reading, *keys):
            if not reading or reading.get("t") is None or now - reading["t"] > LIVE_STALE_S:
                return False
            return all(reading.get(k) is not None for k in keys)

        # --- 1. TWA / TWS / STW / SOG ---
        # Valeurs AMORTIES pour l'affichage (moyenne glissante courte, reglee
        # dans Affichage & fenetre) : des chiffres qui sautent a chaque trame
        # sont illisibles en mer. La FRAICHEUR, elle, continue de se juger
        # sur l'horodatage de la lecture BRUTE -- une moyenne calculee sur
        # des trames vieilles de deux minutes doit se griser comme les
        # autres, sans quoi l'amortissement masquerait une panne.
        damped = self._damped_readings(now)
        ok_tw = fresh(instant, "twa", "tws")
        twa = damped.get("twa") if ok_tw else None
        tws = damped.get("tws") if ok_tw else None
        cx, cy, r = self._live_twa_geom
        ok_tw = ok_tw and twa is not None and tws is not None
        ok_aw = fresh(apparent, "awa", "aws")
        awa = damped.get("awa") if ok_aw else None
        aws = damped.get("aws") if ok_aw else None
        ok_aw = ok_aw and awa is not None and aws is not None
        # Deux fleches de vent sur le meme cadran : vent VRAI (bleu, c'est
        # l'axe de la polaire) et vent APPARENT (orange, ce que montre la
        # girouette). Toutes deux soufflent VERS le bateau du centre, et leur
        # longueur suit la vitesse sur une echelle COMMUNE -- le vent le plus
        # fort est donc toujours la fleche la plus longue. Au pres, l'apparent
        # est plus serre ET plus fort que le vrai : la figure doit le montrer
        # telle quelle, sans quoi le tableau de bord ment.
        vmax = max([v for v in (tws if ok_tw else None, aws if ok_aw else None)
                    if v is not None] or [1.0])
        vmax = max(vmax, 1.0)
        # Epaisseurs a l'echelle de l'ecran, comme le cadran qui les porte.
        _draw_wind_arrow(self.live_twa_canvas, cx, cy, r,
                         twa if ok_tw else None, (tws / vmax) if ok_tw else 0.0,
                         FG_DIGIT, tag="wind_true", width=self._px(6))
        _draw_wind_arrow(self.live_twa_canvas, cx, cy, r,
                         awa if ok_aw else None, (aws / vmax) if ok_aw else 0.0,
                         COLOR_ACCENT, tag="wind_app", width=self._px(4))
        if ok_tw:
            side = "Tribord" if twa >= 0 else "Babord"
            self.live_twa_ro.set_value(f"{abs(twa):.0f} deg {side}")
        else:
            self.live_twa_ro.set_value("--- deg", stale=True)
        self.live_tws_ro.set_value(f"{tws:.1f} kn" if ok_tw else "--.- kn", stale=not ok_tw)
        ok_stw = fresh(stw_reading, "stw")
        stw = damped.get("stw") if ok_stw else None
        ok_stw = ok_stw and stw is not None
        self.live_stw_ro.set_value(f"{stw:.1f} kn" if ok_stw else "--.- kn", stale=not ok_stw)
        ok_sog = fresh(sog_reading, "sog")
        sog = damped.get("sog") if ok_sog else None
        ok_sog = ok_sog and sog is not None
        self.live_sog_ro.set_value(f"{sog:.1f} kn" if ok_sog else "--.- kn", stale=not ok_sog)

        # --- 2. Qualite de l'echantillonnage ---
        state = status["state"]
        recording = self.recording_active
        if state == "steady":
            # Hors acquisition, rien n'est "retenu" : on dit que les mesures
            # SERAIENT exploitables. Annoncer "mesures retenues" alors que
            # rien n'enregistre est precisement ce qui trompe.
            txt = ("Regime stable -- mesures retenues" if recording
                   else "Regime stable -- mesures exploitables (rien n'est enregistre)")
            color = COLOR_STARBOARD
        elif state == "filling":
            txt = f"Stabilisation en cours ({status['fill_frac'] * 100:.0f} %)"
            color = COLOR_ACCENT
        elif state in ("maneuver_twa", "maneuver_stw"):
            cause = "angle de vent" if state == "maneuver_twa" else "vitesse"
            txt = (f"Manoeuvre detectee ({cause}) -- mesures ecartees" if recording
                   else f"Manoeuvre detectee ({cause}) -- mesures inexploitables")
            color = COLOR_ACCENT
        else:
            manque = " et ".join(status["missing"]) or "donnees"
            txt, color = f"En attente : {manque}", COLOR_PORT
        self.live_quality_lbl.configure(text=txt, fg=color)

        if status["twa_spread_deg"] is None:
            self.live_quality_detail_lbl.configure(text="")
        else:
            self.live_quality_detail_lbl.configure(
                text=f"Variation sur {self.config_data['smoothing_window_s']:.0f} s  --  "
                     f"angle : {status['twa_spread_deg']:.0f} deg "
                     f"(seuil {self.config_data['maneuver_twa_deg']:.0f})   |   "
                     f"vitesse : {status['stw_spread_frac'] * 100:.0f} % "
                     f"(seuil {self.config_data['maneuver_stw_frac'] * 100:.0f})")

        last_t = last_smoothed.get("t") if last_smoothed else None
        self.live_yield_lbl.configure(
            text=(f"{n_kept} echantillon(s) retenu(s)   |   dernier : {self._format_age(last_t, now)}")
            if recording else "Aucune acquisition en cours -- aucun echantillon n'est produit")

        # Cas ou l'on enregistre pour rien : des echantillons sont bien
        # calcules, mais aucune configuration voiles/moteurs n'etait active
        # pour les rattacher. Signale ici parce que c'est precisement pendant
        # l'enregistrement qu'il est encore temps de corriger.
        if rejected_no_config > 0 and n_kept == 0:
            self.live_warning_lbl.configure(
                text=f"{rejected_no_config} echantillon(s) calcule(s) mais ECARTE(S) : aucune "
                     "configuration voiles/moteurs n'est active. Arretez l'acquisition et "
                     "redemarrez-la en cochant la configuration -- sinon cette prise ne "
                     "produira rien.")
            if not self.live_warning_lbl.winfo_ismapped():
                self.live_warning_lbl.pack(fill="x", padx=6, pady=(2, 6))
        elif self.live_warning_lbl.winfo_ismapped():
            self.live_warning_lbl.pack_forget()

        # --- 3. Configuration enregistree, et etat de l'acquisition ---
        # L'etat est repete dans la barre du haut, toujours visible : sur un
        # ecran qui affiche les memes cadrans qu'on enregistre ou non, la
        # question "est-ce que ca enregistre, la ?" doit avoir une reponse
        # sous les yeux en permanence.
        sails, engines = self._selected_sails(), self._selected_engines()
        cfg_txt = f"Voiles : {'+'.join(sails) or '-'}      Moteurs : {'+'.join(engines) or '-'}"
        self.live_config_lbl.configure(text=cfg_txt)
        if self.recording_active and self.recording_started_at is not None:
            elapsed = now - self.recording_started_at
            parts = [f"Enregistrement en cours depuis {int(elapsed) // 60:02d}:{int(elapsed) % 60:02d}"]
            if self.recording_deadline is not None:
                left = max(0.0, self.recording_deadline - now)
                parts.append(f"reste {int(left) // 60:02d}:{int(left) % 60:02d}")
            self.live_timer_lbl.configure(text="   |   ".join(parts))
        else:
            self.live_timer_lbl.configure(text="Aucune acquisition en cours.")

        # --- 4. Reception --- (ok_aw/awa/aws deja calcules pour les fleches)
        aw_val = f"{abs(awa):3.0f} deg {'Tri' if awa >= 0 else 'Bab'} / {aws:.1f} kn" if ok_aw else "---"
        self.live_rx_mwv_lbl.configure(
            text=f"Vent apparent (MWV)  : {aw_val:<26} {self._format_age(apparent.get('t') if apparent else None, now)}"
                 f"   voie : {self.port_label(active.get('MWV')) if active.get('MWV') else '-'}",
            fg=(FG_LABEL if ok_aw else COLOR_PORT))
        self.live_rx_vhw_lbl.configure(
            text=f"Vitesse surface (VHW): {(f'{stw:.1f} kn' if ok_stw else '---'):<26} "
                 f"{self._format_age(stw_reading.get('t') if stw_reading else None, now)}"
                 f"   voie : {self.port_label(active.get('VHW')) if active.get('VHW') else '-'}",
            fg=(FG_LABEL if ok_stw else COLOR_PORT))
        ecart = ""
        if ok_sog and ok_stw:
            ecart = f"   ecart STW/SOG : {stw - sog:+.1f} kn"
        self.live_rx_vtg_lbl.configure(
            text=f"Vitesse fond (VTG)   : {(f'{sog:.1f} kn' if ok_sog else '---'):<26} "
                 f"{self._format_age(sog_reading.get('t') if sog_reading else None, now)}{ecart}",
            fg=FG_LABEL_DIM)

        self._refresh_ports_state()
        self._update_source_diagnosis({"MWV": ok_aw, "VHW": ok_stw, "VTG": ok_sog})

        self._update_anemo_compare(apparent_by_port, active.get("MWV"), stw if ok_stw else None,
                                    now, station_wind)

    def _update_source_diagnosis(self, ok_by_type):
        """Explique une mesure qui n'arrive pas, quand la cause est un
        reglage et non une panne.

        Le cas vecu : une voie designee comme source du vent n'emettait que
        du MWV en reference 'T' (une direction par rapport au nord, refusee a
        dessein). Elle gagnait l'arbitrage a chaque trame sans jamais rien
        apporter, la vraie girouette etait ecartee en silence, et le vent
        n'etait plus jamais lu -- sans le moindre message. Le moteur ne se
        laisse plus faire (voir _accept_for_priority), mais il faut encore le
        DIRE : un reglage qui ne produit pas ce qu'on attend doit se
        signaler."""
        if not hasattr(self, "live_source_warn_lbl"):
            return
        msgs = []
        with self._engine_lock:
            health = {styp: self.engine.measurement_health(styp)
                      for styp, _lbl in pe.MEASUREMENT_SOURCES}
        for styp, label in pe.MEASUREMENT_SOURCES:
            h = health.get(styp) or {}
            mute = h.get("chain_mute") or []
            if not mute:
                continue
            provs = h.get("providers") or []
            names = ", ".join(self.port_label(p, with_key=False) for p in mute)
            if provs:
                who = ", ".join(self.port_label(p, with_key=False) for p in provs)
                msgs.append(
                    f"{label} : la source designee ({names}) n'apporte rien -- "
                    f"c'est {who} qui fournit la mesure. Corrigez le choix dans "
                    "la roue dentee > Sources & tampon.")
            elif not ok_by_type.get(styp, False):
                talkers = [p for p in (h.get("mute_talkers") or []) if p in mute]
                why = (" (elle emet bien ce type de trame, mais rien d'exploitable : "
                       "vent en reference 'T', champ vide...)" if talkers else
                       " (aucune trame recue de cette voie)")
                msgs.append(f"{label} : la source designee ({names}) n'apporte rien{why}, "
                            "et aucune autre voie ne fournit cette mesure.")
        self.live_source_warn_lbl.configure(text="\n".join(msgs))
        if msgs and not self.live_source_warn_lbl.winfo_ismapped():
            self.live_source_warn_lbl.pack(fill="x", padx=6, pady=(0, 8))
        elif not msgs and self.live_source_warn_lbl.winfo_ismapped():
            self.live_source_warn_lbl.pack_forget()

    # Ecarts au-dela desquels deux girouettes ne peuvent plus etre considerees
    # comme d'accord : au-dela, ce n'est plus du bruit de mesure, c'est un
    # capteur (ou un reglage d'unite) fautif -- et la polaire s'en ressent
    # directement, puisqu'une seule des deux voies la construit.
    ANEMO_DIFF_TWA_DEG = 8.0
    ANEMO_DIFF_AWS_FRAC = 0.15

    # Ecart au-dela duquel une girouette et la station meteo du bord ne
    # racontent plus la meme histoire. Plus tolerant que l'ecart entre deux
    # girouettes : la station moyenne le vent sur plusieurs minutes, la
    # girouette le donne instantanement -- un ecart modere est normal.
    STATION_DIFF_TWS_FRAC = 0.25

    def _update_anemo_compare(self, apparent_by_port, active_mwv, stw, now,
                              station_wind=None):
        """Bloc "Comparaison des girouettes" : une ligne par voie qui annonce
        du vent apparent, avec le TWA qu'elle DONNERAIT si elle etait
        prioritaire -- et, quand le bord en porte une, le vent VRAI mesure
        par la station meteo ($PEUMA), qui sert d'arbitre.

        Masque tant qu'il n'y a rien a comparer : deux girouettes, ou une
        girouette et la station."""
        if not hasattr(self, "live_anemo_lbl"):
            return
        fresh = {p: v for p, v in apparent_by_port.items()
                 if v.get("t") is not None and now - v["t"] <= LIVE_STALE_S
                 and v.get("awa") is not None and v.get("aws") is not None}
        station = station_wind if (station_wind and station_wind.get("t") is not None
                                    and now - station_wind["t"] <= LIVE_STALE_S
                                    and station_wind.get("tws") is not None) else None
        # Une seule girouette SUFFIT des lors que la station peut l'arbitrer :
        # c'est meme le cas le plus utile (savoir si l'unique girouette du
        # bord dit vrai). Sans station, il faut toujours deux voies.
        if len(fresh) < 2 and not (fresh and station):
            if self.f_anemo.winfo_manager():
                self.f_anemo.pack_forget()
            return
        if not self.f_anemo.winfo_manager():
            # Reprend sa place dans le flux de la vue en direct (juste avant
            # le flux NMEA brut, qui ferme la page).
            self.f_anemo.pack(fill="x", padx=10, pady=(10, 4), before=self._live_feed_frame)

        lines, twas, awss = [], {}, {}
        # Colonne du nom a la largeur du plus long : les noms de voie vont
        # jusqu'a 40 caracteres, une largeur figee a 22 decalait les colonnes
        # "vent apparent" et "->" des que deux noms differaient de longueur.
        name_w = max([len(self.port_label(p)) for p in fresh]
                     + [len("Station meteo du bord")])
        for p in sorted(fresh):
            v = fresh[p]
            mark = "\u25b8" if p == active_mwv else " "  # voie prioritaire
            twa_p, tws_p = (None, None) if stw is None else \
                pe.apparent_to_true(v["awa"], v["aws"], stw)
            if twa_p is not None:
                twas[p], awss[p] = twa_p, v["aws"]
                calc = f"TWA {twa_p:+6.1f} deg   TWS {tws_p:5.2f} kn"
            else:
                awss[p] = v["aws"]
                calc = "TWA/TWS : vitesse surface inconnue"
            lines.append(f"{mark} {self.port_label(p):<{name_w}s} vent apparent {v['awa']:+6.1f} deg"
                         f" / {v['aws']:5.2f} kn   ->   {calc}")
        if station is not None:
            extra = []
            if station.get("gust") is not None:
                extra.append(f"rafale {station['gust']:5.2f} kn")
            if station.get("dir") is not None:
                extra.append(f"direction {station['dir']:.0f} deg / nord")
            lines.append(f"  {'Station meteo du bord':<{name_w}s} vent VRAI mesure "
                         f"{station['tws']:5.2f} kn"
                         + ("   (" + ", ".join(extra) + ")" if extra else ""))
        lines.append("  (\u25b8 = voie prioritaire : c'est elle qui construit la polaire)")
        if station is not None:
            lines.append("  (la station donne un vent vrai REFERENCE AU NORD : elle arbitre "
                         "la FORCE du vent, jamais l'angle a l'etrave)")
        self.live_anemo_lbl.configure(text="\n".join(lines))

        alerts = []
        if station is not None and twas:
            # Arbitrage par la station : c'est exactement ce recoupement qui a
            # permis de confondre une girouette mal etalonnee sur ce bateau.
            ref = station["tws"]
            for p, twa_p in twas.items():
                _awa_p, _aws_p = fresh[p]["awa"], fresh[p]["aws"]
                tws_p = pe.apparent_to_true(_awa_p, _aws_p, stw)[1]
                if ref > 0.5 and tws_p is not None and abs(tws_p - ref) / ref >= self.STATION_DIFF_TWS_FRAC:
                    alerts.append(f"{self.port_label(p, with_key=False)} annonce "
                                  f"{tws_p:.1f} kn la ou la station en mesure {ref:.1f}")
        if len(twas) >= 2:
            spread = max(twas.values()) - min(twas.values())
            if spread >= self.ANEMO_DIFF_TWA_DEG:
                alerts.append(f"{spread:.0f} deg d'ecart de TWA entre les voies")
        if len(awss) >= 2:
            lo, hi = min(awss.values()), max(awss.values())
            if lo > 0 and (hi - lo) / lo >= self.ANEMO_DIFF_AWS_FRAC:
                alerts.append(f"{100 * (hi - lo) / lo:.0f} % d'ecart de force du vent")
        if alerts:
            self.live_anemo_warn_lbl.configure(
                text="Les girouettes ne sont pas d'accord (" + ", ".join(alerts) + "). "
                     "Votre polaire est construite sur la voie prioritaire uniquement : si c'est "
                     "la mauvaise, tous les angles en heritent. Verifiez laquelle est juste "
                     "(la station meteo du bord, quand il y en a une, sert d'arbitre pour la "
                     "force du vent) et designez la bonne source dans la roue dentee > "
                     "Sources & tampon.")
        else:
            self.live_anemo_warn_lbl.configure(text="")

    # =====================================================================
    # Boucle de rafraichissement
    # =====================================================================
    def report_callback_exception(self, exc_type, exc_value, exc_tb):
        """Erreur survenue DANS un gestionnaire d'evenement Tk (un clic, une
        touche). Par defaut Tk l'ecrit sur la sortie d'erreur et poursuit --
        sans console, elle disparaissait donc sans laisser de trace, et
        l'utilisateur voyait seulement un bouton qui "ne fait rien"."""
        _report_crash(exc_type, exc_value, exc_tb, "interface")

    def _refresh_tick(self):
        try:
            self._update_once()
        except Exception:
            # Le tick tourne 2,5 fois par seconde : une erreur qui s'y
            # repete noierait l'utilisateur sous les boites de dialogue.
            # On la CONSIGNE (une seule fois par type d'erreur) sans
            # l'annoncer, et la boucle survit -- c'est elle qui tient le
            # tampon, l'acquisition et le suivi en direct.
            exc_type, exc_value, exc_tb = sys.exc_info()
            key = (exc_type, str(exc_value))
            if key not in self._tick_errors_seen:
                self._tick_errors_seen.add(key)
                log_exception(exc_type, exc_value, exc_tb, "boucle de rafraichissement")
        self._refresh_tick_id = self.after(400, self._refresh_tick)

    @staticmethod
    def _format_live_reading(reading, label):
        if not reading:
            return f"{label} : (en attente de donnees)"
        twa, tws, stw = reading.get("twa"), reading.get("tws"), reading.get("stw")
        parts = []
        if twa is not None:
            parts.append(f"TWA {twa:+.0f} deg")
        if tws is not None:
            parts.append(f"TWS {tws:.1f} nds")
        if stw is not None:
            parts.append(f"STW {stw:.1f} nds")
        t = reading.get("t")
        when = f" (a {time.strftime('%H:%M:%S', time.localtime(t))})" if t else ""
        return f"{label} : {'  '.join(parts) or '?'}{when}"

    def _update_once(self):
        self._drain_feed()
        with self._engine_lock:
            # Le moteur collecte SI ET SEULEMENT SI une acquisition est en
            # cours -- realigne a chaque tick plutot que laisse a la charge
            # de chaque appelant. Deux drapeaux qu'il faut penser a tenir
            # d'accord finissent toujours par diverger, et la divergence
            # signifierait ici des echantillons fabriques hors de toute
            # prise. (Les rejeux -- import, re-analyse, recalcul -- sont
            # synchrones : aucun tick ne s'intercale pour les contredire.)
            self.engine.collecting = bool(self.recording_active)
        if self.recording_active:
            now = time.time()
            with self._engine_lock:
                self.engine.tick(now)
            # Guetteur de manoeuvre : uniquement PENDANT une prise (hors
            # prise, il n'y a rien a arreter), et jamais deux questions a
            # la fois (voir _poll_maneuver_watch).
            self._poll_maneuver_watch(now)
            if self.recording_deadline is not None:
                remaining = self.recording_deadline - now
                if remaining <= 0:
                    # Meme geste que le bouton d'arret : la session est
                    # traitee dans la foulee et part dans l'entrepot (un
                    # simple arret laisserait une session en attente, sans
                    # plus aucun bouton pour la traiter depuis la v1.0b).
                    self._process_session()
                    messagebox.showinfo("Enregistrement",
                                        "Duree d'enregistrement atteinte : la prise a ete arretee "
                                        "et la session envoyee a l'entrepot.")
                else:
                    mins, secs = divmod(int(remaining), 60)
                    # Le compte a rebours s'affiche dans la barre permanente
                    # (voir _refresh_acq_bar) : rien a faire ici.
                    pass

        with self._engine_lock:
            stats = dict(self.engine.stats)
            n_store = len(self.engine.store)
            has_segments = bool(self.engine.journal.all_segments())
            last_instant = self.engine.last_instant
            last_smoothed = self.engine.last_smoothed
        self._refresh_acq_bar()
        self._update_instruments()

        # Theme automatique : controle une fois par minute environ (le tick
        # tourne a 400 ms), suffisant pour un phenomene aussi lent qu'un
        # coucher de soleil -- et gratuit le reste du temps.
        self._theme_check_counter += 1
        if self._theme_check_counter >= 150:
            self._theme_check_counter = 0
            self._theme_tick()

        if hasattr(self, "save_params_btn"):
            pending = self.recording_active or n_store > 0 or has_segments
            # Le bouton reste TOUJOURS actif : la quasi-totalite des reglages
            # peut se modifier a tout moment. Seuls quelques-uns entrent en
            # conflit avec une prise, et _save_params les traite au cas par
            # cas -- desactiver le bouton en bloc empechait par exemple de
            # regler le tampon apres un enregistrement, ce qui n'avait aucune
            # raison d'etre interdit.
            self.save_params_btn.configure(state="normal")
            if self.recording_active:
                msg = ("Enregistrement en cours : le lissage, les listes voiles/moteurs et les "
                       "voies UDP ne peuvent pas changer en route. Les autres reglages "
                       "(tampon, table polaire, duree par defaut) restent modifiables.")
            elif pending:
                msg = ("Session en attente de traitement : si vous modifiez le lissage ou la "
                       "detection de manoeuvre, il vous sera propose de RE-ANALYSER cette "
                       "session avec les nouveaux reglages, sans perdre vos segments. Les "
                       "autres reglages se modifient sans consequence.")
            else:
                msg = ""
            # Rouge tant qu'il s'agit d'une vraie restriction (enregistrement
            # en cours) ; simple information, en gris, quand il ne s'agit plus
            # que de signaler qu'une re-analyse sera proposee.
            self.params_pending_lbl.configure(
                text=msg, fg=(COLOR_PORT if self.recording_active else FG_LABEL_DIM))
            if hasattr(self, "goto_session_btn"):
                if pending:
                    # Servi AVANT le message (before=) : c'est le message qui
                    # passe a la ligne, jamais le bouton qui est ecrase.
                    self.goto_session_btn.pack(side="right", padx=(10, 0),
                                               before=self.params_pending_lbl)
                else:
                    self.goto_session_btn.pack_forget()

    # =====================================================================
    # Fermeture
    # =====================================================================
    def _on_close(self):
        """Croix de la fenetre. Si l'option 'zone de notification' est active
        ET que pystray est disponible, la croix RANGE l'application au lieu de
        la fermer : tout continue de tourner (tampon glissant, enregistrement
        en cours...), et l'extinction reelle se fait depuis l'icone de
        notification (menu Quitter). Sinon, comportement classique."""
        if self.config_data.get("close_to_tray", True) and self._hide_to_tray():
            return
        self._shutdown()

    def _hide_to_tray(self):
        """Tente de ranger la fenetre dans la zone de notification. False si
        pystray (ou Pillow, dont il a besoin pour l'icone) est absent ou si
        le lancement de l'icone echoue -- l'appelant ferme alors normalement,
        l'option ne peut jamais bloquer la fermeture."""
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:
            # ImportError, mais aussi tout echec de backend au chargement
            # (environnement sans zone de notification).
            return False
        try:
            if self._tray_icon is None:
                # Icone dessinee a la volee (aucun fichier a distribuer) :
                # disque bleu accent + voile blanche stylisee.
                img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                d.ellipse((2, 2, 62, 62), fill=(26, 95, 180, 255))
                d.polygon([(34, 10), (34, 44), (16, 44)], fill=(255, 255, 255, 255))
                d.polygon([(38, 16), (48, 44), (38, 44)], fill=(255, 255, 255, 230))
                d.line([(14, 50), (50, 50)], fill=(255, 255, 255, 255), width=3)
                menu = pystray.Menu(
                    pystray.MenuItem("Ouvrir Allure", self._tray_open, default=True),
                    pystray.MenuItem("Quitter Allure", self._tray_quit))
                self._tray_icon = pystray.Icon(
                    "allure", img, f"{pcfg.APP_NAME} v{pcfg.APP_VERSION}", menu)
                # Boucle de l'icone dans son propre thread : la boucle Tk
                # continue de tourner independamment (fenetre juste retiree).
                self._tray_icon.run_detached()
            self.withdraw()
            return True
        except Exception:
            # pystray peut echouer sur certains environnements (pas de zone
            # de notification, affichage distant...) : on ferme normalement.
            self._tray_icon = None
            return False

    # Les rappels pystray arrivent dans le THREAD DE L'ICONE : ils ne font
    # que reposter vers la boucle Tk via after(), seule habilitee a toucher
    # aux widgets.
    def _tray_open(self, icon=None, item=None):
        self.after(0, self._restore_from_tray)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit_from_tray)

    def _restore_from_tray(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except tk.TclError:
            pass

    def _quit_from_tray(self):
        # La fenetre d'abord : une eventuelle confirmation (session non
        # traitee) a besoin d'etre visible pour etre comprise.
        self._restore_from_tray()
        self._shutdown()

    def _shutdown(self):
        """Extinction reelle. Les rappels du tick ne sont annules qu'UNE FOIS
        la fermeture decidee : repondre 'Annuler' a la confirmation laisse
        une application parfaitement vivante (l'ancienne version tuait le
        rafraichissement avant de poser la question...)."""
        with self._engine_lock:
            pending = len(self.engine.store) > 0 or bool(self.engine.journal.all_segments())
        if pending:
            choice = messagebox.askyesnocancel(
                "Fermeture",
                "Une session n'a pas encore ete envoyee a l'entrepot. "
                "Voulez-vous la traiter maintenant avant de quitter ?\n\n"
                "Oui = traiter puis quitter -- Non = quitter sans traiter (les donnees de cette "
                "session seront perdues) -- Annuler = ne pas quitter.")
            if choice is None:
                return
            if choice:
                self._process_session()

        ident = getattr(self, "_refresh_tick_id", None)
        if ident is not None:
            try:
                self.after_cancel(ident)
            except tk.TclError:
                pass
            self._refresh_tick_id = None
        if self.recording_active:
            self._stop_recording()
        self._close_udp_listeners()
        if self.buffer is not None:
            self.buffer.close()
        # Derniere position GPS memorisee pour le theme auto du prochain
        # lancement (voir _theme_tick) -- ecrite avec le reste de l'etat,
        # comme la taille de la fenetre.
        self._remember_main_window()
        pcfg.save_ui_state(self.ui_state)
        if self._tray_icon is not None:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None
        self.destroy()



# =========================================================================
# Instance unique
# =========================================================================

# Port de bouclage servant de verrou : ouvrir une seconde application
# echoue a le reserver, ce qui suffit a la detecter. Un socket plutot qu'un
# fichier de verrou parce qu'il n'a PAS de probleme de verrou fantome : si
# l'application est tuee ou si la machine s'eteint brutalement, le systeme
# libere le port tout seul, alors qu'un fichier survivrait et interdirait
# tout lancement ulterieur. Port eleve et peu banal pour ne rien bousculer.
SINGLE_INSTANCE_PORT = 49721


def acquire_single_instance_lock():
    """Reserve le verrou d'instance. Retourne le socket (a garder vivant tant
    que l'application tourne) ou None si une autre instance le detient deja.

    L'ecoute est strictement locale (127.0.0.1) : aucun autre poste du reseau
    ne peut voir ni prendre ce verrou, et deux ordinateurs du bord peuvent
    donc parfaitement faire tourner Allure chacun de leur cote."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # PAS de SO_REUSEADDR ici : c'est justement l'exclusivite qu'on veut.
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


# =========================================================================
# Point d'entree
#
# Un SEUL fichier de lancement : allure.py. Ce qui decide de la presence
# d'une console sous Windows n'est pas l'extension mais l'EXECUTABLE :
#
#   python allure.py     avec console -- utile pour voir une erreur de
#                           demarrage en direct, ou pour les tests.
#   pythonw allure.py    sans console (raccourci Windows dont la cible
#                           est "...\pythonw.exe" "...\allure.py").
#
# Un lanceur .pyw a existe ici : il ne servait qu'a demander pythonw.exe par
# l'extension, ce qu'un raccourci fait aussi bien et sans dependre d'une
# association de fichiers qui, sur un poste reel, pointe rarement ou l'on
# croit. Un fichier de moins, un piege de moins.
#
# Sans console, une erreur non rattrapee disparaitrait SANS UN MOT : c'est
# la contrepartie qu'il faut payer, et elle est payee ici. Toute exception
# est ecrite dans journaux/erreurs.log ET annoncee par une boite de
# dialogue (voir install_crash_reporting). Une application qui se ferme
# toute seule sans rien dire est le pire des comportements.
# =========================================================================

ERROR_LOG_NAME = "erreurs.log"


class _NullStream:
    """Sortie muette. Sous pythonw.exe, sys.stdout et sys.stderr valent
    None : le moindre print() -- le notre, ou celui d'une bibliotheque --
    leverait AttributeError et emporterait l'application. Mieux vaut une
    sortie qui avale tout qu'un plantage pour une ligne de trace."""

    def write(self, _data):
        return 0

    def flush(self):
        pass

    def isatty(self):
        return False


def error_log_path():
    pcfg.ensure_dirs()
    return os.path.join(pcfg.LOGS_DIR, ERROR_LOG_NAME)


def log_exception(exc_type, exc_value, exc_tb, context=""):
    """Ecrit une exception dans journaux/erreurs.log et retourne le chemin.

    Ne leve jamais : appelee depuis un gestionnaire d'erreur, elle serait
    la derniere chose a pouvoir echouer."""
    try:
        path = error_log_path()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} "
                     f"-- {pcfg.APP_NAME} v{pcfg.APP_VERSION}"
                     + (f" -- {context}" if context else "") + " =====\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=fh)
        return path
    except Exception:
        return None


def _report_crash(exc_type, exc_value, exc_tb, context=""):
    path = log_exception(exc_type, exc_value, exc_tb, context)
    try:
        messagebox.showerror(
            f"{pcfg.APP_NAME} -- erreur inattendue",
            f"{exc_type.__name__} : {exc_value}\n\n"
            + (f"Le detail complet a ete ecrit dans :\n{path}\n\n" if path else "")
            + "L'application essaie de continuer. Si le probleme se repete, "
              "ce fichier dit exactement ce qui s'est passe.")
    except Exception:
        pass


def install_crash_reporting():
    """Rend visible ce qu'une fenetre sans console rendrait invisible.

    Deux chemins distincts, et il faut les deux : sys.excepthook attrape ce
    qui casse au demarrage (avant la boucle Tk), report_callback_exception
    attrape ce qui casse DANS un gestionnaire d'evenement -- un clic, un
    tick -- ou Tk avale l'exception et poursuit comme si de rien n'etait."""
    if getattr(sys, "stdout", None) is None:
        sys.stdout = _NullStream()
    if getattr(sys, "stderr", None) is None:
        sys.stderr = _NullStream()
    sys.excepthook = lambda t, v, tb: _report_crash(t, v, tb, "demarrage")
    # Les threads ont leur PROPRE gestionnaire : sys.excepthook ne les voit
    # pas. Une voie d'ecoute qui meurt le ferait donc en silence -- et l'on
    # chercherait longtemps pourquoi plus rien n'arrive.
    if hasattr(threading, "excepthook"):
        threading.excepthook = lambda a: log_exception(
            a.exc_type, a.exc_value, a.exc_traceback,
            f"thread {getattr(a.thread, 'name', '?')}")


# Les sorties standard etaient-elles ABSENTES au chargement ? C'est la
# signature d'un lancement sans console (pythonw.exe, ou un .exe PyInstaller
# construit en mode fenetre), relevee AVANT que install_crash_reporting ne
# les remplace par des flux muets -- apres, l'indice a disparu.
_STDIO_ABSENT_AT_START = sys.stdout is None or sys.stderr is None


def running_windowless():
    """Le programme tourne-t-il VRAIMENT sans console ?

    Ni le nom du fichier ni la facon dont on l'a ouvert ne repondent : un
    raccourci peut annoncer pythonw.exe et lancer python.exe, une
    association de fichiers peut avoir ete reprise par un autre programme.
    Les juges : l'executable reellement en train de tourner, et l'absence
    de sorties standard au chargement (le cas d'un .exe autonome construit
    en mode fenetre, dont le nom ne dit rien)."""
    return (os.path.basename(sys.executable or "").lower().startswith("pythonw")
            or _STDIO_ABSENT_AT_START)


def main():
    """Lance l'application. Unique chemin de demarrage, que l'on passe par
    'python allure.py' ou par 'pythonw allure.py' -- pour qu'il n'y
    ait jamais deux comportements a maintenir."""
    install_crash_reporting()
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            f"{pcfg.APP_NAME} deja en service",
            f"{pcfg.APP_NAME} est DEJA EN SERVICE sur cet ordinateur.\n\n"
            "Cette fenetre-ci ne s'ouvrira pas : deux exemplaires ecouteraient les memes "
            "voies UDP et ecriraient dans le meme entrepot, ce qui melangerait vos mesures.\n\n"
            "Retrouvez la fenetre deja ouverte (barre des taches, ou icone de la zone de "
            "notification a cote de l'horloge si vous l'y avez rangee).")
        return 0
    try:
        app = App()
    except ImportError as e:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Dependance manquante",
            f"Module manquant : {e.name}.\n\nOuvrez PowerShell et executez :\n\n"
            "    python -m pip install matplotlib\n\npuis relancez l'application.",
        )
        return 1
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
