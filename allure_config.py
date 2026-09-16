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
allure_config.py
================
Persistance JSON des reglages du generateur de polaires "Allure" (fichier
"parametrage", separe du fichier "entrepot cumulatif" -- voir plus bas).

Deux fichiers JSON distincts, a dessein :

- POLAR_SETTINGS_PATH : reglages de l'application (ports UDP + ordre de
  priorite entre voies, listes voiles/moteurs, bins, symetrie,
  statistique, parametres de lissage). Petit fichier, ecrase a chaque
  changement de reglage.

- POLAR_STORE_PATH : l'entrepot cumulatif d'echantillons lisses
  (PolarSampleStore.to_list()), qui grossit passe apres passe et ne
  doit JAMAIS etre efface par une simple reinitialisation des reglages.

Meme pattern defensif que le reste du projet : default_config() /
fixup(cfg) tolerant aux fichiers manquants/corrompus / load_config() /
save_config().

Un troisieme type de fichier, distinct des deux precedents et NON gere
automatiquement (choisi explicitement par l'utilisateur via un dialogue
Exporter/Importer) : la sauvegarde portable (save_backup_bundle() /
load_backup_bundle()) -- un seul fichier JSON regroupant entrepot +
index des passes, pense pour faciliter une sauvegarde manuelle ou un
changement de version/de poste sans avoir a recopier les fichiers
internes un par un.

Un quatrieme fichier, minuscule et purement pratique (aucun impact sur le
calcul des polaires) : l'etat d'interface (load_ui_state()/save_ui_state()),
qui retient simplement la derniere etape visitee pour y revenir directement
au prochain lancement -- ecrit automatiquement a chaque changement d'etape,
sans bouton "Enregistrer" (contrairement a POLAR_SETTINGS_PATH).

Un cinquieme fichier : la corbeille (load_trash()/save_trash()),
qui conserve une copie des passes supprimees (bouton "Supprimer" d'une
passe, ou "Reinitialiser tout l'entrepot") afin de pouvoir les restaurer en
cas d'erreur -- volontairement un fichier a part de POLAR_STORE_PATH : on ne
veut jamais qu'une corruption/relecture de l'un affecte l'autre. Contenu
opaque du point de vue de ce module (charge/sauve tel quel, structure
definie et interpretee cote application -- voir App._delete_session /
App._reset_store / App._restore_trash_item dans allure.py) ; seule la
forme generale (liste de dicts portant chacun "kind" et "deleted_at") est
validee ici, une entree individuelle invalide etant simplement ignoree
plutot que d'invalider toute la corbeille.

Un sixieme et dernier couple de fonctions, symetrique de save_backup_bundle()/
load_backup_bundle() mais pour les REGLAGES seuls (pas l'entrepot) :
export_settings()/import_settings() -- voir la section correspondante plus
bas. Meme motivation (changement de version/de poste), fichier de format
different (SETTINGS_BACKUP_FORMAT != BACKUP_FORMAT) : on ne veut pas qu'un
import de sauvegarde d'entrepot soit accidentellement accepte comme un
import de reglages, ni l'inverse.

Allure -- ETDEL 2026
"""

import json
import os
import sys
import time

APP_NAME = "Allure"
APP_TAGLINE = "Generateur de polaires"
APP_CREDIT = "ETDEL 2026"

# Version de l'application -- SEULE LIGNE A MODIFIER pour changer de
# version. Incrementee A CHAQUE LIVRAISON : ce
# numero est destine a l'humain (identifier une version installee,
# rattacher une donnee a la version qui l'a produite), il ne pilote AUCUN
# comportement du programme.
#
# A NE PAS CONFONDRE avec BACKUP_FORMAT_VERSION plus bas, qui decrit le
# FORMAT des fichiers et ne bouge que si celui-ci change reellement. Cette
# separation est ce qui protege le contrat de compatibilite (voir
# allure_engine.fixup_sample) : l'application peut passer en 2.0, 3.0 ou
# 10.0 sans qu'un seul fichier deja ecrit devienne illisible.
#
# FORME DU NUMERO : "X.Y" puis une LETTRE pour les livraisons mineures --
# 2.0a, 2.0b, 2.0c... Le chiffre change quand l'application change de
# visage (nouvelle etape, nouveau format, refonte) ; la lettre suffit pour
# une correction ou un ajustement. Chaque livraison a son entree dans
# CHANGELOG.md, a la racine du depot.
APP_VERSION = "2.0b"

# =========================================================================
# Ou vivent les fichiers
#
# Tout ce que le programme ecrit est range dans QUATRE dossiers a cote de
# lui, plutot qu'en vrac dans le meme repertoire que les .py : on distingue
# d'un coup d'oeil le programme de ses donnees, une sauvegarde manuelle se
# reduit a copier un dossier, et supprimer le tampon ne risque plus
# d'emporter l'entrepot par megarde.
#
#   donnees/    l'entrepot cumulatif, les passes, la corbeille -- LE bien
#               precieux, celui qu'on sauvegarde
#   reglages/   les reglages et l'etat d'interface -- reconstructibles
#   tampon/     le tampon glissant de trames brutes -- volumineux, jetable
#   journaux/   les journaux bruts des prises -- jetables une fois traitees
#
# Les versions precedentes ecrivaient tout a la racine : les fichiers qui
# s'y trouvent encore sont DEPLACES automatiquement au premier lancement
# (voir _migrate_legacy_files). Personne ne doit avoir a ranger a la main,
# ni risquer de croire son entrepot perdu.
# =========================================================================

# Empaquete en .exe autonome (PyInstaller), __file__ pointe dans le dossier
# TEMPORAIRE ou l'exe se deballe a chaque lancement -- et qui est efface a
# la fermeture. S'y fier aurait ecrit l'entrepot, les reglages et le tampon
# dans un dossier jetable : perte de TOUT a chaque sortie du programme. En
# mode exe, la reference est donc l'executable lui-meme : les quatre
# dossiers naissent a cote de Allure.exe, exactement comme ils naissent a
# cote des .py en mode script.
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "donnees")
SETTINGS_DIR = os.path.join(APP_DIR, "reglages")
BUFFER_DIR = os.path.join(APP_DIR, "tampon")
LOGS_DIR = os.path.join(APP_DIR, "journaux")
ALL_DIRS = (DATA_DIR, SETTINGS_DIR, BUFFER_DIR, LOGS_DIR)


def ensure_dirs():
    """Cree les dossiers manquants. Appelee a l'import et avant chaque
    ecriture sensible : un dossier efface a la main pendant que le programme
    tourne ne doit pas faire perdre la sauvegarde suivante."""
    for d in ALL_DIRS:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass


POLAR_SETTINGS_PATH = os.path.join(SETTINGS_DIR, "polar_settings.json")
POLAR_STORE_PATH = os.path.join(DATA_DIR, "polar_store.json")

PORT_KEYS = ["port1", "port2", "port3", "port4"]

DEFAULT_SAILS = ["J0", "J1A", "MSA", "J1F", "MSF"]
DEFAULT_ENGINES = ["1ME", "2ME", "1SB", "2SB"]

STATISTICS = ("p90", "median", "mean", "max")


def default_config():
    return {
        # Chaque voie porte un NOM libre ("Station nav", "Meteo France",
        # "GPS passerelle"...) : sur un bord, on ne raisonne pas en numeros
        # de port UDP mais en equipements, et c'est ce nom qui s'affiche
        # partout ou une voie est citee.
        "port1": {"ip": "0.0.0.0", "port": 4001, "enabled": True, "name": ""},
        "port2": {"ip": "0.0.0.0", "port": 4003, "enabled": True, "name": ""},
        "port3": {"ip": "0.0.0.0", "port": 10112, "enabled": False, "name": ""},
        "port4": {"ip": "0.0.0.0", "port": 10113, "enabled": False, "name": ""},
        # Ordre de priorite entre voies quand plusieurs envoient le meme type
        # de trame (VHW/MWV) simultanement -- voir PolarEngine._accept_for_priority.
        # Port1 prioritaire par defaut (ordre naturel/previsible) ; a ajuster
        # dans Parametres selon quel instrument s'avere le plus fiable sur
        # VOTRE installation (voir la colonne "Priorite" de l'onglet Parametres).
        "priority": ["port1", "port2", "port3", "port4"],
        # Sources CHOISIES pour chaque grandeur, en ordre de preference PROPRE
        # A CETTE GRANDEUR : {"MWV": ["port2", "port1"], ...} -- la premiere
        # voie fait foi, les suivantes prennent le relais si elle se tait.
        # Une entree absente = automatique (ordre "priority" ci-dessus).
        #
        # C'est un ordre PAR MESURE et non par voie : une passerelle peut
        # porter la meilleure girouette et le plus mauvais loch, classer ses
        # voies en bloc n'aurait alors aucun sens. Et deux voies qui portent
        # le meme type de trame ne different pas par leur "fiabilite" mais
        # par leur CONTENU (deux girouettes qui ne s'accordent pas) : cela se
        # designe, ca ne se devine pas.
        "sources": {},
        # Mode d'acquisition en direct. Une CHAINE et non un booleen : seule
        # l'ecoute UDP existe aujourd'hui, mais une liaison serie ou un
        # fichier suivi en continu s'ajouteront sans changer le format du
        # fichier de reglages (une valeur inconnue retombe simplement sur
        # "udp" -- voir fixup).
        "acquisition_type": "udp",
        # Grandeurs d'ambiance affichees dans le detail d'une minute
        # archivee (double-clic dans la recherche) : liste de cles de
        # allure_engine.AMBIENT_FIELDS. Vide = aucune. On ne devine pas ce qui
        # interesse : c'est un choix, fait dans les Parametres a partir de ce
        # que l'analyse des voies a reellement trouve a bord.
        "archive_detail_fields": [],
        # Duree sur laquelle ces grandeurs sont moyennees, en minutes,
        # centree sur la minute consultee. Une pression instantanee ne veut
        # rien dire ; une moyenne sur dix minutes, si.
        "archive_detail_window_min": 10,
        # Repli automatique quand la source choisie se tait : mieux vaut une
        # source de secours qu'un trou dans la mesure. Decochable pour une
        # source unique, non negociable.
        "source_fallback": True,
        "stale_timeout": 30.0,

        "sail_list": list(DEFAULT_SAILS),
        "engine_list": list(DEFAULT_ENGINES),

        "twa_bin_deg": 5.0,
        "tws_bin_kn": 2.0,
        "symmetric_port_starboard": True,
        "aggregation_stat": "p90",

        "smoothing_window_s": 15.0,
        "sample_period_s": 10.0,
        "maneuver_twa_deg": 15.0,
        "maneuver_stw_frac": 0.25,
        "min_fill_frac": 0.5,

        # --- Arret automatique sur manoeuvre (ManeuverWatch) ---
        # DESACTIVE par defaut : une fonction qui arrete une prise et pose
        # des questions doit etre choisie, jamais subie. Fondee sur la route
        # et la vitesse FOND (VTG), seuils distincts du filtre d'echantillons
        # ci-dessus -- l'un ecarte des mesures, l'autre interpelle l'equipage.
        "maneuver_stop_enabled": False,
        "maneuver_stop_window_s": 45.0,
        "maneuver_stop_cog_deg": 30.0,
        "maneuver_stop_sog_frac": 0.35,
        "maneuver_stop_cooldown_s": 120.0,

        # --- Polaire max (routage) ---
        # Sur ce bateau (cargo a voile ET moteur), le moteur fait partie de
        # la voilure : chaque cas de vent appelle un JEU voiles/moteur, pas
        # une voile seule. Les configurations moteur concourent donc par
        # defaut dans la polaire max. Mettre a False pour retrouver le
        # comportement "voilier pur" (moteur decoche par defaut).
        "max_polar_include_engine": True,

        # --- Tri automatique a l'entree de l'entrepot ---
        # Une passe dont l'indice de confiance est trop faible peut entrer
        # DECOCHEE plutot qu'incluse. Desactive par defaut : rien ne doit
        # se decider a la place de l'utilisateur sans qu'il l'ait demande.
        # Et meme active, cela ne SUPPRIME rien -- la passe est entreposee
        # normalement, sa case "Incluse" est simplement pre-positionnee, et
        # se recoche d'un clic.
        "auto_exclude_low_confidence": False,
        # Seuil conseille : 40, la frontiere entre "indicative" (C) et
        # "fragile" (D) -- voir allure_engine.CONF_LEVELS.
        "auto_exclude_threshold": 40,

        # --- Polaire max : ecretage par la derivee ---
        # Desactive par defaut : boucher un trou AJOUTE la ou il n'y avait
        # rien, ecreter MODIFIE une valeur mesuree. Ce n'est pas le meme
        # geste, et le second se demande.
        "max_polar_clip_enabled": False,
        "max_polar_clip_slope": 1.5,      # kn par 10 degres de TWA

        # --- Export TimeZero ---
        # Correspondance entre les codes de voile du bord (J1A, MSA...) et
        # le vocabulaire de TimeZero (GV/1Reef/2Reef/3Reef/Off pour la
        # grand-voile, LightUpWind/MediumReaching/... pour l'avant). Elle
        # ne peut pas se deviner : seul l'equipage sait si "MSF" est une
        # grand-voile a deux ris ou un solent de gros temps.
        # {code Allure: valeur TimeZero} ; un code absent est ignore.
        "timezero_sail_map": {},

        # --- Page Statistiques ---
        # Grandeurs AFFICHEES sur la page (cles de allure_engine.TREND_FIELDS).
        # Le catalogue complet est volontairement plus large que ce choix par
        # defaut : tout afficher noyait l'essentiel -- on choisit ici, dans
        # les Parametres, ce qui merite l'ecran. Une cle inconnue de la
        # version qui relit ce fichier est simplement ignoree a l'affichage.
        "stats_fields": ["tws", "aws", "twa", "awa", "stw", "sog", "pressure"],
        # Tableau par tranches (facon table de prevision) : affiche ou non.
        "stats_show_grid": True,
        # Les quatre fenetres de moyenne glissante, en SECONDES, de la plus
        # courte a la plus longue. Quatre, parce que c'est ce qu'une colonne
        # de tableau lisible d'un coup d'oeil peut porter -- et parce que la
        # question posee en mer ("le vent forcit-il ?") se lit dans la
        # comparaison de quelques echelles, pas dans une collection.
        "stats_windows": [300, 600, 1800, 3600],
        # Profondeur d'historique tenue en memoire, en heures : elle borne a
        # la fois la plus longue moyenne possible et l'etendue des courbes.
        # 6 h suffit largement pour une tendance barometrique, tout en
        # gardant l'empreinte memoire negligeable.
        "stats_history_h": 6,
        # Tableau facon Windguru : largeur d'une colonne (minutes) et nombre
        # de colonnes affichees.
        "stats_grid_step_min": 30,
        "stats_grid_cols": 12,
        # Etendue des courbes (heures) et nombre d'heures pleines affichees
        # dans la distance parcourue.
        "stats_curve_h": 6,
        "stats_hours_shown": 12,

        "default_recording_duration_min": 60,

        # --- Tampon d'enregistrement glissant (voir allure_buffer.py) ---
        # Actif par defaut : son interet est justement de tourner sans qu'on
        # y pense, pour que la question "avais-je lance l'enregistrement ?"
        # ne se pose plus jamais. 48 h au debit mesure sur une passerelle
        # reelle (2,6 lignes/s) represente environ 32 Mo -- negligeable.
        "buffer_enabled": True,
        "buffer_retention_h": 48,

        # --- Affichage & fenetre ---
        # Theme : "clair", "sombre", ou "auto" (sombre entre le coucher et le
        # lever du soleil, calcules depuis la derniere position GPS connue --
        # voir allure_engine.sun_times). "clair" par defaut : c'est l'aspect
        # historique de l'application, aucune surprise a la mise a jour.
        # Amortissement des valeurs AFFICHEES dans le suivi en direct, en
        # secondes (0 = valeurs brutes). Sans rapport avec le lissage de la
        # polaire, bien plus long et assorti d'un filtre de manoeuvre : ceci
        # ne sert que l'oeil, et ne change RIEN a ce qui est enregistre.
        # 5 s : de quoi calmer les chiffres sans que le cadran cesse de
        # suivre le bateau.
        "display_damping_s": 5.0,

        "theme_mode": "clair",
        # La croix de la fenetre REDUIT dans la zone de notification au lieu
        # de fermer, quand la bibliotheque pystray est installee (facultative,
        # voir LISEZ-MOI) ; la fermeture reelle se fait alors depuis l'icone.
        # Sans pystray, la croix garde son comportement normal quel que soit
        # ce reglage.
        "close_to_tray": True,
    }


THEME_MODES = ("clair", "sombre", "auto")

# Modes d'acquisition en direct reconnus (voir "acquisition_type").
ACQUISITION_TYPES = ("udp",)


def _fixup_port(p, defaults):
    if not isinstance(p, dict):
        return dict(defaults)
    out = dict(defaults)
    ip = p.get("ip")
    if isinstance(ip, str) and ip.strip():
        out["ip"] = ip.strip()
    try:
        port_num = int(p.get("port"))
        if 1 <= port_num <= 65535:
            out["port"] = port_num
    except (TypeError, ValueError):
        pass
    out["enabled"] = bool(p.get("enabled", defaults["enabled"]))
    name = p.get("name")
    # Nom libre, borne a une longueur raisonnable pour ne pas defoncer la
    # mise en page des tableaux ou il s'affiche. Absent dans les fichiers
    # ecrits par les versions anterieures : chaine vide, l'application
    # retombe alors sur "port1", "port2"...
    out["name"] = name.strip()[:40] if isinstance(name, str) else ""
    return out


def _fixup_str_list(value, defaults):
    if not isinstance(value, list):
        return list(defaults)
    out = []
    for v in value:
        if isinstance(v, str) and v.strip() and v.strip() not in out:
            out.append(v.strip())
    return out or list(defaults)


def _clamp(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not (v == v):  # NaN
        return default
    return max(lo, min(hi, v))


def fixup(cfg):
    """Tolerant aux fichiers manquants/corrompus/partiels : comble tout champ absent
    ou hors-plage avec la valeur par defaut correspondante."""
    d = default_config()
    if not isinstance(cfg, dict):
        return d
    out = dict(d)

    for key in PORT_KEYS:
        out[key] = _fixup_port(cfg.get(key), d[key])

    priority = cfg.get("priority")
    if isinstance(priority, list) and sorted(priority) == sorted(PORT_KEYS):
        out["priority"] = list(priority)
    else:
        out["priority"] = list(d["priority"])

    # Sources choisies : seules les entrees qui designent une voie CONNUE
    # sont retenues. Un fichier ecrit par une version plus recente peut
    # nommer une grandeur que cette version ne connait pas -- on la garde
    # telle quelle si elle pointe une voie valide (le contrat de
    # compatibilite : ignorer ce qu'on ne comprend pas, ne rien casser).
    sources = cfg.get("sources")
    out["sources"] = {}
    if isinstance(sources, dict):
        for styp, val in sources.items():
            if not isinstance(styp, str):
                continue
            # Accepte la forme LISTE (ordre de preference) comme la forme
            # CHAINE (une seule voie) : la seconde a existe brievement, et un
            # reglage deja ecrit ne doit jamais devenir illisible.
            chain = [val] if isinstance(val, str) else (list(val) if isinstance(val, list) else [])
            clean = []
            for port in chain:
                if port in PORT_KEYS and port not in clean:
                    clean.append(port)
            if clean:
                out["sources"][styp.strip().upper()] = clean
    out["source_fallback"] = bool(cfg.get("source_fallback", d["source_fallback"]))
    acq = cfg.get("acquisition_type")
    out["acquisition_type"] = acq if acq in ACQUISITION_TYPES else d["acquisition_type"]

    # Les cles inconnues de CETTE version sont conservees telles quelles
    # (contrat de compatibilite : un reglage ecrit par une version plus
    # recente ne doit pas etre efface par un aller-retour dans une version
    # anterieure) ; seuls les doublons et les valeurs non textuelles partent.
    fields = cfg.get("archive_detail_fields")
    out["archive_detail_fields"] = []
    if isinstance(fields, list):
        for k in fields:
            if isinstance(k, str) and k and k not in out["archive_detail_fields"]:
                out["archive_detail_fields"].append(k)
    out["archive_detail_window_min"] = int(_clamp(
        cfg.get("archive_detail_window_min"), 1, 120, d["archive_detail_window_min"]))

    out["stale_timeout"] = _clamp(cfg.get("stale_timeout"), 2.0, 300.0, d["stale_timeout"])

    out["sail_list"] = _fixup_str_list(cfg.get("sail_list"), d["sail_list"])
    out["engine_list"] = _fixup_str_list(cfg.get("engine_list"), d["engine_list"])

    out["twa_bin_deg"] = _clamp(cfg.get("twa_bin_deg"), 1.0, 45.0, d["twa_bin_deg"])
    out["tws_bin_kn"] = _clamp(cfg.get("tws_bin_kn"), 0.5, 20.0, d["tws_bin_kn"])
    out["symmetric_port_starboard"] = bool(cfg.get("symmetric_port_starboard", d["symmetric_port_starboard"]))

    stat = cfg.get("aggregation_stat")
    out["aggregation_stat"] = stat if stat in STATISTICS else d["aggregation_stat"]

    out["smoothing_window_s"] = _clamp(cfg.get("smoothing_window_s"), 3.0, 300.0, d["smoothing_window_s"])
    out["sample_period_s"] = _clamp(cfg.get("sample_period_s"), 1.0, 300.0, d["sample_period_s"])
    out["maneuver_twa_deg"] = _clamp(cfg.get("maneuver_twa_deg"), 2.0, 90.0, d["maneuver_twa_deg"])
    out["maneuver_stw_frac"] = _clamp(cfg.get("maneuver_stw_frac"), 0.02, 2.0, d["maneuver_stw_frac"])
    out["min_fill_frac"] = _clamp(cfg.get("min_fill_frac"), 0.1, 1.0, d["min_fill_frac"])

    out["maneuver_stop_enabled"] = bool(cfg.get("maneuver_stop_enabled",
                                                 d["maneuver_stop_enabled"]))
    out["maneuver_stop_window_s"] = _clamp(cfg.get("maneuver_stop_window_s"), 10.0, 600.0,
                                            d["maneuver_stop_window_s"])
    out["maneuver_stop_cog_deg"] = _clamp(cfg.get("maneuver_stop_cog_deg"), 5.0, 180.0,
                                           d["maneuver_stop_cog_deg"])
    out["maneuver_stop_sog_frac"] = _clamp(cfg.get("maneuver_stop_sog_frac"), 0.05, 2.0,
                                            d["maneuver_stop_sog_frac"])
    out["maneuver_stop_cooldown_s"] = _clamp(cfg.get("maneuver_stop_cooldown_s"), 10.0, 3600.0,
                                              d["maneuver_stop_cooldown_s"])

    out["max_polar_include_engine"] = bool(cfg.get("max_polar_include_engine",
                                                    d["max_polar_include_engine"]))
    out["auto_exclude_low_confidence"] = bool(cfg.get(
        "auto_exclude_low_confidence", d["auto_exclude_low_confidence"]))
    out["auto_exclude_threshold"] = int(_clamp(
        cfg.get("auto_exclude_threshold"), 0, 100, d["auto_exclude_threshold"]))

    out["max_polar_clip_enabled"] = bool(cfg.get("max_polar_clip_enabled",
                                                  d["max_polar_clip_enabled"]))
    out["max_polar_clip_slope"] = _clamp(cfg.get("max_polar_clip_slope"), 0.1, 10.0,
                                          d["max_polar_clip_slope"])

    tzmap = cfg.get("timezero_sail_map")
    out["timezero_sail_map"] = ({str(k): str(v) for k, v in tzmap.items()
                                  if isinstance(k, str) and isinstance(v, str) and v}
                                 if isinstance(tzmap, dict) else {})

    # Statistiques : grandeurs affichees -- liste de cles texte, dedoublonnee.
    # La validite des cles est jugee a l'AFFICHAGE (allure croise avec le
    # catalogue de allure_engine) : ce module ne doit dependre d'aucun autre,
    # et une cle venue d'une version plus recente ne doit pas etre perdue.
    fields = cfg.get("stats_fields")
    if isinstance(fields, (list, tuple)):
        clean = []
        for k in fields:
            if isinstance(k, str) and k.strip() and k.strip() not in clean:
                clean.append(k.strip())
        out["stats_fields"] = clean[:32] or list(d["stats_fields"])
    else:
        out["stats_fields"] = list(d["stats_fields"])
    out["stats_show_grid"] = bool(cfg.get("stats_show_grid", d["stats_show_grid"]))

    # Statistiques : quatre fenetres, bornees a 1 min / 12 h, DEDOUBLONNEES
    # et triees. Deux colonnes identiques ne diraient rien de plus, et une
    # liste dans le desordre ferait lire une tendance a l'envers.
    wins = cfg.get("stats_windows")
    seen = []
    if isinstance(wins, (list, tuple)):
        for w in wins:
            try:
                v = int(float(w))
            except (TypeError, ValueError):
                continue
            v = max(60, min(43200, v))
            if v not in seen:
                seen.append(v)
    out["stats_windows"] = sorted(seen)[:4] if seen else list(d["stats_windows"])
    while len(out["stats_windows"]) < 4:
        # Complete avec les valeurs par defaut manquantes : le tableau a
        # quatre colonnes, il en faut quatre.
        for cand in d["stats_windows"]:
            if cand not in out["stats_windows"]:
                out["stats_windows"].append(cand)
                break
        else:
            out["stats_windows"].append(out["stats_windows"][-1] * 2)
        out["stats_windows"] = sorted(set(out["stats_windows"]))
    out["stats_history_h"] = int(_clamp(cfg.get("stats_history_h"), 1, 48,
                                         d["stats_history_h"]))
    out["stats_grid_step_min"] = int(_clamp(cfg.get("stats_grid_step_min"), 1, 180,
                                             d["stats_grid_step_min"]))
    out["stats_grid_cols"] = int(_clamp(cfg.get("stats_grid_cols"), 3, 48,
                                         d["stats_grid_cols"]))
    out["stats_curve_h"] = int(_clamp(cfg.get("stats_curve_h"), 1, 48, d["stats_curve_h"]))
    out["stats_hours_shown"] = int(_clamp(cfg.get("stats_hours_shown"), 2, 48,
                                           d["stats_hours_shown"]))

    out["default_recording_duration_min"] = int(_clamp(
        cfg.get("default_recording_duration_min"), 1, 1440, d["default_recording_duration_min"]))

    out["buffer_enabled"] = bool(cfg.get("buffer_enabled", d["buffer_enabled"]))
    # Bornes alignees sur allure_buffer.MIN/MAX_RETENTION_H, recopiees ici
    # plutot qu'importees : allure_config.py ne doit dependre d'aucun autre
    # module du projet (il est charge en tout premier, avant meme que
    # l'interface n'existe).
    out["buffer_retention_h"] = int(_clamp(cfg.get("buffer_retention_h"), 1, 720,
                                            d["buffer_retention_h"]))

    out["display_damping_s"] = _clamp(cfg.get("display_damping_s"), 0.0, 60.0,
                                       d["display_damping_s"])

    theme = cfg.get("theme_mode")
    out["theme_mode"] = theme if theme in THEME_MODES else d["theme_mode"]
    out["close_to_tray"] = bool(cfg.get("close_to_tray", d["close_to_tray"]))

    return out


def load_config():
    if not os.path.exists(POLAR_SETTINGS_PATH):
        return default_config()
    try:
        with open(POLAR_SETTINGS_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return default_config()
    return fixup(raw)


def save_config(cfg):
    cfg = fixup(cfg)
    tmp_path = POLAR_SETTINGS_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        # "app_version" est ecrit dans le FICHIER mais ne fait pas partie des
        # reglages eux-memes (fixup() l'ignore au rechargement) : c'est une
        # trace de provenance -- savoir quelle version a ecrit ce fichier
        # quand on en inspecte un venu d'un autre poste -- pas un parametre
        # que l'utilisateur reglerait.
        json.dump(dict(cfg, app_version=APP_VERSION), fh, indent=2, ensure_ascii=False)
    os.replace(tmp_path, POLAR_SETTINGS_PATH)
    return cfg


# =========================================================================
# Entrepot cumulatif (PolarSampleStore serialise) -- fichier separe
# =========================================================================

def load_store_data():
    """Retourne la liste brute d'echantillons (voir PolarSampleStore.from_list),
    ou [] si le fichier est absent/corrompu (jamais d'exception : on prefere
    repartir d'un entrepot vide plutot que de bloquer le demarrage)."""
    if not os.path.exists(POLAR_STORE_PATH):
        return []
    try:
        with open(POLAR_STORE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def save_store_data(data):
    """Ecriture atomique (fichier temporaire + os.replace) pour ne jamais
    corrompre l'entrepot cumulatif en cas de coupure pendant l'ecriture --
    ces donnees s'accumulent sur des mois, elles ne doivent pas se perdre."""
    tmp_path = POLAR_STORE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp_path, POLAR_STORE_PATH)


# =========================================================================
# Index des "passes" (sessions enregistrees puis traitees) -- fichier
# separe egalement : metadonnees legeres (date, source, nombre
# d'echantillons, inclusion ou non dans le calcul de polaire) permettant
# de retrouver/gerer chaque passe individuellement sans avoir a
# reinspecter l'entrepot complet. Chaque entree porte un "id" qui
# correspond au session_id tague sur les echantillons de
# PolarSampleStore issus de cette passe (voir allure_engine.PolarSampleStore).
# =========================================================================

POLAR_SESSIONS_PATH = os.path.join(DATA_DIR, "polar_sessions.json")


def _fixup_ranges(value):
    """Plages horaires [[debut, fin], ...] nettoyees : bornes numeriques,
    remises dans l'ordre, plages absurdes ignorees une par une."""
    out = []
    if not isinstance(value, (list, tuple)):
        return out
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        try:
            a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            continue
        if a != a or b != b:          # NaN
            continue
        out.append([min(a, b), max(a, b)])
    return out


def _fixup_session_record(rec):
    if not isinstance(rec, dict) or not rec.get("id"):
        return None
    try:
        sample_count = int(rec.get("sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    created_at = rec.get("created_at")
    try:
        created_at = float(created_at) if created_at is not None else None
    except (TypeError, ValueError):
        created_at = None
    return {
        "id": str(rec["id"]),
        "label": str(rec.get("label") or rec["id"]),
        "created_at": created_at,
        "mode": rec.get("mode") if rec.get("mode") in ("direct", "import") else "?",
        "source": str(rec.get("source") or ""),
        "sample_count": sample_count,
        "included": bool(rec.get("included", True)),
        # Version de l'application qui a produit cette passe. Absente pour
        # toutes les passes anterieures a l'introduction du numero de
        # version : "" y signifie "inconnue", pas une erreur. Purement
        # documentaire -- permet, quand une passe se revele douteuse, de
        # savoir avec quelle version elle a ete enregistree.
        "app_version": str(rec.get("app_version") or ""),
        # Plages horaires ECARTEES du calcul a l'interieur de la passe :
        # [[t_debut, t_fin], ...]. C'est ainsi qu'un TRONCON se decoche --
        # par la plage qu'il occupe, et non par son numero. Un troncon est
        # calcule (allure_engine.split_into_legs), pas stocke : changer un
        # parametre de decoupage redessinerait les frontieres, et des
        # exclusions attachees a des numeros se retrouveraient
        # silencieusement sur d'autres mesures. Une plage, elle, dit ce
        # qu'elle exclut et le dira encore dans dix ans.
        # Absente des passes anterieures : liste vide, tout est compte.
        "excluded_ranges": _fixup_ranges(rec.get("excluded_ranges")),
        # Les echantillons SANS HORODATAGE de cette passe sont-ils ecartes ?
        # Une plage horaire ne peut rien dire d'un echantillon qui n'a pas
        # d'heure : il lui faut ce drapeau a part, sans quoi le troncon qui
        # les porte se decocherait sans rien retirer du calcul. Absent des
        # passes anterieures : False, tout est compte.
        "exclude_undated": bool(rec.get("exclude_undated", False)),
    }


def load_sessions_index():
    """Retourne la liste des passes connues (metadonnees), ou [] si le
    fichier est absent/corrompu/partiel -- chaque entree est nettoyee
    individuellement, une entree invalide isolee n'invalide pas les autres."""
    if not os.path.exists(POLAR_SESSIONS_PATH):
        return []
    try:
        with open(POLAR_SESSIONS_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for rec in data:
        fixed = _fixup_session_record(rec)
        if fixed is not None:
            out.append(fixed)
    return out


def save_sessions_index(records):
    tmp_path = POLAR_SESSIONS_PATH + ".tmp"
    cleaned = [r for r in (_fixup_session_record(rec) for rec in records) if r is not None]
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, indent=2, ensure_ascii=False)
    os.replace(tmp_path, POLAR_SESSIONS_PATH)


# =========================================================================
# Etat d'interface -- fichier separe, minuscule, ecrit automatiquement a
# chaque changement d'etape (voir App._show_step) pour rouvrir l'appli sur
# la derniere etape visitee plutot que de toujours repartir de Parametres.
# Aucun impact sur le calcul des polaires : pas besoin d'un bouton
# "Enregistrer" explicite comme pour POLAR_SETTINGS_PATH.
# =========================================================================

POLAR_UI_STATE_PATH = os.path.join(SETTINGS_DIR, "polar_ui_state.json")


def load_ui_state():
    """Retourne un dict d'etat d'interface, ou {} si absent/corrompu (jamais
    d'exception : au pire, on repart des valeurs par defaut de l'appli)."""
    if not os.path.exists(POLAR_UI_STATE_PATH):
        return {}
    try:
        with open(POLAR_UI_STATE_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_ui_state(state):
    tmp_path = POLAR_UI_STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False)
    os.replace(tmp_path, POLAR_UI_STATE_PATH)


# =========================================================================
# Sauvegarde portable (export/import manuel, hors des fichiers internes) --
# un seul fichier JSON regroupant l'entrepot cumulatif et l'index des
# passes, destine a etre choisi explicitement par l'utilisateur (Entrepot :
# boutons Exporter/Importer), pour faciliter une sauvegarde manuelle ou le
# transfert des donnees accumulees lors d'un changement de version ou de
# poste -- sans avoir a retrouver/recopier POLAR_STORE_PATH et
# POLAR_SESSIONS_PATH separement.
# =========================================================================

BACKUP_FORMAT = "allure_backup"
BACKUP_FORMAT_VERSION = 1


POLAR_TRASH_PATH = os.path.join(DATA_DIR, "polar_trash.json")


def _migrate_legacy_files():
    """Deplace vers les nouveaux dossiers ce que les versions precedentes
    ecrivaient a la racine du programme. Silencieux et sans risque :

    - un fichier n'est deplace que si la destination N'EXISTE PAS (jamais
      d'ecrasement : en cas de doute, c'est la version deja rangee qui
      gagne, et l'ancienne reste a la racine pour inspection) ;
    - la moindre erreur (fichier verrouille, droits, disque plein) laisse
      l'original en place -- au pire on continue de tourner sans avoir
      range, jamais on ne perd.

    Rendue une seule fois a l'import : le cout est nul une fois le
    rangement fait (quelques os.path.exists sur des chemins absents)."""
    moved = []
    simple = ((("polar_settings.json", "polar_ui_state.json"), SETTINGS_DIR),
              (("polar_store.json", "polar_sessions.json", "polar_trash.json"), DATA_DIR))
    for names, dest_dir in simple:
        for name in names:
            old_path = os.path.join(APP_DIR, name)
            new_path = os.path.join(dest_dir, name)
            if os.path.exists(old_path) and not os.path.exists(new_path):
                try:
                    os.replace(old_path, new_path)
                    moved.append(name)
                except OSError:
                    pass
    # Journaux de prise : nom variable, meme regle fichier par fichier.
    try:
        entries = os.listdir(APP_DIR)
    except OSError:
        entries = []
    for name in entries:
        if name.startswith("polar_session_") and name.endswith(".log"):
            old_path = os.path.join(APP_DIR, name)
            new_path = os.path.join(LOGS_DIR, name)
            if os.path.isfile(old_path) and not os.path.exists(new_path):
                try:
                    os.replace(old_path, new_path)
                    moved.append(name)
                except OSError:
                    pass
    # Tampon glissant : ancien dossier "nmea_buffer" -> "tampon". Tranche
    # par tranche plutot qu'en bloc, pour qu'un fichier verrouille (celui
    # en cours d'ecriture) n'empeche pas le deplacement des autres.
    legacy_buffer = os.path.join(APP_DIR, "nmea_buffer")
    if os.path.isdir(legacy_buffer):
        try:
            names = os.listdir(legacy_buffer)
        except OSError:
            names = []
        for name in names:
            old_path = os.path.join(legacy_buffer, name)
            new_path = os.path.join(BUFFER_DIR, name)
            if os.path.isfile(old_path) and not os.path.exists(new_path):
                try:
                    os.replace(old_path, new_path)
                    moved.append(name)
                except OSError:
                    pass
        try:
            os.rmdir(legacy_buffer)   # ne part que s'il est vide
        except OSError:
            pass
    return moved


ensure_dirs()
# Rangement des donnees d'une version anterieure, au chargement du module :
# aucune autre partie du programme ne doit avoir a s'en soucier, ni pouvoir
# lire un chemin avant que le deplacement ait eu lieu.
MIGRATED_FILES = _migrate_legacy_files()


def _fixup_trash_entry(entry):
    if not isinstance(entry, dict) or entry.get("kind") not in ("session", "store_reset"):
        return None
    try:
        deleted_at = float(entry.get("deleted_at"))
    except (TypeError, ValueError):
        return None
    out = {"kind": entry["kind"], "deleted_at": deleted_at,
           "samples": entry.get("samples") if isinstance(entry.get("samples"), list) else []}
    if entry["kind"] == "session":
        rec = _fixup_session_record(entry.get("session_record"))
        if rec is None:
            return None
        out["session_record"] = rec
    else:
        sessions_raw = entry.get("sessions_index")
        out["sessions_index"] = [r for r in (_fixup_session_record(rec) for rec in
                                  (sessions_raw if isinstance(sessions_raw, list) else [])) if r is not None]
    return out


def load_trash():
    """Retourne la liste des entrees de corbeille (les plus recentes en
    dernier), ou [] si le fichier est absent/corrompu -- meme tolerance
    "une entree invalide isolee n'invalide pas les autres" que le reste de
    ce module."""
    if not os.path.exists(POLAR_TRASH_PATH):
        return []
    try:
        with open(POLAR_TRASH_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [e for e in (_fixup_trash_entry(entry) for entry in data) if e is not None]


def save_trash(entries):
    tmp_path = POLAR_TRASH_PATH + ".tmp"
    cleaned = [e for e in (_fixup_trash_entry(entry) for entry in entries) if e is not None]
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, ensure_ascii=False)
    os.replace(tmp_path, POLAR_TRASH_PATH)


def save_backup_bundle(path, store_data, sessions):
    """Ecrit une sauvegarde portable a l'emplacement choisi par l'utilisateur
    (PAS un chemin interne fixe comme POLAR_STORE_PATH -- celui-ci est
    explicitement fourni par l'appelant, typiquement issu d'un dialogue
    'Enregistrer sous')."""
    bundle = {
        "format": BACKUP_FORMAT,
        "format_version": BACKUP_FORMAT_VERSION,
        "app": APP_NAME,
        # Version de l'APPLICATION qui a produit la sauvegarde, distincte de
        # format_version ci-dessus (voir APP_VERSION en tete de module) :
        # utile pour situer une sauvegarde retrouvee sur un autre poste, sans
        # aucune incidence sur la relecture.
        "app_version": APP_VERSION,
        "exported_at": time.time(),
        "samples": store_data,
        "sessions": sessions,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)


def load_backup_bundle(path):
    """Retourne (samples, sessions) depuis un fichier produit par
    save_backup_bundle(). sessions est deja nettoye (meme validation que
    load_sessions_index()) -- une entree de passe invalide isolee n'invalide
    pas les autres. Leve ValueError si le fichier n'a manifestement pas le
    bon format (protection contre un import accidentel d'un fichier qui
    n'est pas une sauvegarde Allure) ; OSError/ValueError de json.load()
    remontent tels quels pour un fichier illisible/corrompu -- a l'appelant
    (interface) de choisir le message affiche a l'utilisateur dans ce cas."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise ValueError("Ce fichier ne semble pas etre une sauvegarde Allure valide.")
    samples = data.get("samples")
    sessions_raw = data.get("sessions")
    samples = samples if isinstance(samples, list) else []
    sessions = [r for r in (_fixup_session_record(rec) for rec in
                             (sessions_raw if isinstance(sessions_raw, list) else [])) if r is not None]
    return samples, sessions


def backup_bundle_origin(path):
    """Version d'Allure ayant produit cette sauvegarde, en texte pret a
    afficher ("inconnue" pour une sauvegarde anterieure a l'introduction du
    numero de version). Volontairement une fonction SEPAREE de
    load_backup_bundle() : ajouter un element a son tuple de retour aurait
    casse tous ses appelants, alors que cette information est facultative
    et purement informative."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return "inconnue"
    if not isinstance(data, dict):
        return "inconnue"
    return str(data.get("app_version") or "inconnue")


# =========================================================================
# Export / import des REGLAGES seuls (POLAR_SETTINGS_PATH) -- distinct de
# save_backup_bundle()/load_backup_bundle() ci-dessus, qui portent
# l'entrepot cumulatif + l'index des passes, PAS les reglages. Les deux
# s'exportent/s'importent independamment (changer de poste peut vouloir
# dire migrer l'un, l'autre, ou les deux) -- meme motivation que le
# fichier de sauvegarde portable : simplifier un changement de version ou
# d'ordinateur sans avoir a retrouver/recopier POLAR_SETTINGS_PATH a la main.
# =========================================================================

SETTINGS_BACKUP_FORMAT = "allure_settings"
SETTINGS_BACKUP_FORMAT_VERSION = 1


def export_settings(path, cfg):
    """Ecrit les reglages (config_data) a l'emplacement choisi par
    l'utilisateur -- toujours passes par fixup() avant l'ecriture, pour ne
    jamais exporter un reglage hors-plage."""
    bundle = {
        "format": SETTINGS_BACKUP_FORMAT,
        "format_version": SETTINGS_BACKUP_FORMAT_VERSION,
        "app": APP_NAME,
        "app_version": APP_VERSION,
        "exported_at": time.time(),
        "settings": fixup(cfg),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, indent=2, ensure_ascii=False)


def import_settings(path):
    """Retourne un dict de reglages nettoye (fixup(), donc toujours
    utilisable meme si le fichier est partiel) depuis un fichier produit
    par export_settings(). Leve ValueError si le fichier n'a manifestement
    pas le bon format (protection contre un import accidentel d'un fichier
    qui n'est pas un export de reglages Allure -- notamment une sauvegarde
    portable de l'ENTREPOT, format different, voir save_backup_bundle) ;
    OSError/ValueError de json.load() remontent tels quels pour un fichier
    illisible/corrompu -- a l'appelant (interface) de choisir le message
    affiche a l'utilisateur dans ce cas."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or data.get("format") != SETTINGS_BACKUP_FORMAT:
        raise ValueError("Ce fichier ne semble pas etre un export de reglages Allure valide.")
    return fixup(data.get("settings"))


# =========================================================================
# L'icone d'Allure -- le logo historique, bibliotheque standard seule
# =========================================================================
# Un disque bleu portant une voile blanche stylisee (deux pans) et sa ligne
# de flottaison : le dessin d'origine de la zone de notification (voir
# _hide_to_tray dans allure.py). Le meme sert partout -- notification,
# .exe Windows (construire_exe.bat appelle write_ico avant PyInstaller).

_ICON_CACHE = {}


def _png_encode(width, height, rows, alpha=False):
    """PNG minimal : signature, IHDR, un IDAT deflate, IEND. rows = liste
    de bytes (3 octets par pixel, ou 4 avec alpha). L'alpha sert a l'icone :
    hors du disque bleu, transparent -- le logo reste un disque sur tous les
    fonds, clairs comme sombres."""
    import struct
    import zlib

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + r for r in rows)
    ctype = 6 if alpha else 2
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def _icon_pixels(size):
    """Pixels (r, g, b, a) de l'icone d'Allure, aux cotes du dessin
    d'origine (64 x 64), mises a l'echelle. Sur-echantillonnage 3 x 3 :
    les bords du disque et de la voile restent nets aux petites tailles,
    la ou un rendu au pixel ferait des marches d'escalier."""
    import math as _m
    blue, white = (26, 95, 180), (255, 255, 255)
    # Dessin d'origine, en coordonnees 0..64 :
    disc_c, disc_r = 32.0, 30.0                       # ellipse (2,2,62,62)
    tri1 = ((34.0, 10.0), (34.0, 44.0), (16.0, 44.0))  # grand pan de voile
    tri2 = ((38.0, 16.0), (48.0, 44.0), (38.0, 44.0))  # petit pan
    line_y0, line_y1, line_x0, line_x1 = 48.5, 51.5, 14.0, 50.0

    def in_tri(px, py, tri):
        (x1, y1), (x2, y2), (x3, y3) = tri
        d1 = (px - x2) * (y1 - y2) - (x1 - x2) * (py - y2)
        d2 = (px - x3) * (y2 - y3) - (x2 - x3) * (py - y3)
        d3 = (px - x1) * (y3 - y1) - (x3 - x1) * (py - y1)
        neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (neg and pos)

    def sample(u, v):
        """Couleur (r, g, b, a) d'un point du dessin 64 x 64."""
        if _m.hypot(u - disc_c, v - disc_c) > disc_r:
            return (0, 0, 0, 0)
        if in_tri(u, v, tri1) or in_tri(u, v, tri2):
            return white + (255,)
        if line_y0 <= v <= line_y1 and line_x0 <= u <= line_x1:
            return white + (255,)
        return blue + (255,)

    scale = 64.0 / size
    offsets = (1.0 / 6.0, 0.5, 5.0 / 6.0)
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            r = g = b = a = 0
            for oy in offsets:
                for ox in offsets:
                    sr, sg, sb, sa = sample((x + ox) * scale, (y + oy) * scale)
                    r += sr * sa
                    g += sg * sa
                    b += sb * sa
                    a += sa
            if a == 0:
                row.append((0, 0, 0, 0))
            else:
                row.append((r // a, g // a, b // a, a // 9))
        rows.append(row)
    return rows


def app_icon_png(size=192):
    """Le logo d'Allure (disque bleu, voile blanche) en PNG. Calcule une
    fois par taille puis servi depuis le cache."""
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    rows = [b"".join(bytes(px) for px in row) for row in _icon_pixels(size)]
    _ICON_CACHE[size] = _png_encode(size, size, rows, alpha=True)
    return _ICON_CACHE[size]


def write_ico(path, sizes=(16, 24, 32, 48, 256)):
    """Ecrit l'icone d'Allure au format .ico de Windows -- pour le .exe
    autonome (construire_exe.bat la genere avant d'appeler PyInstaller).

    Toujours la bibliotheque standard seule : les petites tailles sont des
    DIB 32 bits (le format historique, que tout Windows sait lire), la
    grande un PNG (la forme que Vista et suivants attendent en 256)."""
    import struct

    def dib_entry(size):
        px = _icon_pixels(size)
        # BITMAPINFOHEADER : hauteur DOUBLE (image + masque), lignes de bas
        # en haut, pixels BGRA -- l'alpha du dessin est conserve, le disque
        # reste un disque. Masque ET a zero : c'est l'alpha qui gouverne.
        header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                             size * size * 4, 0, 0, 0, 0)
        body = bytearray(header)
        for row in reversed(px):
            for (r, g, b, a) in row:
                body += bytes((b, g, r, a))
        mask_row = b"\x00" * (((size + 31) // 32) * 4)
        body += mask_row * size
        return bytes(body)

    entries = []
    for size in sizes:
        data = app_icon_png(size) if size >= 256 else dib_entry(size)
        entries.append((size, data))
    offset = 6 + 16 * len(entries)
    out = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    for size, data in entries:
        out += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                           len(data), offset)
        offset += len(data)
    for _size, data in entries:
        out += data
    with open(path, "wb") as fh:
        fh.write(out)
    return path
