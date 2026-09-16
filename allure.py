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
       fichier .log -- annotation voiles/moteurs/derive, puis "Traiter la
       session".
    2. ENTREPOT : les echantillons de chaque passe traitee s'y accumulent
       durablement (polar_store.json, jamais ecrase) ; interrogation des
       archives par date/heure, correction a posteriori d'une annotation,
       corbeille.
    3. POLAIRES : trace (automatique, recalcule des qu'on arrive sur
       l'etape ou qu'on change de configuration) + export .pol / .csv.

- Les PARAMETRES s'ouvrent par la roue dentee en haut a droite : une page
  qui remplace le contenu courant, organisee par categories (bandeau de
  navigation a droite) -- voiles/moteurs, voies d'ecoute, lissage,
  tampon glissant, table polaire, sauvegardes. "Enregistrer" ramene a
  l'etape d'ou l'on venait.

Prerequis -- UNE SEULE commande :
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
import re
import socket
import sys
import threading
import time
import traceback
import tkinter as tk
from collections import deque
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
from matplotlib.ticker import MaxNLocator
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


def make_scrollable(parent, height=420):
    """Zone defilante verticale (canvas + frame interne). Le point delicat :
    caler la scrollregion sur bbox("all") EN PERMANENCE (comme avant) rend le
    canvas "defilable" d'une poignee de pixels meme quand tout le contenu
    tient deja dans la zone visible (marges internes, arrondis de calcul...) --
    ca se traduisait par un contenu qui glissait legerement de haut en bas
    sans raison a la molette/au trackpad. Ici, quand le contenu (inner) est
    moins haut que la zone visible (canvas), la scrollregion est calee EXACTEMENT
    sur la hauteur visible : aucun defilement residuel n'est plus possible, et
    la molette est carrement ignoree (evenement laisse remonter) tant qu'il n'y
    a reellement rien a faire defiler."""
    outer = tk.Frame(parent, bg=BG_APP)
    canvas = tk.Canvas(outer, bg=BG_APP, highlightthickness=0, height=height)
    vsb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    inner = tk.Frame(canvas, bg=BG_APP)
    win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    state = {"canvas_h": height, "inner_h": 0}

    def _sync_scrollregion():
        width = canvas.winfo_width() or 1
        if state["inner_h"] <= state["canvas_h"]:
            canvas.configure(scrollregion=(0, 0, width, state["canvas_h"]))
        else:
            canvas.configure(scrollregion=(0, 0, width, state["inner_h"]))

    def _on_inner_configure(_event):
        state["inner_h"] = inner.winfo_reqheight()
        _sync_scrollregion()

    def _on_canvas_configure(event):
        canvas.itemconfig(win_id, width=event.width)
        state["canvas_h"] = event.height
        _sync_scrollregion()

    def _on_mousewheel(event):
        if state["inner_h"] <= state["canvas_h"]:
            return  # contenu deja entierement visible -- rien a defiler
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)
    canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
    canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

    return outer, inner


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


def note(parent, text, pady=(0, 8), wraplength=620):
    """wraplength="auto" : au lieu d'une largeur de retour a la ligne fixe en
    pixels (qui deborde du texte hors de la fenetre si celle-ci est plus
    etroite que prevu, ou gaspille de la place si elle est plus large), le
    label suit la largeur reellement disponible de son parent a chaque
    redimensionnement -- vraiment responsive, pas juste une valeur fixe
    choisie pour une taille de fenetre particuliere."""
    lbl = tk.Label(parent, text=text, bg=BG_APP, fg=FG_LABEL_DIM,
                    justify="left", font=("Segoe UI", 8))
    if wraplength == "auto":
        lbl.pack(anchor="w", fill="x", padx=6, pady=pady)
        lbl.bind("<Configure>", lambda e: lbl.configure(wraplength=max(200, e.width - 12)))
    else:
        lbl.configure(wraplength=wraplength)
        lbl.pack(anchor="w", padx=6, pady=pady)
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
    cx = cy = size / 2.0
    r = size / 2.0 - 24
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=180,
                       fill=GAUGE_PORT_TINT, outline="", style="pieslice")
    canvas.create_arc(cx - r, cy - r, cx + r, cy + r, start=270, extent=180,
                       fill=GAUGE_STARBOARD_TINT, outline="", style="pieslice")
    canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=COLOR_TICK, width=1)
    for mag in (30, 60, 90, 120, 150):
        for side in (-1, 1):  # -1 = babord (gauche), +1 = tribord (droite)
            ang = math.radians(90 - side * mag)
            x1, y1 = cx + (r - 7) * math.cos(ang), cy - (r - 7) * math.sin(ang)
            x2, y2 = cx + r * math.cos(ang), cy - r * math.sin(ang)
            canvas.create_line(x1, y1, x2, y2, fill=COLOR_TICK, width=1)
            tx, ty = cx + (r - 20) * math.cos(ang), cy - (r - 20) * math.sin(ang)
            canvas.create_text(tx, ty, text=str(mag), fill=FG_LABEL_DIM, font=("Segoe UI", 7))
    # Axes 0/180 marques par un simple trait plus frappe -- AUCUN texte dans
    # ni autour du cadran (ni "etrave"/"arriere", ni "babord"/"tribord") :
    # les teintes des deux moities et la position des chiffres suffisent, et
    # tout texte finirait de toute facon sous l'une des aiguilles.
    for mag in (0, 180):
        ang = math.radians(90 - mag)
        x1, y1 = cx + (r - 10) * math.cos(ang), cy - (r - 10) * math.sin(ang)
        x2, y2 = cx + r * math.cos(ang), cy - r * math.sin(ang)
        canvas.create_line(x1, y1, x2, y2, fill=FG_LABEL_DIM, width=2)

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
# Statistiques -- petits composants et formatages (voir
# App._build_step_statistiques). Regroupes ici, au niveau du module, parce
# qu'ils ne dependent d'aucun etat de l'application : ils se testent seuls.
# =========================================================================

def _fmt_span(seconds):
    """Duree en toutes lettres courtes : "45 s", "5 min", "1 h 30", "2 h"."""
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        s = 0.0
    if s < 90:
        return f"{s:.0f} s"
    minutes = s / 60.0
    if minutes < 60:
        return f"{minutes:.0f} min"
    hours = int(minutes // 60)
    rest = int(round(minutes - hours * 60))
    if rest >= 60:      # arrondi qui deborde (119,7 min -> 2 h, pas 1 h 60)
        hours += 1
        rest = 0
    return f"{hours} h" if rest == 0 else f"{hours} h {rest:02d}"


def _parse_span(text):
    """Inverse de _fmt_span, pour relire ce qu'un menu deroulant affiche.
    None si la chaine n'est pas une duree."""
    if not isinstance(text, str):
        return None
    # L'espace insecable est celui que produisent certaines polices/
    # locales dans "5 min" : sans cette normalisation, le menu
    # deroulant se relirait comme une chaine inconnue.
    t = text.strip().lower().replace("\u00a0", " ")
    m = re.match(r"^(\d+)\s*h(?:\s*(\d+))?$", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2) or 0) * 60
    m = re.match(r"^(\d+)\s*min$", t)
    if m:
        return int(m.group(1)) * 60
    m = re.match(r"^(\d+)\s*s$", t)
    if m:
        return int(m.group(1))
    return None


def _fmt_measure(value, kind, decimals):
    """Valeur formatee pour une case de tableau. Les angles bord-relatifs
    gardent leur SIGNE (babord/tribord), les releves absolus sont ecrits sur
    trois chiffres comme un cap."""
    if value is None:
        return "--"
    if kind == "angle_rel":
        return f"{value:+.0f}"
    if kind == "angle_abs":
        return f"{value % 360.0:03.0f}"
    return f"{value:.{decimals}f}"


# Variation horaire au-dela de laquelle une tendance merite d'etre signalee,
# par grandeur. En dessous, la pente n'est que du bruit de mesure : afficher
# une fleche pour 0,1 hPa/h ferait croire a un evenement.
_TREND_THRESHOLDS = {
    "tws": 1.0, "aws": 1.0, "wind_gust_kn": 1.5,
    "stw": 0.3, "sog": 0.3,
    "pressure": 0.5, "air_temp": 0.5, "water_temp": 0.5, "humidity": 3.0,
    "depth": 2.0,
}
# Grandeurs dont la HAUSSE merite l'oeil, et celles dont c'est la BAISSE.
# Le barometre est le seul a l'envers -- et c'est justement celui qu'on
# regarde pour cette raison.
_TREND_WATCH_UP = ("tws", "aws", "wind_gust_kn")
_TREND_WATCH_DOWN = ("pressure",)


def _trend_arrow(key, slope_per_h, _unit):
    """(fleche, couleur) resumant une pente horaire."""
    thr = _TREND_THRESHOLDS.get(key, 0.5)
    if abs(slope_per_h) < thr:
        return "→", FG_LABEL_DIM
    up = slope_per_h > 0
    watched = (up and key in _TREND_WATCH_UP) or (not up and key in _TREND_WATCH_DOWN)
    return ("↗" if up else "↘"), (COLOR_ACCENT if watched else FG_LABEL)


# Echelle de couleur du vent : les paliers habituels des tables meteo, en
# noeuds. Deux jeux, un par theme -- une case pastel concue pour le jour
# devient aveuglante sur fond sombre, et le texte y disparait.
WIND_SCALE_KN = (5, 10, 15, 20, 25, 30, 35, 40)
_WIND_COLORS_CLAIR = ("#e4edf5", "#d5ecdc", "#e0efb8", "#f7ecab", "#f9d491",
                      "#f5b587", "#ef9270", "#e46f6f", "#c9524f")
_WIND_COLORS_SOMBRE = ("#26313f", "#25402f", "#37461f", "#4c4620", "#55411f",
                       "#5c3820", "#612f27", "#65262c", "#70202a")


def _wind_cell_colors(kn):
    """(fond, encre) d'une case du tableau, selon la force du vent."""
    idx = 0
    for i, limit in enumerate(WIND_SCALE_KN):
        if kn >= limit:
            idx = i + 1
    if ACTIVE_THEME == "sombre":
        return _WIND_COLORS_SOMBRE[idx], "#e6ecf2"
    return _WIND_COLORS_CLAIR[idx], "#1c2733"


# Angles qui designent une DESTINATION (la route suivie) et non une
# provenance. La distinction n'est pas cosmetique : dessiner une route fond
# comme un vent la ferait lire a 180 degres de la verite.
_TREND_ANGLE_TOWARD = ("cog", "heading")


def _draw_mini_arrow(canvas, deg, color, toward=False):
    """Fleche de direction dans une case de tableau. MEME convention que le
    cadran du suivi en direct : pour un VENT, elle montre ou il va en
    partant du cote d'ou il vient -- un vent de face pointe donc vers le
    bas. toward=True inverse ce sens pour les angles qui designent deja une
    destination (la route fond).

    Le repere (etrave en haut pour un angle bord-relatif, nord en haut pour
    un releve absolu) n'est pas dessine : a cette taille il ferait une tache,
    et l'intitule de la ligne le dit deja."""
    canvas.delete("all")
    if deg is None:
        return
    if toward:
        deg = float(deg) + 180.0
    w = int(canvas.cget("width"))
    h = int(canvas.cget("height"))
    cx, cy = w / 2.0, h / 2.0
    r = min(w, h) / 2.0 - 2.0
    ang = math.radians(90.0 - float(deg))
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    tail = (cx + r * cos_a, cy - r * sin_a)          # d'ou vient le vent
    tip = (cx - r * cos_a, cy + r * sin_a)           # ou il va
    neck = (cx - (r * 0.15) * cos_a, cy + (r * 0.15) * sin_a)
    px, py = sin_a, cos_a
    canvas.create_line(tail[0], tail[1], neck[0], neck[1], fill=color, width=2,
                       capstyle="round")
    canvas.create_polygon(tip[0], tip[1],
                          neck[0] + px * 3.2, neck[1] + py * 3.2,
                          neck[0] - px * 3.2, neck[1] - py * 3.2,
                          fill=color, outline="")


class StatCell(tk.Frame):
    """Case de tableau statistique : une valeur, une precision facultative
    en petit dessous, et pour une grandeur angulaire une fleche a gauche.

    compact=True supprime la ligne de precision : c'est la forme du tableau
    par tranches, ou vingt colonnes doivent tenir dans une largeur d'ecran."""

    def __init__(self, master, arrow=False, compact=False, toward=False):
        super().__init__(master, bg=BG_PANEL, relief="solid", bd=1)
        self._arrow = bool(arrow)
        self._toward = bool(toward)
        self.canvas = None
        if self._arrow:
            size = 16 if compact else 20
            self.canvas = tk.Canvas(self, width=size, height=size, bg=BG_PANEL,
                                     highlightthickness=0)
            self.canvas.pack(side="left", padx=(2, 0), pady=1)
        box = tk.Frame(self, bg=BG_PANEL)
        box.pack(side="left", fill="both", expand=True)
        self.main = tk.Label(box, text="--", bg=BG_PANEL, fg=FG_DIGIT,
                              font=("Consolas", 10 if compact else 11, "bold"))
        self.main.pack(fill="x")
        self.sub = None
        if not compact:
            self.sub = tk.Label(box, text="", bg=BG_PANEL, fg=FG_LABEL_DIM,
                                 font=("Segoe UI", 7))
            self.sub.pack(fill="x")

    def _paint(self, bg):
        self.configure(bg=bg)
        for w in (self.main, self.sub, self.canvas, self.main.master):
            if w is not None:
                w.configure(bg=bg)

    def set_empty(self):
        self._paint(BG_PANEL)
        self.main.configure(text="--", fg=FG_LABEL_DIM)
        if self.sub is not None:
            self.sub.configure(text="", fg=FG_LABEL_DIM)
        if self.canvas is not None:
            _draw_mini_arrow(self.canvas, None, FG_LABEL_DIM, self._toward)

    def set_value(self, main, sub="", angle=None, stale=False, bg=None, fg=None):
        bg = BG_PANEL if bg is None else bg
        self._paint(bg)
        ink = fg if fg is not None else (FG_LABEL_DIM if stale else FG_DIGIT)
        self.main.configure(text=main, fg=ink)
        if self.sub is not None:
            self.sub.configure(text=sub, fg=FG_LABEL_DIM)
        if self.canvas is not None:
            _draw_mini_arrow(self.canvas, angle, ink, self._toward)


# =========================================================================
# Application
# =========================================================================

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{pcfg.APP_NAME} v{pcfg.APP_VERSION} — {pcfg.APP_TAGLINE}")
        self.configure(bg=BG_APP)
        self.minsize(1040, 700)
        self.geometry("1240x800")

        self.config_data = pcfg.load_config()
        self.persistent_store = pe.PolarSampleStore.from_list(pcfg.load_store_data())
        self.sessions_index = pcfg.load_sessions_index()
        # Plages ecartees a l'interieur des passes : appliquees des le
        # chargement, sinon la premiere polaire tracee au demarrage
        # compterait des mesures que l'utilisateur avait deja mises de cote.
        self._apply_excluded_ranges()
        self.ui_state = pcfg.load_ui_state()
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
        # Enregistreur de tendances (page Statistiques) -- il appartient a
        # l'APPLICATION et non au moteur : chaque prise fabrique un moteur
        # neuf, alors que la meteo, elle, ne recommence pas a chaque bouton
        # "Demarrer". Voir allure_engine.TrendRecorder et _backfill_trend.
        self.trend = pe.TrendRecorder(
            horizon_s=self.config_data.get("stats_history_h", 6) * 3600.0)
        self._trend_backfill_state = "jamais"   # jamais / en cours / fait / echec
        self._trend_backfill_msg = ""
        self._trend_backfill_thread = None
        self._trend_backfill_result = None
        self._trend_backfill_lock = threading.Lock()
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
        self.derive_var = tk.StringVar(value="")
        # Widgets de la modale de configuration : inexistants tant qu'elle
        # n'est pas ouverte (voir _ask_config / _forget_config_widgets).
        self.sails_frame = None
        self.engines_frame = None
        self.sail_checkbuttons = {}
        self.engine_checkbuttons = {}
        self.derive_radiobuttons = []
        self.derive_reset_btn = None
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
        # Relecture du tampon pour les statistiques, quelques secondes apres
        # l'ouverture : en tache de fond, et volontairement PAS pendant le
        # demarrage -- l'application doit s'afficher tout de suite, et
        # personne ne consulte des tendances dans la premiere seconde.
        self._trend_backfill_after_id = self.after(
            4000, lambda: self._start_trend_backfill(auto=True))

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
            # Les statistiques survivent au renouvellement du moteur : c'est
            # le meme enregistreur qu'on rebranche, jamais un neuf.
            self.engine.trend = self.trend

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
        self.tab_statistiques = tk.Frame(self.content_area, bg=BG_APP)
        self._step_frames = {
            "parametres": self.tab_parametres,
            "acquisition": self.tab_acquisition,
            "entrepot": self.tab_entrepot,
            "polaires": self.tab_polaires,
            "statistiques": self.tab_statistiques,
        }

        self._build_step_parametres(self.tab_parametres)
        self._build_step_acquisition(self.tab_acquisition)
        self._build_step_entrepot(self.tab_entrepot)
        self._build_step_polaires(self.tab_polaires)
        self._build_step_statistiques(self.tab_statistiques)

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

        # Bouton STATISTIQUES, troisieme raccourci permanent. Il n'a pas sa
        # place dans le bandeau des trois etapes : les statistiques ne sont
        # pas une etape du parcours des donnees (elles ne produisent aucune
        # polaire), elles sont une LECTURE du flux, disponible en permanence
        # et sans rien enregistrer -- au meme titre que le suivi en direct.
        self.stats_btn = tk.Button(
            inner_h, text="\U0001f4c8", font=("Segoe UI Symbol", 13), relief="flat", bd=0,
            bg=BG_HEADER, fg=FG_LABEL_DIM, activebackground=BG_NAV,
            activeforeground=FG_DIGIT, cursor="hand2", padx=10, pady=3,
            command=lambda: self._show_step("statistiques"))
        self.stats_btn.pack(side="right")

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

        # Bandeau d'alerte manoeuvre, SOUS la barre d'acquisition et comme
        # elle visible sur toutes les pages : il survit a la fermeture de la
        # boite de dialogue (on peut cliquer la croix de la boite, changer de
        # page, et repondre plus tard depuis le bandeau). Cache au repos.
        self._man_banner = tk.Frame(parent, bg=COLOR_PORT)
        man_inner = tk.Frame(self._man_banner, bg=COLOR_PORT)
        man_inner.pack(fill="x", padx=12, pady=6)
        self._man_banner_lbl = tk.Label(man_inner, text="", bg=COLOR_PORT, fg="#ffffff",
                                         font=FONT_LABEL_BOLD, anchor="w")
        self._man_banner_lbl.pack(side="left", fill="x", expand=True)
        tk.Button(man_inner, text="Oui, j'ai manoeuvre",
                  command=lambda: self._resolve_maneuver(True),
                  bg="#ffffff", fg=COLOR_PORT, font=FONT_LABEL_BOLD,
                  padx=10, pady=2, cursor="hand2").pack(side="left", padx=(10, 6))
        tk.Button(man_inner, text="Non, fausse alerte",
                  command=lambda: self._resolve_maneuver(False),
                  bg="#ffffff", fg=FG_LABEL, font=FONT_LABEL,
                  padx=10, pady=2, cursor="hand2").pack(side="left")
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
                   "troughcolor", "selectbackground")
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
            self._live_twa_geom = _draw_twa_gauge_background(self.live_twa_canvas)
        if hasattr(self, "polar_fig"):
            self.polar_fig.set_facecolor(BG_APP)
            self.polar_ax.set_facecolor(BG_PANEL)
            self.polar_ax.tick_params(colors=FG_LABEL, labelsize=8)
            self.polar_canvas.get_tk_widget().configure(bg=BG_APP)
            if self._current_step == "polaires":
                self._draw_polar(auto=True)
            else:
                self.polar_canvas.draw_idle()
        if hasattr(self, "stats_fig"):
            self.stats_fig.set_facecolor(BG_APP)
            self.stats_ax_wind.set_facecolor(BG_PANEL)
            self.stats_ax_press.set_facecolor(BG_PANEL)
            self.stats_canvas.get_tk_widget().configure(bg=BG_APP)
            # Les cases colorees du tableau par tranches n'ont aucune
            # correspondance dans la palette (leurs teintes sortent de
            # l'echelle de vent) : elles sont repeintes par le
            # rafraichissement, qu'on force ici plutot que d'attendre le
            # prochain tick.
            if self._current_step == "statistiques":
                self._refresh_stats(force=True)
            else:
                self.stats_canvas.draw_idle()

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
        in_stats = (key == "statistiques")
        for k, btn in self._nav_buttons.items():
            active = (k == key)
            btn.configure(bg=BG_NAV_ACTIVE if active else BG_NAV,
                          fg=FG_ON_ACCENT if active else FG_LABEL,
                          font=FONT_NAV_BOLD if active else FONT_NAV)
        # Quand la page Parametres est ouverte, c'est la roue dentee qui
        # joue le role d'onglet actif : elle s'allume, et le bandeau des
        # etapes disparait entierement -- on est dans les reglages, pas dans
        # le parcours des donnees. Il revient a sa place exacte en sortant
        # (before=content_area : sans cela, un pack() le rangerait APRES la
        # zone de contenu, en bas de la fenetre).
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
        if hasattr(self, "stats_btn"):
            self.stats_btn.configure(
                bg=BG_NAV_ACTIVE if in_stats else BG_HEADER,
                fg=FG_ON_ACCENT if in_stats else FG_LABEL_DIM)
        if hasattr(self, "_steps_row"):
            if in_settings or in_stats:
                self._steps_row.pack_forget()
            elif not self._steps_row.winfo_manager():
                self._steps_row.pack(fill="x", before=self.content_area)
        self._current_step = key
        # Memorise la derniere etape visitee (voir __init__) -- ecriture
        # automatique a chaque navigation, fichier separe et minuscule.
        self.ui_state["last_step"] = key
        pcfg.save_ui_state(self.ui_state)
        # Rafraichissements ponctuels a l'entree de certaines etapes, pour
        # rester a jour meme si l'utilisateur a modifie autre chose entre
        # deux visites.
        if key == "polaires":
            self._refresh_config_choices()
            # Trace automatique : arriver sur l'etape suffit, plus de bouton
            # "Tracer / rafraichir" (le clic n'apportait rien -- on venait
            # forcement ici POUR voir la polaire). Silencieux si l'entrepot
            # est vide : un message d'accueil remplace le trace.
            self._draw_polar(auto=True)
        elif key == "entrepot":
            self._refresh_store_size_label()
            self._refresh_sessions_list()
            self._refresh_trash_list()
        elif key == "acquisition":
            self._refresh_ports_summary()
        elif key == "parametres":
            self._refresh_buffer_state()
        elif key == "statistiques":
            # Premiere visite : on va chercher dans le tampon ce qui s'est
            # passe AVANT le lancement de l'application. Sans cela, une
            # "tendance sur 60 minutes" ouverte deux minutes apres le
            # demarrage ne porterait que sur deux minutes.
            self._start_trend_backfill(auto=True)
            self._refresh_stats(force=True)

    # ---------------------------------------------------------------
    # Etape 2 : Enregistrement
    # ---------------------------------------------------------------
    def _build_step_acquisition(self, parent):
        """Etape 1 -- ACQUISITION. Son contenu est le suivi en direct, EN
        PERMANENCE : qu'on enregistre ou non, on voit ce qui rentre et si
        c'est exploitable. C'est l'ecran qu'on laisse ouvert en passerelle.

        Il n'y a plus de formulaire prealable a remplir : le choix de la
        source (UDP) vit dans les Parametres, l'import d'un fichier vit dans
        l'Entrepot, et la configuration voiles/moteurs/derive est demandee au
        moment ou l'on demarre -- la ou la question se pose reellement."""
        self._build_live_view(parent)
        self._refresh_ports_summary()
        self._apply_recording_lock()

    # ---------------------------------------------------------------
    # Dialogue de configuration -- pose la question voiles/moteurs/derive
    # AU MOMENT OU ELLE SE POSE (on demarre, ou on importe un fichier),
    # plutot que d'imposer un formulaire a remplir d'avance. Les variables
    # elles-memes (sail_vars/engine_vars/derive_var) vivent dans
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

        tk.Label(dlg, text=intro, bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                 justify="left", wraplength=470).pack(anchor="w", padx=14, pady=(12, 8))

        body = tk.Frame(dlg, bg=BG_APP)
        body.pack(fill="x", padx=14)
        self.sails_frame = tk.Frame(body, bg=BG_APP)
        self.sails_frame.pack(fill="x", pady=(0, 4))
        self.engines_frame = tk.Frame(body, bg=BG_APP)
        self.engines_frame.pack(fill="x", pady=(0, 4))
        self._rebuild_sail_engine_checkboxes()

        derive_row = tk.Frame(body, bg=BG_APP)
        derive_row.pack(fill="x", pady=(0, 6))
        tk.Label(derive_row, text="Derive :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
        self.derive_radiobuttons = []
        for value, text in (("haute", "Haute"), ("basse", "Basse")):
            rb = tk.Radiobutton(derive_row, text=text, variable=self.derive_var, value=value,
                                 bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                                 activebackground=BG_APP, activeforeground=FG_LABEL)
            rb.pack(side="left", padx=4)
            self.derive_radiobuttons.append(rb)
        self.derive_reset_btn = tk.Button(derive_row, text="Non renseignee",
                                           command=self._clear_derive_choice)
        self.derive_reset_btn.pack(side="left", padx=(8, 0))

        if with_duration:
            dur_row = tk.Frame(body, bg=BG_APP)
            dur_row.pack(fill="x", pady=(0, 6))
            tk.Label(dur_row, text="Duree max :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
            self.duration_spinbox = tk.Spinbox(dur_row, from_=0, to=1440, increment=1, width=6,
                                                textvariable=self.duration_var)
            self.duration_spinbox.pack(side="left", padx=(4, 6))
            tk.Label(dur_row, text="minutes (0 = illimitee)", bg=BG_APP,
                     fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(side="left")
        if with_range:
            rng_row = tk.Frame(body, bg=BG_APP)
            rng_row.pack(fill="x", pady=(0, 6))
            tk.Label(rng_row, text="Plage :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
            tk.Label(rng_row, text="de", bg=BG_APP, fg=FG_LABEL).pack(side="left")
            tk.Entry(rng_row, width=13, textvariable=self.seg_start_var).pack(side="left", padx=(4, 8))
            tk.Label(rng_row, text="a", bg=BG_APP, fg=FG_LABEL).pack(side="left")
            tk.Entry(rng_row, width=13, textvariable=self.seg_end_var).pack(side="left", padx=(4, 0))

        err_lbl = tk.Label(dlg, text="", bg=BG_APP, fg=COLOR_PORT,
                           font=("Segoe UI", 8, "bold"), justify="left", wraplength=470)
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
        self.derive_radiobuttons = []
        self.derive_reset_btn = None
        self.duration_spinbox = None
        self.sails_frame = None
        self.engines_frame = None

    def _center_on_parent(self, dlg):
        dlg.update_idletasks()
        try:
            x = self.winfo_rootx() + max(0, (self.winfo_width() - dlg.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - dlg.winfo_height()) // 3)
            dlg.geometry(f"+{x}+{y}")
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
        tk.Label(self.sails_frame, text="Voiles :", bg=BG_APP, fg=FG_LABEL,
                  font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
        self.sail_checkbuttons = {}
        for code, var in self.sail_vars.items():
            cb = tk.Checkbutton(self.sails_frame, text=code, variable=var, bg=BG_APP, fg=FG_LABEL,
                            selectcolor=BG_PANEL, activebackground=BG_APP, activeforeground=FG_LABEL,
                            command=self._on_config_checkbox_changed)
            cb.pack(side="left", padx=4)
            self.sail_checkbuttons[code] = cb
        tk.Label(self.engines_frame, text="Moteurs :", bg=BG_APP, fg=FG_LABEL,
                  font=FONT_LABEL_BOLD, width=10, anchor="w").pack(side="left")
        self.engine_checkbuttons = {}
        for code, var in self.engine_vars.items():
            cb = tk.Checkbutton(self.engines_frame, text=code, variable=var, bg=BG_APP, fg=FG_LABEL,
                            selectcolor=BG_PANEL, activebackground=BG_APP, activeforeground=FG_LABEL,
                            command=self._on_config_checkbox_changed)
            cb.pack(side="left", padx=4)
            self.engine_checkbuttons[code] = cb

    def _selected_sails(self):
        return [code for code, var in self.sail_vars.items() if var.get()]

    def _selected_engines(self):
        return [code for code, var in self.engine_vars.items() if var.get()]

    def _selected_derive(self):
        return self.derive_var.get() or None

    def _clear_derive_choice(self):
        """Les tk.Radiobutton ne peuvent pas se "decocher" eux-memes une fois
        selectionnes (contrairement aux Checkbutton) -- ce bouton dedie est
        le seul moyen de revenir a 'Non renseignee' (derive_var = "")."""
        self.derive_var.set("")
        self._on_config_checkbox_changed()

    def _on_config_checkbox_changed(self):
        if self.mode.get() == "live" and self.recording_active:
            self._apply_live_config()

    def _apply_live_config(self):
        sails, engines, derive = self._selected_sails(), self._selected_engines(), self._selected_derive()
        with self._engine_lock:
            self.engine.set_live_config(time.time(), sails, engines, derive)

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
        for key in pcfg.PORT_KEYS:
            pc = self.config_data[key]
            if not pc.get("enabled"):
                continue
            name = f"{self.port_label(key, with_key=False)} ({pc['ip'] or '*'}:{pc['port']})"
            li = self.listeners.get(key)
            if li is None:
                lines.append(f"  {name:<34s} fermee")
                continue
            if li.bound is False:
                lines.append(f"  {name:<34s} REFUSEE : {li.bind_error}")
                refused.append(key)
                continue
            if li.bound is None:
                lines.append(f"  {name:<34s} ouverture...")
                continue
            if li.frames == 0:
                waited = now - li.started_at
                lines.append(f"  {name:<34s} a l'ecoute, AUCUNE trame "
                             f"(depuis {waited:.0f} s)")
                if waited >= self.NET_SILENCE_ALERT_S:
                    mutes.append(key)
            else:
                age = (now - li.last_frame_at) if li.last_frame_at else 0.0
                lines.append(f"  {name:<34s} {li.frames} trame(s), "
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

        if mutes:
            exe = os.path.basename(sys.executable or "python")
            self.net_warn_lbl.configure(
                text=("Le port est bien ouvert, mais AUCUNE trame n'arrive sur : "
                      + ", ".join(self.port_label(k, with_key=False) for k in mutes)
                      + f".\nSous Windows, le pare-feu autorise un EXECUTABLE, pas un "
                        f"programme : lance par {exe}, Allure lui est inconnu meme si une "
                        "autre version de Python avait ete autorisee. Le socket s'ouvre "
                        "quand meme, et les trames sont jetees avant d'arriver.\n"
                        "Verifiez : Parametres Windows > Reseau > Pare-feu > Autoriser une "
                        f"application, et cochez {exe} en reseau PRIVE. Verifiez aussi que "
                        "l'emetteur envoie bien vers cet ordinateur (adresse et port)."))
        elif refused:
            self.net_warn_lbl.configure(
                text="Une voie n'a pas pu s'ouvrir (voir ci-dessus) : le port est "
                     "probablement deja pris par un autre programme.")
        else:
            self.net_warn_lbl.configure(text="")

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
        # la configuration voiles/moteurs/derive est desormais verrouillee
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
            sails, engines, derive = (self._selected_sails(), self._selected_engines(),
                                       self._selected_derive())
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
                    self.engine.add_manual_segment(seg_start, seg_end, sails, engines, derive)
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
                             f":\n{raw_path}\n\nVous pouvez le reimporter (mode Import, etape "
                             "Enregistrement) apres avoir ajuste les reglages ou corrige la "
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
        ("statistiques", "Statistiques"),
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
        self.params_pending_lbl = tk.Label(
            self.params_pending_row, text="", bg=BG_PANEL, fg=COLOR_PORT,
            font=("Segoe UI", 8, "bold"), wraplength=680, justify="left")
        self.params_pending_lbl.pack(side="left")
        # Raccourci affiche uniquement quand pertinent (voir _update_once) :
        # va directement traiter/vider la session en attente.
        self.goto_session_btn = tk.Button(
            self.params_pending_row, text="Aller a l'etape Acquisition...",
            command=lambda: self._show_step("acquisition"))

        # Bandeau de categories, a gauche -- empaquete avant le corps pour
        # reserver sa colonne (meme regle du packer que pour la roue dentee).
        catbar = tk.Frame(parent, bg=BG_NAV, width=200)
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
        self._build_settings_statistiques(self._settings_frames["statistiques"])
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
        note(f_lists, "Ces listes definissent les cases a cocher de l'etape Enregistrement. "
                       "Modifiable librement -- les valeurs par defaut correspondent au greement "
                       "habituel (J0, J1A, MSA, J1F, MSF / 1ME, 2ME, 1SB, 2SB).")
        lists_row = tk.Frame(f_lists, bg=BG_APP)
        lists_row.pack(fill="x", padx=6, pady=(0, 8))

        self.sail_list_box, self.sail_list_entry = self._build_editable_list(
            lists_row, "Voiles", self.config_data["sail_list"])
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
        choices = [self.TZ_MAP_NONE] + list(pe.TZ_MAINSAILS) + list(pe.TZ_FRONTSAILS)
        saved = dict(self.config_data.get("timezero_sail_map") or {})
        self.tz_map_vars = {}
        grid = tk.Frame(f_tz, bg=BG_APP)
        grid.pack(fill="x", padx=6, pady=(0, 8))
        for i, code in enumerate(self.config_data["sail_list"]):
            r, c = i // 2, (i % 2) * 2
            tk.Label(grid, text=f"{code} :", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL_BOLD, anchor="e", width=8).grid(
                row=r, column=c, sticky="e", padx=(0, 4), pady=2)
            var = tk.StringVar(value=saved.get(code) or self.TZ_MAP_NONE)
            menu = ttk.OptionMenu(grid, var, var.get(), *choices)
            menu.configure(width=18)
            menu.grid(row=r, column=c + 1, sticky="w", padx=(0, 20), pady=2)
            self.tz_map_vars[code] = var

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
            tk.Label(f_type, text=desc, bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                     justify="left", wraplength=620).pack(anchor="w", padx=28, pady=(0, 4))
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
        self.scan_status_lbl = tk.Label(scan_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                         font=("Segoe UI", 8))
        self.scan_status_lbl.pack(side="left", padx=(12, 0))

        scan_tree_row = tk.Frame(f_scan, bg=BG_APP)
        scan_tree_row.pack(fill="x", padx=6, pady=(2, 2))
        scols = ("voie", "trame", "role", "cadence", "unite", "valeur", "source")
        self.scan_tree = ttk.Treeview(scan_tree_row, columns=scols, show="headings",
                                       height=8, selectmode="browse")
        # Tri par clic sur l'en-tete, comme le tableau des passes. Utile ici
        # pour rassembler les lignes d'un meme type de trame -- c'est ainsi
        # qu'on VOIT que deux voies portent le meme vent, et qu'on les compare.
        self._scan_sort = ("voie", False)
        for c, label, w, anchor in (("voie", "Voie", 145, "w"), ("trame", "Trame", 60, "center"),
                                     ("role", "Ce qu'elle apporte", 215, "w"),
                                     ("cadence", "Cadence", 90, "center"),
                                     ("unite", "Unite / reference", 145, "center"),
                                     ("valeur", "Derniere valeur lue", 175, "w"),
                                     ("source", "Source", 90, "center")):
            self.scan_tree.heading(c, text=label, command=lambda k=c: self._sort_scan_by(k))
            self.scan_tree.column(c, width=w, anchor=anchor)
        scan_vsb = tk.Scrollbar(scan_tree_row, orient="vertical", command=self.scan_tree.yview)
        self.scan_tree.configure(yscrollcommand=scan_vsb.set)
        self.scan_tree.pack(side="left", fill="x", expand=True)
        scan_vsb.pack(side="right", fill="y")
        self._scan_rows = []
        # Le tableau n'est pas qu'un rapport : un double-clic sur une ligne
        # exploitable DESIGNE cette voie comme source de la grandeur. Voir ce
        # que porte une voie et le choisir sont le meme geste -- sinon il
        # faut retenir la ligne, descendre, retrouver la voie dans un menu.
        self.scan_tree.bind("<Double-1>", lambda _e: self._use_scan_row_as_source())
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
        Repart des donnees deja collectees : trier ne relit jamais le tampon."""
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
            self.scan_tree.insert("", "end", values=(
                self.port_label(r["port"], live=True), r["styp"], r["role"],
                r["cadence"], r["unite"], r["valeur"],
                self._source_role_of(r["port"], r["styp"])))
        for key, label in self.SCAN_HEADINGS:
            mark = ("  \u25bc" if descending else "  \u25b2") if key == sort_col else ""
            self.scan_tree.heading(key, text=f"{label}{mark}")

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
        self.archive_detect_lbl = tk.Label(win_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                            font=("Segoe UI", 8))
        self.archive_detect_lbl.pack(side="left", padx=(10, 0))

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

    # ---------- Categorie : statistiques ----------
    def _build_settings_statistiques(self, parent):
        """Tout ce que la page Statistiques affiche se regle ICI, comme le
        reste de l'application se regle dans la roue dentee -- la page
        elle-meme reste une page de lecture, sans menus qui l'encombrent.
        Seule l'echelle de temps des graphes vit sur la page : c'est le seul
        reglage qu'on tourne l'oeil sur le resultat."""
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)

        f_src = section(inner, "D'ou viennent les mesures")
        note(f_src, "Les statistiques lisent les MEMES valeurs arbitrees que les cadrans du "
                     "suivi en direct : la source de chaque grandeur (quelle voie, quels "
                     "replis) se choisit dans Sources & tampon, et s'applique ici a "
                     "l'identique. L'historique d'avant le lancement vient du tampon "
                     "glissant, relu automatiquement a l'ouverture.", wraplength="auto")

        f_fields = section(inner, "Grandeurs affichees")
        note(f_fields, "Tout afficher noie l'essentiel : cochez ce qui compte pour VOTRE "
                        "passerelle. Une grandeur cochee mais jamais recue n'occupe aucune "
                        "ligne ; une grandeur recue mais decochee est comptee en pied de "
                        "tableau, sans etre affichee.", wraplength="auto")
        fields_grid = tk.Frame(f_fields, bg=BG_APP)
        fields_grid.pack(fill="x", padx=6, pady=(0, 8))
        chosen = set(self.config_data.get("stats_fields") or [])
        self.stats_field_vars = {}
        for i, (key, label, unit, _dec, _kind) in enumerate(pe.TREND_FIELDS):
            var = tk.BooleanVar(value=key in chosen)
            self.stats_field_vars[key] = var
            tk.Checkbutton(fields_grid, text=f"{label} ({unit})", variable=var,
                           bg=BG_APP, fg=FG_LABEL, activebackground=BG_APP,
                           anchor="w").grid(row=i % 7, column=i // 7, sticky="w",
                                             padx=(0, 24))

        f_win = section(inner, "Tendances : les quatre durees")
        note(f_win, "Les quatre fenetres de moyenne glissante du tableau des tendances, de "
                     "la plus courte a la plus longue. Doublons refuses -- deux colonnes "
                     "identiques ne diraient rien de plus.", wraplength="auto")
        win_row = tk.Frame(f_win, bg=BG_APP)
        win_row.pack(fill="x", padx=6, pady=(0, 8))
        self.stats_window_vars = []
        choices = [_fmt_span(s) for s in pe.TREND_WINDOW_CHOICES]
        for w in self._stats_windows():
            var = tk.StringVar(value=_fmt_span(w))
            ttk.OptionMenu(win_row, var, var.get(), *choices).pack(side="left", padx=(0, 6))
            self.stats_window_vars.append(var)

        f_hist = section(inner, "Profondeur d'historique")
        hist_row = tk.Frame(f_hist, bg=BG_APP)
        hist_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(hist_row, text="Conserver en memoire :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL).pack(side="left")
        self.stats_history_var = tk.StringVar(
            value=f"{self.config_data.get('stats_history_h', 6)} h")
        ttk.OptionMenu(hist_row, self.stats_history_var, self.stats_history_var.get(),
                       *[f"{h} h" for h in (1, 2, 3, 6, 12, 24, 48)]).pack(
            side="left", padx=(6, 0))
        note(f_hist, "Borne la plus longue moyenne possible et le recul maximal des "
                      "graphes. Au-dela, seules les distances horaires sont conservees "
                      "(un simple total par heure).", wraplength="auto")

        f_grid = section(inner, "Tableau des dernieres heures")
        self.stats_grid_show_var = tk.BooleanVar(
            value=self.config_data.get("stats_show_grid", True))
        tk.Checkbutton(f_grid, text="Afficher le tableau par tranches",
                       variable=self.stats_grid_show_var, bg=BG_APP, fg=FG_LABEL,
                       activebackground=BG_APP, anchor="w").pack(fill="x", padx=6)
        grid_row = tk.Frame(f_grid, bg=BG_APP)
        grid_row.pack(fill="x", padx=6, pady=(2, 4))
        tk.Label(grid_row, text="Pas :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL).pack(side="left")
        self.stats_grid_step_var = tk.StringVar(
            value=_fmt_span(self.config_data.get("stats_grid_step_min", 30) * 60))
        ttk.OptionMenu(grid_row, self.stats_grid_step_var, self.stats_grid_step_var.get(),
                       *[_fmt_span(m * 60) for m in (5, 10, 15, 30, 60, 120)]).pack(
            side="left", padx=(6, 14))
        tk.Label(grid_row, text="Colonnes :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL).pack(side="left")
        self.stats_grid_cols_var = tk.StringVar(
            value=str(self.config_data.get("stats_grid_cols", 12)))
        ttk.OptionMenu(grid_row, self.stats_grid_cols_var, self.stats_grid_cols_var.get(),
                       *[str(n) for n in (6, 8, 10, 12, 16, 20, 24)]).pack(
            side="left", padx=(6, 0))
        note(f_grid, "Ses lignes suivent les grandeurs cochees plus haut (suivre le vent "
                      "reel amene ses rafales et sa direction). Le nombre de colonnes se "
                      "borne tout seul a ce que la fenetre peut afficher.", wraplength="auto")

        f_dist = section(inner, "Distance heure par heure")
        dist_row = tk.Frame(f_dist, bg=BG_APP)
        dist_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(dist_row, text="Heures pleines affichees :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL).pack(side="left")
        self.stats_hours_shown_var = tk.StringVar(
            value=str(self.config_data.get("stats_hours_shown", 12)))
        ttk.OptionMenu(dist_row, self.stats_hours_shown_var,
                       self.stats_hours_shown_var.get(),
                       *[str(n) for n in (6, 8, 12, 18, 24, 36, 48)]).pack(
            side="left", padx=(6, 0))

    def _collect_stats_settings(self, new_cfg):
        """Verse les reglages de la categorie Statistiques dans new_cfg --
        appele par _save_params, au meme titre que les autres categories."""
        fields = [k for k, var in getattr(self, "stats_field_vars", {}).items()
                  if var.get()]
        if fields:
            new_cfg["stats_fields"] = [k for k in pe.TREND_KEYS if k in fields]
        wins = []
        for var in getattr(self, "stats_window_vars", []):
            secs = _parse_span(var.get())
            if secs:
                wins.append(secs)
        if wins:
            new_cfg["stats_windows"] = wins
        h = _parse_span(self.stats_history_var.get()) if hasattr(self, "stats_history_var") else None
        if h:
            new_cfg["stats_history_h"] = max(1, int(round(h / 3600.0)))
        if hasattr(self, "stats_grid_show_var"):
            new_cfg["stats_show_grid"] = bool(self.stats_grid_show_var.get())
        step = _parse_span(self.stats_grid_step_var.get()) if hasattr(self, "stats_grid_step_var") else None
        if step:
            new_cfg["stats_grid_step_min"] = max(1, int(round(step / 60.0)))
        for attr, key in (("stats_grid_cols_var", "stats_grid_cols"),
                          ("stats_hours_shown_var", "stats_hours_shown")):
            try:
                new_cfg[key] = int(getattr(self, attr).get())
            except (AttributeError, tk.TclError, ValueError):
                pass

    def _apply_stats_settings_effects(self):
        """Fait suivre l'etat vivant apres un enregistrement des parametres :
        horizon de l'enregistreur, menus recales sur ce que le fixup a
        REELLEMENT retenu (dedoublonnage des durees), page rafraichie."""
        self.trend.set_horizon(self.config_data.get("stats_history_h", 6) * 3600.0)
        if hasattr(self, "stats_window_vars"):
            for var, w in zip(self.stats_window_vars, self._stats_windows()):
                var.set(_fmt_span(w))
        if hasattr(self, "stats_field_vars"):
            chosen = set(self.config_data.get("stats_fields") or [])
            for key, var in self.stats_field_vars.items():
                var.set(key in chosen)
        if hasattr(self, "stats_state_lbl"):
            self._sync_stats_sections()
            self._refresh_stats(force=True)

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
        tray_row.pack(fill="x", padx=6, pady=(0, 8))
        self.close_to_tray_var = tk.BooleanVar(value=self.config_data.get("close_to_tray", True))
        tk.Checkbutton(tray_row, text="La croix range dans la zone de notification",
                       variable=self.close_to_tray_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP,
                       font=FONT_LABEL_BOLD).pack(side="left")
        self.tray_avail_lbl = tk.Label(tray_row, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                        font=("Segoe UI", 8))
        self.tray_avail_lbl.pack(side="left", padx=(12, 0))
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
        bins_row = tk.Frame(f_bins, bg=BG_APP)
        bins_row.pack(fill="x", padx=6, pady=(0, 8))
        tk.Label(bins_row, text="Case TWA (deg) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.twa_bin_var = tk.DoubleVar(value=self.config_data["twa_bin_deg"])
        tk.Spinbox(bins_row, from_=1, to=45, increment=1, width=5,
                   textvariable=self.twa_bin_var).pack(side="left", padx=(4, 16))
        tk.Label(bins_row, text="Case TWS (noeuds) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.tws_bin_var = tk.DoubleVar(value=self.config_data["tws_bin_kn"])
        tk.Spinbox(bins_row, from_=0.5, to=20, increment=0.5, width=5,
                   textvariable=self.tws_bin_var).pack(side="left", padx=(4, 16))
        tk.Label(bins_row, text="Symetrie babord/tribord :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.symmetric_var = tk.BooleanVar(value=self.config_data["symmetric_port_starboard"])
        tk.Checkbutton(bins_row, variable=self.symmetric_var, bg=BG_APP, selectcolor=BG_PANEL).pack(
            side="left", padx=(4, 16))
        tk.Label(bins_row, text="Statistique :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self.stat_var = tk.StringVar(value=self.config_data["aggregation_stat"])
        self.stat_menu = ttk.OptionMenu(bins_row, self.stat_var, self.stat_var.get(), *pe.STATISTICS)
        self.stat_menu.configure(width=10)
        self.stat_menu.pack(side="left", padx=(4, 0))

        f_maxp = section(inner, "Polaire max (routage)")
        note(f_maxp, "Sur un cargo a voile ET moteur, le moteur fait partie de la "
                      "voilure : chaque cas de vent (force, orientation) appelle un JEU "
                      "voiles/moteur, pas une voile seule. Cochee, cette option met les "
                      "configurations moteur en concurrence dans la polaire max, au meme "
                      "titre que les voiles -- et le guide de voilure dira donc aussi "
                      "QUAND passer au moteur. Decochee (voilier pur), elles restent "
                      "disponibles mais decochees a l'ouverture de la page.",
             wraplength="auto")
        maxp_row = tk.Frame(f_maxp, bg=BG_APP)
        maxp_row.pack(fill="x", padx=6, pady=(0, 8))
        self.max_allure_engine_var = tk.BooleanVar(
            value=bool(self.config_data.get("max_polar_include_engine", True)))
        tk.Checkbutton(maxp_row, text="Le moteur fait partie de la voilure "
                                       "(configurations moteur en concurrence par defaut)",
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
        # Le seuil CONSEILLE est affiche a cote du champ, et il est motive :
        # un nombre sans justification ne se regle qu'au hasard.
        tk.Label(auto_row,
                 text=f"conseille : {pe.CONF_ADVISED_THRESHOLD}  "
                      f"(frontiere entre C indicative et D fragile)",
                 bg=BG_APP, fg=COLOR_ACCENT, font=FONT_LABEL_BOLD).pack(side="left", padx=(14, 0))
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
        self.diag_lbl = tk.Label(f_diag, text="", bg=BG_APP, fg=FG_LABEL,
                                  font=FONT_MONO, anchor="w", justify="left")
        self.diag_lbl.pack(fill="x", padx=6, pady=(0, 4))
        self.diag_lbl.configure(text=self.diagnostic_text())
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

    def _build_editable_list(self, parent, title, items):
        col = tk.Frame(parent, bg=BG_APP)
        col.pack(side="left", padx=(0, 24), fill="y")
        tk.Label(col, text=title, bg=BG_APP, fg=FG_LABEL, font=FONT_LABEL_BOLD).pack(anchor="w")
        listbox = tk.Listbox(col, height=6, width=14, bg=BG_FIELD, fg=FG_DIGIT,
                              selectbackground=COLOR_TICK, font=FONT_MONO)
        for item in items:
            listbox.insert("end", item)
        listbox.pack(pady=(4, 4))
        row = tk.Frame(col, bg=BG_APP)
        row.pack()
        entry = tk.Entry(row, width=8)
        entry.pack(side="left")

        def add_item():
            val = entry.get().strip().upper()
            if val and val not in listbox.get(0, "end"):
                listbox.insert("end", val)
                entry.delete(0, "end")

        def remove_item():
            sel = listbox.curselection()
            if sel:
                listbox.delete(sel[0])

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
        new_cfg["timezero_sail_map"] = {
            code: var.get() for code, var in getattr(self, "tz_map_vars", {}).items()
            if var.get() and var.get() != self.TZ_MAP_NONE}

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

        self._collect_stats_settings(new_cfg)

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
        # La page Statistiques suit immediatement (grandeurs, durees,
        # horizon, tableau montre/cache) -- comme le theme ou le tampon.
        self._apply_stats_settings_effects()

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
                "Impossible d'importer des reglages : une session est en cours "
                "d'enregistrement, ou contient des segments/echantillons pas encore "
                "traites (etape Enregistrement).\n\n"
                "Arretez l'enregistrement puis cliquez sur 'Traiter la session' (ou "
                "supprimez les segments en attente) avant d'importer des reglages.")
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

        archive_tree_row = tk.Frame(f_archive, bg=BG_APP)
        archive_tree_row.pack(fill="x", padx=6, pady=(2, 2))
        acols = ("heure", "passe", "voiles", "moteurs", "derive", "stw", "twa", "tws")
        self.archive_tree = ttk.Treeview(archive_tree_row, columns=acols, show="headings",
                                          height=7, selectmode="browse")
        # Colonnes de VALEURS centrees (derive, stw, twa, tws) : ce sont des
        # grandeurs courtes et de largeur homogene, que l'oeil compare d'une
        # ligne a l'autre -- centrees, elles forment des colonnes nettes,
        # alors qu'un alignement a droite ou a gauche les laissait flotter
        # loin de leur en-tete.
        for c, label, w, anchor in (("heure", "Heure", 60, "center"), ("passe", "Passe", 220, "w"),
                                     ("voiles", "Voiles", 90, "w"), ("moteurs", "Moteurs", 90, "w"),
                                     ("derive", "Derive", 90, "center"), ("stw", "STW (kn)", 70, "center"),
                                     ("twa", "TWA (deg)", 75, "center"), ("tws", "TWS (kn)", 70, "center")):
            self.archive_tree.heading(c, text=label)
            self.archive_tree.column(c, width=w, anchor=anchor)
        arch_vsb = tk.Scrollbar(archive_tree_row, orient="vertical", command=self.archive_tree.yview)
        self.archive_tree.configure(yscrollcommand=arch_vsb.set)
        self.archive_tree.pack(side="left", fill="x", expand=True)
        arch_vsb.pack(side="right", fill="y")
        self._archive_tree_rows = {}  # iid -> (epoch, sample)
        self.archive_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_archive_detail())
        # Double-clic : la fiche complete de la minute, grandeurs d'ambiance
        # comprises (pression, temperatures, humidite...) telles que reglees
        # dans les Parametres. La selection simple garde son resume d'une
        # ligne -- on ne relit pas le tampon a chaque coup de fleche.
        self.archive_tree.bind("<Double-1>", lambda _e: self._show_archive_sheet())
        tk.Label(f_archive, text="Double-cliquez une ligne pour la fiche complete de la minute "
                                  "(grandeurs a choisir dans la roue dentee > Lissage & table).",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack(anchor="w", padx=6)

        self.archive_detail_lbl = tk.Label(f_archive, text="", bg=BG_APP, fg=FG_LABEL, font=FONT_MONO,
                                            justify="left", anchor="w")
        self.archive_detail_lbl.pack(anchor="w", padx=6, pady=(4, 8), fill="x")

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
                       "reellement utilisee pendant cette passe (voiles, moteurs, derive) ; une "
                       "passe qui a change de voilure en route en affiche plusieurs, separees "
                       "par des virgules et dominante en tete. Un clic sur un en-tete trie le "
                       "tableau (un second clic inverse le sens). Un clic sur la case de la "
                       "colonne 'Incluse' inclut/"
                       "exclut directement la passe du calcul de polaire, sans la supprimer -- "
                       "pratique pour ecarter une sortie douteuse. 'Modifier' permet de corriger "
                       "a posteriori les voiles/moteurs/derive d'une passe deja entreposee -- "
                       "utile quand on s'apercoit longtemps apres que l'enregistrement portait la "
                       "mauvaise etiquette. 'Recalculer depuis le tampon' va plus loin : il "
                       "rejoue les trames BRUTES de la passe et refait tous les calculs avec les "
                       "reglages du moment -- c'est ce qui repare une passe enregistree sur la "
                       "mauvaise girouette, sans rien estimer, tant qu'elle reste couverte par le "
                       "tampon glissant. La suppression, elle, passe par la Corbeille.")
        self.store_size_lbl = tk.Label(f_store, text="", bg=BG_APP, fg=FG_LABEL, font=FONT_MONO)
        self.store_size_lbl.pack(anchor="w", padx=6, pady=(0, 6))

        tree_row = tk.Frame(f_store, bg=BG_APP)
        tree_row.pack(fill="x", padx=6, pady=(0, 4))
        # La passe est decrite par sa configuration COMPLETE -- voiles,
        # moteurs ET derive : afficher les seules voiles laissait invisible
        # la moitie de ce qui distingue deux polaires (un bord au moteur ou
        # derive haute n'a rien a voir avec le meme bord a la voile seule),
        # alors meme que l'application enregistre soigneusement ces trois
        # informations depuis le debut.
        cols = ("date", "mode", "source", "voiles", "moteurs", "derive", "ech",
                "conf", "incluse")
        self.sessions_tree = ttk.Treeview(tree_row, columns=cols, show="headings",
                                           height=6, selectmode="browse")
        # Tri par clic sur l'en-tete : un clic trie sur cette colonne, un
        # second inverse le sens (voir _sort_sessions_by). Colonnes courtes
        # (mode, echantillons) centrees comme la case Incluse.
        # Ordre par defaut : la date croissante, c'est-a-dire l'ordre dans
        # lequel les passes sont arrivees -- l'ordre historique du tableau.
        self._sessions_sort = ("date", False)  # (colonne, decroissant)
        for c, label, w, anchor in (("date", "Date", 125, "w"), ("mode", "Mode", 65, "center"),
                                     ("source", "Source", 170, "w"), ("voiles", "Voiles", 130, "w"),
                                     ("moteurs", "Moteurs", 110, "center"),
                                     ("derive", "Derive", 95, "center"),
                                     ("ech", "Echantillons", 90, "center"),
                                     ("conf", "Confiance", 130, "center"),
                                     ("incluse", "Incluse", 70, "center")):
            self.sessions_tree.heading(c, text=label,
                                        command=lambda k=c: self._sort_sessions_by(k))
            self.sessions_tree.column(c, width=w, anchor=anchor)
        sess_vsb = tk.Scrollbar(tree_row, orient="vertical", command=self.sessions_tree.yview)
        self.sessions_tree.configure(yscrollcommand=sess_vsb.set)
        self.sessions_tree.pack(side="left", fill="x", expand=True)
        sess_vsb.pack(side="right", fill="y")
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
                       "bord. Purement informative : seule la case Incluse decide de ce qui "
                       "entre dans le calcul.", wraplength="auto")
        self.session_conf_lbl = tk.Label(f_store, text="", bg=BG_APP, fg=FG_LABEL,
                                          font=FONT_MONO, anchor="w", justify="left")
        self.session_conf_lbl.pack(fill="x", padx=6, pady=(0, 4))

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
        tk.Label(self.session_edit_frame, bg=BG_PANEL, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                 justify="left", anchor="w", wraplength=880,
                 text="Une passe peut contenir plusieurs configurations (changement de voilure en "
                      "cours de route) : chacune se corrige separement, pour ne pas ecraser un "
                      "changement legitime. Seules les etiquettes changent -- les mesures "
                      "(vitesse, angle et force du vent) ne sont jamais recalculees."
                 ).pack(fill="x", padx=8, pady=(0, 4))
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
        n = len(self.persistent_store)
        n_cfg = len(self.persistent_store.configs())
        self.store_size_lbl.configure(
            text=f"{n} echantillon(s) accumule(s), repartis sur {n_cfg} configuration(s) voiles/moteurs.")

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
            tk.Label(self.trash_list_frame, text=detail, bg=BG_APP, fg=FG_LABEL, font=("Segoe UI", 8),
                     anchor="w", wraplength=280, justify="left").grid(row=i, column=2, sticky="w", padx=(0, 10))
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
        """(voiles, moteurs, derive) d'une passe, en texte, pour le tableau
        de l'entrepot. Une passe peut porter PLUSIEURS configurations (un
        changement de voilure en cours de route en cree une nouvelle) : les
        valeurs distinctes sont alors listees separees par des virgules,
        dans l'ordre decroissant du nombre d'echantillons -- la
        configuration dominante de la passe se lit donc en premier.

        Les trois colonnes sont construites d'un seul tenant, a partir de la
        meme source (configs_for_session) : impossible qu'elles se
        desynchronisent.

        configs : le resultat deja regroupe de session_overview(), quand
        l'appelant affiche PLUSIEURS passes -- sans lui, chaque ligne du
        tableau reparcourait l'entrepot entier."""
        if configs is None:
            configs = self.persistent_store.configs_for_session(session_id)
        if not configs:
            return "-", "-", "-"

        def _distinct(render):
            out = []
            for cfg, _n in configs:  # deja trie par nombre d'echantillons
                txt = render(cfg)
                if txt not in out:
                    out.append(txt)
            return ", ".join(out) or "-"

        return (_distinct(lambda c: "+".join(c[0]) if c[0] else "-"),
                _distinct(lambda c: "+".join(c[1]) if c[1] else "-"),
                _distinct(lambda c: pe.DERIVE_LABELS.get(c[2], "-")))

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
            voiles_txt, moteurs_txt, derive_txt = self._session_config_texts(
                session_id, configs=(ov["configs"] if ov else []))
            # Indice de confiance de la passe : ce que vaut CET
            # enregistrement -- purement informatif, il n'exclut rien et ne
            # change aucune vitesse (c'est la case "Incluse", et elle seule,
            # qui decide de ce qui entre dans le calcul).
            cinfo = ov["confidence"] if ov else self.persistent_store.session_confidence(
                session_id,
                twa_bin_deg=self.config_data["twa_bin_deg"],
                tws_bin_kn=self.config_data["tws_bin_kn"],
                symmetric=self.config_data["symmetric_port_starboard"])
            conf_txt = f"{cinfo['level']}  {cinfo['score']:3d}  {cinfo['word']}"
            rows.append({
                "i": i, "id": session_id,
                "values": (date_txt, rec.get("mode", "?"),
                           os.path.basename(rec.get("source", "")) or "-",
                           voiles_txt, moteurs_txt, derive_txt, rec.get("sample_count", 0),
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
                         "derive": derive_txt.lower(),
                         "ech": rec.get("sample_count", 0),
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
        # Realignement systematique : sessions_index change par bien des
        # chemins (restauration de corbeille, import de sauvegarde, tri
        # automatique...), et l'entrepot doit suivre a tous les coups.
        self._apply_excluded_ranges()

        if hasattr(self, "store_size_lbl"):
            self._refresh_store_size_label()
        # Referme le panneau de correction si la passe editee a disparu entre
        # temps (suppression, reinitialisation, import remplacant l'entrepot) :
        # il proposerait sinon de corriger une passe qui n'existe plus.
        if getattr(self, "_editing_session", None) is not None and \
                self._session_record(self._editing_session) is None:
            self._cancel_edit_session()

    SESSIONS_HEADINGS = (("date", "Date"), ("mode", "Mode"), ("source", "Source"),
                          ("voiles", "Voiles"), ("moteurs", "Moteurs"), ("derive", "Derive"),
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
        self.session_conf_lbl.configure(
            text=f"Passe selectionnee : {c['level']} {c['score']}/100 ({c['word']})   "
                 f"{c['n']} echantillon(s), {c['blocks']} moment(s) independant(s), "
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
        try:
            scr_h = dlg.winfo_screenheight()
        except tk.TclError:
            scr_h = 800
        dlg.geometry(f"1120x{min(760, max(560, scr_h - 120))}")
        dlg.minsize(900, 540)
        dlg.update_idletasks()
        dlg.bind("<Escape>", lambda _e: dlg.destroy())

        date_txt = (time.strftime("%d/%m/%Y %H:%M", time.localtime(rec["created_at"]))
                    if rec.get("created_at") else "?")
        cinfo = self.persistent_store.session_confidence(
            session_id, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric)
        head = tk.Frame(dlg, bg=BG_APP)
        head.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(head, text=f"Passe du {date_txt}", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL_BOLD, anchor="w").pack(fill="x")
        tk.Label(head, text=f"Confiance {cinfo['level']} {cinfo['score']}/100 "
                            f"({cinfo['word']}) -- {cinfo['n']} echantillon(s), "
                            f"{cinfo['cells']} case(s), {int(cinfo['duration_s'] // 60)} min"
                            f"\n{cinfo['why']}",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8), anchor="w",
                 justify="left").pack(fill="x")

        # --- Panneau des TRONCONS, a droite ---
        # La passe est un contenant, pas une unite de mesure. On la decoupe
        # en bords homogenes (voir pe.split_into_legs) pour pouvoir ecarter
        # le quart d'heure ou le loch s'est encrasse sans jeter les deux
        # heures qui vont avec.
        body = tk.Frame(dlg, bg=BG_APP)
        body.pack(fill="both", expand=True, padx=10, pady=(4, 4))
        right = tk.Frame(body, bg=BG_APP, width=360)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        left = tk.Frame(body, bg=BG_APP)
        left.pack(side="left", fill="both", expand=True)

        fig = Figure(figsize=(5.4, 4.6), dpi=100, facecolor=BG_APP)
        fig.subplots_adjust(top=0.98, bottom=0.14)
        ax = fig.add_subplot(111, projection="polar", facecolor=BG_PANEL)
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.tick_params(colors=FG_LABEL, labelsize=8)
        ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)

        canvas = FigureCanvasTkAgg(fig, master=left)
        canvas.get_tk_widget().configure(bg=BG_APP, highlightthickness=0)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        empty_lbl = tk.Label(left, text="", bg=BG_APP, fg=COLOR_PORT, font=FONT_LABEL,
                             wraplength=520, justify="left", anchor="w")
        empty_lbl.pack(fill="x")

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
            max_radius, n_series = 0.0, 0
            # Une passe peut porter PLUSIEURS configurations (il suffit
            # d'avoir change de voile en route) : chacune a sa courbe, sinon
            # on melangerait des voilures differentes sur un meme trace.
            for ci, ((sails, engines, derive), _n) in enumerate(
                    self.persistent_store.configs_for_session(session_id)):
                twa_v, tws_v, grid, _cnt = self.persistent_store.table(
                    sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                    symmetric=symmetric, stat=stat, session_ids=only)
                cfg_txt = (f"{'+'.join(sails) or '-'}/{'+'.join(engines) or '-'}"
                           f"/{pe.DERIVE_LABELS.get(derive, '-')}")
                for j, tws in enumerate(tws_v):
                    pts = sorted((twa_v[i], grid[i][j]) for i in range(len(twa_v))
                                 if grid[i][j] is not None)
                    if len(pts) < 2:
                        continue
                    color = TWS_COLORS[(ci + j) % len(TWS_COLORS)]
                    th = [math.radians(a) for a, _v in pts]
                    ra = [v for _a, v in pts]
                    max_radius = max(max_radius, max(ra))
                    n_series += 1
                    if symmetric:
                        ax.plot([-t for t in reversed(th)], list(reversed(ra)),
                                linewidth=1.2, color=color)
                    ax.plot(th, ra, linewidth=1.2, color=color,
                            label=f"{cfg_txt}  {tws:g} nds")
                    ax.scatter(th, ra, s=14, color=color, edgecolors="none", zorder=3)
            ticks = [i * math.pi / 4.0 for i in range(8)]
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [f"{abs(int(round(math.degrees(((t + math.pi) % (2 * math.pi)) - math.pi))))}°"
                 for t in ticks])
            if max_radius > 0:
                ax.set_rmax(max_radius + 0.5)
            if n_series:
                lg = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.05),
                               ncol=max(1, min(n_series, 2)), facecolor=BG_PANEL,
                               edgecolor=COLOR_TICK, fontsize=7)
                for t in lg.get_texts():
                    t.set_color(FG_LABEL)
            empty_lbl.configure(
                text=("" if n_series else
                      "Aucune courbe tracable avec les troncons retenus : il faut au moins "
                      "deux angles mesures pour une meme force de vent."))
            canvas.draw_idle()

        self._build_session_legs_panel(right, session_id, _redraw)
        _redraw()

        foot = tk.Frame(dlg, bg=BG_APP)
        foot.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(foot, text="Cette polaire ne montre QUE cette passe -- l'etape Polaires, "
                            "elle, trace l'entrepot cumulatif.",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8), anchor="w").pack(side="left")
        tk.Button(foot, text="Fermer", command=dlg.destroy, padx=14, pady=3).pack(side="right")
        self._center_on_parent(dlg)
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
        legs = pe.split_into_legs(samples)
        tk.Label(parent, text=f"Troncons de la passe ({len(legs)})", bg=BG_APP,
                 fg=FG_LABEL, font=FONT_LABEL_BOLD, anchor="w").pack(fill="x")
        note_txt = ("Une passe de plusieurs heures n'est pas un bloc : elle se decoupe "
                    "toute seule aux manoeuvres, aux changements de voilure et aux "
                    "interruptions. Decochez un troncon pour l'ecarter du calcul -- "
                    "rien n'est supprime, la polaire se retrace aussitot.")
        tk.Label(parent, text=note_txt, bg=BG_APP, fg=FG_LABEL_DIM,
                 font=("Segoe UI", 8), wraplength=340, justify="left",
                 anchor="w").pack(fill="x", pady=(0, 6))
        box_outer, box = make_scrollable(parent, height=1)
        box_outer.pack(fill="both", expand=True)
        summary = tk.Label(parent, text="", bg=BG_APP, fg=FG_LABEL,
                           font=("Segoe UI", 8), anchor="w", justify="left",
                           wraplength=340)
        summary.pack(fill="x", pady=(6, 0))

        excluded = self._session_excluded_ranges(session_id)

        def _is_excluded(leg):
            if leg["start"] is None:
                return False
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
            ranges = []
            for lg, var in vars_by_leg:
                if not var.get() and lg["start"] is not None:
                    # Bornes elargies d'une demi-seconde : un echantillon
                    # pile sur la frontiere doit tomber du bon cote, sans
                    # dependre d'un arrondi de flottant.
                    ranges.append((lg["start"] - 0.5, lg["end"] + 0.5))
            self._set_session_excluded_ranges(session_id, ranges)
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
            txt = (f"{when}  {dur:.0f} min  {lg['n']} mes.\n"
                   f"    {twa_txt}  |  {pe.LEG_TACK_LABELS[lg['tack']]}\n"
                   f"    {cfg_txt}  |  confiance {conf['level']} {conf['score']}\n"
                   f"    depuis : {lg['cause']}")
            tk.Checkbutton(box, text=txt, variable=var, command=_apply,
                           bg=BG_APP, fg=FG_LABEL, selectcolor=BG_PANEL,
                           activebackground=BG_APP, font=("Segoe UI", 8),
                           anchor="w", justify="left").pack(fill="x", pady=(0, 4))
        _refresh_summary()
        btn_row = tk.Frame(parent, bg=BG_APP)
        btn_row.pack(fill="x", pady=(6, 0))

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

    def _session_excluded_ranges(self, session_id):
        rec = self._session_record(session_id)
        return [list(r) for r in ((rec or {}).get("excluded_ranges") or [])]

    def _set_session_excluded_ranges(self, session_id, ranges):
        """Enregistre les plages ecartees d'une passe, persiste et retrace.
        Rien n'est supprime : les echantillons restent dans l'entrepot et
        redeviennent comptables des que la plage est levee."""
        rec = self._session_record(session_id)
        if rec is None:
            return
        rec["excluded_ranges"] = [[float(a), float(b)] for a, b in ranges]
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
        for idx, ((sails, engines, derive), count) in enumerate(configs):
            # Deux lignes par configuration : l'etat actuel + le bouton en
            # haut, les cases a cocher en dessous. Tout sur une seule ligne
            # deborderait de la fenetre (voiles + moteurs + derive + bouton)
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
            tk.Label(head, text=f"{count} echantillon(s)   -   actuellement : "
                                f"{'+'.join(sails) or '-'} / {'+'.join(engines) or '-'} / "
                                f"{pe.DERIVE_LABELS.get(derive, '-')}",
                     bg=BG_PANEL, fg=FG_LABEL, font=FONT_LABEL_BOLD,
                     anchor="w").pack(side="left", fill="x", expand=True)

            body = tk.Frame(block, bg=BG_PANEL)
            body.pack(fill="x", padx=6, pady=(2, 5))
            sail_vars, engine_vars = {}, {}
            tk.Label(body, text="Voiles :", bg=BG_PANEL, fg=FG_LABEL_DIM,
                     font=("Segoe UI", 8)).pack(side="left", padx=(0, 2))
            for code in self.config_data["sail_list"]:
                v = tk.BooleanVar(value=code in sails)
                tk.Checkbutton(body, text=code, variable=v, bg=BG_PANEL, fg=FG_LABEL,
                                selectcolor=BG_APP, activebackground=BG_PANEL).pack(side="left")
                sail_vars[code] = v
            tk.Label(body, text="   Moteurs :", bg=BG_PANEL, fg=FG_LABEL_DIM,
                     font=("Segoe UI", 8)).pack(side="left", padx=(6, 2))
            for code in self.config_data["engine_list"]:
                v = tk.BooleanVar(value=code in engines)
                tk.Checkbutton(body, text=code, variable=v, bg=BG_PANEL, fg=FG_LABEL,
                                selectcolor=BG_APP, activebackground=BG_PANEL).pack(side="left")
                engine_vars[code] = v
            dvar = tk.StringVar(value=derive or "")
            tk.Label(body, text="   Derive :", bg=BG_PANEL, fg=FG_LABEL_DIM,
                     font=("Segoe UI", 8)).pack(side="left", padx=(6, 2))
            for value, text in (("haute", "Haute"), ("basse", "Basse")):
                tk.Radiobutton(body, text=text, variable=dvar, value=value, bg=BG_PANEL,
                                fg=FG_LABEL, selectcolor=BG_APP,
                                activebackground=BG_PANEL).pack(side="left")
            tk.Button(body, text="Non renseignee",
                      command=lambda v=dvar: v.set("")).pack(side="left", padx=(4, 0))
            self._session_edit_vars.append({
                "old": (sails, engines, derive), "count": count,
                "sails": sail_vars, "engines": engine_vars, "derive": dvar,
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
        derive = entry["derive"].get() or None
        old = entry["old"]
        if (tuple(sorted(sails)), tuple(sorted(engines)), derive) == \
                (tuple(sorted(old[0])), tuple(sorted(old[1])), old[2]):
            messagebox.showinfo("Modifier la passe", "Cette configuration est deja celle indiquee.")
            return
        if not sails and not engines:
            if not messagebox.askyesno("Modifier la passe",
                                        "Aucune voile ni moteur coche -- enregistrer quand meme "
                                        "cette configuration comme 'a nu' ?"):
                return
        n = self.persistent_store.relabel_session_config(session_id, old, sails, engines, derive)
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
            f"{'+'.join(old[0]) or '-'} / {'+'.join(old[1]) or '-'} / "
            f"{pe.DERIVE_LABELS.get(old[2], '-')}\n"
            f"->  {'+'.join(sorted(sails)) or '-'} / {'+'.join(sorted(engines)) or '-'} / "
            f"{pe.DERIVE_LABELS.get(derive, '-')}")

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

        Retourne [(debut, fin, voiles, moteurs, derive)], bornes en epoch."""
        pts = sorted((s for s in samples if s.get("t") is not None), key=lambda s: s["t"])
        if not pts:
            return []
        groups = []
        for s in pts:
            cfg = (tuple(s.get("sails") or ()), tuple(s.get("engines") or ()), s.get("derive"))
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
            out.append((start, end, list(cfg[0]), list(cfg[1]), cfg[2]))
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
        rejouer. L'annotation (voiles/moteurs/derive) est preservee, la passe
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
        for start, end, sails, engines, derive in segments:
            try:
                engine.add_manual_segment(start, end, sails, engines, derive)
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
                    pe.DERIVE_LABELS.get(smp.get("derive"), "-"),
                    f"{smp['stw']:.2f}", f"{smp['twa']:+.0f}", f"{smp['tws']:.2f}",
                )
            else:
                entry = buffer_only[minute]
                self._archive_tree_rows[iid] = ("tampon", entry["t"], entry)
                values = (
                    time.strftime("%H:%M", time.localtime(minute * 60)),
                    "(tampon)", "-", "-", "-",
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
            f"moteurs={'+'.join(smp.get('engines') or []) or '-'}   "
            f"derive={pe.DERIVE_LABELS.get(smp.get('derive'), '-')}",
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
                                  f"Moteurs : {'+'.join(smp.get('engines') or []) or '-'}      "
                                  f"Derive : {pe.DERIVE_LABELS.get(smp.get('derive'), '-')}",
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

    # ---------------------------------------------------------------
    # Etape 3 : Polaires (trace + export). Le trace est AUTOMATIQUE :
    # arriver sur l'etape, changer de configuration ou enregistrer de
    # nouveaux parametres retrace aussitot -- l'ancien bouton "Tracer /
    # rafraichir" n'apportait rien (on ne venait ici que pour voir la
    # polaire). Les reglages de la table (case, symetrie, statistique)
    # vivent dans les Parametres, categorie "Lissage & table" : ce sont des
    # reglages qu'on fixe une fois, pas des commandes de trace.
    # ---------------------------------------------------------------
    def _build_step_polaires(self, parent):
        top = tk.Frame(parent, bg=BG_APP)
        top.pack(fill="x", padx=10, pady=(10, 4))

        tk.Label(top, text="Configuration :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL_BOLD).pack(side="left")
        self.allure_config_var = tk.StringVar(value="")
        self.allure_config_menu = ttk.OptionMenu(top, self.allure_config_var, "")
        # Largeur AJUSTEE au contenu (voir _refresh_config_choices) et non
        # figee : une configuration complete -- plusieurs voiles, plusieurs
        # moteurs, la derive et l'effectif -- depasse largement les 32
        # caracteres d'origine, et l'on se retrouvait a choisir entre des
        # libelles coupes au milieu. Le menu s'etire en plus avec la
        # fenetre, pour profiter de la place quand il y en a.
        self.allure_config_menu.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self._refresh_polar_menu_width([])
        # Le rappel des reglages passe SOUS la ligne : cote a cote, il
        # disputait sa largeur au menu, et c'est le menu qui perdait.
        tk.Label(parent, text="Reglages de la table (case, symetrie, statistique) : "
                               "roue dentee \u2699 > Lissage & table.",
                 bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x", padx=10, pady=(0, 2))

        export_row = tk.Frame(parent, bg=BG_APP)
        export_row.pack(fill="x", padx=10, pady=(0, 2))
        tk.Button(export_row, text="Exporter .pol (configuration selectionnee)",
                  command=self._export_pol).pack(side="left", padx=(0, 8))
        tk.Button(export_row, text="Exporter .csv (configuration selectionnee)",
                  command=self._export_csv_one).pack(side="left", padx=(0, 8))
        tk.Button(export_row, text="Exporter .csv (toutes les configurations)",
                  command=self._export_csv_all).pack(side="left")
        tk.Button(export_row, text="Polaire max (routage)...",
                  command=self._open_max_polar_window,
                  bg=COLOR_ACCENT, fg=FG_ON_ACCENT,
                  font=FONT_LABEL_BOLD).pack(side="left", padx=(16, 0))

        # Titre + legende textuelle (taille des points) affiches comme des
        # Label Tk plutot que via ax.set_title()/un texte matplotlib : un
        # titre matplotlib est positionne par une marge FIXE en points
        # au-dessus des axes -- sur un canvas devenu tres court (petite
        # fenetre), cette marge peut ne plus suffire et le titre se retrouve
        # rogne hors de la figure (invisible). Un Label Tk, lui, est mis en
        # page par Tk comme n'importe quel autre widget : il reste TOUJOURS
        # visible, quelle que soit la taille de la fenetre, et c'est le
        # graphique lui-meme (seul widget en fill="both", expand=True) qui
        # absorbe la difference en se redimensionnant.
        self.polar_title_lbl = tk.Label(parent, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_LABEL_BOLD)
        self.polar_title_lbl.pack(anchor="w", padx=10, pady=(0, 0))
        self.polar_hint_lbl = tk.Label(parent, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                        font=("Segoe UI", 8))
        self.polar_hint_lbl.pack(anchor="w", padx=10, pady=(0, 2))

        # Figure/axes crees UNE SEULE FOIS (reutilises a chaque _draw_polar via
        # .clear()) -- plus de titre matplotlib (voir plus haut) : la marge du
        # haut peut donc etre quasiment entierement rendue au cercle. Marge
        # en bas conservee pour la legende horizontale sous le cercle plutot
        # que sur le cote (voir _draw_polar) : contrairement a une legende
        # posee a droite (bbox_to_anchor hors-figure), une legende horizontale
        # ne rogne jamais la largeur du cercle.
        self.polar_fig = Figure(figsize=(7, 6), dpi=100, facecolor=BG_APP)
        self.polar_fig.subplots_adjust(top=0.98, bottom=0.14)
        self.polar_ax = self.polar_fig.add_subplot(111, projection="polar", facecolor=BG_PANEL)
        self.polar_ax.set_theta_zero_location("N")
        self.polar_ax.set_theta_direction(-1)
        self.polar_ax.tick_params(colors=FG_LABEL, labelsize=8)
        self.polar_ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)
        self.polar_canvas = FigureCanvasTkAgg(self.polar_fig, master=parent)
        self.polar_canvas.get_tk_widget().configure(bg=BG_APP, highlightthickness=0)
        self.polar_canvas.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(0, 6))

        self._last_table = None  # (sails, engines, twa_values, tws_values, grid, counts)
        self._refresh_config_choices()

    # Bornes de la largeur du menu de configuration, en caracteres : assez
    # large pour ne pas couper une configuration ordinaire, pas au point de
    # chasser tout le reste de la ligne hors de l'ecran.
    POLAR_MENU_MIN_CHARS = 32
    POLAR_MENU_MAX_CHARS = 64

    def _refresh_polar_menu_width(self, labels):
        """Ajuste la largeur du menu deroulant au plus long libelle."""
        if not hasattr(self, "allure_config_menu"):
            return
        longest = max((len(l) for l in labels), default=0)
        self.allure_config_menu.configure(
            width=max(self.POLAR_MENU_MIN_CHARS,
                      min(longest + 2, self.POLAR_MENU_MAX_CHARS)))

    def _refresh_config_choices(self):
        configs = self.persistent_store.configs(session_ids=self._included_session_ids())
        labels = []
        self._config_by_label = {}
        for (sails, engines, derive), count in configs:
            label = (f"{'+'.join(sails) or '-'} / {'+'.join(engines) or '-'} / "
                     f"{pe.DERIVE_LABELS.get(derive, '-')}  (n={count})")
            labels.append(label)
            self._config_by_label[label] = (sails, engines, derive)
        self._refresh_polar_menu_width(labels)
        menu = self.allure_config_menu["menu"]
        menu.delete(0, "end")
        for label in labels:
            # Choisir une configuration retrace immediatement : c'est ce
            # que le clic veut dire, plus besoin d'un bouton a part.
            menu.add_command(label=label, command=lambda v=label: self._on_allure_config_selected(v))
        if labels:
            if self.allure_config_var.get() not in labels:
                self.allure_config_var.set(labels[0])
        else:
            self.allure_config_var.set("")
        if hasattr(self, "store_size_lbl"):
            self._refresh_store_size_label()

    def _on_allure_config_selected(self, label):
        self.allure_config_var.set(label)
        self._draw_polar(auto=True)

    def _draw_polar(self, auto=False):
        """Trace la polaire de la configuration selectionnee. auto=True =
        appel declenche par la navigation ou un changement de selection :
        dans ce cas un entrepot vide s'affiche comme un message d'accueil
        dans la zone de titre, JAMAIS comme une boite de dialogue -- une
        popup a chaque passage sur l'etape serait insupportable."""
        label = self.allure_config_var.get()
        if not label or label not in self._config_by_label:
            if auto:
                self.polar_ax.clear()
                self.polar_canvas.draw_idle()
                self.polar_title_lbl.configure(
                    text="Entrepot vide : traitez d'abord une session (etape 1) -- "
                         "la polaire se tracera ici automatiquement.")
                self.polar_hint_lbl.configure(text="")
                self._last_table = None
            else:
                messagebox.showinfo("Polaires", "Aucune configuration disponible dans l'entrepot "
                                                 "cumulatif (traitez d'abord au moins une session).")
            return
        sails, engines, derive = self._config_by_label[label]
        twa_bin = float(self.twa_bin_var.get()) if hasattr(self, "twa_bin_var") else self.config_data["twa_bin_deg"]
        tws_bin = float(self.tws_bin_var.get()) if hasattr(self, "tws_bin_var") else self.config_data["tws_bin_kn"]
        symmetric = bool(self.symmetric_var.get()) if hasattr(self, "symmetric_var") \
            else self.config_data["symmetric_port_starboard"]
        stat = self.stat_var.get() if hasattr(self, "stat_var") else self.config_data["aggregation_stat"]

        twa_values, tws_values, grid, counts = self.persistent_store.table(
            sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric, stat=stat,
            session_ids=self._included_session_ids())
        # Indice de confiance, calcule sur EXACTEMENT le meme decoupage
        # (voir PolarSampleStore._cells). Purement informatif : il ne
        # retranche ni ne modifie une seule vitesse.
        _t2, _w2, conf = self.persistent_store.confidence_table(
            sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
            symmetric=symmetric, session_ids=self._included_session_ids())
        self._last_conf = conf
        self._last_table = (sails, engines, derive, twa_values, tws_values, grid, counts)

        if hasattr(self, "polar_title_lbl"):
            self.polar_title_lbl.configure(
                text=f"Voiles: {'+'.join(sails) or '-'}   Moteurs: {'+'.join(engines) or '-'}   "
                     f"Derive: {pe.DERIVE_LABELS.get(derive, '-')}")

        self.polar_ax.clear()
        self.polar_ax.set_theta_zero_location("N")
        self.polar_ax.set_theta_direction(-1)
        self.polar_ax.tick_params(colors=FG_LABEL, labelsize=8)
        self.polar_ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)
        self.polar_ax.set_facecolor(BG_PANEL)

        if not tws_values:
            if hasattr(self, "polar_hint_lbl"):
                self.polar_hint_lbl.configure(text="")
            self.polar_canvas.draw_idle()
            return

        # Prepasse : rassemble tous les points (avec leur nombre d'echantillons)
        # pour pouvoir mettre a l'echelle la taille des marqueurs de facon
        # coherente sur tout le graphique -- une case batie sur 2 echantillons
        # ne doit pas avoir l'air aussi fiable qu'une case qui en a 200.
        # La taille des marqueurs suit desormais l'INDICE DE CONFIANCE et
        # non le simple nombre d'echantillons : 200 mesures prises dans un
        # seul bord de dix minutes ne valent pas 20 mesures reparties sur
        # huit sorties, et le trace doit le montrer plutot que le masquer.
        series = []
        max_count = 1
        max_radius = 0.0
        for j, tws in enumerate(tws_values):
            pts = []
            for i, (twa, row) in enumerate(zip(twa_values, grid)):
                if row[j] is None:
                    continue
                c = (conf[i][j] if conf and i < len(conf) and j < len(conf[i]) else None)
                pts.append((twa, row[j], (c or {}).get("score", 0)))
            if not pts:
                continue
            pts.sort(key=lambda p: p[0])
            series.append((tws, pts))
            max_count = 100
            max_radius = max(max_radius, max(stw for _, stw, _ in pts))

        for j, (tws, pts) in enumerate(series):
            thetas = [math.radians(twa) for twa, _, _ in pts]
            radii = [stw for _, stw, _ in pts]
            csamples = [c for _, _, c in pts]
            color = TWS_COLORS[j % len(TWS_COLORS)]
            if symmetric:
                # Deux DEMI-COURBES tracees separement (et non une seule
                # polyligne allant de -170 a +170 en passant par le milieu) :
                # une polyligne unique refermait les deux bords l'un sur
                # l'autre par une corde au ras de l'etrave -- disgracieux, et
                # surtout faux (elle pretendait une vitesse au vent debout, ou
                # aucune mesure n'existe). Une seule des deux moities porte
                # l'etiquette de legende, sinon chaque TWS y figurerait deux
                # fois.
                halves = [([-t for t in reversed(thetas)], list(reversed(radii))),
                          (thetas, radii)]
                counts_full = list(reversed(csamples)) + csamples
                thetas_full = halves[0][0] + halves[1][0]
                radii_full = halves[0][1] + halves[1][1]
                for k, (th, ra) in enumerate(halves):
                    self.polar_ax.plot(th, ra, linewidth=1.2, color=color,
                                        label=(f"TWS {tws:g} nds" if k == 0 else None))
            else:
                thetas_full, radii_full, counts_full = thetas, radii, csamples
                # Ligne fine (forme generale, toujours lisible) ; les
                # marqueurs, dont la taille reflete le nombre d'echantillons
                # de chaque case, sont poses ensuite pour les deux cas.
                self.polar_ax.plot(thetas_full, radii_full, linewidth=1.2, color=color,
                                    label=f"TWS {tws:g} nds")
            # Marqueurs (6pt min pour rester visibles, 42pt max pour une case
            # tres fournie) -- poses en UN seul appel, symetrie ou non.
            sizes = [6.0 + 36.0 * (c / max_count) for c in counts_full]
            self.polar_ax.scatter(thetas_full, radii_full, s=sizes, color=color,
                                   edgecolors="none", zorder=3)

        # Graduations angulaires en ANGLE DE VENT (0-180 de chaque bord) et
        # non en 0-360 : le demi-cercle babord porte des angles negatifs
        # (-90 = travers babord), que matplotlib etiquetterait "270 deg" --
        # un cap, alors qu'une polaire ne parle jamais que d'angles au vent.
        # Les deux bords se lisent donc desormais pareil, 0 a l'etrave, 180
        # a l'arriere.
        ticks = [i * math.pi / 4.0 for i in range(8)]
        self.polar_ax.set_xticks(ticks)
        self.polar_ax.set_xticklabels(
            [f"{abs(int(round(math.degrees(((t + math.pi) % (2 * math.pi)) - math.pi))))}°"
             for t in ticks])

        # Marge d'un demi-noeud au-dela du point le plus rapide : sans ca,
        # matplotlib cale automatiquement le bord exterieur du cercle
        # pile sur la valeur max, et un point qui tombe dessus se retrouve
        # colle/coupe par le trait de bordure -- difficile a lire quand la
        # vitesse est justement a la limite. La marge laisse de l'air autour
        # de tous les points, y compris les plus rapides.
        self.polar_ax.set_rmax(max_radius + 0.5)

        # Legende HORIZONTALE sous le cercle plutot qu'a droite (l'ancien
        # bbox_to_anchor=(1.35, 1.1) reservait une grande marge laterale hors
        # de la figure, ce qui laissait moins de place au cercle lui-meme et
        # se lisait mal en petite fenetre) -- ncol s'adapte au nombre de
        # series TWS pour rester sur 1-2 lignes maximum.
        ncol = max(1, min(len(series), 6))
        legend = self.polar_ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.06), ncol=ncol,
                                       facecolor=BG_PANEL, edgecolor=COLOR_TICK, fontsize=8)
        for text in legend.get_texts():
            text.set_color(FG_LABEL)
        # Pas de ax.set_title() ici : le titre vit desormais dans
        # polar_title_lbl (Label Tk, deja mis a jour plus haut), toujours
        # visible quelle que soit la taille de la fenetre -- voir la note
        # dans _build_step_polaires.
        if hasattr(self, "polar_hint_lbl"):
            # Bilan de confiance : combien de cases meritent qu'on s'y fie,
            # et ce qui manque encore a cette configuration pour etre mure.
            mat = self.persistent_store.config_confidence(
                sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                symmetric=symmetric, session_ids=self._included_session_ids())
            txt = ("La taille des points reflete l'INDICE DE CONFIANCE de chaque case "
                   "(nombre de mesures, repartition dans le temps, dispersion) -- "
                   "un gros point est une case sur laquelle on peut compter.\n"
                   f"Maturite de cette polaire : {mat['score']}/100 ({mat['word']}) -- "
                   f"{mat['solid']} case(s) solide(s), {mat['usable']} bonne(s), "
                   f"{mat['weak']} fragile(s), sur {mat['sessions']} sortie(s).")
            if mat["missing"]:
                txt += "  Jamais mesure : " + ", ".join(mat["missing"]) + "."
            self.polar_hint_lbl.configure(text=txt)
        self.polar_canvas.draw_idle()

    def _export_pol(self):
        if not self._last_table:
            messagebox.showinfo("Export", "Tracez d'abord une polaire.")
            return
        sails, engines, derive, twa_values, tws_values, grid, _counts = self._last_table
        if not twa_values:
            messagebox.showinfo("Export", "Aucune donnee a exporter pour cette configuration.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".pol", filetypes=[("Fichier polaire", "*.pol")],
            initialfile=f"polaire_{'-'.join(sails) or 'nu'}_{'-'.join(engines) or 'nu'}_{derive or 'nu'}.pol")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(pe.format_pol_table(twa_values, tws_values, grid))
        messagebox.showinfo("Export", f"Fichier exporte :\n{path}")

    def _export_csv_one(self):
        if not self._last_table:
            messagebox.showinfo("Export", "Tracez d'abord une polaire.")
            return
        sails, engines, derive, twa_values, tws_values, grid, counts = self._last_table
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"polaire_{'-'.join(sails) or 'nu'}_{'-'.join(engines) or 'nu'}_{derive or 'nu'}.csv")
        if not path:
            return
        content = pe.format_csv_table(
            [(sails, engines, derive, twa_values, tws_values, grid, counts)],
            confidences=[getattr(self, "_last_conf", None)])
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        messagebox.showinfo("Export", f"Fichier exporte :\n{path}")

    def _export_csv_all(self):
        session_ids = self._included_session_ids()
        configs = self.persistent_store.configs(session_ids=session_ids)
        if not configs:
            messagebox.showinfo("Export", "L'entrepot cumulatif est vide (ou aucune passe incluse).")
            return
        twa_bin = float(self.twa_bin_var.get()) if hasattr(self, "twa_bin_var") else self.config_data["twa_bin_deg"]
        tws_bin = float(self.tws_bin_var.get()) if hasattr(self, "tws_bin_var") else self.config_data["tws_bin_kn"]
        symmetric = bool(self.symmetric_var.get()) if hasattr(self, "symmetric_var") \
            else self.config_data["symmetric_port_starboard"]
        stat = self.stat_var.get() if hasattr(self, "stat_var") else self.config_data["aggregation_stat"]
        configs_tables, confidences = [], []
        for (sails, engines, derive), _count in configs:
            twa_values, tws_values, grid, counts = self.persistent_store.table(
                sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin, symmetric=symmetric,
                stat=stat, session_ids=session_ids)
            if twa_values:
                configs_tables.append((sails, engines, derive, twa_values, tws_values, grid, counts))
                confidences.append(self.persistent_store.confidence_table(
                    sails, engines, derive=derive, twa_bin_deg=twa_bin, tws_bin_kn=tws_bin,
                    symmetric=symmetric, session_ids=session_ids)[2])
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="polaires_toutes_configs.csv")
        if not path:
            return
        content = pe.format_csv_table(configs_tables, confidences=confidences)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        messagebox.showinfo("Export", f"Fichier exporte :\n{path}")

    # ---------------------------------------------------------------
    # Polaire MAX exploitable (routage) -- page qui s'ouvre PAR-DESSUS
    # l'etape Polaires. Les polaires par configuration repondent a "que
    # vaut le bateau sous cette voilure ?" ; un logiciel de routage, lui,
    # veut UNE seule table pleine : "que vaut le bateau, point". Cette
    # page la construit : pour chaque case cap/vent, la meilleure vitesse
    # parmi les configurations retenues -- et LAQUELLE l'a obtenue, ce qui
    # donne en prime le guide de voilure ("pour ce cap et ce vent, portez
    # ca"). Les trous interieurs sont bouches par interpolation le long du
    # TWA (jamais d'extrapolation au-dela des mesures).
    # ---------------------------------------------------------------
    def _open_max_polar_window(self):
        session_ids = self._included_session_ids()
        configs = self.persistent_store.configs(session_ids=session_ids)
        if not configs:
            messagebox.showinfo("Polaire max", "L'entrepot cumulatif est vide (ou aucune "
                                               "passe incluse) : rien a combiner.")
            return
        if getattr(self, "_max_polar_win", None) is not None:
            try:
                self._max_polar_win.lift()
                return
            except tk.TclError:
                self._max_polar_win = None

        win = tk.Toplevel(self)
        self._max_polar_win = win
        win.title("Polaire max exploitable (routage)")
        win.configure(bg=BG_APP)
        win.transient(self)
        # Taille bornee par l'ECRAN : sur un portable 1366x768, une fenetre
        # de 1080x760 demandee telle quelle depasse la zone utile, et le bas
        # -- ou vivent justement les boutons d'export -- se retrouve sous la
        # barre des taches. On prend donc le plus petit des deux.
        try:
            scr_w, scr_h = win.winfo_screenwidth(), win.winfo_screenheight()
        except tk.TclError:
            scr_w, scr_h = 1280, 800
        win_w, win_h = min(1080, max(720, scr_w - 80)), min(760, max(520, scr_h - 120))
        win.geometry(f"{win_w}x{win_h}")
        win.minsize(720, 520)
        # Geometrie APPLIQUEE avant de construire le contenu : sans cela, la
        # colonne de gauche (hauteur imposee, pack_propagate coupe) se met en
        # page contre une fenetre encore haute d'un pixel, et son bas --
        # les exports -- n'apparait qu'au premier redimensionnement. C'etait
        # exactement le symptome : "obligé de resize pour récupérer le csv".
        win.update_idletasks()
        win.protocol("WM_DELETE_WINDOW", self._close_max_polar_window)
        win.bind("<Escape>", lambda _e: self._close_max_polar_window())

        # Rien ne defile ici : la page est batie pour TENIR dans la fenetre.
        # Les commandes (exports, recherche de voilure, options) sont ancrees
        # aux bords, ou Tk les sert en premier ; seule la liste des
        # configurations, dont la hauteur depend du bateau, absorbe le reste.
        # Une commande qu'il faut decouvrir en secouant la fenetre n'existe
        # pas pour celui qui la cherche.
        # --- Pied de page : les EXPORTS, sur toute la largeur ---
        # Pose EN PREMIER et en side="bottom" : Tk sert les bords avant le
        # centre, ces boutons ne peuvent donc plus etre repousses hors de la
        # fenetre par quoi que ce soit au-dessus. C'est aussi leur vraie
        # place -- ils concluent la page, ils n'appartiennent pas a la
        # colonne des reglages.
        footer = tk.Frame(win, bg=BG_PANEL)
        footer.pack(side="bottom", fill="x")
        tk.Frame(win, bg=COLOR_BORDER, height=1).pack(side="bottom", fill="x")
        foot_in = tk.Frame(footer, bg=BG_PANEL)
        foot_in.pack(fill="x", padx=12, pady=8)
        tk.Button(foot_in, text="Exporter .pol (routage)",
                  command=self._max_polar_export_pol, bg=COLOR_ACCENT, fg=FG_ON_ACCENT,
                  font=FONT_LABEL_BOLD, padx=14, pady=4, cursor="hand2").pack(side="left")
        tk.Button(foot_in, text="Exporter .csv (vitesses + voilure gagnante)",
                  command=self._max_polar_export_csv, padx=14, pady=4,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Button(foot_in, text="Exporter TimeZero (2 fichiers .xml)",
                  command=self._max_polar_export_timezero, padx=14, pady=4,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        tk.Button(foot_in, text="Fermer", command=self._close_max_polar_window,
                  padx=14, pady=4, cursor="hand2").pack(side="right")
        # Aucune explication en pied de page : un texte long coince entre des
        # boutons se replie mal au redimensionnement et pousse le reste. Ce
        # qu'il faut savoir sur chaque format est dit la ou on le decide --
        # dans la section "Trous de la grille" pour le remplissage, et dans
        # le compte rendu qui suit chaque export.

        left = tk.Frame(win, bg=BG_APP, width=346)
        left.pack(side="left", fill="y", padx=(10, 4), pady=10)
        left.pack_propagate(False)
        right = tk.Frame(win, bg=BG_APP)
        right.pack(side="left", fill="both", expand=True, padx=(4, 10), pady=10)

        # Dans la colonne de gauche, les deux blocs du BAS sont poses en
        # premier, en side="bottom" : ils gardent leur place quoi qu'il
        # arrive, et c'est la liste des configurations -- seule partie dont
        # la hauteur depend des donnees -- qui absorbe ce qui reste. Rien ne
        # defile : tout tient dans la fenetre, par construction.
        f_ask = section(left, "Quelle voilure pour ce cap / ce vent ?", side="bottom")
        f_opt = section(left, "Trous de la grille", side="bottom")

        # La liste des configurations est le SEUL bloc dont la hauteur depend
        # des donnees : elle prend donc tout ce qui reste. Au-dela d'une
        # dizaine de configurations elle se donne sa propre barre de
        # defilement -- mais elle seule, et seulement dans ce cas : sur un
        # bateau ordinaire, rien ne defile nulle part.
        f_cfg = section(left, "Configurations mises en concurrence", expand=True)
        note(f_cfg, "La meilleure vitesse parmi les configurations cochees l'emporte, "
                    "case par case. Le moteur fait partie de la voilure (defaut "
                    "modifiable : roue dentee > Lissage & table).",
             wraplength=300)
        if len(configs) > 10:
            cfg_outer, cfg_box = make_scrollable(f_cfg, height=1)
            cfg_outer.pack(fill="both", expand=True)
        else:
            cfg_box = tk.Frame(f_cfg, bg=BG_APP)
            cfg_box.pack(fill="both", expand=True)
        self._max_cfg_vars = []
        # Le moteur fait-il partie de la voilure ? Sur ce bateau (cargo a
        # voile et moteur), OUI par defaut : tout concourt. En mode
        # "voilier pur" (reglage decoche), les configurations moteur
        # restent proposees mais decochees -- sauf si le bateau n'a QUE des
        # configurations moteur, auquel cas tout est coche plutot que
        # d'ouvrir sur une page vide.
        engine_in = bool(self.config_data.get("max_polar_include_engine", True))
        any_pure_sail = any(not engines for (_s, engines, _d), _c in configs)
        for (sails, engines, derive), count in configs:
            var = tk.BooleanVar(value=engine_in or (not engines) or not any_pure_sail)
            label = (f"{'+'.join(sails) or '-'} / {'+'.join(engines) or '-'} / "
                     f"{pe.DERIVE_LABELS.get(derive, '-')}  (n={count})")
            # wraplength : une configuration complete (voiles + moteurs +
            # derive + effectif) depasse largement la largeur de la colonne,
            # et une etiquette tronquee ne permet plus de savoir CE QU'ON
            # COCHE -- elle passe donc a la ligne au lieu d'etre coupee.
            tk.Checkbutton(cfg_box, text=label, variable=var, bg=BG_APP, fg=FG_LABEL,
                           selectcolor=BG_PANEL, activebackground=BG_APP,
                           font=FONT_LABEL, anchor="w", justify="left",
                           wraplength=290,
                           command=self._max_polar_recompute).pack(fill="x", padx=6)
            self._max_cfg_vars.append((var, (sails, engines, derive), label))

        self._max_fill_var = tk.BooleanVar(value=True)
        tk.Checkbutton(f_opt, text="Boucher par interpolation (le long du TWA)",
                       variable=self._max_fill_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP, font=FONT_LABEL,
                       command=self._max_polar_recompute).pack(fill="x", padx=6)
        note(f_opt, "Uniquement ENTRE deux cases mesurees d'une meme colonne de vent, "
                    "jamais au-dela. Les cases interpolees sont marquees dans le .csv. "
                    "A l'export .pol, les cases restees sans mesure ni interpolation "
                    "valent 0 : un routeur y lit 'le bateau ne marche pas la'.",
             wraplength=300)

        clip_row = tk.Frame(f_opt, bg=BG_APP)
        clip_row.pack(fill="x", padx=6, pady=(4, 0))
        self._max_clip_var = tk.BooleanVar(
            value=bool(self.config_data.get("max_polar_clip_enabled", False)))
        tk.Checkbutton(clip_row, text="Ecreter les pointes au-dela de",
                       variable=self._max_clip_var, bg=BG_APP, fg=FG_LABEL,
                       selectcolor=BG_PANEL, activebackground=BG_APP, font=FONT_LABEL,
                       command=self._max_polar_recompute).pack(side="left")
        self._max_clip_slope_var = tk.DoubleVar(
            value=float(self.config_data.get("max_polar_clip_slope", 1.5)))
        sp = tk.Spinbox(clip_row, from_=0.1, to=10.0, increment=0.1, width=5,
                        textvariable=self._max_clip_slope_var,
                        command=self._max_polar_recompute)
        sp.pack(side="left", padx=(4, 4))
        sp.bind("<Return>", lambda _e: self._max_polar_recompute())
        sp.bind("<FocusOut>", lambda _e: self._max_polar_recompute())
        tk.Label(clip_row, text="kn / 10 deg", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        note(f_opt, "Une polaire est une courbe PHYSIQUEMENT LISSE : la vitesse d'un "
                    "bateau ne peut pas grimper de trois noeuds entre 60 et 65 degres. "
                    "Une case qui le pretend ne decrit pas le bateau -- elle decrit un "
                    "surf sur une vague, une risee ou une poussee de courant, un instant "
                    "que le routage prendrait pour un acquis. L'ecretage borne la PENTE "
                    "de la courbe et rabaisse ces pointes. Uniquement vers le BAS (un "
                    "creux n'est pas suspect, le remonter serait inventer une "
                    "performance) et jamais aux extremites, qui n'ont qu'un voisin. "
                    "Conseille : 1,5 kn / 10 deg.", wraplength=300)
        self._max_clip_lbl = tk.Label(f_opt, text="", bg=BG_APP, fg=COLOR_ACCENT,
                                       font=("Segoe UI", 8), anchor="w", wraplength=300,
                                       justify="left")
        self._max_clip_lbl.pack(fill="x", padx=6, pady=(0, 6))

        ask_row = tk.Frame(f_ask, bg=BG_APP)
        ask_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(ask_row, text="TWA (deg) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self._max_ask_twa = tk.Entry(ask_row, width=5)
        self._max_ask_twa.pack(side="left", padx=(4, 10))
        tk.Label(ask_row, text="TWS (nds) :", bg=BG_APP, fg=FG_LABEL).pack(side="left")
        self._max_ask_tws = tk.Entry(ask_row, width=5)
        self._max_ask_tws.pack(side="left", padx=(4, 10))
        tk.Button(ask_row, text="Chercher", command=self._max_polar_lookup,
                  padx=8).pack(side="left")
        for w in (self._max_ask_twa, self._max_ask_tws):
            w.bind("<Return>", lambda _e: self._max_polar_lookup())
        self._max_ask_result = tk.Label(f_ask, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_LABEL_BOLD, wraplength=300,
                                         justify="left", anchor="w")
        self._max_ask_result.pack(fill="x", padx=6, pady=(0, 6))

        # --- Colonne de droite : trace + guide de voilure ---
        self._max_title_lbl = tk.Label(right, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=FONT_LABEL_BOLD, anchor="w")
        self._max_title_lbl.pack(side="top", fill="x")
        self._max_zoom_lbl = tk.Label(right, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                       font=("Segoe UI", 8), anchor="w")
        self._max_zoom_lbl.pack(side="top", fill="x")

        # ORDRE DE POSE : la legende et le guide EN PREMIER, ancres en bas.
        # Tk sert les bords avant le centre ; le graphique, lui, prend ce
        # qui reste. Poses apres lui, ils se faisaient ecraser par son
        # expand=True et la legende devenait illisible -- on lisait "C3"
        # dans le guide sans pouvoir savoir de quelle voilure il s'agit.
        # La legende dit a quoi correspondent les codes C1, C2... : une
        # ligne par configuration, et un retour a la ligne qui suit la
        # largeur reelle de la colonne.
        self._max_legend_lbl = tk.Label(right, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, justify="left", anchor="w")
        self._max_legend_lbl.pack(side="bottom", fill="x", pady=(4, 0))
        guide_wrap = tk.Frame(right, bg=BG_APP)
        guide_wrap.pack(side="bottom", fill="x")
        self._max_guide = ttk.Treeview(guide_wrap, show="headings", height=7)
        guide_sb = ttk.Scrollbar(guide_wrap, orient="vertical", command=self._max_guide.yview)
        self._max_guide.configure(yscrollcommand=guide_sb.set)
        self._max_guide.pack(side="left", fill="x", expand=True)
        guide_sb.pack(side="left", fill="y")
        # CLIC sur une case du guide (ou du trace) : la fiche complete du
        # point s'ouvre -- voir _open_max_cell_details.
        self._max_guide.bind("<Button-1>", self._on_max_guide_click)
        tk.Label(right, text="Guide de voilure (configuration gagnante par case) "
                             "-- cliquez une case pour la fiche detaillee du point :",
                 bg=BG_APP, fg=FG_LABEL, font=FONT_LABEL_BOLD, anchor="w").pack(
            side="bottom", fill="x", pady=(6, 0))

        self._max_fig = Figure(figsize=(6, 4.6), dpi=100, facecolor=BG_APP)
        self._max_fig.subplots_adjust(top=0.97, bottom=0.13)
        self._max_ax = self._max_fig.add_subplot(111, projection="polar", facecolor=BG_PANEL)
        self._max_canvas = FigureCanvasTkAgg(self._max_fig, master=right)
        self._max_canvas.get_tk_widget().configure(bg=BG_APP, highlightthickness=0)
        self._max_canvas.get_tk_widget().pack(side="top", fill="both", expand=True)
        # Infobulle de SURVOL : une polaire porte des centaines de cases, et
        # aller chercher chacune dans un tableau casse le fil de la lecture.
        # Sous le curseur, la case dit tout ce qu'elle sait d'elle-meme --
        # vitesse, voilure gagnante, origine, confiance.
        self._max_tip = tk.Label(right, text="", bg=BG_PANEL, fg=FG_LABEL,
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
        self._max_view = None          # None = ajuste a la fenetre
        self._max_drag = None
        self._max_canvas.mpl_connect("scroll_event", self._on_max_polar_scroll)
        self._max_canvas.mpl_connect("button_press_event", self._on_max_polar_press)
        self._max_canvas.mpl_connect("button_release_event", self._on_max_polar_release)
        self._max_canvas.get_tk_widget().bind(
            "<Double-1>", lambda _e: self._reset_max_polar_zoom())
        right.bind("<Configure>", lambda e: self._max_legend_lbl.configure(
            wraplength=max(200, e.width - 12)))

        self._max_result = None   # (twa, tws, grid, counts, winners, mask, labels, clipped)
        # Le cadre carre se deduit des proportions de la FIGURE : redimensionner
        # la fenetre les change, la vue doit donc se reposer.
        self._max_canvas.get_tk_widget().bind(
            "<Configure>", lambda _e: self._apply_max_view(), add="+")
        self._max_polar_recompute()
        win.update_idletasks()
        self._center_on_parent(win)

    def _close_max_polar_window(self):
        self._close_max_cell_details()
        if getattr(self, "_max_polar_win", None) is not None:
            try:
                self._max_polar_win.destroy()
            except tk.TclError:
                pass
            self._max_polar_win = None

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
        tac, comme le trace principal."""
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
        if hasattr(self, "_max_clip_lbl"):
            self._max_clip_lbl.configure(
                text=("" if not self._max_clip_var.get() else
                      (f"{n_clip} case(s) rabaissee(s) par l'ecretage." if n_clip
                       else "Aucune pointe a ecreter a ce seuil.")))
        mask = [[False] * len(r) for r in grid]
        if self._max_fill_var.get() and grid:
            grid, mask = pe.fill_polar_holes(twa_values, grid)
        # Le reglage d'ecretage suit l'utilisateur d'une ouverture a l'autre :
        # c'est un choix de methode, pas un geste a refaire chaque fois.
        try:
            self.config_data["max_polar_clip_enabled"] = bool(self._max_clip_var.get())
            self.config_data["max_polar_clip_slope"] = float(self._max_clip_slope_var.get())
            pcfg.save_config(self.config_data)
        except (tk.TclError, ValueError, OSError):
            pass
        self._max_result = (twa_values, tws_values, grid, counts, winners, mask,
                            labels, clipped)
        self._max_polar_redraw(symmetric)
        self._max_polar_refresh_guide()
        # Une fiche de point encore ouverte decrirait l'ANCIENNE table :
        # fermee plutot que laissee mentir.
        self._close_max_cell_details()

    def _max_polar_redraw(self, symmetric):
        twa_values, tws_values, grid, counts, _winners, mask, labels, clipped = self._max_result
        ax = self._max_ax
        ax.clear()
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.tick_params(colors=FG_LABEL, labelsize=8)
        ax.grid(True, color=COLOR_TICK, linestyle="--", linewidth=0.5, alpha=0.6)
        ax.set_facecolor(BG_PANEL)
        n_cfg = len(labels)
        if not tws_values:
            self._max_title_lbl.configure(
                text="Aucune case : cochez au moins une configuration ayant des mesures.")
            self._max_canvas.draw_idle()
            return
        self._max_title_lbl.configure(
            text=f"Meilleure vitesse toutes voilures confondues -- {n_cfg} configuration(s) "
                 "en concurrence. Points pleins : mesure ; creux : interpolation ; "
                 "croix : pointe ecretee. Survolez un point pour son detail.")
        max_radius = 0.0
        for j, tws in enumerate(tws_values):
            pts = [(twa_values[i], grid[i][j], mask[i][j], clipped[i][j])
                   for i in range(len(twa_values)) if grid[i][j] is not None]
            if not pts:
                continue
            pts.sort(key=lambda p: p[0])
            color = TWS_COLORS[j % len(TWS_COLORS)]
            thetas = [math.radians(t) for t, _v, _m, _c in pts]
            radii = [v for _t, v, _m, _c in pts]
            max_radius = max(max_radius, max(radii))
            if symmetric:
                ax.plot([-t for t in reversed(thetas)], list(reversed(radii)),
                        linewidth=1.2, color=color)
                ax.plot(thetas, radii, linewidth=1.2, color=color, label=f"TWS {tws:g} nds")
                sides = ((thetas, pts), ([-t for t in thetas], pts))
            else:
                ax.plot(thetas, radii, linewidth=1.2, color=color, label=f"TWS {tws:g} nds")
                sides = ((thetas, pts),)
            for th_list, pt_list in sides:
                meas = [(th, v) for th, (_t, v, m, c) in zip(th_list, pt_list)
                        if not m and not c]
                interp = [(th, v) for th, (_t, v, m, c) in zip(th_list, pt_list) if m]
                clip = [(th, v) for th, (_t, v, m, c) in zip(th_list, pt_list)
                        if c and not m]
                if meas:
                    ax.scatter([p[0] for p in meas], [p[1] for p in meas], s=14,
                               color=color, edgecolors="none", zorder=3)
                if interp:
                    ax.scatter([p[0] for p in interp], [p[1] for p in interp], s=14,
                               facecolors="none", edgecolors=color, zorder=3)
                if clip:
                    ax.scatter([p[0] for p in clip], [p[1] for p in clip], s=26,
                               marker="x", color=color, linewidths=1.0, zorder=4)
        ticks = [i * math.pi / 4.0 for i in range(8)]
        ax.set_xticks(ticks)
        ax.set_xticklabels(
            [f"{abs(int(round(math.degrees(((t + math.pi) % (2 * math.pi)) - math.pi))))}°"
             for t in ticks])
        if max_radius > 0:
            ax.set_rmax(max_radius + 0.5)
        # Le zoom/deplacement en cours SURVIT au retrace : cocher une
        # configuration ne doit pas rejeter l'utilisateur a la vue de
        # depart sans qu'il l'ait demande.
        self._apply_max_view()
        legend = ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.05),
                           ncol=max(1, min(len(tws_values), 6)),
                           facecolor=BG_PANEL, edgecolor=COLOR_TICK, fontsize=8)
        for text in legend.get_texts():
            text.set_color(FG_LABEL)
        self._max_canvas.draw_idle()

    # Facteur applique a chaque cran de molette. 1,15 : assez pour qu'un
    # cran se voie, assez peu pour viser sans depasser.
    MAX_POLAR_ZOOM_STEP = 1.15
    MAX_POLAR_ZOOM_MAX = 8.0

    def _max_view_base(self):
        """Cote du cadre du trace, en fractions de figure, a l'echelle 1.

        CARRE : une polaire garde son aspect quoi qu'il arrive, et un cadre
        rectangulaire ferait flotter le cercle a l'interieur -- le
        deplacement deviendrait imprevisible, le curseur ne suivrait plus le
        dessin."""
        w_in, h_in = self._max_fig.get_size_inches()
        side = min(w_in, h_in) * 0.92
        return side / max(w_in, 1e-6), side / max(h_in, 1e-6)

    def _apply_max_view(self):
        """Place le cadre du trace selon le zoom et le deplacement courants.

        La vue retient le point du DESSIN (en fraction du cadre) qu'on veut
        garder au centre de la figure : c'est ce qui permet de zoomer sous
        le curseur sans que l'image parte sur le cote."""
        bw, bh = self._max_view_base()
        view = getattr(self, "_max_view", None)
        if not view:
            self._max_ax.set_position([0.5 - bw / 2.0, 0.5 - bh / 2.0, bw, bh])
        else:
            s_, cx, cy = view["scale"], view["cx"], view["cy"]
            w, h = bw * s_, bh * s_
            self._max_ax.set_position([0.5 - cx * w, 0.5 - cy * h, w, h])
        if hasattr(self, "_max_zoom_lbl"):
            self._max_zoom_lbl.configure(
                text=("Molette : zoom  |  glisser : se deplacer  |  double-clic : "
                      "vue entiere"
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
        bw, bh = self._max_view_base()
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
        who = labels[ci] if ci is not None else "-- (interpolation)"
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
            for j, _tws in enumerate(tws_values):
                v = grid[i][j]
                if v is None:
                    continue
                d = math.hypot(math.radians(abs(twa) - deg) * max(r, 0.5), v - r)
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None or best[0] > max(0.6, 0.08 * max(1.0, r)):
            return None
        return best[1], best[2]

    def _max_cell_confidence(self, i, j):
        """Confiance de la case (i, j) de la polaire max : celle de la
        configuration GAGNANTE, seule a l'avoir produite."""
        if not self._max_result:
            return None
        twa_values, tws_values, _g, _c, winners, _m, _l, _cl = self._max_result
        ci = winners[i][j]
        chosen = [cfg for var, cfg, _lbl in self._max_cfg_vars if var.get()]
        if ci is None or ci >= len(chosen):
            return None
        sails, engines, derive = chosen[ci]
        try:
            _t, _w, conf = self.persistent_store.confidence_table(
                sails, engines, derive=derive,
                twa_bin_deg=float(self.config_data["twa_bin_deg"]),
                tws_bin_kn=float(self.config_data["tws_bin_kn"]),
                symmetric=bool(self.config_data["symmetric_port_starboard"]),
                session_ids=self._included_session_ids())
        except Exception:
            return None
        # Les axes d'une configuration ne couvrent pas forcement toute la
        # grille max : on retrouve la case par sa VALEUR d'angle et de vent,
        # jamais par son indice.
        try:
            ii = _t.index(twa_values[i])
            jj = _w.index(tws_values[j])
        except ValueError:
            return None
        return conf[ii][jj] if ii < len(conf) and jj < len(conf[ii]) else None

    def _max_polar_refresh_guide(self):
        twa_values, tws_values, _grid, _counts, winners, mask, labels, _clip = self._max_result
        tree = self._max_guide
        tree.delete(*tree.get_children())
        cols = ["twa"] + [f"tws{j}" for j in range(len(tws_values))]
        tree.configure(columns=cols)
        tree.heading("twa", text="TWA \\ TWS")
        tree.column("twa", width=80, anchor="center", stretch=False)
        for j, tws in enumerate(tws_values):
            tree.heading(f"tws{j}", text=f"{tws:g} nds")
            tree.column(f"tws{j}", width=64, anchor="center", stretch=True)
        for i, twa in enumerate(twa_values):
            row = [f"{twa:g}°"]
            for j in range(len(tws_values)):
                ci = winners[i][j]
                if ci is None:
                    row.append("~" if mask[i][j] else "")
                else:
                    row.append(f"C{ci + 1}")
            tree.insert("", "end", values=row)
        # UNE LIGNE PAR CONFIGURATION : mises bout a bout, les etiquettes
        # completes debordaient et le guide devenait indechiffrable -- on
        # lisait "C3" sans pouvoir savoir de quelle voilure il s'agit.
        leg = "\n".join(f"C{i + 1} = {lbl}" for i, lbl in enumerate(labels))
        if leg:
            leg += "\n(~ = case interpolee, sans gagnante mesuree)"
        self._max_legend_lbl.configure(text=leg)

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

        parent = self._max_polar_win or self
        win = tk.Toplevel(parent)
        self._max_detail_win = win
        self._max_detail_cell = (i, j)
        win.title(f"Point {twa_values[i]:g}\u00b0 / {tws_values[j]:g} nds -- detail")
        win.configure(bg=BG_APP)
        win.transient(parent)
        try:
            scr_h = win.winfo_screenheight()
        except tk.TclError:
            scr_h = 800
        win.geometry(f"660x{min(680, max(480, scr_h - 140))}")
        win.minsize(560, 420)
        win.bind("<Escape>", lambda _e: self._close_max_cell_details())
        win.protocol("WM_DELETE_WINDOW", self._close_max_cell_details)

        outer, inner = make_scrollable(win, height=1)
        outer.pack(fill="both", expand=True)

        mono = {"bg": BG_APP, "fg": FG_LABEL, "font": FONT_MONO,
                "anchor": "w", "justify": "left"}

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
                lines.append(f"Voilure gagnante : C{ci + 1} = {labels[ci]}")
        tk.Label(f_cell, text="\n".join(lines), **mono).pack(fill="x", padx=6, pady=(0, 6))
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
                         "maximale reglee dans la fenetre principale ; elle a ete "
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
            tv = ttk.Treeview(f_comp, show="headings", columns=cols,
                              height=min(6, len(entries)))
            for c, h, w in zip(cols, heads, widths):
                tv.heading(c, text=h)
                tv.column(c, width=w, anchor="center", stretch=(c == "code"))
            for e in entries:
                conf = e["confidence"]
                spread = ("-" if e["spread"] is None else f"{100 * e['spread']:.0f} %")
                tv.insert("", "end", values=(
                    f"C{e['config_index'] + 1}", e["n"],
                    f"{e['speed']:.2f}" if e["speed"] is not None else "-",
                    f"{e['min']:.2f}", f"{e['median']:.2f}", f"{e['max']:.2f}",
                    spread, f"{conf['level']} {conf['score']}"))
            tv.pack(fill="x", padx=6, pady=(0, 6))

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
            tk.Label(f_win, text="\n".join(wl), **mono).pack(fill="x", padx=6, pady=(0, 6))

            # Les passes qui ont nourri la case, nommees comme dans l'Entrepot.
            if best["sessions"]:
                srows = []
                for se in best["sessions"]:
                    rec = self._session_record(se["session_id"]) if se["session_id"] else None
                    name = (rec.get("label") if rec else None) or se["session_id"] or "(sans passe)"
                    when = ("" if se["t_first"] is None else
                            "  " + time.strftime("%d/%m/%Y %H:%M", time.localtime(se["t_first"])))
                    srows.append(f"  {se['n']:>3d} mesure(s)  {name}{when}")
                tk.Label(f_win, text="Passes ayant nourri la case :\n" + "\n".join(srows),
                         **mono).pack(fill="x", padx=6, pady=(0, 6))

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
        win.update_idletasks()
        self._center_on_parent(win)

    def _max_polar_lookup(self):
        if not self._max_result or not self._max_result[1]:
            self._max_ask_result.configure(text="Aucune table : cochez une configuration.")
            return
        twa_values, tws_values, grid, _counts, winners, mask, labels, clipped = self._max_result
        try:
            twa = abs(float(self._max_ask_twa.get().replace(",", ".")))
            tws = float(self._max_ask_tws.get().replace(",", "."))
        except ValueError:
            self._max_ask_result.configure(text="Entrez un angle et un vent numeriques.")
            return
        i = min(range(len(twa_values)), key=lambda k: abs(twa_values[k] - twa))
        j = min(range(len(tws_values)), key=lambda k: abs(tws_values[k] - tws))
        v = grid[i][j]
        if v is None:
            self._max_ask_result.configure(
                text=f"Case {twa_values[i]:g}° / {tws_values[j]:g} nds : aucune mesure "
                     f"ni interpolation. Le bateau n'a jamais ete observe la.")
            return
        ci = winners[i][j]
        who = (labels[ci] if ci is not None
               else "interpolation entre deux voilures mesurees")
        origin = "interpolee" if mask[i][j] else "mesuree"
        self._max_ask_result.configure(
            text=f"Case {twa_values[i]:g}° / {tws_values[j]:g} nds : {v:.2f} nds "
                 f"({origin}).\nVoilure : {who}")

    def _max_polar_export_pol(self):
        if not self._max_result or not self._max_result[1]:
            messagebox.showinfo("Export", "Aucune table max a exporter.")
            return
        twa_values, tws_values, grid, _c, _w, _m, _l, _cl = self._max_result
        path = filedialog.asksaveasfilename(
            defaultextension=".pol", filetypes=[("Fichier polaire", "*.pol")],
            initialfile="polaire_max_routage.pol", parent=self._max_polar_win)
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
            parent=self._max_polar_win)

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
            initialfile="Allure_Wind_Polar.xml", parent=self._max_polar_win)
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
                                 parent=self._max_polar_win)
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
            "Parcourir." + warn, parent=self._max_polar_win)

    def _max_polar_export_csv(self):
        if not self._max_result or not self._max_result[1]:
            messagebox.showinfo("Export", "Aucune table max a exporter.")
            return
        twa_values, tws_values, grid, counts, winners, mask, labels, clipped = self._max_result
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="polaire_max_routage.csv", parent=self._max_polar_win)
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
                            parent=self._max_polar_win)

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
        self.live_twa_canvas = tk.Canvas(gauge_col, width=240, height=240, bg=BG_APP,
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
        qual_row = tk.Frame(f_qual, bg=BG_APP)
        qual_row.pack(fill="x", padx=6, pady=(2, 2))
        self.live_quality_lbl = tk.Label(qual_row, text="En attente de donnees",
                                          bg=BG_APP, fg=FG_LABEL_DIM, font=("Segoe UI", 13, "bold"))
        self.live_quality_lbl.pack(side="left")
        self.live_yield_lbl = tk.Label(qual_row, text="", bg=BG_APP, fg=FG_LABEL,
                                        font=FONT_MONO)
        self.live_yield_lbl.pack(side="right")
        self.live_quality_detail_lbl = tk.Label(f_qual, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                                 font=FONT_MONO, justify="left", anchor="w")
        self.live_quality_detail_lbl.pack(fill="x", padx=6, pady=(0, 2))
        # N'apparait QUE quand le probleme se produit (voir _update_instruments) :
        # un avertissement affiche en permanence cesse d'etre lu.
        self.live_warning_lbl = tk.Label(f_qual, text="", bg=BG_APP, fg=COLOR_PORT,
                                          font=("Segoe UI", 9, "bold"), justify="left", anchor="w",
                                          wraplength=900)

        # --- 3. Configuration enregistree ---
        f_cfg = section(inner, "Configuration enregistree")
        self.live_config_lbl = tk.Label(f_cfg, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=("Segoe UI", 11, "bold"), anchor="w")
        self.live_config_lbl.pack(fill="x", padx=6, pady=(2, 0))
        self.live_timer_lbl = tk.Label(f_cfg, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                        font=FONT_MONO, anchor="w")
        self.live_timer_lbl.pack(fill="x", padx=6, pady=(0, 6))

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
        # Etat REEL de chaque voie : ouverte ? combien de trames ? depuis
        # quand ? Une voie qui s'ouvre sans rien recevoir etait jusqu'ici
        # indiscernable d'une voie qui marche.
        self.ports_state_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.ports_state_lbl.pack(fill="x", padx=6, pady=(0, 2))
        self.net_warn_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=COLOR_PORT,
                                      font=FONT_LABEL_BOLD, anchor="w", justify="left",
                                      wraplength=900)
        self.net_warn_lbl.pack(fill="x", padx=6, pady=(0, 4))
        self.live_rx_mwv_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_mwv_lbl.pack(fill="x", padx=6)
        self.live_rx_vhw_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_vhw_lbl.pack(fill="x", padx=6)
        self.live_rx_vtg_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=FG_LABEL,
                                         font=FONT_MONO, anchor="w", justify="left")
        self.live_rx_vtg_lbl.pack(fill="x", padx=6, pady=(0, 8))
        # Diagnostic d'une mesure MUETTE. Une grandeur qui n'arrive pas est
        # ce qui coute le plus cher : sans explication, on soupconne le
        # reseau ou l'instrument alors que la cause tient le plus souvent a
        # un simple reglage de source. Ce bandeau nomme la voie fautive.
        self.live_source_warn_lbl = tk.Label(f_rx, text="", bg=BG_APP, fg=COLOR_PORT,
                                              font=FONT_LABEL_BOLD, anchor="w",
                                              justify="left", wraplength=900)
        self.live_source_warn_lbl.pack(fill="x", padx=6, pady=(0, 8))

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
        self.live_anemo_warn_lbl = tk.Label(self.f_anemo, text="", bg=BG_APP, fg=COLOR_PORT,
                                             font=("Segoe UI", 9, "bold"), anchor="w",
                                             justify="left", wraplength=900)
        self.live_anemo_warn_lbl.pack(fill="x", padx=6, pady=(2, 8))

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
        feed_vsb = tk.Scrollbar(feed_row, orient="vertical", command=self.feed_text.yview)
        self.feed_text.configure(yscrollcommand=feed_vsb.set)
        self.feed_text.pack(side="left", fill="both", expand=True)
        feed_vsb.pack(side="right", fill="y")

        # Aucun bouton en bas : la seule commande de cet ecran est celle de
        # la barre du haut, toujours visible (voir plus haut).
        self._live_twa_geom = _draw_twa_gauge_background(self.live_twa_canvas)

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
        _draw_wind_arrow(self.live_twa_canvas, cx, cy, r,
                         twa if ok_tw else None, (tws / vmax) if ok_tw else 0.0,
                         FG_DIGIT, tag="wind_true", width=6)
        _draw_wind_arrow(self.live_twa_canvas, cx, cy, r,
                         awa if ok_aw else None, (aws / vmax) if ok_aw else 0.0,
                         COLOR_ACCENT, tag="wind_app", width=4)
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
        sails, engines, derive = self._selected_sails(), self._selected_engines(), self._selected_derive()
        cfg_txt = (f"Voiles : {'+'.join(sails) or '-'}      Moteurs : {'+'.join(engines) or '-'}"
                   f"      Derive : {pe.DERIVE_LABELS.get(derive, '-')}")
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
            lines.append(f"{mark} {self.port_label(p):<22s} vent apparent {v['awa']:+6.1f} deg"
                         f" / {v['aws']:5.2f} kn   ->   {calc}")
        if station is not None:
            extra = []
            if station.get("gust") is not None:
                extra.append(f"rafale {station['gust']:5.2f} kn")
            if station.get("dir") is not None:
                extra.append(f"direction {station['dir']:.0f} deg / nord")
            lines.append(f"  {'Station meteo du bord':<22s} vent VRAI mesure "
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
    # Statistiques (icone en haut a droite)
    #
    # Cette page ne produit AUCUNE polaire et n'enregistre rien : elle lit.
    # Elle repond a des questions que le reste de l'application ne pose
    # jamais -- le vent forcit-il, la pression baisse-t-elle, ai-je fait plus
    # de route cette heure-ci que la precedente -- et elle y repond meme
    # quand aucune acquisition ne tourne, puisque le tampon glissant, lui,
    # tourne toujours (voir allure_buffer.py et allure_engine.TrendRecorder).
    #
    # La page elle-meme est une page de LECTURE, pas de reglage : ce qu'elle
    # montre (grandeurs, durees, tableau) se choisit dans la roue dentee >
    # Statistiques, comme tous les autres reglages de l'application. Le seul
    # bouton qui reste sur la page est celui dont on se sert en regardant le
    # resultat : l'echelle de temps des graphes.
    #
    # Trois echelles de lecture, volontairement distinctes :
    #   - les TENDANCES comparent la meme grandeur sur quatre fenetres :
    #     c'est la comparaison, et non la valeur, qui dit si quelque chose
    #     est en train de changer ;
    #   - les GRAPHES donnent la pente -- vent et barometre SEPARES, chacun
    #     sur sa propre echelle, mais sur le MEME axe de temps ;
    #   - le TABLEAU PAR TRANCHES donne la forme du temps sur les dernieres
    #     heures, d'un seul coup d'oeil colore.
    # =====================================================================

    # Cadences de rafraichissement, en secondes. Les tableaux suivent le
    # flux, la figure matplotlib beaucoup moins souvent : la redessiner deux
    # fois par seconde couterait plus cher que tout le reste de la page
    # reuni, pour un trace qui ne bouge pas a l'oeil.
    STATS_TABLE_PERIOD_S = 2.0
    STATS_HOURS_PERIOD_S = 10.0
    STATS_CURVE_PERIOD_S = 15.0

    # Largeur minimale d'une colonne du tableau par tranches, en pixels :
    # sert a decider COMBIEN de colonnes tiennent reellement dans la fenetre
    # (voir _stats_grid_cols). Un tableau qui deborde est un tableau qu'on ne
    # lit pas -- autant afficher moins de colonnes et qu'elles soient lisibles.
    STATS_GRID_MIN_COL_PX = 58
    STATS_GRID_LABEL_PX = 150

    # Etendues proposees par les boutons des graphes, en heures. Des boutons
    # et non un menu deroulant : changer d'echelle de temps est LE geste de
    # cette page, il doit se faire d'un clic, l'oeil sur la courbe.
    STATS_SPAN_PRESETS_H = (1, 3, 6, 12, 24)
    # Bornes du zoom molette (voir _on_stats_scroll).
    STATS_MIN_SPAN_S = 600.0

    def _build_step_statistiques(self, parent):
        outer, inner = make_scrollable(parent, height=1)
        outer.pack(fill="both", expand=True)
        self._stats_inner = inner
        self._stats_last_table = 0.0
        self._stats_last_hours = 0.0
        self._stats_last_curve = 0.0
        # Fenetre de temps VISIBLE des graphes. end=None signifie "suit le
        # present" (le bord droit est toujours maintenant) ; un panoramique
        # vers le passe fige end, et les boutons d'etendue ramenent au direct.
        self._stats_view = {
            "span_s": float(self.config_data.get("stats_curve_h", 6)) * 3600.0,
            "end": None,
        }
        self._stats_drag = None

        self._build_stats_bar(inner)
        self._build_stats_trends(inner)
        self._build_stats_curves(inner)
        self._build_stats_hours(inner)
        self._build_stats_grid(inner)
        self._sync_stats_sections()

    def _stats_fields(self):
        """Grandeurs a afficher : le choix fait dans les Parametres, croise
        avec le catalogue reellement connu de cette version, dans l'ordre du
        catalogue (stable a l'ecran, quel que soit l'ordre du fichier)."""
        chosen = set(self.config_data.get("stats_fields") or [])
        fields = [k for k in pe.TREND_KEYS if k in chosen]
        return fields or [k for k in pe.TREND_KEYS
                          if k in ("tws", "aws", "twa", "awa", "stw", "sog", "pressure")]

    # ---------- Barre d'etat (fine, pas une section) ----------
    def _build_stats_bar(self, inner):
        bar = tk.Frame(inner, bg=BG_PANEL)
        bar.pack(fill="x", padx=10, pady=(10, 0))
        row = tk.Frame(bar, bg=BG_PANEL)
        row.pack(fill="x", padx=10, pady=6)
        self.stats_state_lbl = tk.Label(row, text="", bg=BG_PANEL, fg=FG_LABEL,
                                         font=("Segoe UI", 8), anchor="w", justify="left")
        self.stats_state_lbl.pack(side="left", fill="x", expand=True)
        # Raccourci vers les reglages de CETTE page : la roue dentee generale
        # y mene aussi, mais le chemin doit etre evident depuis ici.
        tk.Button(row, text="Reglages...", command=self._open_stats_settings,
                  bg=BTN_BG, fg=FG_LABEL, font=FONT_LABEL, padx=10,
                  cursor="hand2").pack(side="right", padx=(8, 0))
        self.stats_backfill_btn = tk.Button(
            row, text="Relire le tampon", command=lambda: self._start_trend_backfill(auto=False),
            bg=BTN_BG, fg=FG_LABEL, font=FONT_LABEL, padx=10, cursor="hand2")
        self.stats_backfill_btn.pack(side="right")
        self.stats_backfill_lbl = tk.Label(bar, text="", bg=BG_PANEL, fg=FG_LABEL_DIM,
                                            font=("Segoe UI", 8), anchor="w", justify="left")
        self.stats_backfill_lbl.pack(fill="x", padx=10, pady=(0, 6))

    def _open_stats_settings(self):
        self._show_step("parametres")
        self._show_settings_category("statistiques")

    # ---------- 1. Tendances ----------
    STATS_TREND_COLS = 7  # grandeur + instantane + 4 fenetres + tendance

    def _build_stats_trends(self, inner):
        f = section(inner, "Tendances")
        note(f, "La meme grandeur sur quatre durees : c'est l'ecart entre les colonnes qui "
                "dit si quelque chose change. Une case vide = aucune mesure sur cette duree.",
             wraplength="auto")

        grid = tk.Frame(f, bg=BG_APP)
        grid.pack(fill="x", padx=6, pady=(0, 8))
        self.stats_trend_grid = grid
        for c in range(1, self.STATS_TREND_COLS):
            grid.columnconfigure(c, weight=1, uniform="stats")
        grid.columnconfigure(0, weight=0)

        self.stats_trend_headers = []
        for c in range(self.STATS_TREND_COLS):
            lbl = tk.Label(grid, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                            font=("Segoe UI", 8, "bold"),
                            anchor="w" if c == 0 else "center")
            lbl.grid(row=0, column=c, sticky="ew", padx=1, pady=(0, 2))
            self.stats_trend_headers.append(lbl)
        tk.Frame(grid, bg=COLOR_BORDER, height=1).grid(
            row=1, column=0, columnspan=self.STATS_TREND_COLS, sticky="ew", pady=(0, 2))

        # Une ligne par grandeur du catalogue, construite une fois pour
        # toutes puis simplement masquee (grandeur non choisie, ou jamais
        # recue) : reconstruire le tableau a chaque rafraichissement le
        # ferait clignoter deux fois par seconde.
        self.stats_trend_rows = {}
        for i, key in enumerate(pe.TREND_KEYS):
            label, unit, dec, kind = pe.TREND_META[key]
            name = tk.Label(grid, text=f"{label} ({unit})", bg=BG_APP, fg=FG_LABEL,
                             font=FONT_LABEL, anchor="w")
            cells = [StatCell(grid, arrow=(kind != "linear"),
                              toward=(key in _TREND_ANGLE_TOWARD)) for _ in range(5)]
            trend_lbl = tk.Label(grid, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                  font=("Segoe UI", 8), anchor="center")
            self.stats_trend_rows[key] = (i + 2, name, cells, trend_lbl)

        self.stats_trend_empty = tk.Label(
            f, text="", bg=BG_APP, fg=COLOR_ACCENT, font=FONT_LABEL, anchor="w",
            justify="left", wraplength=760)
        self.stats_trend_empty.pack(fill="x", padx=6, pady=(0, 8))

    # ---------- 2. Graphes vent / pression ----------
    def _build_stats_curves(self, inner):
        f = section(inner, "Vent et pression")
        self.stats_curve_section = f
        note(f, "Deux graphes SEPARES -- chacun sa propre echelle -- mais un seul axe de "
                "temps, commun : zoomer ou se deplacer sur l'un deplace l'autre a "
                "l'identique. Molette : zoom. Cliquer-glisser : remonter dans le passe. "
                "Un bouton d'etendue ramene au direct.", wraplength="auto")

        btn_row = tk.Frame(f, bg=BG_APP)
        btn_row.pack(fill="x", padx=6, pady=(0, 4))
        tk.Label(btn_row, text="Etendue :", bg=BG_APP, fg=FG_LABEL,
                 font=FONT_LABEL_BOLD).pack(side="left", padx=(0, 6))
        self.stats_span_btns = {}
        for h in self.STATS_SPAN_PRESETS_H:
            b = tk.Button(btn_row, text=_fmt_span(h * 3600), padx=10, pady=1,
                          font=FONT_LABEL, relief="flat", bd=1, cursor="hand2",
                          bg=BTN_BG, fg=FG_LABEL,
                          command=lambda hh=h: self._stats_set_span(hh))
            b.pack(side="left", padx=(0, 3))
            self.stats_span_btns[h] = b
        self.stats_curve_note_lbl = tk.Label(btn_row, text="", bg=BG_APP, fg=COLOR_ACCENT,
                                              font=("Segoe UI", 8), anchor="e")
        self.stats_curve_note_lbl.pack(side="right", fill="x", expand=True)

        self.stats_fig = Figure(figsize=(9.0, 4.6), dpi=100)
        self.stats_fig.set_facecolor(BG_APP)
        gs = self.stats_fig.add_gridspec(2, 1, height_ratios=(3.0, 2.0), hspace=0.14)
        self.stats_ax_wind = self.stats_fig.add_subplot(gs[0])
        self.stats_ax_press = self.stats_fig.add_subplot(gs[1], sharex=self.stats_ax_wind)
        # Marges FIXES plutot que tight_layout : celui-ci ne s'entend pas
        # avec un gridspec partage, et des marges qui bougent a chaque
        # redessin feraient trembler les graphes pendant un zoom.
        self.stats_fig.subplots_adjust(left=0.075, right=0.985, top=0.975,
                                        bottom=0.085, hspace=0.14)
        self.stats_canvas = FigureCanvasTkAgg(self.stats_fig, master=f)
        widget = self.stats_canvas.get_tk_widget()
        widget.configure(bg=BG_APP, height=430)
        widget.pack(fill="x", padx=6, pady=(0, 8))
        self.stats_canvas.mpl_connect("scroll_event", self._on_stats_scroll)
        self.stats_canvas.mpl_connect("button_press_event", self._on_stats_press)
        self.stats_canvas.mpl_connect("motion_notify_event", self._on_stats_motion)
        self.stats_canvas.mpl_connect("button_release_event", self._on_stats_release)
        self._sync_stats_span_buttons()

    # ----- Fenetre de temps commune aux deux graphes -----
    def _stats_set_span(self, hours):
        """Bouton d'etendue : fixe l'echelle ET revient au direct (bord droit
        = maintenant). C'est aussi la sortie de secours d'un zoom ou d'un
        panoramique qui a emmene trop loin."""
        self._stats_view = {"span_s": float(hours) * 3600.0, "end": None}
        cfg = dict(self.config_data)
        cfg["stats_curve_h"] = int(hours)
        self.config_data = pcfg.fixup(cfg)
        try:
            pcfg.save_config(self.config_data)
        except OSError:
            pass
        self._sync_stats_span_buttons()
        self._stats_last_curve = time.time()
        self._refresh_stats_curves(time.time())

    def _sync_stats_span_buttons(self):
        """Le bouton de l'etendue ACTIVE s'allume comme un onglet -- mais
        seulement en mode direct : une vue zoomee ou figee n'allume rien,
        puisqu'aucun preset ne la decrit."""
        view = getattr(self, "_stats_view", None)
        if view is None or not hasattr(self, "stats_span_btns"):
            return
        for h, btn in self.stats_span_btns.items():
            active = (view["end"] is None
                      and abs(view["span_s"] - h * 3600.0) < 1.0)
            btn.configure(bg=BG_NAV_ACTIVE if active else BTN_BG,
                          fg=FG_ON_ACCENT if active else FG_LABEL)

    def _stats_clamp_view(self, span_s, end, now):
        horizon = max(3600.0, self.config_data.get("stats_history_h", 6) * 3600.0)
        span_s = max(self.STATS_MIN_SPAN_S, min(span_s, horizon))
        if end is not None:
            oldest = now - horizon
            end = min(end, now)
            end = max(end, oldest + span_s)
            # Bord droit revenu au present : on repasse en mode direct, la
            # vue recommence a suivre le flux toute seule.
            if end >= now - 5.0:
                end = None
        return span_s, end

    def _stats_axes_of_event(self, event):
        return event.inaxes if event.inaxes in (
            getattr(self, "stats_ax_wind", None), getattr(self, "stats_ax_press", None)) else None

    def _on_stats_scroll(self, event):
        """Molette = zoom de l'axe de temps, autour du point vise, applique
        aux DEUX graphes a la fois (ils partagent la meme fenetre)."""
        if self._stats_axes_of_event(event) is None or event.xdata is None:
            return
        now = time.time()
        view = self._stats_view
        factor = 0.75 if event.step > 0 else 1.0 / 0.75
        old_span = view["span_s"]
        end = view["end"] if view["end"] is not None else now
        # Garder le point vise au meme endroit de l'ecran : la distance
        # entre ce point et le bord droit est simplement mise a l'echelle.
        anchor = event.xdata
        new_span = old_span * factor
        new_end = anchor + (end - anchor) * (new_span / old_span)
        new_span, new_end = self._stats_clamp_view(new_span, new_end, now)
        self._stats_view = {"span_s": new_span, "end": new_end}
        self._sync_stats_span_buttons()
        self._stats_last_curve = now
        self._refresh_stats_curves(now)

    def _on_stats_press(self, event):
        if event.button != 1 or self._stats_axes_of_event(event) is None:
            return
        view = self._stats_view
        end = view["end"] if view["end"] is not None else time.time()
        self._stats_drag = {"x_px": event.x, "end0": end, "span": view["span_s"],
                            "moved": False}

    def _on_stats_motion(self, event):
        drag = self._stats_drag
        if drag is None or event.x is None:
            return
        ax = getattr(self, "stats_ax_wind", None)
        if ax is None:
            return
        bbox = ax.get_window_extent()
        width_px = max(1.0, bbox.width)
        dt = (drag["x_px"] - event.x) * drag["span"] / width_px
        if abs(event.x - drag["x_px"]) > 3:
            drag["moved"] = True
        now = time.time()
        span, end = self._stats_clamp_view(drag["span"], drag["end0"] + dt, now)
        self._stats_view = {"span_s": span, "end": end}
        self._sync_stats_span_buttons()
        # Redessin immediat mais au plus 10 fois par seconde : le pointeur
        # bouge bien plus vite que matplotlib ne redessine.
        if now - self._stats_last_curve >= 0.1:
            self._stats_last_curve = now
            self._refresh_stats_curves(now)

    def _on_stats_release(self, _event):
        if self._stats_drag is not None:
            self._stats_drag = None
            now = time.time()
            self._stats_last_curve = now
            self._refresh_stats_curves(now)

    # ---------- 3. Distance par heure pleine ----------
    def _build_stats_hours(self, inner):
        f = section(inner, "Distance sur le fond, heure par heure")
        note(f, "Vitesse fond integree, decoupee aux heures pleines du bord. Une heure au "
                "flux interrompu affiche sa couverture : sa distance ne vaut pas une heure "
                "entiere.", wraplength="auto")
        self.stats_hours_frame = tk.Frame(f, bg=BG_APP)
        self.stats_hours_frame.pack(fill="x", padx=6, pady=(0, 4))
        self.stats_hours_total_lbl = tk.Label(f, text="", bg=BG_APP, fg=FG_LABEL,
                                               font=FONT_LABEL_BOLD, anchor="w")
        self.stats_hours_total_lbl.pack(fill="x", padx=6, pady=(0, 8))
        self._stats_hour_cards = []

    # ---------- 4. Tableau par tranches ----------
    def _build_stats_grid(self, inner):
        f = section(inner, "Tableau des dernieres heures")
        self.stats_grid_section = f
        note(f, "Une table de prevision tournee vers le passe : ce qui a reellement ete "
                "mesure, tranche par tranche, colore par la force du vent.",
             wraplength="auto")
        self.stats_grid_note_lbl = tk.Label(f, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                                             font=("Segoe UI", 8), anchor="w")
        self.stats_grid_note_lbl.pack(fill="x", padx=6)
        self.stats_grid_frame = tk.Frame(f, bg=BG_APP)
        self.stats_grid_frame.pack(fill="x", padx=6, pady=(2, 8))
        self._stats_grid_shape = None      # (colonnes, lignes) reellement construits
        self._stats_grid_cells = {}
        self._stats_grid_headers = []

    def _sync_stats_sections(self):
        """Montre ou cache les blocs selon les Parametres. Le tableau par
        tranches est le seul optionnel ; il est en fin de page, un pack()
        simple le remet donc exactement a sa place."""
        if not hasattr(self, "stats_grid_section"):
            return
        if self.config_data.get("stats_show_grid", True):
            if not self.stats_grid_section.winfo_manager():
                self.stats_grid_section.pack(fill="x", padx=10, pady=(10, 4))
        else:
            self.stats_grid_section.pack_forget()

    # ---------- Reglages (lus depuis config_data ; edites dans la roue
    # dentee > Statistiques, voir _build_settings_statistiques) ----------
    def _stats_windows(self):
        """Les quatre fenetres du tableau des tendances. TOUJOURS quatre :
        le tableau a quatre colonnes, construites une fois pour toutes, et
        un fichier de reglages venu d'ailleurs ne doit pas pouvoir laisser
        une colonne orpheline."""
        wins = [w for w in (self.config_data.get("stats_windows") or []) if w]
        for cand in pe.TREND_WINDOWS_DEFAULT:
            if len(wins) >= 4:
                break
            if cand not in wins:
                wins.append(cand)
        return sorted(wins)[:4]

    # ---------- Remplissage depuis le tampon ----------
    def _start_trend_backfill(self, auto=False):
        """Rejoue le tampon glissant dans un enregistreur NEUF, en tache de
        fond, puis le verse dans l'enregistreur vivant (voir
        TrendRecorder.absorb). Sans cela, une "moyenne sur 60 minutes"
        consultee cinq minutes apres le lancement ne porterait que sur cinq
        minutes -- alors que le tampon, lui, sait tout."""
        with self._trend_backfill_lock:
            if self._trend_backfill_state == "en cours":
                return False
            if auto and self._trend_backfill_state != "jamais":
                return False  # une seule relecture automatique par lancement
            self._trend_backfill_state = "en cours"
            self._trend_backfill_msg = "Relecture du tampon en cours..."
        if not self.config_data.get("buffer_enabled", True):
            with self._trend_backfill_lock:
                self._trend_backfill_state = "echec"
                self._trend_backfill_msg = (
                    "Tampon desactive : les statistiques ne portent que sur ce qui a ete recu "
                    "depuis le lancement. Activez-le dans la roue dentee > Sources & tampon.")
            self._refresh_stats(force=True)
            return False
        span_h = max(int(self.config_data.get("stats_history_h", 6)),
                     int(self.config_data.get("stats_hours_shown", 12)) + 1)
        th = threading.Thread(target=self._trend_backfill_worker, args=(span_h,), daemon=True)
        self._trend_backfill_thread = th
        th.start()
        self._refresh_stats(force=True)
        return True

    def _trend_backfill_worker(self, span_h):
        """Fil de fond. Ne touche a AUCUN widget : il depose son resultat et
        laisse la boucle Tk le prendre (voir _finish_trend_backfill)."""
        try:
            now = time.time()
            cfg = self.config_data
            rec = pe.TrendRecorder(horizon_s=cfg.get("stats_history_h", 6) * 3600.0)
            engine = pe.PolarEngine(port_priority=cfg.get("priority"),
                                     source_by_type=cfg.get("sources"),
                                     allow_fallback=cfg.get("source_fallback", True))
            engine.collecting = False
            engine.trend = rec
            n = 0
            last = None
            for t, port, raw in self.buffer.iter_range(now - span_h * 3600.0, now):
                engine.ingest_line(t, raw, port=port)
                n += 1
                last = t
                if n % 5000 == 0:
                    # Ce fil est presque uniquement du calcul Python : sans
                    # cette respiration, il accapare l'interpreteur (GIL) et
                    # l'interface rame pendant toute la relecture d'un gros
                    # tampon. Un souffle par 5000 trames suffit -- invisible
                    # sur la duree totale, decisif pour la fluidite.
                    time.sleep(0.001)
            self._trend_backfill_result = (rec, n, last, None)
        except Exception as exc:   # le fil ne doit jamais mourir en silence
            log_exception(type(exc), exc, exc.__traceback__, "relecture du tampon")
            self._trend_backfill_result = (None, 0, None, str(exc))
        try:
            self.after(0, self._finish_trend_backfill)
        except RuntimeError:
            pass  # application en train de se fermer

    def _finish_trend_backfill(self):
        res = self._trend_backfill_result
        self._trend_backfill_result = None
        if res is None:
            return
        rec, n, last, err = res
        with self._trend_backfill_lock:
            if err is not None:
                self._trend_backfill_state = "echec"
                self._trend_backfill_msg = f"Relecture du tampon impossible : {err}"
            elif n == 0:
                self._trend_backfill_state = "fait"
                self._trend_backfill_msg = (
                    "Tampon vide : les statistiques ne portent que sur ce qui arrive "
                    "maintenant.")
            else:
                self.trend.absorb(rec, replace_upto=last)
                self._trend_backfill_state = "fait"
                self._trend_backfill_msg = (
                    f"Tampon relu : {n} trame(s), jusqu'a "
                    f"{time.strftime('%H:%M:%S', time.localtime(last))}.")
        self._refresh_stats(force=True)

    # ---------- Rafraichissement ----------
    def _refresh_stats(self, force=False):
        """Appele a chaque tick quand la page est visible, et une fois a
        l'entree. Chaque bloc a sa propre cadence -- voir STATS_*_PERIOD_S."""
        if not hasattr(self, "stats_state_lbl"):
            return
        now = time.time()
        if force or now - self._stats_last_table >= self.STATS_TABLE_PERIOD_S:
            self._stats_last_table = now
            self._refresh_stats_state(now)
            self._refresh_stats_trends(now)
            self._refresh_stats_grid(now)
        if force or now - self._stats_last_hours >= self.STATS_HOURS_PERIOD_S:
            self._stats_last_hours = now
            self._refresh_stats_hours(now)
        if force or now - self._stats_last_curve >= self.STATS_CURVE_PERIOD_S:
            self._stats_last_curve = now
            self._refresh_stats_curves(now)

    def _refresh_stats_state(self, now):
        """Barre d'etat : UNE ligne. Le detail (bornes du tampon, relecture)
        passe sur la seconde ligne, discrete -- l'essentiel de la page, ce
        sont les mesures, pas leur inventaire."""
        covered = self.trend.covered_s(now)
        parts = [f"Historique : {_fmt_span(covered)}"]
        last = self.trend.last_t
        if last is None:
            parts.append("aucune mesure recue")
        else:
            age = now - last
            if age < 30:
                parts.append("flux en direct")
            else:
                parts.append(f"flux muet depuis {_fmt_span(age)}")
        parts.append("Lecture seule : rien ici n'entre dans une polaire")
        self.stats_state_lbl.configure(text="   \u2022   ".join(parts))
        with self._trend_backfill_lock:
            state, msg = self._trend_backfill_state, self._trend_backfill_msg
        self.stats_backfill_btn.configure(
            state=("disabled" if state == "en cours" else "normal"))
        self.stats_backfill_lbl.configure(
            text=msg, fg=(COLOR_PORT if state == "echec" else FG_LABEL_DIM))

    def _refresh_stats_trends(self, now):
        windows = self._stats_windows()
        heads = ["Grandeur", "Instantane"] + [_fmt_span(w) for w in windows] + ["Tendance / h"]
        for lbl, text in zip(self.stats_trend_headers, heads):
            lbl.configure(text=text)
        table = self.trend.table(now, windows)
        fields = self._stats_fields()
        shown = 0
        hidden_with_data = 0
        for key in pe.TREND_KEYS:
            row_i, name, cells, trend_lbl = self.stats_trend_rows[key]
            label, unit, dec, kind = pe.TREND_META[key]
            row = table[key]
            has = row["instant"] is not None or any(row.get(w) for w in windows)
            if has and key not in fields:
                # Recue mais non choisie : comptee pour le petit rappel en
                # pied de tableau, jamais affichee -- c'est le reglage qui
                # commande, pas le flux.
                hidden_with_data += 1
            if not has or key not in fields:
                name.grid_remove()
                for c in cells:
                    c.grid_remove()
                trend_lbl.grid_remove()
                continue
            shown += 1
            name.grid(row=row_i, column=0, sticky="w", padx=(0, 8), pady=1)
            for c, cell in enumerate(cells):
                cell.grid(row=row_i, column=c + 1, sticky="ew", padx=1, pady=1)
            trend_lbl.grid(row=row_i, column=6, sticky="ew", padx=1, pady=1)

            # Colonne "Instantane" : la derniere valeur connue, avec son age
            # quand elle n'est plus fraiche -- une valeur perimee affichee
            # comme si elle etait actuelle serait un piege.
            inst = row["instant"]
            if inst is None:
                cells[0].set_empty()
            else:
                age = now - (row["instant_t"] or now)
                cells[0].set_value(_fmt_measure(inst, kind, dec),
                                   "" if age < 15 else f"il y a {_fmt_span(age)}",
                                   angle=(inst if kind != "linear" else None),
                                   stale=age >= 15)
            for i, w in enumerate(windows):
                st = row.get(w)
                cell = cells[i + 1]
                if not st:
                    cell.set_empty()
                    continue
                if kind == "linear":
                    sub = f"{st['min']:.{dec}f} - {st['max']:.{dec}f}"
                else:
                    sub = f"+/-{st['spread_deg']:.0f} deg"
                cell.set_value(_fmt_measure(st["mean"], kind, dec), sub,
                               angle=(st["mean"] if kind != "linear" else None))
            # Tendance : pente de la regression sur la PLUS LONGUE fenetre --
            # c'est la seule qui contienne assez de recul pour qu'une pente
            # veuille dire quelque chose.
            longest = table[key].get(windows[-1])
            slope = longest.get("slope_per_h") if longest else None
            if slope is None:
                trend_lbl.configure(text="—", fg=FG_LABEL_DIM)
            else:
                arrow, color = _trend_arrow(key, slope, unit)
                trend_lbl.configure(text=f"{arrow} {slope:+.{dec}f} {unit}/h", fg=color)
        if shown:
            self.stats_trend_empty.configure(
                fg=FG_LABEL_DIM,
                text=("" if not hidden_with_data else
                      f"+ {hidden_with_data} grandeur(s) recue(s) mais non affichee(s) "
                      "-- a choisir dans Reglages."))
        elif hidden_with_data:
            self.stats_trend_empty.configure(
                fg=COLOR_ACCENT,
                text="Des mesures arrivent, mais aucune des grandeurs choisies n'en fait "
                     "partie : ouvrez Reglages... pour ajuster la selection.")
        else:
            self.stats_trend_empty.configure(
                fg=COLOR_ACCENT,
                text="Aucune mesure pour l'instant. Verifiez que les voies UDP recoivent "
                     "quelque chose (icone chronometre), puis relisez le tampon ci-dessus.")

    def _refresh_stats_hours(self, now):
        count = int(self.config_data.get("stats_hours_shown", 12))
        rows = self.trend.hourly_distances(now, count)
        # Les heures vides EN TETE (avant la premiere mesure connue) sont
        # elaguees : une rangee de douze cartes "sans flux" avant trois
        # heures mesurees n'apprend rien et noie ce qui compte. Les trous
        # AU MILIEU, eux, restent visibles -- un trou entre deux heures
        # mesurees est une information.
        while len(rows) > 6 and rows[0][1] is None:
            rows.pop(0)
        for w in self._stats_hour_cards:
            w.destroy()
        self._stats_hour_cards = []
        total = 0.0
        counted = 0
        for h0, nm, cov in rows:
            card = tk.Frame(self.stats_hours_frame, bg=BG_PANEL, relief="solid", bd=1,
                             padx=6, pady=3)
            card.pack(side="left", padx=(0, 4), pady=2)
            tk.Label(card, text=time.strftime("%Hh", time.localtime(h0)), bg=BG_PANEL,
                     fg=FG_LABEL_DIM, font=("Segoe UI", 8)).pack()
            if nm is None:
                tk.Label(card, text="--", bg=BG_PANEL, fg=FG_LABEL_DIM,
                         font=("Consolas", 11, "bold")).pack()
                tk.Label(card, text="sans flux", bg=BG_PANEL, fg=FG_LABEL_DIM,
                         font=("Segoe UI", 7)).pack()
            else:
                total += nm
                counted += 1
                partial = cov < 0.9
                tk.Label(card, text=f"{nm:.1f}", bg=BG_PANEL,
                         fg=(COLOR_ACCENT if partial else FG_DIGIT),
                         font=("Consolas", 11, "bold")).pack()
                tk.Label(card, text=("M" if not partial else f"M / {cov * 100:.0f} %"),
                         bg=BG_PANEL, fg=FG_LABEL_DIM, font=("Segoe UI", 7)).pack()
            self._stats_hour_cards.append(card)
        if counted:
            self.stats_hours_total_lbl.configure(
                text=f"Total sur les {counted} heure(s) mesuree(s) : {total:.1f} milles "
                     f"(soit {total / counted:.1f} M/h en moyenne).")
        else:
            self.stats_hours_total_lbl.configure(
                text="Aucune vitesse fond recue : la distance ne peut pas etre calculee.")

    def _stats_grid_cols(self):
        """Nombre de colonnes REELLEMENT affichables : le reglage, borne par
        ce qui tient dans la largeur disponible. Un tableau plus large que la
        fenetre serait tronque sans le dire."""
        want = int(self.config_data.get("stats_grid_cols", 12))
        width = self._stats_inner.winfo_width()
        if width <= 1:
            return want
        room = (width - self.STATS_GRID_LABEL_PX - 40) // self.STATS_GRID_MIN_COL_PX
        return max(4, min(want, int(room) if room > 0 else 4))

    def _stats_grid_rows(self):
        """Lignes du tableau par tranches, deduites des grandeurs choisies :
        suivre le vent reel amene ses rafales et sa direction (c'est la meme
        histoire), la vitesse fond et la pression ne viennent que si on les
        a demandees. Selection vide de tout cela : on retombe sur le tableau
        complet plutot que d'afficher un cadre vide."""
        fields = self._stats_fields()
        rows = []
        if "tws" in fields:
            rows += ["tws", "wind_gust_kn", "wind_dir_deg"]
        elif "aws" in fields:
            rows.append("aws")
        if "sog" in fields:
            rows.append("sog")
        if "pressure" in fields:
            rows.append("pressure")
        return rows or ["tws", "wind_gust_kn", "wind_dir_deg", "sog", "pressure"]

    def _refresh_stats_grid(self, now):
        if not self.config_data.get("stats_show_grid", True):
            return
        cols = self._stats_grid_cols()
        rows = self._stats_grid_rows()
        step_s = self.config_data.get("stats_grid_step_min", 30) * 60.0
        if self._stats_grid_shape != (cols, tuple(rows)):
            self._rebuild_stats_grid(cols, rows)
        want = int(self.config_data.get("stats_grid_cols", 12))
        self.stats_grid_note_lbl.configure(
            text="" if cols >= want else
            f"{cols} colonnes affichees : la fenetre est trop etroite pour {want}.")

        data = {k: self.trend.buckets(k, now, step_s, cols) for k in rows}
        ref = data[rows[0]]
        for c in range(cols):
            t0 = ref[c]["t0"] if c < len(ref) else now
            lt = time.localtime(t0)
            # L'heure ronde porte le jour : sur 24 colonnes de 60 minutes on
            # traverse minuit, et douze colonnes "00:00 01:00..." sans date
            # ne diraient pas de quel jour il s'agit.
            txt = time.strftime("%H:%M", lt)
            if lt.tm_hour == 0 and lt.tm_min < step_s / 60.0:
                txt = time.strftime("%d/%m", lt)
            self._stats_grid_headers[c].configure(text=txt)
        for key in rows:
            _label, unit, dec, kind = pe.TREND_META[key]
            cells = data[key]
            for c in range(cols):
                widget = self._stats_grid_cells[(key, c)]
                cell = cells[c] if c < len(cells) else None
                if cell is None or cell["mean"] is None:
                    widget.set_empty()
                    continue
                bg, fg = (_wind_cell_colors(cell["mean"])
                          if key in ("tws", "wind_gust_kn", "aws") else (BG_PANEL, FG_LABEL))
                widget.set_value(_fmt_measure(cell["mean"], kind, dec), "",
                                 angle=(cell["mean"] if kind != "linear" else None),
                                 bg=bg, fg=fg)

    def _rebuild_stats_grid(self, cols, rows):
        for child in self.stats_grid_frame.winfo_children():
            child.destroy()
        self._stats_grid_cells = {}
        self._stats_grid_headers = []
        grid = self.stats_grid_frame
        for c in range(cols + 1):
            grid.columnconfigure(c, weight=0 if c == 0 else 1,
                                 uniform="" if c == 0 else "statsgrid")
        tk.Label(grid, text="", bg=BG_APP).grid(row=0, column=0)
        for c in range(cols):
            lbl = tk.Label(grid, text="", bg=BG_APP, fg=FG_LABEL_DIM,
                            font=("Segoe UI", 7, "bold"))
            lbl.grid(row=0, column=c + 1, sticky="ew", padx=1)
            self._stats_grid_headers.append(lbl)
        for r, key in enumerate(rows):
            label, unit, dec, kind = pe.TREND_META[key]
            tk.Label(grid, text=f"{label} ({unit})", bg=BG_APP, fg=FG_LABEL,
                     font=FONT_LABEL, anchor="w").grid(row=r + 1, column=0, sticky="w",
                                                        padx=(0, 8))
            for c in range(cols):
                cell = StatCell(grid, arrow=(kind != "linear"), compact=True,
                                 toward=(key in _TREND_ANGLE_TOWARD))
                cell.grid(row=r + 1, column=c + 1, sticky="ew", padx=1, pady=1)
                self._stats_grid_cells[(key, c)] = cell
        self._stats_grid_shape = (cols, tuple(rows))

    @staticmethod
    def _stats_time_ticks(start, end):
        """Graduations de temps regulieres et RONDES (des multiples entiers
        de 5, 10, 30 minutes... selon l'etendue), entre 4 et 9 sur l'axe.
        Matplotlib saurait en poser, mais pas alignees sur l'horloge du
        bord -- or "15:00, 15:30, 16:00" se lit, "15:07, 15:37" se dechiffre."""
        span = max(1.0, end - start)
        for step in (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800,
                     21600, 43200, 86400):
            if span / step <= 9:
                break
        first = math.ceil(start / step) * step
        return [t for t in range(int(first), int(end) + 1, int(step))], step

    def _refresh_stats_curves(self, now):
        """Les deux graphes, redessines ensemble sur la MEME fenetre de
        temps (self._stats_view). Vent en haut, pression en bas : chacun sa
        propre echelle -- superposes sur un meme axe, l'un ecrasait l'autre
        et les dixiemes d'hectopascal faisaient un escalier illisible."""
        axw, axp = self.stats_ax_wind, self.stats_ax_press
        axw.clear()
        axp.clear()
        self.stats_fig.set_facecolor(BG_APP)

        view = self._stats_view
        span_s, end = self._stats_clamp_view(view["span_s"], view["end"], now)
        self._stats_view = {"span_s": span_s, "end": end}
        t_end = now if end is None else end
        t_start = t_end - span_s

        # Moyennes PAR TRANCHE et non mesures brutes : un anemometre qui
        # parle toutes les deux secondes dessine une pelote ou la tendance
        # disparait. La dispersion reste visible : bande claire mini/maxi.
        step = max(30.0, span_s / 150)
        count = max(2, int(math.ceil(span_s / step)) + 1)
        tws = self.trend.buckets("tws", t_end, step, count)
        gust = self.trend.buckets("wind_gust_kn", t_end, step, count)
        press = self.trend.buckets("pressure", t_end, step, count)

        def xs(cells):
            return [(c["t0"] + c["t1"]) / 2.0 for c in cells]

        def filled(cells):
            return [c for c in cells if c["mean"] is not None]

        # ----- Vent (graphe du haut) -----
        wind_top = 10.0
        cells = filled(tws)
        if cells:
            axw.fill_between(xs(cells), [c["min"] for c in cells],
                             [c["max"] for c in cells],
                             color=COLOR_ACCENT, alpha=0.18, linewidth=0)
            axw.plot(xs(cells), [c["mean"] for c in cells], color=COLOR_ACCENT,
                     lw=1.6, label="Vent reel")
            wind_top = max(wind_top, max(c["max"] for c in cells))
        gcells = filled(gust)
        if gcells:
            axw.plot(xs(gcells), [c["max"] for c in gcells], color=COLOR_PORT,
                     lw=1.0, alpha=0.85, label="Rafales")
            wind_top = max(wind_top, max(c["max"] for c in gcells))
        # L'echelle du vent PART DE ZERO, toujours : c'est le zero qui donne
        # la proportion -- un 12 noeuds trace entre 10 et 14 semble double
        # d'un 11, alors qu'il n'en differe que de 9 %.
        axw.set_ylim(0.0, wind_top * 1.15)
        axw.yaxis.set_major_locator(MaxNLocator(nbins=6, integer=True))
        axw.set_ylabel("vent (kn)", color=FG_LABEL, fontsize=8)
        if cells or gcells:
            leg = axw.legend(loc="upper left", fontsize=7, framealpha=0.85)
            leg.get_frame().set_facecolor(BG_PANEL)
            leg.get_frame().set_edgecolor(COLOR_BORDER)
            for text in leg.get_texts():
                text.set_color(FG_LABEL)
        else:
            axw.text(0.5, 0.5, "aucun vent recu sur cette etendue",
                     transform=axw.transAxes, ha="center", va="center",
                     color=FG_LABEL_DIM, fontsize=9)

        # ----- Pression (graphe du bas) -----
        pcells = filled(press)
        if pcells:
            axp.plot(xs(pcells), [c["mean"] for c in pcells], color=FG_DIGIT,
                     lw=1.7)
            lo = min(c["min"] for c in pcells)
            hi = max(c["max"] for c in pcells)
            # Echelle en HECTOPASCALS ENTIERS, jamais plus fine : au-dela,
            # le bruit du capteur dessine un escalier qu'on prend pour un
            # front. 4 hPa d'amplitude minimum -- une variation qui tient
            # la-dedans est, a l'echelle d'un barometre, un trait plat, et
            # doit se VOIR plate.
            center = (lo + hi) / 2.0
            half = max(2.0, (hi - lo) * 0.65)
            axp.set_ylim(center - half, center + half)
            axp.yaxis.set_major_locator(MaxNLocator(nbins=5, integer=True,
                                                    steps=[1, 2, 5]))
        else:
            axp.text(0.5, 0.5, "aucune pression recue sur cette etendue",
                     transform=axp.transAxes, ha="center", va="center",
                     color=FG_LABEL_DIM, fontsize=9)
        axp.set_ylabel("pression (hPa)", color=FG_LABEL, fontsize=8)

        # ----- Axe de temps COMMUN -----
        axw.set_xlim(t_start, t_end)
        ticks, tick_step = self._stats_time_ticks(t_start, t_end)
        day_span = t_end - t_start > 20 * 3600 or (
            ticks and time.localtime(ticks[0]).tm_mday != time.localtime(t_end).tm_mday)

        def _tick_label(t):
            lt = time.localtime(t)
            if day_span and lt.tm_hour == 0 and lt.tm_min == 0:
                return time.strftime("%d/%m", lt)
            return time.strftime("%H:%M", lt)

        axp.set_xticks(ticks)
        axp.set_xticklabels([_tick_label(t) for t in ticks])
        # Les etiquettes de temps ne vivent que sur le graphe du BAS : les
        # deux partagent le meme axe, les repeter en haut ne ferait que
        # manger la place du vent.
        axw.tick_params(labelbottom=False)

        for a in (axw, axp):
            a.set_facecolor(BG_PANEL)
            a.tick_params(colors=FG_LABEL, labelsize=7)
            for spine in a.spines.values():
                spine.set_color(COLOR_BORDER)
            a.grid(True, color=COLOR_BORDER, lw=0.4, alpha=0.6)

        # Vue figee (panoramique dans le passe) : le dire, et dire comment
        # revenir -- un graphe qui ne bouge plus sans explication ressemble
        # a une panne.
        if end is not None:
            self.stats_curve_note_lbl.configure(
                text="Vue figee sur le passe -- cliquez une etendue pour revenir au direct.")
        else:
            self.stats_curve_note_lbl.configure(text="")

        self.stats_canvas.draw_idle()

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
        # Statistiques : uniquement quand la page est sous les yeux. Les
        # mesures, elles, continuent d'etre enregistrees en permanence
        # (PolarEngine.trend) -- ce sont les tableaux qui coutent, pas la
        # collecte.
        if self._current_step == "statistiques":
            self._refresh_stats()

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
                    self.goto_session_btn.pack(side="left", padx=(10, 0))
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
                "Une session n'a pas encore ete traitee ('Traiter la session'). "
                "Voulez-vous la traiter maintenant avant de quitter ?\n\n"
                "Oui = traiter puis quitter -- Non = quitter sans traiter (les donnees de cette "
                "session seront perdues) -- Annuler = ne pas quitter.")
            if choice is None:
                return
            if choice:
                self._process_session()

        for attr in ("_refresh_tick_id", "_trend_backfill_after_id"):
            ident = getattr(self, attr, None)
            if ident is not None:
                try:
                    self.after_cancel(ident)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        if self.recording_active:
            self._stop_recording()
        self._close_udp_listeners()
        if self.buffer is not None:
            self.buffer.close()
        # Derniere position GPS memorisee pour le theme auto du prochain
        # lancement (voir _theme_tick) -- ecrite avec le reste de l'etat.
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
