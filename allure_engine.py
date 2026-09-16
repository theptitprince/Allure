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
allure_engine.py
================
Logique PURE (aucune dependance UDP/Tkinter) du generateur de polaires :

- analyse du sous-ensemble de trames NMEA0183 necessaire au calcul
  (VHW pour le STW, MWV ref=T pour le TWA/TWS bow-relatif -- qui ne
  necessite PAS de cap vrai, voir note plus bas) ;
- lissage temporel des mesures + filtre de stabilite (rejet des
  echantillons pris pendant une manoeuvre) ;
- journal de configuration voile/moteur, sous forme de segments
  temporels -- pilote en direct (clic = nouveau segment) ou definis a
  la main pour annoter a posteriori une session deja enregistree ;
- un entrepot d'echantillons lisses (PolarSampleStore) qui s'accumule
  passe apres passe (une "passe" = une session enregistree, annotee,
  puis traitee) SANS pre-agreger en cases : la taille des cases TWA/TWS,
  la symetrie babord/tribord et la statistique d'agregation peuvent
  donc changer a tout moment, y compris sur des donnees deja
  accumulees, sans perte d'information ni migration.
- construction de la table de polaire (a la demande, a partir de
  l'entrepot) et export .pol (format "Expedition", largement supporte
  par les logiciels de navigation) et .csv (format long, pour tableur).

Note sur le TWA sans capteur de cap -- et pourquoi on le calcule nous
memes plutot que de faire confiance a un MWV ref='T' externe :

En theorie, le champ MWV ref='T' ("theoretical wind") est cense etre
deja calcule par l'instrument a partir du vent apparent mesure et de la
vitesse surface du bateau (loch), toutes deux exprimees dans le
referentiel du bateau (l'etrave = 0 deg par definition) -- ce qui ne
necessiterait PAS de cap compas, contrairement au calcul vectoriel
utilise ailleurs dans ce projet (wind_vector.py) qui lui recombine
cap/COG/SOG pour obtenir une direction vraie par rapport au nord.

En pratique, sur du materiel reel, cette convention n'est PAS fiable :
sur une session enregistree par l'utilisateur, une des deux sources NMEA
actives envoyait un MWV,ref=T dont l'angle restait stable autour de
270-285 deg, tres proche du cap du bateau (VTG) -- ce qui trahit une
direction de vent vrai PAR RAPPORT AU NORD (TWD), pas un angle par
rapport a l'etrave (TWA), malgre le ref='T'. Utilisee telle quelle,
cette valeur produisait des polaires ou une allure de pres ressemblait
a un travers. Plutot que de deviner au cas par cas si un MWV,T donne
est fiable, ce module calcule desormais LUI-MEME le TWA/TWS a partir du
vent apparent (MWV ref='R', toujours boat-relatif par definition -- pas
d'ambiguite possible) et de la vitesse surface (VHW) : voir
apparent_to_true() plus bas. Toute trame MWV,ref='T' est ignoree.

Concu pour etre teste independamment de toute interface graphique.
"""

import calendar
import csv
import io
import math
import re
import threading
import time
from collections import defaultdict, deque

# =========================================================================
# Analyse NMEA (sous-ensemble necessaire)
# =========================================================================

_SENTENCE_RE = re.compile(r"^\$([A-Za-z]{2})([A-Za-z]{3}),(.*?)(\*[0-9A-Fa-f]{2})?$")
# Trames proprietaires : "$P" suivi du code constructeur et du nom de trame,
# d'un seul tenant (PEUMA, PGRME, PSRDB...). Aucun decoupage talker/type
# n'a de sens ici -- voir parse_sentence().
_PROPRIETARY_RE = re.compile(r"^\$(P[A-Za-z0-9]{2,9}),(.*?)(\*[0-9A-Fa-f]{2})?$")


def checksum_ok(raw):
    """True/False si un checksum est present et verifiable, None si absent/illisible."""
    if "*" not in raw:
        return None
    body, _, cksum = raw.partition("*")
    body = body[1:]
    try:
        expected = int(cksum.strip()[:2], 16)
    except (ValueError, IndexError):
        return None
    computed = 0
    for c in body:
        computed ^= ord(c)
    return computed == expected


def _get(f, i):
    return f[i] if i < len(f) and f[i] not in (None, "") else None


def _speed_to_knots(value, unit):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    u = (unit or "N").strip().upper()
    if u == "K":
        return v / 1.852
    if u == "M":
        return v * 1.943844
    return v  # "N" (noeuds) ou unite inconnue : on suppose deja en noeuds


def parse_sentence(raw):
    """
    Retourne (talker, sentence_type, fields, checksum_valide) ou None si
    la ligne n'est pas syntaxiquement une trame NMEA. fields[0] ==
    sentence_type, fields[1:] == champs (1-based comme les autres
    programmes du projet). checksum_valide vaut None si aucun checksum
    n'etait present dans la ligne.
    """
    raw = raw.strip()
    if not raw.startswith("$"):
        return None
    ck = checksum_ok(raw)
    # Trames PROPRIETAIRES ($P + code constructeur) testees EN PREMIER : la
    # regle generale "2 lettres de talker + 3 lettres de type" les decoupe a
    # tort ($PEUMA y devient talker 'PE', type 'UMA'), et le tableau des
    # voies affichait alors une trame "UMA" que personne ne peut
    # reconnaitre. Une trame proprietaire n'a pas de talker separable : son
    # identite, c'est le mot entier.
    mp = _PROPRIETARY_RE.match(raw)
    if mp:
        sentence_type, body, _ck = mp.groups()
        sentence_type = sentence_type.upper()
        return "P", sentence_type, [sentence_type] + body.split(","), ck
    m = _SENTENCE_RE.match(raw)
    if not m:
        return None
    talker, sentence_type, body, _ck = m.groups()
    fields = [sentence_type.upper()] + body.split(",")
    return talker.upper(), sentence_type.upper(), fields, ck


def extract_stw(fields):
    """VHW champ 5 (noeuds) ; repli champ 7 (km/h) si le champ 5 est absent."""
    v = _get(fields, 5)
    if v is not None:
        try:
            return float(v)
        except ValueError:
            return None
    v = _get(fields, 7)
    if v is not None:
        return _speed_to_knots(v, "K")
    return None



# =========================================================================
# $PEUMA -- station meteo/navigation proprietaire (celle du bord)
# =========================================================================
# Trame non normalisee : sa structure a ete etablie par recoupement sur une
# capture reelle du bord, chaque champ etant valide contre une trame
# normalisee qui porte la meme grandeur :
#
#   champ  3/4   latitude / longitude en degres DECIMAUX signes (et non au
#                format ddmm.mmmm des trames GGA/RMC)
#   champ  5     route fond : 0,4 deg d'ecart median avec le COG des VTG
#   champ  6     vitesse fond en METRES PAR SECONDE : rapport 1,95 avec le
#                SOG des VTG, soit exactement le facteur noeud/(m/s)
#   champ  8     pression au niveau de la STATION (toujours < champ 9)
#   champ  9     pression reduite au niveau de la MER
#   champ 10     temperature de l'air (identique aux XDR AirTemperature)
#   champ 11     humidite relative (identique aux XDR RelativeHumidity)
#   champ 14/15/16  vent VRAI moyen / rafale / mini, en NOEUDS (accorde a
#                une dizaine de pour cent pres avec le TWS recalcule par
#                l'application, et invariant mini <= moyen <= rafale verifie
#                sur 632 trames sur 635)
#   champ 17     direction du vent VRAI par rapport au nord
#
# Le vent de cette station est donc un vent VRAI REFERENCE AU NORD : il sert
# de reference et de recoupement, jamais de source de TWA -- un angle au
# bateau demanderait un cap compas instantane que l'installation ne fournit
# pas (voir la note en tete de module).
PEUMA_FIELD_COUNT = 21          # '$PEUMA' + 20 champs, trame complete
_MS_TO_KN = 1.9438444924406047


def extract_peuma(fields):
    """Grandeurs portees par une trame $PEUMA -> dict, {} si la trame n'est
    pas exploitable.

    Le tampon d'une passerelle reelle contient toujours des trames
    TRONQUEES (coupure reseau en plein milieu d'une ligne), et certaines
    passent le controle de somme par hasard une fois recollees a la
    suivante. Une trame proprietaire n'ayant aucun marqueur de champ, une
    troncature y decale silencieusement TOUT le contenu : la pression
    deviendrait une vitesse. D'ou la double garde -- longueur canonique
    exigee, puis plausibilite champ par champ (meme constat que
    AMBIENT_BOUNDS, et pour la meme raison)."""
    if len(fields) != PEUMA_FIELD_COUNT:
        return {}
    # Date (AAAAMMJJ) et heure (HHMMSS) en tete : deux champs de forme
    # stricte, qui suffisent a reconnaitre une trame correctement alignee.
    if not (_get(fields, 1) or "").isdigit() or len(_get(fields, 1) or "") != 8:
        return {}
    if not (_get(fields, 2) or "").isdigit() or len(_get(fields, 2) or "") != 6:
        return {}
    out = {}

    def num(idx, lo, hi, key=None, factor=1.0):
        raw = _get(fields, idx)
        if not raw:
            return
        try:
            v = float(raw) * factor
        except ValueError:
            return
        if v != v or not (lo <= v <= hi):
            return
        out[key] = v

    num(3, -90.0, 90.0, "lat")
    num(4, -180.0, 180.0, "lon")
    num(5, 0.0, 360.0, "cog")
    num(6, 0.0, 80.0, "sog", factor=_MS_TO_KN)
    num(8, 800.0, 1100.0, "pressure_station")
    num(9, 800.0, 1100.0, "pressure")
    num(10, -60.0, 70.0, "air_temp")
    num(11, 0.0, 100.0, "humidity")
    num(14, 0.0, 200.0, "wind_true_kn")
    num(15, 0.0, 250.0, "wind_gust_kn")
    num(16, 0.0, 200.0, "wind_lull_kn")
    num(17, 0.0, 360.0, "wind_dir_deg")
    # Le triplet moyen/rafale/mini n'a de sens qu'ordonne : s'il ne l'est
    # pas, c'est que la trame est decalee malgre tout, et le vent entier est
    # ecarte plutot que d'en garder une partie fausse.
    trio = [out.get(k) for k in ("wind_lull_kn", "wind_true_kn", "wind_gust_kn")]
    if all(v is not None for v in trio) and not (trio[0] <= trio[1] <= trio[2]):
        for k in ("wind_lull_kn", "wind_true_kn", "wind_gust_kn", "wind_dir_deg"):
            out.pop(k, None)
    return out


def extract_sog(fields):
    """VTG champ 5 (vitesse fond/SOG, noeuds) ; repli champ 7 (km/h) si le
    champ 5 est absent -- meme convention de champs que _parse_vtg() dans
    nmea_parser.py ($--VTG,cogt,T,cogm,M,sogn,N,sogk,K*hh). Utilise
    uniquement par la consultation des archives (voir App._replay_instant) :
    le reste de ce module n'a jamais besoin de la vitesse fond, seulement de
    la vitesse surface (STW, voir extract_stw)."""
    v = _get(fields, 5)
    if v is not None:
        try:
            return float(v)
        except ValueError:
            return None
    v = _get(fields, 7)
    if v is not None:
        return _speed_to_knots(v, "K")
    return None


def extract_mwv(fields):
    """
    Retourne (ref, twa_signe, vitesse_noeuds) ou None si la trame est
    incomplete/invalide.
    twa_signe : -180..+180, 0 = vent dans le nez, +/-180 = vent arriere,
    positif = tribord, negatif = babord (meme convention que le cadran
    de vent apparent du programme d'origine).
    """
    angle = _get(fields, 1)
    ref = _get(fields, 2)
    speed = _get(fields, 3)
    unit = _get(fields, 4)
    status = _get(fields, 5)
    if angle is None or ref is None or speed is None:
        return None
    if status is not None and status.strip().upper() == "V":
        return None
    try:
        angle_v = float(angle) % 360.0
    except ValueError:
        return None
    signed = angle_v if angle_v <= 180.0 else angle_v - 360.0
    speed_kn = _speed_to_knots(speed, unit)
    if speed_kn is None:
        return None
    return ref.strip().upper(), signed, speed_kn


def apparent_to_true(awa_signed_deg, aws_kn, stw_kn):
    """
    Vent vrai (angle signe bord-relatif, vitesse en noeuds) recalcule a
    partir du vent apparent (awa_signed_deg/aws_kn, MEME convention que
    extract_mwv : -180..+180, 0 = etrave, positif = tribord) et de la
    vitesse surface (STW, loch) -- "triangle des vents" classique,
    ENTIEREMENT dans le referentiel du bateau : pas besoin de cap compas
    ni de COG/SOG, contrairement au calcul "vent vrai / vrai nord" de
    wind_vector.py (qui repond a un besoin different -- afficher une
    direction absolue -- et a donc besoin d'un referentiel terrestre).

    Retourne (twa_signe, tws_noeuds), ou (None, None) si une donnee
    necessaire est absente.

    Methode : dans le referentiel du bateau (axe des x = etrave, axe des
    y = tribord), le vecteur vent apparent a pour composantes
    (aws*cos(awa), aws*sin(awa)). Le deplacement propre du bateau (droit
    devant, norme STW, par definition de ce referentiel) se retranche de
    ce vecteur pour obtenir le vecteur vent vrai -- c'est la formule
    standard utilisee par les centrales de navigation pour calculer un
    TWA embarque sans capteur de cap.
    """
    if awa_signed_deg is None or aws_kn is None or stw_kn is None:
        return None, None
    awa_rad = math.radians(awa_signed_deg)
    tx = aws_kn * math.cos(awa_rad) - stw_kn
    ty = aws_kn * math.sin(awa_rad)
    twa = math.degrees(math.atan2(ty, tx))
    tws = math.hypot(tx, ty)
    return twa, tws


# =========================================================================
# Inventaire des voies : que trouve-t-on reellement sur chaque port ?
#
# Sur une passerelle reelle, on ne sait pas a l'avance quelle voie porte
# quoi -- et surtout, deux voies peuvent porter le MEME type de trame avec
# des contenus differents (deux girouettes desaccordees, l'une en noeuds
# l'autre en m/s). Plutot que de le deviner, on ECOUTE et on dresse la
# liste : par voie et par type de trame, la cadence, les unites vues, la
# reference (R/T pour le vent) et un exemple. C'est ce que la page
# Acquisition donne a lire avant de choisir ses sources.
# =========================================================================

# Types de trames que l'application sait EXPLOITER, et la grandeur qu'ils
# apportent. Le reste est inventorie aussi, mais marque comme non utilise :
# savoir qu'une voie envoie de la temperature n'aide pas a faire une
# polaire, mais aide a comprendre ce qu'on a sous la main.
SENTENCE_ROLES = {
    "MWV": "Vent apparent (angle + force)",
    "VHW": "Vitesse surface (loch)",
    "VTG": "Vitesse fond + route (GPS)",
    "GGA": "Position (GPS)",
    "RMC": "Position + vitesse fond (GPS)",
    "MTW": "Temperature de l'eau",
    "XDR": "Capteurs divers (pression, temperature...)",
    "PEUMA": "Station meteo du bord : pression, air, humidite, vent vrai, "
             "route + vitesse fond",
    "ZDA": "Date et heure",
    "DTM": "Datum geodesique",
    "HBT": "Battement de coeur (supervision)",
    "UMA": "Trame proprietaire (meteo/navigation)",
}

# Grandeurs dont l'application a besoin, et le type de trame qui les porte.
# C'est la liste des choix offerts par la page Acquisition.
MEASUREMENT_SOURCES = (
    ("MWV", "Vent apparent"),
    ("VHW", "Vitesse surface"),
    ("VTG", "Vitesse fond"),
)

# Trames qui portent la MEME grandeur qu'une autre sous un nom different.
# Une grandeur n'appartient pas a un type de trame : elle appartient a ce
# qui la mesure. La station du bord ($PEUMA) donne une route et une vitesse
# fond exactement comme une VTG -- elle CONCOURT donc avec les VTG pour la
# grandeur "Vitesse fond", plutot que de former une categorie a part que
# l'utilisateur ne pourrait jamais choisir.
#
# Cette equivalence avait ete retiree apres le blocage du vent en v1.0g,
# par prudence. La vraie cause etait ailleurs (une source designee qui
# n'apportait rien, voir _accept_for_priority), et deux garde-fous la
# rendent desormais sans danger :
#   - une voie ne compte comme source que si elle FOURNIT la grandeur, donc
#     une PEUMA tronquee ou muette n'evince aucun GPS ;
#   - la vitesse fond ne construit AUCUNE polaire : elle sert a l'affichage,
#     au recoupement STW/SOG et a la detection de manoeuvre. Le risque
#     d'une trame proprietaire y est donc borne -- ce qui ne serait pas le
#     cas pour le vent ou la vitesse surface, qui restent, eux, reserves
#     aux trames normalisees.
SENTENCE_MEASURES = {"PEUMA": "VTG"}


def measure_of(styp):
    """Grandeur (au sens de MEASUREMENT_SOURCES) que porte ce type de trame."""
    return SENTENCE_MEASURES.get(styp, styp)

_UNIT_LABELS = {"N": "noeuds", "K": "km/h", "M": "m/s", "S": "mph",
                "T": "vrai", "M_DEG": "magnetique", "C": "degres C", "F": "degres F"}


# Grandeurs "d'ambiance" qu'une passerelle porte souvent et qu'on peut
# vouloir relire a posteriori (journal passerelle, contexte d'une mesure) :
# elles n'entrent dans AUCUN calcul de polaire, mais elles racontent les
# conditions. Chaque entree dit ou la lire et comment l'afficher.
#
# La liste est un CATALOGUE, pas une promesse : l'interface ne propose que
# ce que l'analyse des voies a reellement trouve sur l'installation.
AMBIENT_FIELDS = (
    # cle          libelle                        unite     decimales
    ("pressure",   "Pression atmospherique",      "hPa",    1),
    ("air_temp",   "Temperature de l'air",        "deg C",  1),
    ("water_temp", "Temperature de l'eau",        "deg C",  1),
    ("humidity",   "Humidite relative",           "%",      0),
    ("sog",        "Vitesse fond (SOG)",          "kn",     2),
    ("cog",        "Route fond (COG)",            "deg",    0),
    ("heading",    "Cap",                         "deg",    0),
    ("depth",      "Profondeur sous la sonde",    "m",      1),
    # Vent VRAI mesure par la station meteo du bord ($PEUMA) : une donnee
    # d'ambiance, pas une entree de la polaire -- sa direction est
    # referencee au NORD et non a l'etrave (voir la note en tete de module).
    ("wind_true_kn", "Vent vrai (station)",       "kn",     1),
    ("wind_gust_kn", "Rafale (station)",          "kn",     1),
    ("wind_lull_kn", "Vent mini (station)",       "kn",     1),
    ("wind_dir_deg", "Direction du vent vrai",    "deg",    0),
    ("pressure_station", "Pression a la station", "hPa",    1),
)
AMBIENT_LABELS = {k: (lbl, unit, dec) for k, lbl, unit, dec in AMBIENT_FIELDS}

# Bornes de PLAUSIBILITE physique. Une valeur qui en sort n'est pas une
# mesure extreme, c'est une trame mal formee ou mal interpretee : sur une
# capture reelle on trouve ainsi des "7026 noeuds" de vitesse fond, issus
# d'une trame tronquee. Les ecarter en silence vaut mieux que de laisser une
# seule ligne abimee ruiner une moyenne -- et bien mieux que d'afficher une
# valeur absurde en pretendant l'avoir mesuree.
AMBIENT_BOUNDS = {
    "pressure": (800.0, 1100.0),
    "air_temp": (-60.0, 70.0),
    "water_temp": (-5.0, 45.0),
    "humidity": (0.0, 100.0),
    "sog": (0.0, 80.0),
    "cog": (0.0, 360.0),
    "heading": (0.0, 360.0),
    "depth": (0.0, 2000.0),
    "wind_true_kn": (0.0, 200.0),
    "wind_gust_kn": (0.0, 250.0),
    "wind_lull_kn": (0.0, 200.0),
    "wind_dir_deg": (0.0, 360.0),
    "pressure_station": (800.0, 1100.0),
}
# Grandeurs ANGULAIRES : moyennees circulairement, et sans min/max (l'ecart
# entre 359 et 1 vaut 2 degres, pas 358).
AMBIENT_ANGLES = ("cog", "heading", "wind_dir_deg")


def _ambient_plausible(key, value):
    lo, hi = AMBIENT_BOUNDS.get(key, (float("-inf"), float("inf")))
    return value == value and lo <= value <= hi   # value == value ecarte NaN


def extract_ambient(styp, fields):
    """Grandeurs d'ambiance portees par une trame : {cle: valeur}.

    Volontairement tolerant -- une passerelle reelle melange les conventions
    (temperature en XDR chez l'un, en MTW chez l'autre, pression en bars ou
    en hectopascals) et un champ absent ou illisible doit simplement ne rien
    rapporter, jamais interrompre une lecture."""
    out = {}
    try:
        if styp == "MTW":
            v = _get(fields, 1)
            unit = (_get(fields, 2) or "C").strip().upper()
            if v:
                t = float(v)
                out["water_temp"] = (t - 32.0) * 5.0 / 9.0 if unit == "F" else t
        elif styp == "MTA":
            v = _get(fields, 1)
            if v:
                out["air_temp"] = float(v)
        elif styp == "MMB":
            # Pression en bars (champ 3) ou en pouces de mercure (champ 1).
            bars = _get(fields, 3)
            if bars:
                out["pressure"] = float(bars) * 1000.0
        elif styp == "MDA":
            bars = _get(fields, 3)
            if bars:
                out["pressure"] = float(bars) * 1000.0
            at = _get(fields, 5)
            if at:
                out["air_temp"] = float(at)
            wt = _get(fields, 7)
            if wt:
                out["water_temp"] = float(wt)
            hum = _get(fields, 9)
            if hum:
                out["humidity"] = float(hum)
        elif styp == "VTG":
            cog = _get(fields, 1)
            if cog:
                out["cog"] = float(cog) % 360.0
            sog = extract_sog(fields)
            if sog is not None:
                out["sog"] = sog
        elif styp == "PEUMA":
            # La station du bord porte a elle seule presque toute la fiche :
            # sur l'installation de reference, c'est meme la SEULE source de
            # pression (aucune MDA/MMB n'y circule).
            peu = extract_peuma(fields)
            for key in ("pressure", "pressure_station", "air_temp", "humidity",
                        "cog", "sog", "wind_true_kn", "wind_gust_kn",
                        "wind_lull_kn", "wind_dir_deg"):
                if peu.get(key) is not None:
                    out[key] = peu[key]
        elif styp in ("HDG", "HDT", "HDM"):
            h = _get(fields, 1)
            if h:
                out["heading"] = float(h) % 360.0
        elif styp in ("DPT", "DBT"):
            d = _get(fields, 1)
            if d:
                out["depth"] = float(d)
        elif styp == "XDR":
            # Suites de quadruplets (type, valeur, unite, nom). Le nom est
            # libre : on se fie au TYPE et a l'UNITE, seuls elements
            # normalises.
            groups = fields[1:]
            for i in range(0, max(0, len(groups) - 3), 4):
                typ = (groups[i] or "").strip().upper()
                val = groups[i + 1]
                unit = (groups[i + 2] or "").strip().upper()
                if not val:
                    continue
                try:
                    v = float(val)
                except ValueError:
                    continue
                if typ == "P":
                    # Pascals chez la plupart, bars chez certains.
                    out["pressure"] = v / 100.0 if unit == "P" else (
                        v * 1000.0 if unit == "B" else v)
                elif typ == "C":
                    out.setdefault("air_temp", v)
                elif typ == "H":
                    out["humidity"] = v
                elif typ == "A":
                    out.setdefault("heading", v % 360.0)
    except (TypeError, ValueError, IndexError):
        return {}
    return out


def ambient_over_range(lines, keys=None):
    """Moyenne de chaque grandeur d'ambiance sur une plage de trames.

    lines : iterable de (instant, voie, trame). keys : grandeurs voulues
    (toutes si None). Retourne {cle: {"mean","min","max","n"}} -- seules les
    grandeurs REELLEMENT rencontrees figurent au resultat : on n'affiche
    jamais une case vide en pretendant l'avoir mesuree.

    Les angles (cap, route) sont moyennes CIRCULAIREMENT : la moyenne
    arithmetique de 359 et 1 donnerait 180, soit exactement l'oppose."""
    acc = {}
    ang = {}
    wanted = set(keys) if keys else None
    for t, _port, raw in lines:
        parsed = parse_sentence(raw)
        if parsed is None:
            continue
        _talker, styp, fields, ck = parsed
        if ck is False:
            continue
        for key, val in extract_ambient(styp, fields).items():
            if wanted is not None and key not in wanted:
                continue
            if not _ambient_plausible(key, val):
                continue
            if key in AMBIENT_ANGLES:
                a = ang.setdefault(key, {"x": 0.0, "y": 0.0, "n": 0})
                r = math.radians(val)
                a["x"] += math.cos(r)
                a["y"] += math.sin(r)
                a["n"] += 1
            e = acc.setdefault(key, {"sum": 0.0, "n": 0, "min": val, "max": val})
            e["sum"] += val
            e["n"] += 1
            e["min"] = min(e["min"], val)
            e["max"] = max(e["max"], val)
    out = {}
    for key, e in acc.items():
        if not e["n"]:
            continue
        if key in ang and ang[key]["n"]:
            mean = math.degrees(math.atan2(ang[key]["y"], ang[key]["x"])) % 360.0
        else:
            mean = e["sum"] / e["n"]
        out[key] = {"mean": mean, "min": e["min"], "max": e["max"], "n": e["n"]}
    return out


def format_ambient(key, stats):
    """Ligne lisible pour une grandeur d'ambiance, unite comprise."""
    label, unit, dec = AMBIENT_LABELS.get(key, (key, "", 1))
    mean = stats["mean"]
    spread = ""
    # Pas d'etendue pour un angle : min/max n'y veulent rien dire (une route
    # qui oscille autour du nord afficherait "de 1 a 359").
    if key not in AMBIENT_ANGLES and stats["n"] > 1 and \
            (stats["max"] - stats["min"]) > 10 ** (-dec):
        spread = f"  (de {stats['min']:.{dec}f} a {stats['max']:.{dec}f})"
    return f"{label} : {mean:.{dec}f} {unit}{spread}   [{stats['n']} mesure(s)]"


def scan_nmea_sources(lines, now=None):
    """Inventaire de ce que porte chaque voie, a partir d'un iterable de
    (instant, voie, trame brute) -- typiquement le tampon glissant sur une
    plage, ou un fichier de session rejoue.

    Retourne {(voie, type): dict} avec :
      count      nombre de trames vues
      first/last instants extremes
      hz         cadence moyenne (trames par seconde), None si < 2 trames
      period_s   intervalle median entre deux trames, None si < 2 trames
      units      unites rencontrees, deja traduites ("noeuds", "m/s"...)
      refs       references rencontrees pour le vent ("R" apparent, "T" vrai)
      role       ce que la trame apporte, ou None si l'appli ne l'exploite pas
      sample     un exemple de trame, tel quel
      value      derniere valeur lisible, en clair, quand l'appli sait la lire

    Les lignes illisibles sont ignorees en silence : un tampon reel contient
    toujours quelques trames tronquees (coupure reseau au milieu d'une
    ligne), et elles ne doivent pas fausser l'inventaire ni le faire echouer.
    """
    acc = {}
    for t, port, raw in lines:
        parsed = parse_sentence(raw)
        if parsed is None:
            continue
        _talker, styp, fields, ck = parsed
        if ck is False:
            continue
        key = (port, styp)
        e = acc.get(key)
        if e is None:
            e = acc[key] = {"count": 0, "first": t, "last": t, "gaps": [],
                            "units": [], "refs": [], "sample": raw,
                            "role": SENTENCE_ROLES.get(styp), "value": None,
                            "measure": measure_of(styp) if measure_of(styp) in
                            {k for k, _l in MEASUREMENT_SOURCES} else None,
                            "ambient": [],
                            "usable_count": 0, "_usable_first": None, "_usable_last": None,
                            "_prev_t": None}
        e["count"] += 1
        e["last"] = t
        e["sample"] = raw
        if e["_prev_t"] is not None:
            gap = t - e["_prev_t"]
            if 0 < gap < 3600:
                e["gaps"].append(gap)
        e["_prev_t"] = t

        # Cadence EXPLOITABLE, comptee a part : une voie peut emettre du MWV
        # deux fois par seconde en n'y mettant du vent apparent qu'une fois
        # sur deux (l'autre trame portant un vent "vrai" inexploitable). La
        # cadence brute la ferait alors passer pour deux fois plus rapide
        # qu'elle ne l'est reellement pour ce qui nous interesse -- et c'est
        # precisement cette cadence-la qui decide du nombre d'echantillons.
        usable = False

        if styp == "MWV":
            ref = _get(fields, 2)
            unit = _get(fields, 4)
            if ref:
                ref = ref.strip().upper()
                if ref in ("R", "T") and ref not in e["refs"]:
                    e["refs"].append(ref)
            if unit:
                lbl = _UNIT_LABELS.get(unit.strip().upper())
                if lbl and lbl not in e["units"]:
                    e["units"].append(lbl)
            mwv = extract_mwv(fields)
            if mwv is not None:
                kind = "apparent" if mwv[0] == "R" else "vrai(*)"
                e["value"] = f"{mwv[1]:+.0f} deg / {mwv[2]:.1f} kn ({kind})"
                usable = (mwv[0] == "R")
        elif styp == "VHW":
            stw = extract_stw(fields)
            if stw is not None:
                e["value"] = f"{stw:.2f} kn"
                usable = True
                if "noeuds" not in e["units"]:
                    e["units"].append("noeuds")
        elif styp == "VTG":
            sog = extract_sog(fields)
            if sog is not None:
                e["value"] = f"{sog:.2f} kn"
                usable = True
                if "noeuds" not in e["units"]:
                    e["units"].append("noeuds")
        elif styp == "PEUMA":
            peu = extract_peuma(fields)
            if peu.get("sog") is not None or peu.get("cog") is not None:
                # Exploitable comme VITESSE FOND, au meme titre qu'une VTG :
                # c'est cette ligne-la que l'on peut designer comme source.
                usable = True
                bits = []
                if peu.get("cog") is not None:
                    bits.append(f"{peu['cog']:.0f} deg")
                if peu.get("sog") is not None:
                    bits.append(f"{peu['sog']:.2f} kn")
                if peu.get("pressure") is not None:
                    bits.append(f"{peu['pressure']:.1f} hPa")
                if peu.get("wind_true_kn") is not None:
                    bits.append(f"vent {peu['wind_true_kn']:.1f} kn")
                e["value"] = "  ".join(bits)
                for u in ("noeuds", "m/s (converti)"):
                    if u not in e["units"]:
                        e["units"].append(u)
        elif styp in ("GGA", "RMC"):
            pos = (parse_nmea_latlon(_get(fields, 2), _get(fields, 3),
                                      _get(fields, 4), _get(fields, 5)) if styp == "GGA"
                   else parse_nmea_latlon(_get(fields, 3), _get(fields, 4),
                                           _get(fields, 5), _get(fields, 6)))
            if pos is not None:
                e["value"] = f"{pos[0]:+.4f} / {pos[1]:+.4f}"

        # Grandeurs d'AMBIANCE que cette trame apporte (celles de la fiche
        # d'une minute archivee). Une trame peut n'entrer dans aucune
        # polaire et rester precieuse : la temperature et l'humidite des
        # XDR, la pression de la station... Les annoncer evite le verdict
        # "non exploitee par l'application", qui etait faux.
        for akey in extract_ambient(styp, fields):
            if akey not in e["ambient"]:
                e["ambient"].append(akey)

        if usable:
            e["usable_count"] += 1
            if e["_usable_first"] is None:
                e["_usable_first"] = t
            e["_usable_last"] = t

    for e in acc.values():
        span = e["last"] - e["first"]
        e["hz"] = (e["count"] - 1) / span if e["count"] > 1 and span > 0 else None
        gaps = sorted(e["gaps"])
        e["period_s"] = gaps[len(gaps) // 2] if gaps else None
        uspan = ((e["_usable_last"] - e["_usable_first"])
                 if e["_usable_first"] is not None else 0.0)
        e["usable_hz"] = ((e["usable_count"] - 1) / uspan
                          if e["usable_count"] > 1 and uspan > 0 else None)
        del e["gaps"], e["_prev_t"], e["_usable_first"], e["_usable_last"]
    return acc


def true_to_apparent(twa_signed_deg, tws_kn, stw_kn):
    """Inverse EXACTE de apparent_to_true() : retrouve le vent apparent
    (angle signe bord-relatif, vitesse en noeuds) a partir du vent vrai et
    de la vitesse surface. Meme triangle, parcouru dans l'autre sens : on
    RAJOUTE le deplacement propre du bateau au lieu de le retrancher.

    Sert a reafficher AWA/AWS pour une mesure archivee dont le fichier brut
    n'existe plus : l'entrepot conserve TWA/TWS/STW, et ces trois grandeurs
    suffisent a reconstituer le vent apparent sans aucune perte -- il n'y a
    donc jamais besoin de garder le journal brut rien que pour cela.

    Retourne (awa_signe, aws_noeuds), ou (None, None) si une donnee manque.
    """
    if twa_signed_deg is None or tws_kn is None or stw_kn is None:
        return None, None
    twa_rad = math.radians(twa_signed_deg)
    ax = tws_kn * math.cos(twa_rad) + stw_kn
    ay = tws_kn * math.sin(twa_rad)
    return math.degrees(math.atan2(ay, ax)), math.hypot(ax, ay)


def parse_nmea_latlon(lat_s, ns, lon_s, ew):
    """Position depuis les champs NMEA classiques (ddmm.mmmm / dddmm.mmmm +
    hemisphere) -> (lat_deg, lon_deg) signes (nord/est positifs), ou None si
    les champs sont absents/invalides. Sert aux trames GGA (champs 2-5) et
    RMC (champs 3-6), qui partagent exactement ce format."""
    if not lat_s or not lon_s or not ns or not ew:
        return None
    try:
        lat_raw, lon_raw = float(lat_s), float(lon_s)
        lat = int(lat_raw / 100) + (lat_raw % 100) / 60.0
        lon = int(lon_raw / 100) + (lon_raw % 100) / 60.0
    except (TypeError, ValueError):
        return None
    ns, ew = ns.strip().upper(), ew.strip().upper()
    if ns == "S":
        lat = -lat
    elif ns != "N":
        return None
    if ew == "W":
        lon = -lon
    elif ew != "E":
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


# =========================================================================
# Lever / coucher du soleil -- sert UNIQUEMENT au theme automatique de
# l'interface (clair le jour, sombre la nuit) : aucune donnee de polaire
# n'en depend. Algorithme NOAA classique (equation du temps + declinaison),
# precis a 1-2 minutes pres, largement suffisant pour changer un theme.
# =========================================================================

def sun_times(lat_deg, lon_deg, epoch):
    """Lever et coucher du soleil pour le JOUR UTC contenant 'epoch', a la
    position donnee (degres signes, est positif). Retourne un dict :
      {"state": "normal", "sunrise": epoch_utc, "sunset": epoch_utc}
      {"state": "jour_polaire", ...} soleil jamais couche ce jour-la
      {"state": "nuit_polaire", ...} soleil jamais leve ce jour-la
    (dans les deux cas polaires, sunrise/sunset valent None)."""
    gm = time.gmtime(epoch)
    day_of_year = gm.tm_yday
    midnight = calendar.timegm((gm.tm_year, gm.tm_mon, gm.tm_mday, 0, 0, 0))
    # Angle de l'annee (rad), pris a midi pour representer le jour entier.
    gamma = 2.0 * math.pi / 365.0 * (day_of_year - 1 + 0.5)
    # Equation du temps (minutes) et declinaison solaire (rad) -- NOAA.
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma) - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma) - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma) + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma) + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma) + 0.00148 * math.sin(3 * gamma))
    lat = math.radians(lat_deg)
    # Angle horaire du lever/coucher, zenith 90.833 deg (refraction + demi-
    # diametre apparent du soleil -- la convention "bord superieur affleure
    # l'horizon" de tous les almanachs).
    cos_ha = (math.cos(math.radians(90.833)) / (math.cos(lat) * math.cos(decl))
              - math.tan(lat) * math.tan(decl))
    if cos_ha > 1.0:
        return {"state": "nuit_polaire", "sunrise": None, "sunset": None}
    if cos_ha < -1.0:
        return {"state": "jour_polaire", "sunrise": None, "sunset": None}
    ha_deg = math.degrees(math.acos(cos_ha))
    sunrise_min = 720.0 - 4.0 * (lon_deg + ha_deg) - eqtime
    sunset_min = 720.0 - 4.0 * (lon_deg - ha_deg) - eqtime
    return {"state": "normal",
            "sunrise": midnight + sunrise_min * 60.0,
            "sunset": midnight + sunset_min * 60.0}


def is_daytime(lat_deg, lon_deg, epoch):
    """True si le soleil est leve a cet instant et cette position. Les jours
    UTC voisins sont aussi examines : aux longitudes eloignees de Greenwich,
    le lever/coucher pertinent pour 'epoch' peut appartenir au jour UTC
    precedent ou suivant."""
    for day_shift in (-1, 0, 1):
        st = sun_times(lat_deg, lon_deg, epoch + day_shift * 86400.0)
        if st["state"] == "normal" and st["sunrise"] <= epoch < st["sunset"]:
            return True
    st = sun_times(lat_deg, lon_deg, epoch)
    return st["state"] == "jour_polaire"


# =========================================================================
# Lissage temporel + filtre de manoeuvre
# =========================================================================

def _circular_diff(a, b):
    """Plus petite difference signee entre deux angles (deg) -- resultat dans -180..180."""
    return ((a - b + 180.0) % 360.0) - 180.0


class ManeuverWatch:
    """Detection de manoeuvre pour l'ARRET AUTOMATIQUE d'une prise, fondee
    sur la ROUTE FOND et la VITESSE FOND (trames VTG) -- et non sur le vent :
    un virement se voit d'abord au cap qui tourne et a la vitesse qui chute,
    deux grandeurs GPS insensibles aux sautes de la girouette.

    A ne pas confondre avec le filtre de manoeuvre du SteadyStateSmoother :
    celui-la ECARTE des echantillons douteux (TWA/STW sur la fenetre de
    lissage), sans rien dire a personne ; celui-ci propose d'ARRETER la
    prise, et pose la question a l'equipage. Les deux ont leurs propres
    seuils, regles au meme endroit (Parametres > Manoeuvres) mais distincts.

    Logique volontairement PURE (aucune notion d'interface) :
      add(t, cog, sog)  -- alimente la fenetre glissante
      check(now)        -- None, ou {"t", "cause", ...} si manoeuvre ;
                           respecte le rearmement (cooldown)
      status(now)       -- etat lisible, sans effet de bord, pour affichage
      snooze(until)     -- repousse toute detection (apres un 'non, fausse
                           alerte', ou pendant qu'une question est posee)

    Garde-fous appris des instruments reels :
      - la route fond d'un bateau LENT est du bruit (au mouillage, le GPS
        fait tourner le COG sur place) : sous min_sog_kn, la fenetre de
        route est ignoree ;
      - l'ecart de route est CIRCULAIRE (359 et 1 different de 2 degres,
        pas de 358) ;
      - rien ne se declenche tant que la fenetre n'est pas suffisamment
        remplie : les premieres secondes d'une prise ne sont pas une
        manoeuvre.
    """

    def __init__(self, window_s=45.0, cog_threshold_deg=30.0,
                 sog_threshold_frac=0.35, cooldown_s=120.0, min_sog_kn=1.5,
                 min_fill_frac=0.5):
        self.window_s = float(window_s)
        self.cog_threshold_deg = float(cog_threshold_deg)
        self.sog_threshold_frac = float(sog_threshold_frac)
        self.cooldown_s = float(cooldown_s)
        self.min_sog_kn = float(min_sog_kn)
        self.min_fill_frac = float(min_fill_frac)
        self._cog = deque()   # (t, deg)
        self._sog = deque()   # (t, kn)
        self._snooze_until = 0.0

    def add(self, t, cog=None, sog=None):
        if cog is not None:
            self._cog.append((t, cog % 360.0))
        if sog is not None:
            self._sog.append((t, sog))
        cutoff = t - self.window_s
        for dq in (self._cog, self._sog):
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def snooze(self, until):
        self._snooze_until = max(self._snooze_until, until)

    def reset(self, t=None):
        """Vide la fenetre et rearme. Appele apres un CHANGEMENT D'HEURE du
        bord : des caps horodates dans le futur fausseraient l'etendue de
        route, et un rearmement (_snooze_until) posterieur a la nouvelle
        heure aurait mis le guetteur en sommeil pour toute la difference."""
        self._cog.clear()
        self._sog.clear()
        if t is not None:
            self._snooze_until = min(self._snooze_until, t)

    @staticmethod
    def _circular_span(angles):
        """Etendue angulaire d'un jeu de caps (deg, 0..360) : le plus petit
        arc qui les contient tous. Calculee autour de la moyenne circulaire
        pour que 350..10 rende 20 et non 340."""
        if len(angles) < 2:
            return 0.0
        x = sum(math.cos(math.radians(a)) for a in angles)
        y = sum(math.sin(math.radians(a)) for a in angles)
        if x == 0.0 and y == 0.0:
            return 360.0
        mean = math.degrees(math.atan2(y, x))
        diffs = [((a - mean + 180.0) % 360.0) - 180.0 for a in angles]
        return max(diffs) - min(diffs)

    def status(self, now):
        """Etat courant, sans effet de bord. fill_frac est calcule sur la
        COUVERTURE temporelle de la fenetre, pas sur un nombre de points :
        une voie a 1 Hz et une voie a 0.2 Hz doivent armer pareil."""
        cogs = [a for _t, a in self._cog]
        sogs = [v for _t, v in self._sog]
        span_t = 0.0
        if self._sog or self._cog:
            ts = [t for t, _v in self._sog] + [t for t, _v in self._cog]
            span_t = max(ts) - min(ts)
        fill = min(1.0, span_t / self.window_s) if self.window_s > 0 else 1.0
        mean_sog = (sum(sogs) / len(sogs)) if sogs else None
        cog_span = self._circular_span(cogs) if len(cogs) >= 2 else None
        sog_swing = None
        if len(sogs) >= 2 and mean_sog and mean_sog > 0:
            sog_swing = (max(sogs) - min(sogs)) / mean_sog
        return {
            "fill_frac": fill,
            "armed": fill >= self.min_fill_frac,
            "mean_sog_kn": mean_sog,
            "cog_span_deg": cog_span,
            "sog_swing_frac": sog_swing,
            "snoozed": now < self._snooze_until,
        }

    def check(self, now):
        """Manoeuvre detectee a cet instant ? None, ou un evenement
        {"t", "cause" ("route"/"vitesse"), "cog_span_deg", "sog_swing_frac"}.
        Toute detection ouvre d'elle-meme un rearmement (cooldown) : la meme
        manoeuvre ne doit pas redemander dix fois."""
        if now < self._snooze_until:
            return None
        st = self.status(now)
        if not st["armed"]:
            return None
        cause = None
        if (st["cog_span_deg"] is not None and st["mean_sog_kn"] is not None
                and st["mean_sog_kn"] >= self.min_sog_kn
                and st["cog_span_deg"] >= self.cog_threshold_deg):
            cause = "route"
        elif (st["sog_swing_frac"] is not None
                and st["sog_swing_frac"] >= self.sog_threshold_frac):
            cause = "vitesse"
        if cause is None:
            return None
        self._snooze_until = now + self.cooldown_s
        return {"t": now, "cause": cause,
                "cog_span_deg": st["cog_span_deg"],
                "sog_swing_frac": st["sog_swing_frac"]}


class SteadyStateSmoother:
    """
    Accumule les mesures instantanees de STW / TWA(signe) / TWS sur une
    fenetre glissante et produit, au plus une fois toutes les
    sample_period_s secondes, UN echantillon lisse -- a condition que la
    fenetre soit suffisamment couverte ET stable (pas de manoeuvre en
    cours). Une manoeuvre est detectee si le TWA a varie de plus de
    maneuver_twa_deg sur la fenetre, ou le STW de plus de
    maneuver_stw_frac (fraction relative de sa moyenne).
    """

    def __init__(self, window_s=15.0, sample_period_s=10.0,
                 maneuver_twa_deg=15.0, maneuver_stw_frac=0.25,
                 min_fill_frac=0.5):
        self.window_s = window_s
        self.sample_period_s = sample_period_s
        self.maneuver_twa_deg = maneuver_twa_deg
        self.maneuver_stw_frac = maneuver_stw_frac
        self.min_fill_frac = min_fill_frac
        self._stw = deque()   # (t, valeur)
        self._twa = deque()   # (t, valeur signee)
        self._tws = deque()   # (t, valeur)
        self._last_emit_t = None

    def add_stw(self, t, value):
        if value is not None:
            self._stw.append((t, value))
        self._prune(t)

    def add_wind(self, t, twa, tws):
        if twa is not None:
            self._twa.append((t, twa))
        if tws is not None:
            self._tws.append((t, tws))
        self._prune(t)

    def _prune(self, now):
        cutoff = now - self.window_s
        for dq in (self._stw, self._twa, self._tws):
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def reset(self):
        """Vide les fenetres et rearme l'emission. Appele apres un
        CHANGEMENT D'HEURE du bord : melanger dans une meme fenetre des
        mesures d'avant et d'apres le saut fabriquerait des ecarts de temps
        faux, et surtout _last_emit_t serait dans le futur -- plus AUCUN
        echantillon n'aurait ete emis avant que l'horloge ne le rattrape
        (une heure entiere de prise perdue en silence)."""
        self._stw.clear()
        self._twa.clear()
        self._tws.clear()
        self._last_emit_t = None

    def maybe_sample(self, now):
        """
        Retourne (stw, twa, tws) lisses si un echantillon stable et
        suffisamment documente est disponible et que la periode
        d'echantillonnage est ecoulee ; None sinon (ne consomme rien :
        peut etre appele aussi souvent que voulu).
        """
        if self._last_emit_t is not None and self._last_emit_t > now:
            # Ceinture et bretelles : une date d'emission dans le futur ne
            # peut venir que d'un saut d'horloge -- elle ne doit jamais
            # bloquer l'emission (voir reset()).
            self._last_emit_t = None
        if self._last_emit_t is not None and (now - self._last_emit_t) < self.sample_period_s:
            return None

        stw_samples = [v for t, v in self._stw if t >= now - self.window_s]
        twa_samples = [(t, v) for t, v in self._twa if t >= now - self.window_s]
        tws_samples = [v for t, v in self._tws if t >= now - self.window_s]

        if not stw_samples or not twa_samples or not tws_samples:
            return None

        span_twa = twa_samples[-1][0] - twa_samples[0][0]
        if span_twa < self.window_s * self.min_fill_frac:
            return None

        angles = [v for _, v in twa_samples]
        ref = angles[0]
        max_dev = max(abs(_circular_diff(a, ref)) for a in angles)
        if max_dev > self.maneuver_twa_deg:
            return None

        stw_min, stw_max = min(stw_samples), max(stw_samples)
        stw_mean = sum(stw_samples) / len(stw_samples)
        if stw_mean > 0.05 and (stw_max - stw_min) / stw_mean > self.maneuver_stw_frac:
            return None

        tws_smoothed = sum(tws_samples) / len(tws_samples)
        sum_sin = sum(math.sin(math.radians(a)) for a in angles)
        sum_cos = sum(math.cos(math.radians(a)) for a in angles)
        twa_smoothed = math.degrees(math.atan2(sum_sin, sum_cos))

        self._last_emit_t = now
        return stw_mean, twa_smoothed, tws_smoothed

    def status(self, now):
        """
        Etat courant de la fenetre de lissage, SANS AUCUN EFFET DE BORD
        (contrairement a maybe_sample(), qui memorise la date du dernier
        echantillon emis) : peut donc etre appele aussi souvent que voulu
        par l'interface, notamment pour afficher EN DIRECT si les mesures
        en cours seront retenues ou ecartees -- c'est la seule facon, pour
        l'utilisateur en mer, de savoir tout de suite qu'il alimente
        vraiment sa polaire, plutot que de le decouvrir au traitement.

        Retourne un dict :
          "state"           : "no_data"    -- il manque des trames (voir "missing")
                              "filling"    -- fenetre pas encore assez couverte
                              "maneuver_twa"/"maneuver_stw" -- manoeuvre detectee
                              "steady"     -- regime stable, echantillons retenus
          "missing"         : liste des grandeurs absentes ("STW", "vent")
          "fill_frac"       : 0..1, couverture de la fenetre RAPPORTEE AU SEUIL
                              (window_s * min_fill_frac) et non a window_s --
                              c'est ce seuil qui decide, une barre de progression
                              doit donc s'y referer pour ne pas paraitre bloquee
                              a mi-course alors que tout va bien.
          "twa_spread_deg"  : plus grand ecart d'angle sur la fenetre (vs
                              maneuver_twa_deg), None si pas de donnee
          "stw_spread_frac" : amplitude relative du STW (vs maneuver_stw_frac)
          "cooldown_s"      : temps restant avant le prochain echantillon
                              possible (periode d'echantillonnage), None si
                              aucun echantillon n'a encore ete emis
        """
        stw_samples = [v for t, v in self._stw if t >= now - self.window_s]
        twa_samples = [(t, v) for t, v in self._twa if t >= now - self.window_s]
        tws_samples = [v for t, v in self._tws if t >= now - self.window_s]

        cooldown = None
        if self._last_emit_t is not None:
            cooldown = max(0.0, self.sample_period_s - (now - self._last_emit_t))

        out = {"state": "no_data", "missing": [], "fill_frac": 0.0,
               "twa_spread_deg": None, "stw_spread_frac": None, "cooldown_s": cooldown}
        if not stw_samples:
            out["missing"].append("STW")
        if not twa_samples or not tws_samples:
            out["missing"].append("vent")
        if out["missing"]:
            return out

        span_twa = twa_samples[-1][0] - twa_samples[0][0]
        needed = self.window_s * self.min_fill_frac
        out["fill_frac"] = min(1.0, span_twa / needed) if needed > 0 else 1.0

        angles = [v for _, v in twa_samples]
        ref = angles[0]
        out["twa_spread_deg"] = max(abs(_circular_diff(a, ref)) for a in angles)

        stw_mean = sum(stw_samples) / len(stw_samples)
        # Meme garde que maybe_sample() : sous 0.05 kn de moyenne, l'amplitude
        # relative n'a plus de sens (division par ~0) et le critere de
        # manoeuvre sur le STW est neutralise.
        out["stw_spread_frac"] = ((max(stw_samples) - min(stw_samples)) / stw_mean) \
            if stw_mean > 0.05 else 0.0

        if span_twa < needed:
            out["state"] = "filling"
        elif out["twa_spread_deg"] > self.maneuver_twa_deg:
            out["state"] = "maneuver_twa"
        elif out["stw_spread_frac"] > self.maneuver_stw_frac:
            out["state"] = "maneuver_stw"
        else:
            out["state"] = "steady"
        return out


# =========================================================================
# Journal de configuration voile/moteur/derive (segments temporels)
# =========================================================================

# Position de derive : etat BINAIRE (contrairement aux voiles/moteurs, qui
# peuvent se combiner librement) -- on ne stocke donc pas un tuple de codes
# mais une simple chaine parmi DERIVE_STATES, ou None ("non renseignee",
# y compris pour toutes les donnees accumulees AVANT l'ajout de cette
# fonctionnalite -- aucune migration necessaire, voir PolarSampleStore).
DERIVE_STATES = ("haute", "basse")
DERIVE_LABELS = {"haute": "Haute", "basse": "Basse", None: "Non renseignee"}


def _fixup_derive(value):
    return value if value in DERIVE_STATES else None


# =========================================================================
# Compatibilite ascendante des donnees accumulees
# =========================================================================
#
# CONTRAT : une version recente doit pouvoir relire l'entrepot de N'IMPORTE
# QUELLE version anterieure, sans migration ni manipulation de l'utilisateur.
# Ces donnees sont des heures de mer, parfois irremplacables ; les perdre
# parce qu'un champ a ete ajoute entre deux versions serait inacceptable.
#
# Trois regles tenues par fixup_sample() ci-dessous :
#   1. un champ ABSENT prend sa valeur par defaut (un entrepot d'avant la
#      derive se lit comme "derive non renseignee", pas comme une erreur) ;
#   2. un champ INCONNU est ignore sans bruit -- c'est ce qui permet a une
#      version ANCIENNE de relire un fichier produit par une version plus
#      recente, l'autre sens de la compatibilite ;
#   3. un echantillon reellement inexploitable est ECARTE INDIVIDUELLEMENT,
#      jamais au prix du chargement complet : un seul enregistrement abime
#      (coupure pendant l'ecriture, retouche a la main) ne doit pas rendre
#      tout un entrepot illisible, ni empecher l'application de demarrer.
#
# C'est aussi pourquoi l'entrepot reste serialise comme une LISTE NUE et non
# comme un objet {version: ..., samples: [...]} : encapsuler casserait la
# relecture par les versions deja distribuees. Le format de fichier est un
# engagement, pas un detail interne.

def _fixup_codes(value):
    """Liste de codes (voiles ou moteurs) tolerante : accepte une liste, un
    tuple, une chaine seule (ecriture possible d'une version ancienne ou
    d'une retouche manuelle) ou None."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    try:
        return tuple(sorted(str(v) for v in value))
    except TypeError:
        return ()


def _fixup_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fixup_sample(s):
    """Nettoie un echantillon brut issu d'un fichier. Retourne None si
    l'echantillon est inexploitable -- c'est-a-dire si l'une des trois
    grandeurs qui FONT la polaire (STW, TWA, TWS) manque ou n'est pas un
    nombre. Tout le reste (voiles, moteurs, derive, horodatage, passe
    d'origine) est facultatif et prend une valeur par defaut."""
    if not isinstance(s, dict):
        return None
    stw, twa, tws = _fixup_number(s.get("stw")), _fixup_number(s.get("twa")), _fixup_number(s.get("tws"))
    if stw is None or twa is None or tws is None:
        return None
    session_id = s.get("session_id")
    return {
        "sails": _fixup_codes(s.get("sails")),
        "engines": _fixup_codes(s.get("engines")),
        # "derive" absent = entrepot anterieur a cette fonctionnalite : c'est
        # une valeur par defaut, jamais une erreur (voir la regle 1 ci-dessus).
        "derive": _fixup_derive(s.get("derive")),
        "stw": stw, "twa": twa, "tws": tws,
        "t": _fixup_number(s.get("t")),
        "session_id": str(session_id) if session_id is not None else None,
    }


class ConfigSegment:
    __slots__ = ("start", "end", "sails", "engines", "derive")

    def __init__(self, start, end, sails, engines, derive=None):
        self.start = start
        self.end = end  # None = segment toujours ouvert (mode live, en cours)
        self.sails = tuple(sorted(sails))
        self.engines = tuple(sorted(engines))
        self.derive = _fixup_derive(derive)

    def contains(self, t):
        if t < self.start:
            return False
        if self.end is None:
            return True
        return t <= self.end

    def to_dict(self):
        return {"start": self.start, "end": self.end, "sails": list(self.sails),
                "engines": list(self.engines), "derive": self.derive}

    @classmethod
    def from_dict(cls, d):
        return cls(d["start"], d.get("end"), d.get("sails", []), d.get("engines", []), d.get("derive"))

    def __repr__(self):
        return (f"ConfigSegment({self.start!r}, {self.end!r}, {self.sails!r}, "
                f"{self.engines!r}, derive={self.derive!r})")


class ConfigJournal:
    """
    Historique des configurations voile/moteur actives dans le temps,
    sous forme de segments non chevauchants (par construction : on ne
    verifie pas les chevauchements manuels, l'appelant est responsable
    de la coherence -- cf. add_manual_segment).

    - En LIVE : set_live_config() ferme le segment courant et en ouvre
      un nouveau des que la configuration change (pas de doublon si rien
      ne change).
    - En annotation a posteriori (import) : add_manual_segment() definit
      a la main une plage horaire, puisqu'il n'y a pas eu de pilotage en
      direct au moment de l'enregistrement.
    """

    def __init__(self):
        self._segments = []
        self._live_current = None

    def set_live_config(self, t, sails, engines, derive=None):
        sails = tuple(sorted(sails))
        engines = tuple(sorted(engines))
        derive = _fixup_derive(derive)
        if self._live_current is not None and self._live_current.end is None:
            if (self._live_current.sails == sails and self._live_current.engines == engines
                    and self._live_current.derive == derive):
                return self._live_current
            self._live_current.end = t
        seg = ConfigSegment(t, None, sails, engines, derive)
        self._segments.append(seg)
        self._live_current = seg
        return seg

    def close_live_segment(self, t):
        """Referme le segment live encore ouvert, s'il y en a un -- a appeler
        quand l'enregistrement s'arrete.

        Sans cela, le segment reste ouvert (end=None) apres l'arret : il
        continue de couvrir tout instant posterieur, et surtout il reste
        considere comme "en cours", ce qui interdisait de le modifier ou de
        le restaurer alors meme que plus rien n'enregistrait.

        Ne fait rien si l'instant de fermeture n'est pas posterieur au debut
        (arret immediat, horloge qui recule) : un segment de duree nulle ou
        negative n'aurait aucun sens, mieux vaut le laisser ouvert et le
        laisser traiter comme tel. Retourne le segment referme, ou None."""
        seg = self._live_current
        if seg is None or seg.end is not None:
            return None
        if t is None or t <= seg.start:
            return None
        seg.end = t
        self._live_current = None
        return seg

    def add_manual_segment(self, start, end, sails, engines, derive=None):
        if end is not None and end <= start:
            raise ValueError("l'heure de fin doit etre posterieure a l'heure de debut")
        seg = ConfigSegment(start, end, sails, engines, derive)
        self._segments.append(seg)
        self._segments.sort(key=lambda s: s.start)
        return seg

    def remove_segment(self, seg):
        if seg in self._segments:
            self._segments.remove(seg)
            if self._live_current is seg:
                self._live_current = None

    def resolve(self, t):
        """(sails, engines, derive) actifs a l'instant t, ou None si aucun segment ne couvre t."""
        for seg in self._segments:
            if seg.contains(t):
                return seg.sails, seg.engines, seg.derive
        return None

    def all_segments(self):
        return sorted(self._segments, key=lambda s: s.start)

    def to_list(self):
        return [s.to_dict() for s in self.all_segments()]

    def load_list(self, data):
        """Remplace le journal par les segments donnes (utilise pour reprendre une
        annotation en cours, sauvegardee avant d'avoir clique 'Traiter')."""
        self._segments = [ConfigSegment.from_dict(d) for d in data]
        self._segments.sort(key=lambda s: s.start)
        self._live_current = None


# =========================================================================
# Statistiques d'agregation
# =========================================================================

STATISTICS = ("p90", "median", "mean", "max")
STATISTIC_LABELS = {
    "p90": "90e percentile (vitesse cible)",
    "median": "Mediane (vitesse typique)",
    "mean": "Moyenne simple",
    "max": "Maximum observe",
}


def _percentile(sorted_values, pct):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * (pct / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_values[int(k)]
    d0 = sorted_values[int(f)] * (c - k)
    d1 = sorted_values[int(c)] * (k - f)
    return d0 + d1


def aggregate(values, stat):
    if not values:
        return None
    if stat == "max":
        return max(values)
    if stat == "mean":
        return sum(values) / len(values)
    s = sorted(values)
    if stat == "median":
        return _percentile(s, 50)
    return _percentile(s, 90)


# =========================================================================
# Entrepot d'echantillons (accumulation par passes successives)
# =========================================================================

class PolarSampleStore:
    """
    Stocke les echantillons lisses un par un (config voile/moteur, TWA,
    TWS, STW, horodatage, "passe" d'origine) SANS les pre-agreger en
    cases. La table de polaire (bins, symetrie, statistique) est
    recalculee a la demande a partir de cet entrepot -- ce qui permet
    de changer ces reglages a tout moment, y compris sur des donnees
    accumulees lors de sessions precedentes ("passes"), sans aucune
    perte ni migration.

    Chaque echantillon porte un session_id (opaque, attribue par
    l'appelant -- PolarEngine/l'application -- au moment de la fusion
    dans l'entrepot cumulatif) qui identifie la "passe" (session
    enregistree + traitee) dont il provient. Cela permet a l'appelant
    de retrouver, filtrer (pour la construction de la polaire) ou
    supprimer les echantillons d'une passe donnee independamment des
    autres, sans jamais devoir toucher au reste de l'entrepot -- utile
    pour gérer/inspecter les passes individuellement (ex. exclure une
    sortie suspecte, ou la supprimer si l'annotation etait fausse).
    Un session_id=None (donnees issues d'un entrepot pre-existant a
    l'ajout de cette fonctionnalite) est traite comme "toujours inclus"
    par les filtres -- aucune migration necessaire.

    Concu pour etre serialise tel quel (to_list/from_list) dans un
    fichier de polaire cumulatif, entre deux lancements du programme.
    """

    def __init__(self):
        self._samples = []  # [{"sails":tuple,"engines":tuple,"derive":str|None,"stw":f,"twa":f,"tws":f,"t":f|None,"session_id":str|None}, ...]
        # Nombre d'echantillons ecartes au dernier from_list() parce
        # qu'inexploitables (voir fixup_sample). Toujours 0 pour un entrepot
        # construit en memoire ; consulte par l'interface pour signaler une
        # perte plutot que de la passer sous silence.
        self.dropped_on_load = 0

    def add(self, sails, engines, stw, twa_signed, tws, t=None, session_id=None, derive=None):
        self._samples.append({
            "sails": tuple(sorted(sails)), "engines": tuple(sorted(engines)), "derive": _fixup_derive(derive),
            "stw": stw, "twa": twa_signed, "tws": tws, "t": t, "session_id": session_id,
        })

    def __len__(self):
        return len(self._samples)

    def clear(self):
        self._samples = []

    def set_session_id(self, session_id):
        """Retague TOUS les echantillons actuellement dans cet entrepot avec le
        session_id donne -- utilise par PolarEngine/l'application juste avant
        de fusionner une "passe" fraichement traitee dans l'entrepot cumulatif."""
        for s in self._samples:
            s["session_id"] = session_id

    def session_ids(self):
        """Ensemble des session_id distincts presents (hors None)."""
        return {s["session_id"] for s in self._samples if s["session_id"] is not None}

    def set_excluded_ranges(self, ranges_by_session):
        """Plages horaires ecartees du calcul, passe par passe :
        {session_id: [(t_debut, t_fin), ...]}.

        Pourquoi des PLAGES et non des identifiants de troncon : un troncon
        est CALCULE (voir split_into_legs), pas stocke. Changer un parametre
        de decoupage redessinerait les frontieres, et des exclusions
        attachees a des numeros de troncon se retrouveraient soudain sur
        d'autres mesures -- silencieusement. Une plage horaire, elle, dit ce
        qu'elle exclut et le dira encore dans dix ans, quel que soit le
        decoupage du moment.

        Rien n'est supprime : les echantillons restent dans l'entrepot et
        redeviennent comptables des que la plage est levee."""
        self._excluded_ranges = {
            sid: [(float(a), float(b)) for a, b in (rs or [])]
            for sid, rs in (ranges_by_session or {}).items() if rs}

    def excluded_ranges(self):
        return dict(getattr(self, "_excluded_ranges", {}) or {})

    def _in_excluded_range(self, s):
        rs = getattr(self, "_excluded_ranges", None)
        if not rs:
            return False
        for a, b in rs.get(s.get("session_id"), ()):  # noqa: B007
            t = s.get("t")
            if t is not None and a <= t <= b:
                return True
        return False

    def _included(self, s, session_ids):
        """Un echantillon est inclus si aucun filtre n'est fourni (session_ids
        est None), ou s'il n'a pas de session_id (donnees anciennes non
        taguees, toujours incluses), ou si son session_id figure dans le
        filtre fourni -- et, dans tous les cas, si son instant ne tombe pas
        dans une plage ecartee a la main (voir set_excluded_ranges)."""
        if self._in_excluded_range(s):
            return False
        if session_ids is None:
            return True
        return s["session_id"] is None or s["session_id"] in session_ids

    def configs(self, session_ids=None):
        """[((sails, engines, derive), count), ...] rencontres, tries par nb
        d'echantillons decroissant. derive fait partie de la cle au meme
        titre que voiles/moteurs (voir DERIVE_STATES) : une meme combinaison
        voiles/moteurs avec une derive differente est une configuration
        distincte, avec sa propre polaire."""
        totals = defaultdict(int)
        for s in self._samples:
            if self._included(s, session_ids):
                totals[(s["sails"], s["engines"], s.get("derive"))] += 1
        return sorted(totals.items(), key=lambda kv: -kv[1])

    def sails_for_session(self, session_id):
        """Ensemble trie des combinaisons de voiles (tuples) rencontrees parmi
        les echantillons portant EXACTEMENT ce session_id -- contrairement a
        _included(), pas de repli sur les echantillons non tagues : on veut
        precisement ce qui a ete utilise PAR cette passe, rien d'autre."""
        return sorted({s["sails"] for s in self._samples if s["session_id"] == session_id})

    def configs_for_session(self, session_id):
        """[((voiles, moteurs, derive), nombre)] pour cette passe uniquement,
        triees par nombre d'echantillons decroissant.

        Une meme passe peut en contenir plusieurs : il suffit d'avoir change
        de voile en cours de route pour que le journal ait produit plusieurs
        segments. C'est pourquoi corriger l'annotation d'une passe se fait
        CONFIGURATION PAR CONFIGURATION (voir relabel_session_config) et non
        d'un bloc -- sans quoi une correction ecraserait des changements de
        voilure parfaitement legitimes."""
        counts = defaultdict(int)
        for s in self._samples:
            if s["session_id"] == session_id:
                counts[(s["sails"], s["engines"], s.get("derive"))] += 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def relabel_session_config(self, session_id, old_config, sails, engines, derive=None):
        """Re-annote a posteriori les echantillons d'une passe qui portent la
        configuration old_config = (voiles, moteurs, derive), pour leur donner
        la configuration indiquee. Retourne le nombre d'echantillons modifies.

        Sert a rattraper une annotation fausse APRES coup, une fois la passe
        deja dans l'entrepot -- typiquement un enregistrement lance avec la
        mauvaise voile cochee, decouvert bien plus tard. Ne touche QUE les
        mesures elles-memes (STW/TWA/TWS restent intactes) : c'est une
        correction d'etiquette, pas un recalcul."""
        old_sails, old_engines, old_derive = old_config
        old_sails, old_engines = tuple(sorted(old_sails)), tuple(sorted(old_engines))
        old_derive = _fixup_derive(old_derive)
        new = {"sails": tuple(sorted(sails)), "engines": tuple(sorted(engines)),
               "derive": _fixup_derive(derive)}
        n = 0
        for s in self._samples:
            if (s["session_id"] == session_id and s["sails"] == old_sails
                    and s["engines"] == old_engines and s.get("derive") == old_derive):
                s.update(new)
                n += 1
        return n

    def sample_count(self, sails, engines, derive=None, session_ids=None):
        sails, engines = tuple(sorted(sails)), tuple(sorted(engines))
        return sum(1 for s in self._samples
                    if s["sails"] == sails and s["engines"] == engines and s.get("derive") == derive
                    and self._included(s, session_ids))

    def _cells(self, sails, engines, derive, twa_bin_deg, tws_bin_kn, symmetric,
               session_ids):
        """{(case_twa, case_tws): [echantillons entiers]} pour une
        configuration. Decoupage COMMUN a la table de vitesses et a l'indice
        de confiance : les deux doivent parler exactement des memes cases,
        sans quoi une confiance pourrait se retrouver en face de la mauvaise
        vitesse. Rend les echantillons complets (et non les seules vitesses)
        parce que la confiance a besoin de leur instant et de leur passe."""
        sails, engines = tuple(sorted(sails)), tuple(sorted(engines))
        cells = defaultdict(list)
        for s in self._samples:
            if s["sails"] != sails or s["engines"] != engines or s.get("derive") != derive:
                continue
            if not self._included(s, session_ids):
                continue
            twa = abs(s["twa"]) if symmetric else s["twa"]
            twa_idx = int(math.floor((twa + twa_bin_deg / 2.0) / twa_bin_deg))
            tws_idx = int(math.floor((s["tws"] + tws_bin_kn / 2.0) / tws_bin_kn))
            cells[(twa_idx, tws_idx)].append(s)
        return cells

    def table(self, sails, engines, derive=None, twa_bin_deg=5.0, tws_bin_kn=2.0, symmetric=True, stat="p90",
              session_ids=None):
        """
        Retourne (twa_values, tws_values, grille, comptes) pour la
        config donnee (voiles + moteurs + derive) : twa_values / tws_values =
        centres de case tries (deg / noeuds), grille[i][j] = vitesse agregee
        (None si vide), comptes[i][j] = nombre d'echantillons ayant
        contribue a la case.
        session_ids : si fourni, ne considere que les echantillons dont le
        session_id figure dans cet ensemble (ou qui n'ont pas de session_id
        -- voir _included) ; None = pas de filtre, tout est inclus.
        """
        cells = {k: [s["stw"] for s in v] for k, v in self._cells(
            sails, engines, derive, twa_bin_deg, tws_bin_kn, symmetric, session_ids).items()}
        if not cells:
            return [], [], [], []
        twa_idxs = sorted({k[0] for k in cells})
        tws_idxs = sorted({k[1] for k in cells})
        twa_values = [i * twa_bin_deg for i in twa_idxs]
        tws_values = [i * tws_bin_kn for i in tws_idxs]
        grid, counts = [], []
        for ti in twa_idxs:
            row, crow = [], []
            for twi in tws_idxs:
                values = cells.get((ti, twi))
                row.append(aggregate(values, stat) if values else None)
                crow.append(len(values) if values else 0)
            grid.append(row)
            counts.append(crow)
        return twa_values, tws_values, grid, counts

    def confidence_table(self, sails, engines, derive=None, twa_bin_deg=5.0,
                         tws_bin_kn=2.0, symmetric=True, session_ids=None):
        """Indice de confiance case par case, sur EXACTEMENT le meme
        decoupage que table() -- meme appel a _cells(), donc aucun risque
        qu'une confiance se retrouve en face de la mauvaise vitesse.

        Retourne (twa_values, tws_values, conf) avec conf[i][j] = le dict de
        confidence_of(), ou None pour une case vide. N'agit sur rien : c'est
        une lecture de l'entrepot, en parallele de la table de vitesses."""
        cells = self._cells(sails, engines, derive, twa_bin_deg, tws_bin_kn,
                            symmetric, session_ids)
        if not cells:
            return [], [], []
        twa_idxs = sorted({k[0] for k in cells})
        tws_idxs = sorted({k[1] for k in cells})
        conf = []
        for ti in twa_idxs:
            conf.append([confidence_of(cells[(ti, twi)]) if (ti, twi) in cells else None
                         for twi in tws_idxs])
        return ([i * twa_bin_deg for i in twa_idxs],
                [i * tws_bin_kn for i in tws_idxs], conf)

    def session_confidence(self, session_id, twa_bin_deg=5.0, tws_bin_kn=2.0,
                           symmetric=True):
        """Confiance d'une PASSE : ce que vaut cet enregistrement-la.

        Meme mesure de fond que pour une case (blocs, dispersion, nombre),
        enrichie de ce qui ne concerne qu'une passe : sa duree utile et le
        nombre de cases distinctes qu'elle a remplies. Une passe qui a
        couvert vingt cases apprend beaucoup plus qu'une passe qui a passe
        deux heures dans la meme -- a nombre d'echantillons egal.

        Pour TOUTES les passes d'un coup, voir session_overview() : appeler
        cette methode dans une boucle reparcourt l'entrepot entier a chaque
        passe, et c'est exactement ce qui rendait l'ouverture de l'Entrepot
        proportionnelle a (passes x echantillons)."""
        samples = [s for s in self._samples if s.get("session_id") == session_id]
        return self._session_confidence_of(samples, twa_bin_deg, tws_bin_kn, symmetric)

    def session_overview(self, twa_bin_deg=5.0, tws_bin_kn=2.0, symmetric=True):
        """Tout ce que le tableau de l'Entrepot affiche par passe, pour
        TOUTES les passes, en UN SEUL parcours de l'entrepot :

          {session_id: {"configs": [((voiles, moteurs, derive), n), ...],
                        "confidence": <dict de session_confidence>}}

        Raison d'etre : la liste des passes appelait configs_for_session()
        puis session_confidence() PAR LIGNE, chacune reparcourant tout
        l'entrepot -- au demarrage, trente passes sur un entrepot fourni
        coutaient des secondes pour n'afficher qu'un tableau. Ici, un
        parcours regroupe les echantillons par passe, et chaque calcul ne
        voit plus que les siens."""
        groups = {}
        for s in self._samples:
            sid = s.get("session_id")
            if sid is not None:
                groups.setdefault(sid, []).append(s)
        out = {}
        for sid, samples in groups.items():
            counts = defaultdict(int)
            for s in samples:
                counts[(s["sails"], s["engines"], s.get("derive"))] += 1
            out[sid] = {
                "configs": sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])),
                "confidence": self._session_confidence_of(
                    samples, twa_bin_deg, tws_bin_kn, symmetric),
            }
        return out

    def _session_confidence_of(self, samples, twa_bin_deg, tws_bin_kn, symmetric):
        info = confidence_of(samples)
        ts = sorted(s["t"] for s in samples if s.get("t") is not None)
        info["duration_s"] = (ts[-1] - ts[0]) if len(ts) >= 2 else 0.0
        cells = set()
        for s in samples:
            twa = abs(s["twa"]) if symmetric else s["twa"]
            cells.add((int(math.floor((twa + twa_bin_deg / 2.0) / twa_bin_deg)),
                       int(math.floor((s["tws"] + tws_bin_kn / 2.0) / tws_bin_kn))))
        info["cells"] = len(cells)
        if not samples:
            return info

        # Une PASSE ne se juge pas comme une case, et surtout pas sur les
        # memes criteres. Une prise continue de deux heures est un seul
        # "bloc" au sens des cases -- et c'est parfaitement normal : c'est
        # un bon enregistrement, pas un defaut. Compter les blocs ici
        # condamnerait toute prise propre. De meme, la dispersion d'une
        # passe entiere melange des vents differents et ne veut rien dire.
        #
        # Ce qui fait la valeur d'une passe, c'est ce qu'elle a RAPPORTE :
        #   - combien de cases de polaire differentes elle a nourries (une
        #     passe qui balaie les allures apprend beaucoup ; une passe qui
        #     tourne deux heures dans le meme bord apprend une seule chose) ;
        #   - la matiere deposee dans chacune ;
        #   - le volume total.
        f_cov = min(1.0, len(cells) / 12.0)
        f_dens = min(1.0, (info["n"] / float(len(cells))) / 10.0) if cells else 0.0
        f_vol = min(1.0, info["n"] / 60.0)
        info["score"] = int(round(100.0 * (0.45 * f_cov + 0.30 * f_dens + 0.25 * f_vol)))
        info["level"], info["word"] = confidence_level(info["score"])
        if len(cells) <= 2 and info["n"] > 10:
            info["why"] = (f"toute la passe tient dans {len(cells)} case(s) de polaire : "
                           "elle n'apprend presque rien de neuf")
        elif f_cov < 0.5:
            info["why"] = f"peu d'allures et de vents parcourus ({len(cells)} cases)"
        elif f_dens < 0.5:
            info["why"] = "beaucoup de cases effleurees, peu de matiere dans chacune"
        elif f_vol < 0.5:
            info["why"] = f"passe courte ({info['n']} echantillons)"
        else:
            info["why"] = f"passe riche : {len(cells)} cases nourries par {info['n']} mesures"
        return info

    def config_confidence(self, sails, engines, derive=None, twa_bin_deg=5.0,
                          tws_bin_kn=2.0, symmetric=True, session_ids=None):
        """Maturite d'une polaire de configuration : est-elle prete a servir,
        et sinon, que lui manque-t-il ?

        Ne note pas une moyenne des cases -- une polaire n'est pas la
        moyenne de ses cases, elle vaut par ce qu'elle COUVRE. On rapporte
        donc : combien de cases sont solides ou bonnes, quelles plages
        d'angle et de vent restent absentes. C'est ce qui repond a la seule
        question utile : ou faut-il aller chercher des donnees ?"""
        cells = self._cells(sails, engines, derive, twa_bin_deg, tws_bin_kn,
                            symmetric, session_ids)
        out = {"cells": len(cells), "solid": 0, "usable": 0, "weak": 0,
               "n": 0, "sessions": 0, "score": 0, "level": "D", "word": "fragile",
               "missing": []}
        if not cells:
            out["missing"] = ["tout : aucune mesure pour cette configuration"]
            return out
        all_samples = [s for v in cells.values() for s in v]
        out["n"] = len(all_samples)
        out["sessions"] = len({s.get("session_id") for s in all_samples
                               if s.get("session_id")})
        for v in cells.values():
            c = confidence_of(v)
            if c["level"] == "A":
                out["solid"] += 1
            elif c["level"] == "B":
                out["usable"] += 1
            else:
                out["weak"] += 1
        # Une polaire "mure" est une polaire dont les cases SOLIDES couvrent
        # les allures utiles. On compte donc les cases fiables, rapportees a
        # ce qu'une polaire exploitable demande (une trentaine de cases sur
        # une grille ordinaire), et non a ce qu'elle contient deja.
        out["score"] = int(round(100.0 * min(
            1.0, (out["solid"] + 0.5 * out["usable"]) / 30.0)))
        out["level"], out["word"] = confidence_level(out["score"])

        twas = sorted({abs(s["twa"]) for s in all_samples})
        twss = sorted({s["tws"] for s in all_samples})
        for lo, hi, nom in ((0.0, 50.0, "le pres"), (50.0, 100.0, "le travers"),
                            (100.0, 150.0, "le largue"), (150.0, 181.0, "le portant")):
            if not any(lo <= a < hi for a in twas):
                out["missing"].append(nom)
        for lo, hi, nom in ((0.0, 8.0, "le petit temps (< 8 nds)"),
                            (8.0, 16.0, "le vent moyen (8-16 nds)"),
                            (16.0, 25.0, "le vent frais (16-25 nds)"),
                            (25.0, 999.0, "le vent fort (> 25 nds)")):
            if not any(lo <= w < hi for w in twss):
                out["missing"].append(nom)
        return out

    def max_table(self, configs, twa_bin_deg=5.0, tws_bin_kn=2.0, symmetric=True,
                  stat="p90", session_ids=None):
        """Polaire MAX : pour chaque case TWA x TWS, la MEILLEURE vitesse
        obtenue parmi les configurations donnees -- et laquelle l'a obtenue.

        C'est la polaire qu'attend un logiciel de routage : lui ne demande
        pas "que vaut le bateau sous GV+solent ?", il demande "que vaut le
        bateau, point" -- a charge pour l'equipage de porter la bonne
        voilure. La reponse "laquelle" (winners) est justement ce qui
        transforme la polaire max en guide de voilure : pour ce cap et ce
        vent, voila la configuration qui a gagne.

        configs : liste de (voiles, moteurs, derive) a mettre en
        concurrence (typiquement store.configs(), eventuellement filtree).
        Retourne (twa_values, tws_values, grid, counts, winners) :
        memes conventions que table(), plus winners[i][j] = indice dans
        configs de la configuration gagnante de la case (None si vide).
        counts[i][j] = nombre d'echantillons de la configuration GAGNANTE
        dans la case : c'est sur eux que la vitesse retenue est batie.
        """
        norm = []
        for (sails, engines, derive) in configs:
            norm.append((tuple(sorted(sails)), tuple(sorted(engines)), derive))
        index = {}
        for i, c in enumerate(norm):
            index.setdefault(c, i)
        cells = defaultdict(list)   # (twa_idx, tws_idx, cfg_i) -> [stw]
        for s in self._samples:
            ci = index.get((s["sails"], s["engines"], s.get("derive")))
            if ci is None or not self._included(s, session_ids):
                continue
            twa = abs(s["twa"]) if symmetric else s["twa"]
            ti = int(math.floor((twa + twa_bin_deg / 2.0) / twa_bin_deg))
            wi = int(math.floor((s["tws"] + tws_bin_kn / 2.0) / tws_bin_kn))
            cells[(ti, wi, ci)].append(s["stw"])
        if not cells:
            return [], [], [], [], []
        twa_idxs = sorted({k[0] for k in cells})
        tws_idxs = sorted({k[1] for k in cells})
        twa_values = [i * twa_bin_deg for i in twa_idxs]
        tws_values = [i * tws_bin_kn for i in tws_idxs]
        grid, counts, winners = [], [], []
        for ti in twa_idxs:
            row, crow, wrow = [], [], []
            for wi in tws_idxs:
                best = best_n = best_ci = None
                for ci in range(len(norm)):
                    values = cells.get((ti, wi, ci))
                    if not values:
                        continue
                    speed = aggregate(values, stat)
                    if speed is not None and (best is None or speed > best):
                        best, best_n, best_ci = speed, len(values), ci
                row.append(best)
                crow.append(best_n or 0)
                wrow.append(best_ci)
            grid.append(row)
            counts.append(crow)
            winners.append(wrow)
        return twa_values, tws_values, grid, counts, winners

    def max_cell_detail(self, configs, twa_value, tws_value, twa_bin_deg=5.0,
                        tws_bin_kn=2.0, symmetric=True, stat="p90",
                        session_ids=None):
        """Tout ce que l'entrepot sait d'UNE case de la polaire max : chaque
        configuration en concurrence y expose ses mesures, leurs statistiques
        et leur confiance. C'est la fiche d'identite du point -- la polaire
        n'affiche que la valeur retenue, cette methode raconte d'ou elle
        vient et qui elle a battu.

        Meme decoupage que max_table() (memes arrondis d'indices), sans quoi
        le detail pourrait decrire une autre case que celle cliquee.

        Retourne {"twa", "tws", "twa_range", "tws_range", "stat", "entries"}
        avec entries triees de la plus rapide a la plus lente ; chaque entree
        porte la configuration, ses statistiques completes, la liste triee
        de ses vitesses, sa confiance, ses bords, ses passes et sa plage de
        temps."""
        ti = int(round(float(twa_value) / twa_bin_deg))
        wi = int(round(float(tws_value) / tws_bin_kn))
        out = {
            "twa": ti * twa_bin_deg,
            "tws": wi * tws_bin_kn,
            "twa_range": (ti * twa_bin_deg - twa_bin_deg / 2.0,
                          ti * twa_bin_deg + twa_bin_deg / 2.0),
            "tws_range": (wi * tws_bin_kn - tws_bin_kn / 2.0,
                          wi * tws_bin_kn + tws_bin_kn / 2.0),
            "stat": stat,
            "entries": [],
        }
        for idx, (sails, engines, derive) in enumerate(configs):
            cells = self._cells(sails, engines, derive, twa_bin_deg, tws_bin_kn,
                                symmetric, session_ids)
            samples = cells.get((ti, wi))
            if not samples:
                continue
            values = sorted(s["stw"] for s in samples)
            n = len(values)
            ts = sorted(s["t"] for s in samples if s.get("t") is not None)
            twas = [abs(s["twa"]) if symmetric else s["twa"] for s in samples]
            twss = [s["tws"] for s in samples]
            per_session = {}
            for s in samples:
                per_session.setdefault(s.get("session_id"), []).append(s)
            sessions = []
            for sid, ss in per_session.items():
                sts = sorted(x["t"] for x in ss if x.get("t") is not None)
                sessions.append({"session_id": sid, "n": len(ss),
                                 "t_first": sts[0] if sts else None,
                                 "t_last": sts[-1] if sts else None})
            sessions.sort(key=lambda e: (e["t_first"] is None, e["t_first"] or 0.0))
            out["entries"].append({
                "config_index": idx,
                "config": (tuple(sorted(sails)), tuple(sorted(engines)), derive),
                "n": n,
                "speed": aggregate(values, stat),
                "min": values[0], "max": values[-1],
                "mean": sum(values) / n,
                "median": _percentile(values, 50.0),
                "p90": _percentile(values, 90.0),
                "spread": relative_spread(values),
                "values": values,
                "confidence": confidence_of(samples),
                "twa_min": min(twas), "twa_max": max(twas),
                "tws_min": min(twss), "tws_max": max(twss),
                "t_first": ts[0] if ts else None,
                "t_last": ts[-1] if ts else None,
                # Bords reellement parcourus : au moins l'un des deux quand la
                # grille est symetrique -- une case confirmee sur LES DEUX
                # bords vaut plus qu'une case d'un seul bord.
                "tacks": {"tribord": sum(1 for s in samples if s["twa"] > 0),
                          "babord": sum(1 for s in samples if s["twa"] < 0)},
                "sessions": sessions,
            })
        out["entries"].sort(
            key=lambda e: (e["speed"] is None, -(e["speed"] or 0.0)))
        return out

    def drop_after(self, t):
        """Retire les echantillons datant d'APRES t. Sert a tronquer une
        prise a l'instant ou une manoeuvre a ete detectee : ce qui a ete
        mesure pendant qu'on demandait "avez-vous manoeuvre ?" appartient a
        la manoeuvre, pas a la polaire. Retourne le nombre retire."""
        before = len(self._samples)
        self._samples = [s for s in self._samples
                         if s.get("t") is None or s["t"] <= t]
        return before - len(self._samples)

    def remove_sessions(self, session_ids):
        """Supprime definitivement tous les echantillons dont le session_id
        figure dans l'ensemble donne. Retourne le nombre d'echantillons
        supprimes."""
        session_ids = set(session_ids)
        before = len(self._samples)
        self._samples = [s for s in self._samples if s["session_id"] not in session_ids]
        return before - len(self._samples)

    def to_list(self):
        return [dict(s, sails=list(s["sails"]), engines=list(s["engines"])) for s in self._samples]

    @classmethod
    def from_list(cls, data):
        """Relit un entrepot serialise, quelle que soit la version qui l'a
        ecrit -- voir le CONTRAT de compatibilite en tete de fixup_sample().
        Ne leve jamais : un contenu inattendu donne un entrepot vide plutot
        qu'une exception au demarrage de l'application."""
        store = cls()
        if not isinstance(data, list):
            return store
        for s in data:
            sample = fixup_sample(s)
            if sample is None:
                store.dropped_on_load += 1
            else:
                store._samples.append(sample)
        return store

    def extend(self, other):
        """Fusionne les echantillons d'un autre entrepot (ex. reprise d'une session) dans celui-ci."""
        self._samples.extend(other._samples)


# =========================================================================
# Export
# =========================================================================

def format_pol_table(twa_values, tws_values, grid, decimals=2):
    """
    Contenu texte d'un fichier .pol au format "Expedition" (lignes =
    TWA, colonnes = TWS, separateur tabulation, premiere cellule
    'twa/tws') -- format largement reconnu par les logiciels de
    navigation/routage (Expedition, Adrena, qtVlm, OpenCPN...). Cases
    sans donnee laissees vides plutot qu'a zero (un zero se lirait
    comme "bateau a l'arret", ce qui serait trompeur).
    """
    lines = []
    header = ["twa/tws"] + [f"{v:g}" for v in tws_values]
    lines.append("\t".join(header))
    for twa, row in zip(twa_values, grid):
        cells = [f"{twa:g}"]
        for v in row:
            cells.append(f"{v:.{decimals}f}" if v is not None else "")
        lines.append("\t".join(cells))
    return "\n".join(lines) + "\n"


# =========================================================================
# Indice de confiance
#
# Une polaire ne dit pas seulement une vitesse : elle dit aussi, en creux,
# "crois-moi". Or toutes les cases ne meritent pas la meme croyance, et
# rien jusqu'ici ne les distinguait -- une case batie sur 200 echantillons
# pris dans un seul bord de dix minutes avait exactement la meme autorite
# qu'une case batie sur 20 echantillons repartis sur huit sorties. Ce n'est
# pas la meme chose du tout.
#
# L'indice repose sur quatre constats, du plus important au moins :
#
#  1. L'INDEPENDANCE prime sur le nombre. Deux echantillons espaces de dix
#     secondes sur le meme bord ne sont pas deux mesures : c'est une mesure
#     comptee deux fois (meme etat de mer, meme reglage, meme erreur de
#     capteur, meme courant). On regroupe donc les echantillons en BLOCS
#     separes d'au moins CONF_BLOCK_GAP_S, et c'est le nombre de blocs --
#     pas le nombre d'echantillons -- qui porte l'essentiel de l'indice.
#  2. Les SORTIES DISTINCTES valent mieux encore : d'un jour a l'autre
#     changent la mer, le chargement, le courant, l'etalonnage. Une case
#     confirmee sur trois sorties est une case a laquelle on peut se fier.
#  3. La DISPERSION dit si les mesures se recoupent : une case serree est
#     une case sure, une case etalee cache quelque chose.
#  4. Le NOMBRE compte encore, mais en dernier -- et il sature vite.
#
# Et un garde-fou qui vaut mieux que tous les reglages de poids : un
# PLAFOND par nombre de blocs. Quoi qu'annoncent les autres facteurs, une
# case observee une seule fois ne depassera jamais "indicative". Aucune
# accumulation d'echantillons redondants ne peut acheter la confiance que
# seule la repetition dans le temps donne.
#
# L'indice N'AGIT SUR RIEN : il n'ecarte aucune case, ne modifie aucune
# vitesse, ne change aucun export de valeur. Il informe, c'est tout.
# =========================================================================

CONF_BLOCK_GAP_S = 300.0      # 5 min : au-dela, on change de "moment"
CONF_BLOCKS_FULL = 6          # nb de blocs pour le plein credit
CONF_SESSIONS_FULL = 3        # nb de sorties pour le plein credit
CONF_N_FULL = 30              # nb d'echantillons pour le plein credit
CONF_SPREAD_BAD = 0.20        # dispersion relative jugee redhibitoire

# Plafond de l'indice selon le nombre de blocs independants. C'est la regle
# la plus importante du lot : elle dit qu'on n'achete pas la confiance a
# coups d'echantillons redondants.
CONF_CAP_BY_BLOCKS = {0: 0, 1: 40, 2: 65, 3: 85}

CONF_LEVELS = ((80, "A", "solide"), (60, "B", "bonne"),
               (40, "C", "indicative"), (0, "D", "fragile"))

# Seuil CONSEILLE pour le tri automatique a l'entree de l'entrepot : la
# frontiere entre "indicative" et "fragile". En dessous, une passe apporte
# si peu, ou de facon si peu repartie, qu'elle a plus de chances de tirer
# une case dans la mauvaise direction que de l'affermir. Conseille, jamais
# impose : c'est un point de depart raisonnable, pas une verite.
CONF_ADVISED_THRESHOLD = 40


def confidence_level(score):
    """(lettre, mot) correspondant a un indice 0-100."""
    for floor, letter, word in CONF_LEVELS:
        if score >= floor:
            return letter, word
    return "D", "fragile"


def count_blocks(times, gap_s=CONF_BLOCK_GAP_S):
    """Nombre de BLOCS temporels independants dans une suite d'instants :
    on ouvre un bloc a chaque trou d'au moins gap_s. Les echantillons sans
    horodatage (donnees tres anciennes) comptent pour un bloc a eux tous --
    on ne peut rien affirmer de leur repartition, autant ne rien
    surestimer."""
    ts = sorted(t for t in times if t is not None)
    n_undated = sum(1 for t in times if t is None)
    if not ts:
        return 1 if n_undated else 0
    blocks = 1
    for a, b in zip(ts, ts[1:]):
        if b - a >= gap_s:
            blocks += 1
    return blocks + (1 if n_undated else 0)


def relative_spread(values):
    """Dispersion RELATIVE d'un jeu de vitesses : ecart interdecile rapporte
    a la mediane. L'interdecile plutot que l'ecart-type parce qu'une seule
    mesure aberrante ne doit pas condamner une case entiere ; rapporte a la
    mediane parce que 0,5 nd d'ecart n'a pas le meme sens a 3 nd qu'a 12.
    None si l'on ne peut rien en dire (moins de 3 valeurs, ou mediane
    nulle)."""
    vals = sorted(v for v in values if v is not None)
    if len(vals) < 3:
        return None
    med = aggregate(vals, "median")
    if not med:
        return None
    lo = _percentile(vals, 10)
    hi = _percentile(vals, 90)
    return max(0.0, (hi - lo) / med)


def confidence_of(samples):
    """Indice de confiance d'un jeu d'echantillons (une case de polaire, une
    passe, une configuration entiere : la mesure est la meme partout).

    Retourne un dict lisible tel quel par l'interface :
      score       0-100
      level/word  "A".."D" / "solide".."fragile"
      n           nombre d'echantillons
      blocks      nombre de moments independants
      sessions    nombre de sorties distinctes
      spread      dispersion relative (None si indeterminable)
      capped      True si le PLAFOND par blocs a limite l'indice -- c'est
                  alors la seule chose a dire a l'utilisateur : "revenez-y
                  un autre jour", et non "prenez plus de mesures".
      why         raison principale, en clair
    """
    samples = list(samples)
    n = len(samples)
    if not n:
        return {"score": 0, "level": "D", "word": "fragile", "n": 0, "blocks": 0,
                "sessions": 0, "spread": None, "capped": False, "why": "aucune mesure"}
    blocks = count_blocks([s.get("t") for s in samples])
    sessions = len({s.get("session_id") for s in samples if s.get("session_id")})
    sessions = max(sessions, 1)
    spread = relative_spread([s.get("stw") for s in samples])

    f_blocks = min(1.0, blocks / float(CONF_BLOCKS_FULL))
    f_sessions = min(1.0, sessions / float(CONF_SESSIONS_FULL))
    f_n = min(1.0, n / float(CONF_N_FULL))
    # Dispersion inconnue : ni recompensee ni punie (facteur neutre), sinon
    # une case de deux echantillons serait penalisee deux fois pour la meme
    # raison -- elle l'est deja par le nombre et les blocs.
    f_spread = 0.5 if spread is None else max(0.0, 1.0 - spread / CONF_SPREAD_BAD)

    score = 100.0 * (0.35 * f_blocks + 0.20 * f_sessions
                     + 0.20 * f_n + 0.25 * f_spread)
    cap = CONF_CAP_BY_BLOCKS.get(blocks, 100)
    capped = score > cap
    score = int(round(min(score, cap)))
    letter, word = confidence_level(score)

    if capped:
        why = (f"observee sur {blocks} moment(s) seulement : "
               "il y manque de la repetition dans le temps")
    elif spread is not None and f_spread < 0.4:
        why = f"mesures dispersees ({100 * spread:.0f} % d'ecart)"
    elif f_n < 0.5:
        why = f"peu d'echantillons ({n})"
    elif f_sessions < 1.0:
        why = f"confirmee sur {sessions} sortie(s)"
    else:
        why = "nombreuse, repartie et coherente"
    return {"score": score, "level": letter, "word": word, "n": n, "blocks": blocks,
            "sessions": sessions, "spread": spread, "capped": capped, "why": why}


# =========================================================================
# Decoupage d'une passe en TRONCONS
#
# Une passe de trois heures est une unite de PROVENANCE (on est sorti une
# fois), pas une unite de mesure : les mesures, elles, sont deja atomiques
# -- un echantillon toutes les dix secondes, chacun horodate. Ce qui etait
# trop grossier, c'est la DECISION : inclure ou ecarter deux heures d'un
# bloc, alors qu'il suffit souvent d'un quart d'heure de mauvais (loch
# encrasse, moteur demarre sans etre coche, grain qui passe) pour gater le
# reste.
#
# Le bon grain intermediaire, c'est le BORD : entre deux manoeuvres, les
# conditions sont homogenes ; au passage d'une manoeuvre, tout change. Il
# se retrouve APRES COUP dans les echantillons deja entreposes, sans avoir
# rien enregistre de plus, par trois signaux :
#
#   - le changement de CONFIGURATION (voiles, moteurs, derive) : frontiere
#     absolue, jamais fusionnee -- deux voilures differentes ne sont pas le
#     meme bord, meme si le changement a dure trente secondes ;
#   - le VIREMENT ou l'EMPANNAGE : le TWA signe change de bord et y reste.
#     Il faut exiger qu'il Y RESTE, sinon un vent qui oscille autour de
#     l'etrave decouperait la passe en confettis ;
#   - le TROU de temps : au-dela de quelques minutes sans mesure, on n'est
#     plus dans le meme moment.
#
# Puis on RECOLLE ce qui est trop court : un bord de deux minutes n'apprend
# rien et n'a pas a encombrer la liste.
# =========================================================================

# Portee de l'ecretage : nombre de cases regardees de chaque cote. Deux
# suffiraient ; trois pardonne un plateau un peu plus large sans jamais
# aller chercher si loin que la courbure naturelle de la polaire soit prise
# pour une anomalie.
LEG_CLIP_REACH = 3

LEG_MIN_DURATION_S = 180.0    # en dessous, un bord est recolle au precedent
LEG_MIN_SAMPLES = 5
LEG_GAP_S = 300.0             # trou au-dela duquel on change de moment
LEG_TACK_MIN_TWA = 15.0       # sous cet angle, le bord ne veut rien dire
LEG_TACK_CONFIRM = 3          # echantillons a confirmer pour un vrai virement
# Manoeuvre vue APRES COUP : un TWA qui balaie plus de LEG_TURN_DEG en
# moins de LEG_TURN_WINDOW_S. C'est la transposition exacte du guetteur en
# direct (ManeuverWatch), qui travaille lui sur la route fond -- mais la
# route fond n'est pas conservee dans l'entrepot, alors que le TWA l'est.
# Le critere de VITESSE du changement est essentiel : un bateau qui abat
# lentement de 40 a 120 degres au fil d'une heure ne manoeuvre pas, il
# change d'allure dans les memes conditions ; c'est le meme moment, et le
# decouper n'aurait aucun sens.
LEG_TURN_DEG = 25.0
LEG_TURN_WINDOW_S = 120.0


def _tack_of(twa):
    """Bord d'un echantillon : +1 tribord amures, -1 babord, 0 indecis
    (trop pres de l'axe du vent pour que le signe ait un sens)."""
    if twa is None or abs(twa) < LEG_TACK_MIN_TWA:
        return 0
    return 1 if twa > 0 else -1


def split_into_legs(samples, min_duration_s=LEG_MIN_DURATION_S,
                    min_samples=LEG_MIN_SAMPLES, gap_s=LEG_GAP_S):
    """Decoupe les echantillons d'une passe en troncons homogenes.

    Retourne une liste de dicts :
      i, start, end, n, samples, sails, engines, derive, tack, cause
    ou 'cause' dit POURQUOI le troncon commence la -- une frontiere qu'on
    ne peut pas expliquer est une frontiere en laquelle on ne peut pas
    avoir confiance.

    Ne modifie rien : c'est une lecture. Les echantillons sans horodatage
    (donnees tres anciennes) forment un unique troncon, faute de pouvoir
    en dire quoi que ce soit."""
    ordered = sorted((s for s in samples), key=lambda s: (s.get("t") is None, s.get("t") or 0))
    if not ordered:
        return []
    dated = [s for s in ordered if s.get("t") is not None]
    if not dated:
        return [{"i": 0, "start": None, "end": None, "n": len(ordered),
                 "samples": list(ordered), "sails": tuple(ordered[0]["sails"]),
                 "engines": tuple(ordered[0]["engines"]),
                 "derive": ordered[0].get("derive"),
                 "tack": 0, "cause": "sans horodatage"}]

    def cfg(s):
        # Tuples systematiques : to_list() serialise les voiles en LISTES,
        # et comparer une liste a un tuple ne dirait jamais l'egalite. Le
        # decoupage doit donner le meme resultat qu'on lui passe l'entrepot
        # vivant ou sa forme serialisee.
        return (tuple(s["sails"]), tuple(s["engines"]), s.get("derive"))

    # --- 1. Coupures brutes ---
    cuts = []           # indices de DEBUT de troncon (hors 0)
    causes = {}
    tack_run, tack_cur = 0, _tack_of(dated[0].get("twa"))
    window = deque()    # (indice, t, twa) sur LEG_TURN_WINDOW_S
    for k in range(1, len(dated)):
        prev, cur = dated[k - 1], dated[k]

        def _cut(idx, why):
            if idx > 0 and idx not in causes:
                cuts.append(idx)
                causes[idx] = why

        if cfg(prev) != cfg(cur):
            _cut(k, "changement de voilure")
            tack_cur, tack_run = _tack_of(cur.get("twa")), 0
            window.clear()
            continue
        if cur["t"] - prev["t"] >= gap_s:
            _cut(k, "interruption de la mesure")
            tack_cur, tack_run = _tack_of(cur.get("twa")), 0
            window.clear()
            continue

        # --- Manoeuvre : le TWA balaie beaucoup, et VITE ---
        if cur.get("twa") is not None:
            window.append((k, cur["t"], cur["twa"]))
            while window and cur["t"] - window[0][1] > LEG_TURN_WINDOW_S:
                window.popleft()
            if len(window) >= 3:
                angles = [a for _i, _t, a in window]
                if max(angles) - min(angles) >= LEG_TURN_DEG:
                    _cut(window[0][0], "changement de cap")
                    window.clear()
                    tack_cur, tack_run = _tack_of(cur.get("twa")), 0
                    continue

        t_now = _tack_of(cur.get("twa"))
        if t_now == 0 or t_now == tack_cur:
            tack_run = 0
            continue
        # Bord oppose : on ne coupe que s'il se CONFIRME, sinon un vent qui
        # oscille autour de l'etrave hacherait la passe en confettis.
        tack_run += 1
        if tack_run >= LEG_TACK_CONFIRM:
            _cut(k - tack_run + 1, "virement / empannage")
            tack_cur, tack_run = t_now, 0
            window.clear()

    # --- 2. Troncons bruts ---
    edges = [0] + sorted(set(cuts)) + [len(dated)]
    raw = []
    for a, b in zip(edges, edges[1:]):
        if b > a:
            raw.append({"lo": a, "hi": b, "cause": causes.get(a, "debut de passe")})

    # --- 3. Recollage des troncons trop courts ---
    # Un changement de VOILURE n'est jamais efface : c'est une frontiere de
    # sens, pas de commodite. Le reste se recolle au voisin.
    merged = []
    for seg in raw:
        block = dated[seg["lo"]:seg["hi"]]
        dur = block[-1]["t"] - block[0]["t"]
        too_small = (dur < min_duration_s or len(block) < min_samples)
        if (merged and too_small and seg["cause"] != "changement de voilure"
                and cfg(dated[merged[-1]["lo"]]) == cfg(block[0])):
            merged[-1]["hi"] = seg["hi"]
        else:
            merged.append(dict(seg))

    legs = []
    for i, seg in enumerate(merged):
        block = dated[seg["lo"]:seg["hi"]]
        tacks = [_tack_of(s.get("twa")) for s in block]
        nz = [t for t in tacks if t]
        legs.append({
            "i": i, "start": block[0]["t"], "end": block[-1]["t"], "n": len(block),
            "samples": block, "sails": tuple(block[0]["sails"]),
            "engines": tuple(block[0]["engines"]), "derive": block[0].get("derive"),
            "tack": (1 if sum(nz) > 0 else -1) if nz else 0,
            "cause": seg["cause"],
        })
    # Les echantillons sans horodatage rejoignent le dernier troncon : ils
    # existent, ils doivent rester comptables et visibles quelque part.
    undated = [s for s in ordered if s.get("t") is None]
    if undated and legs:
        legs[-1]["samples"] = legs[-1]["samples"] + undated
        legs[-1]["n"] += len(undated)
    return legs


LEG_TACK_LABELS = {1: "tribord amures", -1: "babord amures", 0: "vent de face/arriere"}


def leg_confidence(leg, within_same_session=True):
    """Confiance d'un TRONCON.

    Attention au piege : decouper ne cree pas d'information. Huit troncons
    d'une meme apres-midi, ce n'est pas huit sorties -- meme mer, meme
    etalonnage, meme courant, meme equipage. Les compter comme independants
    gonflerait la confiance, soit exactement l'inverse de ce pour quoi elle
    existe. Un troncon issu d'une seule sortie est donc PLAFONNE comme
    l'est une case observee sur un seul moment (voir CONF_CAP_BY_BLOCKS) :
    il peut etre bon, il ne peut pas etre 'solide' a lui tout seul."""
    info = confidence_of(leg.get("samples") or [])
    dur = ((leg["end"] - leg["start"])
           if leg.get("end") is not None and leg.get("start") is not None else 0.0)
    info["duration_s"] = dur
    if within_same_session:
        cap = CONF_CAP_BY_BLOCKS.get(1, 40)
        if info["score"] >= cap:
            info["score"] = min(info["score"], cap)
            info["capped"] = True
            info["why"] = ("un seul bord d'une seule sortie : bon en soi, mais il faudra "
                           "d'autres jours pour en faire une certitude")
        info["level"], info["word"] = confidence_level(info["score"])
    return info


def clip_polar_slope(twa_values, grid, max_slope_kn_per_10deg, passes=4):
    """ECRETE les pointes d'une grille polaire en bornant sa DERIVEE le long
    du TWA : une case ne peut pas depasser ses voisines de plus que la pente
    autorisee.

    Pourquoi la derivee plutot qu'un ecart a une moyenne : une polaire est
    une courbe PHYSIQUEMENT LISSE. La vitesse d'un bateau ne peut pas
    grimper de trois noeuds entre 60 et 65 degres, quelles que soient les
    mesures. Une case qui le pretend ne decrit pas le bateau, elle decrit un
    surf sur une vague, une risée, ou une poussee de courant -- un instant
    qui ne se reproduira pas et que le routage prendrait pour un acquis.

    On n'ecrete QUE VERS LE BAS, jamais vers le haut. La polaire max
    cherche le meilleur atteignable : une pointe trop haute est suspecte,
    un creux ne l'est pas (c'est simplement une case ou le bateau n'a pas
    encore fait mieux, et le remonter serait inventer une performance).

    Iteratif : deux pointes voisines se soutiennent mutuellement au premier
    passage, il en faut quelques-uns pour que le plateau redescende.

    Retourne (grille_ecretee, masque, n) -- masque[i][j] True si la case a
    ete rabaissee. La grille d'origine n'est pas modifiee."""
    if not grid or not max_slope_kn_per_10deg or max_slope_kn_per_10deg <= 0:
        return [list(r) for r in grid], [[False] * len(r) for r in grid], 0
    slope = float(max_slope_kn_per_10deg) / 10.0     # kn par degre
    out = [list(r) for r in grid]
    mask = [[False] * len(r) for r in grid]
    n_rows = len(out)
    n_cols = len(out[0]) if out else 0
    for _ in range(max(1, int(passes))):
        changed = False
        for j in range(n_cols):
            col = [(i, out[i][j]) for i in range(n_rows) if out[i][j] is not None]
            if len(col) < 3:
                continue           # deux points ne definissent aucune pointe
            for k in range(len(col)):
                i, v = col[k]
                # Pour CHAQUE cote, la borne la plus PERMISSIVE parmi les
                # quelques points voisins ; puis la plus STRICTE des deux
                # cotes. Ce double mouvement est ce qui distingue les trois
                # situations qu'il faut absolument separer :
                #   - une pointe isolee depasse des DEUX cotes -> ecretee ;
                #   - le voisin d'un CREUX ne depasse que d'un cote (le
                #     creux), l'autre cote le justifie -> intact. Sans cela,
                #     une seule mesure basse aberrante rabotait toute la
                #     courbe autour d'elle ;
                #   - un PLATEAU de deux pointes : chacune est justifiee par
                #     l'autre a distance 1, mais plus rien au-dela -> les
                #     deux redescendent.
                sides = []
                for direction in (-1, 1):
                    best = None
                    for step in range(1, LEG_CLIP_REACH + 1):
                        kk = k + direction * step
                        if not (0 <= kk < len(col)):
                            break
                        oi, ov = col[kk]
                        b = ov + slope * abs(twa_values[i] - twa_values[oi])
                        best = b if best is None else max(best, b)
                    if best is not None:
                        sides.append(best)
                # Les EXTREMITES n'ont qu'un cote : les borner reviendrait a
                # raboter le pres ou le grand largue sur la foi d'une seule
                # direction. On ne touche qu'aux cases encadrees.
                if len(sides) < 2:
                    continue
                limit = min(sides)
                if v > limit + 1e-9:
                    out[i][j] = limit
                    mask[i][j] = True
                    changed = True
        if not changed:
            break
    return out, mask, sum(1 for row in mask for m in row if m)


def fill_polar_holes(twa_values, grid):
    """Bouche les TROUS d'une grille polaire par interpolation lineaire le
    long du TWA, colonne de TWS par colonne de TWS -- STRICTEMENT entre
    deux cases mesurees : jamais d'extrapolation au-dela de la premiere ou
    de la derniere mesure (pretendre une vitesse au vent debout ou plein
    vent arriere qu'on n'a jamais mesuree serait un mensonge que le
    logiciel de routage prendrait au pied de la lettre).

    Les logiciels de routage veulent une grille PLEINE et reguliere ; nos
    mesures, elles, sont trouees (on ne navigue pas a tous les angles par
    tous les vents). L'interpolation le long du TWA est la moins risquee :
    entre 60 et 80 degres au meme vent, la vitesse d'un voilier varie
    continument.

    Retourne (grille_bouchee, masque) -- masque[i][j] = True si la case a
    ete interpolee (False : mesuree, ou toujours vide). Grille d'origine
    non modifiee."""
    n_rows = len(grid)
    filled = [list(row) for row in grid]
    mask = [[False] * len(row) for row in grid]
    n_cols = len(grid[0]) if grid else 0
    for j in range(n_cols):
        known = [i for i in range(n_rows) if grid[i][j] is not None]
        for a, b in zip(known, known[1:]):
            if b - a <= 1:
                continue
            va, vb = grid[a][j], grid[b][j]
            ta, tb = twa_values[a], twa_values[b]
            for i in range(a + 1, b):
                frac = (twa_values[i] - ta) / (tb - ta) if tb != ta else 0.0
                filled[i][j] = va + frac * (vb - va)
                mask[i][j] = True
    return filled, mask


# =========================================================================
# Export TimeZero (MaxSea / Nobeltec TZ)
#
# Format releve sur DEUX fichiers reels du bord (Artemis), et non devine :
# TimeZero ne publie pas sa specification, mais ses fichiers sont du XML
# parfaitement lisible et leurs conventions sautent aux yeux des qu'on en
# ouvre un. Trois d'entre elles sont des pieges silencieux :
#
#   1. le separateur decimal est une VIRGULE ("10,5") -- un point produit
#      un fichier que TimeZero lit sans broncher mais interprete faux ;
#   2. une case sans donnee ne vaut ni 0 ni vide, mais "0,1" -- un dixieme
#      de noeud. Zero serait pris pour une vitesse mesuree ; 0,1 dit "le
#      bateau n'avance pas la" sans trouer la grille ;
#   3. toutes les courbes portent EXACTEMENT la meme liste d'angles.
#
# Deux fichiers distincts, et c'est une aubaine :
#   Polar        : la vitesse, une courbe par force de vent ;
#   SailSetPolar : la VOILURE a porter par plage d'angle et de vent --
#                  c'est trait pour trait le "guide de voilure" que la
#                  polaire max d'Allure calcule deja. Ce que le bateau sait
#                  de lui-meme peut donc aller nourrir directement le
#                  routage, voilure comprise.
# =========================================================================

TZ_MAINSAILS = ("GV", "1Reef", "2Reef", "3Reef", "Off")
TZ_FRONTSAILS = ("LightUpWind", "MediumUpWind", "StrongUpWind", "GaleUpWind",
                 "LightReaching", "MediumReaching",
                 "LightDownWind", "MediumDownWind", "StrongDownWind", "GaleDownWind")
# Couleur (ARGB) associee a chaque voile d'avant : relevee telle quelle
# dans les fichiers du bord, ou la correspondance est exacte et sans
# exception. La respecter evite que le bateau change de couleur d'un
# fichier a l'autre dans TimeZero.
TZ_SAIL_COLORS = {
    "LightUpWind": "FFFFFF99", "MediumUpWind": "FFFF9933",
    "StrongUpWind": "FFFD5003", "GaleUpWind": "FFFF0000",
    "LightReaching": "FF66CCFF", "MediumReaching": "FF0066FF",
    "LightDownWind": "FF99FF99", "MediumDownWind": "FF00CC00",
    "StrongDownWind": "FF0000CC", "GaleDownWind": "FF000082",
}
TZ_NO_DATA = "0,1"            # convention TimeZero : pas 0, pas vide
TZ_DEFAULT_MAIN = "GV"
TZ_DEFAULT_FRONT = "LightUpWind"


def _tz_num(value):
    """Nombre au format TimeZero : virgule decimale, pas de zero inutile
    ("7" et non "7,0" -- c'est ainsi que TimeZero ecrit les siens)."""
    txt = f"{value:.1f}".replace(".", ",")
    return txt[:-2] if txt.endswith(",0") else txt


def format_timezero_polar(twa_values, tws_values, grid):
    """Fichier <Polar> TimeZero : une courbe par force de vent, la meme
    liste d'angles pour toutes, virgule decimale, et TZ_NO_DATA pour les
    cases jamais mesurees.

    Les angles negatifs (polaire dissymetrique) sont ecartes : TimeZero
    raisonne en angle au vent de 0 a 180 et symetrise lui-meme."""
    out = ['<?xml version="1.0"?>',
           '<Polar xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">']
    for j, tws in enumerate(tws_values):
        out.append("  <PolarCurve>")
        out.append(f'    <PolarCurveIndex value="{_tz_num(tws)}" />')
        for i, twa in enumerate(twa_values):
            if twa < 0:
                continue
            v = grid[i][j] if i < len(grid) and j < len(grid[i]) else None
            out.append("    <PolarItem>")
            out.append(f'      <Angle value="{_tz_num(twa)}" />')
            out.append(f'      <Value value="{TZ_NO_DATA if v is None else _tz_num(v)}" />')
            out.append("    </PolarItem>")
        out.append("  </PolarCurve>")
    out.append("</Polar>")
    out.append("")
    return "\n".join(out)


def timezero_bands(twa_values, winners, sails_of_winner):
    """Regroupe les cases consecutives de meme voilure en PLAGES d'angle,
    pour une colonne de vent donnee : TimeZero ne veut pas une voilure par
    case, mais des plages contigues couvrant 0 a 180 degres.

    winners : liste (par angle) de l'indice de configuration gagnante, ou
    None. sails_of_winner : indice -> (mainsail, frontsail).
    Retourne [(twa_min, twa_max, main, front), ...] couvrant 0..180 sans
    trou : une plage sans gagnante herite de sa voisine, faute de quoi
    TimeZero se retrouverait avec des angles non renseignes."""
    angles = [a for a in twa_values if a >= 0]
    if not angles:
        return []
    pairs = []
    for a, w in zip(twa_values, winners):
        if a < 0:
            continue
        pairs.append((a, sails_of_winner.get(w) if w is not None else None))
    # Trous combles par la voisine la plus proche deja connue -- d'abord en
    # descendant (une voilure tient jusqu'a ce qu'une autre la remplace),
    # puis en remontant pour le debut de la grille.
    known = None
    for k, (a, s) in enumerate(pairs):
        if s is None:
            pairs[k] = (a, known)
        else:
            known = s
    known = None
    for k in range(len(pairs) - 1, -1, -1):
        a, s = pairs[k]
        if s is None:
            pairs[k] = (a, known)
        else:
            known = s
    if all(s is None for _a, s in pairs):
        pairs = [(a, (TZ_DEFAULT_MAIN, TZ_DEFAULT_FRONT)) for a, _s in pairs]

    bands, start, cur = [], 0.0, pairs[0][1]
    for k in range(1, len(pairs)):
        if pairs[k][1] != cur:
            # Frontiere posee A MI-CHEMIN entre les deux angles mesures :
            # on ne sait pas ou la bascule a lieu exactement, le milieu est
            # l'hypothese la moins fausse.
            edge = round((pairs[k - 1][0] + pairs[k][0]) / 2.0)
            bands.append((start, float(edge), cur[0], cur[1]))
            start, cur = float(edge), pairs[k][1]
    bands.append((start, 180.0, cur[0], cur[1]))
    return bands


def format_timezero_sailset(tws_values, bands_by_tws):
    """Fichier <SailSetPolar> TimeZero : la voilure a porter, par force de
    vent et par plage d'angle. bands_by_tws : liste parallele a tws_values
    de [(twa_min, twa_max, mainsail, frontsail), ...]."""
    out = ['<?xml version="1.0"?>',
           '<SailSetPolar xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
           'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">']
    for tws, bands in zip(tws_values, bands_by_tws):
        out.append("  <WindSet>")
        out.append(f'    <TWS value="{_tz_num(tws)}" />')
        for lo, hi, main, front in bands:
            color = TZ_SAIL_COLORS.get(front, TZ_SAIL_COLORS[TZ_DEFAULT_FRONT])
            out.append("    <SailSet>")
            out.append(f'      <ColorLeft value="{color}" />')
            out.append(f'      <ColorRight value="{color}" />')
            out.append("      <TWARange>")
            out.append(f'        <TWAMin value="{_tz_num(lo)}" />')
            out.append(f'        <TWAMax value="{_tz_num(hi)}" />')
            out.append("      </TWARange>")
            out.append(f'      <MainSail value="{main}" />')
            out.append(f'      <FrontSail value="{front}" />')
            out.append("    </SailSet>")
        out.append("  </WindSet>")
    out.append("</SailSetPolar>")
    out.append("")
    return "\n".join(out)


def timezero_sails_for_config(sails, engines, sail_map):
    """(MainSail, FrontSail) TimeZero d'une configuration Allure.

    La correspondance ne peut pas se deviner : "J1A" ou "MSF" ne veulent
    rien dire pour TimeZero, et seul l'equipage sait si telle voile est une
    grand-voile arisee ou un solent de petit temps. Elle se regle donc une
    fois pour toutes (Parametres > Voiles & moteurs) et vit dans sail_map :
    {code Allure: valeur TimeZero}.

    Une configuration sans aucune voile (moteur seul) devient "Off" : c'est
    la valeur que TimeZero emploie lui-meme quand la grand-voile est
    affalee, et c'est exactement le cas d'un cargo qui avance au moteur."""
    main = front = None
    for code in sails:
        v = (sail_map or {}).get(code)
        if v in TZ_MAINSAILS and main is None:
            main = v
        elif v in TZ_FRONTSAILS and front is None:
            front = v
    if main is None:
        main = TZ_DEFAULT_MAIN if sails else "Off"
    if front is None:
        front = TZ_DEFAULT_FRONT
    return main, front


def format_csv_table(configs_tables, decimals=2, confidences=None):
    """
    configs_tables : liste de (sails, engines, derive, twa_values, tws_values, grid, counts).
    Format "long" (une ligne par case renseignee) : moins visuel que le
    .pol mais plus facile a retraiter/filtrer dans un tableur, et permet
    d'exporter plusieurs configurations dans un seul fichier.

    confidences : liste PARALLELE de grilles de confiance (voir
    PolarSampleStore.confidence_table), ou None. Fournie, elle ajoute les
    colonnes qui disent ce que vaut chaque case -- sans jamais en retirer
    aucune : l'indice informe, il ne filtre pas.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    head = ["voiles", "moteurs", "derive", "twa_deg", "tws_kn", "stw_kn", "n_echantillons"]
    if confidences is not None:
        head += ["confiance", "niveau", "moments_independants", "sorties", "dispersion_pct"]
    writer.writerow(head)
    for idx, (sails, engines, derive, twa_values, tws_values, grid, counts) in enumerate(
            configs_tables):
        conf = (confidences[idx] if confidences is not None and idx < len(confidences)
                else None)
        for i, (twa, row, count_row) in enumerate(zip(twa_values, grid, counts)):
            for j, (tws, v, n) in enumerate(zip(tws_values, row, count_row)):
                if v is None:
                    continue
                line = ["+".join(sails) or "-", "+".join(engines) or "-",
                        DERIVE_LABELS.get(derive, "-"), twa, tws, round(v, decimals), n]
                if confidences is not None:
                    c = (conf[i][j] if conf and i < len(conf) and j < len(conf[i]) else None)
                    if c:
                        line += [c["score"], c["level"], c["blocks"], c["sessions"],
                                 ("" if c["spread"] is None
                                  else round(100.0 * c["spread"], 1))]
                    else:
                        line += ["", "", "", "", ""]
                writer.writerow(line)
    return buf.getvalue()


# =========================================================================
# Rejeu d'un fichier .log (mode import)
# =========================================================================

_LOG_LINE_RE = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d+) \[(port\d+)\] (.*)$")


def replay_log_lines(path):
    """
    Generateur (t_secondes, port, raw) a partir d'un fichier .log
    produit par nmea_sniffer.py ou ce meme programme
    ("HH:MM:SS.mmm [portX] RAW"). t_secondes est un flottant monotone
    croissant DEPUIS MINUIT (gere le passage de minuit en detectant un
    retour en arriere de plus d'1 s) -- l'appelant (couche application)
    est responsable d'y ajouter l'epoch de la date du fichier si des
    horodatages absolus sont necessaires (ex. pour croiser avec des
    segments de configuration exprimes en date+heure).
    """
    day_offset = 0.0
    prev_secs = None
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _LOG_LINE_RE.match(line.rstrip("\n"))
            if not m:
                continue
            hh, mm, ss, frac, port, raw = m.groups()
            secs = int(hh) * 3600 + int(mm) * 60 + int(ss) + float("0." + frac)
            # Un retour en arriere de l'heure a DEUX causes possibles, et les
            # confondre coutait une session entiere :
            #   - le passage de MINUIT (23:59 -> 00:00) : recul enorme, proche
            #     de 24 h -- il faut ajouter un jour ;
            #   - un CHANGEMENT D'HEURE du bord (on retarde l'horloge d'une
            #     heure en changeant de fuseau, ou l'heure d'hiver) : recul
            #     d'une heure ou deux -- il ne faut RIEN ajouter. L'ancien
            #     code ajoutait un jour, et tout ce qui suivait le changement
            #     se retrouvait 23 h dans le futur : hors de tous les
            #     segments d'annotation, donc rejete en silence.
            # Le seuil a 12 h separe les deux sans ambiguite : aucun bord ne
            # retarde son horloge de plus de quelques heures, et un passage
            # de minuit recule toujours de presque un jour.
            if prev_secs is not None and secs < prev_secs - 1.0:
                if prev_secs - secs > 12 * 3600.0:
                    day_offset += 86400.0
                # sinon : changement d'heure -- les instants restent tels
                # qu'ecrits, coherents avec l'horloge du bord et les
                # annotations saisies sur cette meme horloge.
            prev_secs = secs
            yield secs + day_offset, port, raw


# =========================================================================
# Statistiques de tendance (page "Statistiques")
# =========================================================================
#
# Ce bloc repond a une question differente de tout le reste du module. La
# polaire demande des instants STABLES et jette tout le reste ; les
# statistiques, elles, veulent au contraire TOUT ce qui passe, manoeuvres
# comprises, parce que ce qu'on y cherche n'est pas la performance du bateau
# mais la METEO et la marche du bord : le vent forcit-il, la pression
# baisse-t-elle, ai-je fait plus de route cette heure-ci que la precedente.
#
# D'ou un enregistreur separe, volontairement simple : des series
# (instant, valeur) bornees par un horizon, et un accumulateur de distance
# par heure pleine. Aucun lissage, aucun filtre de manoeuvre -- les moyennes
# glissantes se calculent a la demande sur la fenetre demandee.
#
# Il est alimente par PolarEngine (attribut .trend), donc par les MEMES
# valeurs arbitrees que les cadrans : les statistiques ne peuvent pas
# raconter une autre histoire que le suivi en direct. Et comme le meme
# moteur sait rejouer un fichier, remplir l'historique au demarrage depuis
# le tampon glissant ne demande aucun code de decodage supplementaire (voir
# App._backfill_trend).

# Grandeurs suivies. kind : "linear" (moyenne arithmetique, min/max),
# "angle_rel" (angle SIGNE bord/bord, -180..180, moyenne circulaire),
# "angle_abs" (releve 0..360, moyenne circulaire).
TREND_FIELDS = (
    # cle            libelle                       unite  dec  kind
    ("tws",          "Vent reel",                  "kn",   1, "linear"),
    ("aws",          "Vent apparent",              "kn",   1, "linear"),
    ("wind_gust_kn", "Rafales (station)",          "kn",   1, "linear"),
    ("twa",          "Angle du vent reel",         "deg",  0, "angle_rel"),
    ("awa",          "Angle du vent apparent",     "deg",  0, "angle_rel"),
    ("wind_dir_deg", "Direction du vent reel",     "deg",  0, "angle_abs"),
    ("stw",          "Vitesse surface",            "kn",   2, "linear"),
    ("sog",          "Vitesse fond",               "kn",   2, "linear"),
    ("cog",          "Route fond",                 "deg",  0, "angle_abs"),
    ("pressure",     "Pression atmospherique",     "hPa",  1, "linear"),
    ("air_temp",     "Temperature de l'air",       "deg C", 1, "linear"),
    ("water_temp",   "Temperature de l'eau",       "deg C", 1, "linear"),
    ("humidity",     "Humidite relative",          "%",    0, "linear"),
    ("depth",        "Profondeur",                 "m",    1, "linear"),
)
TREND_KEYS = tuple(k for k, _l, _u, _d, _t in TREND_FIELDS)
TREND_META = {k: (lbl, unit, dec, kind) for k, lbl, unit, dec, kind in TREND_FIELDS}

# Grandeurs qui viennent des cadrans (PolarEngine._note_display) plutot que
# de extract_ambient : elles sont deja arbitrees et, pour le vent vrai,
# RECALCULEES a partir du vent apparent (voir la note en tete de module).
TREND_FROM_DISPLAY = ("twa", "tws", "stw", "sog", "awa", "aws")

# Fenetres proposees par defaut, en secondes. L'instantane n'en fait pas
# partie : c'est la derniere valeur connue, pas une moyenne.
TREND_WINDOWS_DEFAULT = (300, 600, 1800, 3600)
TREND_WINDOW_CHOICES = (60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600)

# Au-dela de ce trou entre deux mesures de vitesse fond, on n'integre plus :
# supposer que le bateau a tenu sa vitesse pendant une coupure de dix
# minutes fabriquerait des milles qui n'ont pas ete parcourus.
TREND_MAX_GAP_S = 120.0

# Horizon par defaut des series (2 h) : de quoi servir une fenetre de 60 min
# et une courbe qui montre d'ou l'on vient. L'accumulateur horaire, lui, ne
# garde que des totaux et couvre bien plus loin pour presque rien.
TREND_HORIZON_DEFAULT_S = 7200.0
TREND_HOURS_KEPT = 72


def _circular_mean_deg(values):
    """Moyenne circulaire, en degres. None si la resultante est nulle (des
    angles exactement opposes n'ont pas de moyenne, et en inventer une
    serait pire que de ne rien dire)."""
    x = sum(math.cos(math.radians(v)) for v in values)
    y = sum(math.sin(math.radians(v)) for v in values)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return math.degrees(math.atan2(y, x))


def _linear_slope(points):
    """Pente d'une regression lineaire sur [(t, v)], en unite PAR SECONDE.
    None si moins de deux instants distincts."""
    n = len(points)
    if n < 2:
        return None
    t0 = points[0][0]
    sx = sy = sxx = sxy = 0.0
    for t, v in points:
        x = t - t0
        sx += x
        sy += v
        sxx += x * x
        sxy += x * v
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-9:
        return None
    return (n * sxy - sx * sy) / denom


class TrendRecorder:
    """
    Historique court de toutes les grandeurs suivies + distance sur le fond
    par heure pleine.

    Ecrit depuis le fil d'ecoute UDP (via PolarEngine) et lu depuis
    l'interface : add() est protege par un verrou, et toutes les lectures
    rendent des COPIES -- une liste qu'on parcourt pendant qu'une trame
    arrive n'a pas a se demander si elle change sous elle.
    """

    def __init__(self, horizon_s=TREND_HORIZON_DEFAULT_S, hours_kept=TREND_HOURS_KEPT):
        self.horizon_s = float(horizon_s)
        self.hours_kept = int(hours_kept)
        self._lock = threading.Lock()
        self._series = {k: deque() for k in TREND_KEYS}
        # {cle_heure_epoch: {"nm": distance, "sec": duree integree}}
        self._hours = {}
        self._last_sog = None      # (t, sog) precedent, pour l'integration
        self.first_t = None
        self.last_t = None
        self.n_added = 0
        self.clock_jumps = 0       # changements d'heure encaisses (voir clock_jump)

    # ---------- Ecriture ----------
    def set_horizon(self, horizon_s):
        with self._lock:
            self.horizon_s = max(60.0, float(horizon_s))
            for key in self._series:
                self._prune_locked(key)

    def add(self, key, t, value):
        """Ajoute une mesure. Les valeurs implausibles sont ECARTEES en
        silence (voir AMBIENT_BOUNDS) : une capture reelle contient des
        trames tronquees, et une seule vitesse a 7000 noeuds suffirait a
        rendre une moyenne horaire absurde."""
        if key not in self._series or value is None:
            return False
        try:
            v = float(value)
        except (TypeError, ValueError):
            return False
        if v != v:  # NaN
            return False
        bounds = _TREND_BOUNDS.get(key)
        if bounds is not None and not (bounds[0] <= v <= bounds[1]):
            return False
        with self._lock:
            dq = self._series[key]
            dq.append((t, v))
            self.n_added += 1
            if self.first_t is None or t < self.first_t:
                self.first_t = t
            if self.last_t is None or t > self.last_t:
                self.last_t = t
            if key == "sog":
                self._integrate_locked(t, v)
            self._prune_locked(key)
        return True

    def _prune_locked(self, key):
        dq = self._series[key]
        if not dq:
            return
        cutoff = dq[-1][0] - self.horizon_s
        while dq and dq[0][0] < cutoff:
            dq.popleft()

    def _integrate_locked(self, t, sog):
        """Distance sur le fond, repartie par HEURE PLEINE locale.

        Trapeze entre deux lectures consecutives, decoupe aux frontieres
        d'heure : une lecture a 09:59 et la suivante a 10:01 n'appartiennent
        pas a la meme heure, et tout mettre dans l'une des deux fausserait
        les deux."""
        prev = self._last_sog
        self._last_sog = (t, sog)
        if prev is None:
            return
        t0, v0 = prev
        dt = t - t0
        if dt <= 0 or dt > TREND_MAX_GAP_S:
            return
        speed = (v0 + sog) / 2.0
        # Decoupage aux frontieres d'heure locale.
        cur = t0
        while cur < t:
            h_start = _hour_floor_epoch(cur)
            h_end = h_start + 3600.0
            seg_end = min(t, h_end)
            seg = seg_end - cur
            if seg > 0:
                e = self._hours.setdefault(h_start, {"nm": 0.0, "sec": 0.0})
                e["nm"] += speed * seg / 3600.0
                e["sec"] += seg
            cur = seg_end if seg_end > cur else cur + 1.0
        if len(self._hours) > self.hours_kept:
            for k in sorted(self._hours)[:len(self._hours) - self.hours_kept]:
                del self._hours[k]

    def clear(self):
        with self._lock:
            for dq in self._series.values():
                dq.clear()
            self._hours.clear()
            self._last_sog = None
            self.first_t = self.last_t = None
            self.n_added = 0

    def clock_jump(self, t):
        """L'horloge du bord vient de RECULER jusqu'a t : les mesures deja
        enregistrees avec des horodatages posterieurs sont jetees. Elles ne
        sont pas fausses en soi, mais chaque fenetre glissante les aurait
        comptees comme "recentes" pendant toute la duree du recul -- une
        moyenne sur 5 minutes melangee a une heure d'ecart, c'est une
        moyenne qui ment. Perdre une heure de tendances (affichage seul,
        rien n'entre dans une polaire) vaut mieux que les afficher fausses."""
        with self._lock:
            for dq in self._series.values():
                while dq and dq[-1][0] > t:
                    dq.pop()
            if self._last_sog is not None and self._last_sog[0] > t:
                self._last_sog = None
            h_now = _hour_floor_epoch(t)
            for key in [k for k in self._hours if k > h_now]:
                del self._hours[key]
            starts = [dq[0][0] for dq in self._series.values() if dq]
            ends = [dq[-1][0] for dq in self._series.values() if dq]
            self.first_t = min(starts) if starts else None
            self.last_t = max(ends) if ends else None
            self.clock_jumps += 1

    def absorb(self, other, replace_upto=None):
        """Incorpore l'historique d'un AUTRE enregistreur.

        Sert au remplissage depuis le tampon glissant : celui-ci est
        rejoue dans un enregistreur neuf, a part, puis verse ici. Rejouer
        des heures anciennes directement dans l'enregistreur vivant aurait
        melange des horodatages passes a des horodatages presents -- series
        dans le desordre, et surtout une integration de distance qui aurait
        compte des allers-retours dans le temps.

        replace_upto : les mesures locales anterieures a cet instant sont
        JETEES avant la fusion. C'est le cas normal du remplissage depuis
        le tampon, qui contient exactement les memes trames que le direct :
        sans cela, la plage commune serait comptee deux fois -- et pas
        proprement, car l'horodatage du tampon (arrondi a la milliseconde a
        l'ecriture) ne retombe pas au meme flottant que celui du direct, si
        bien qu'aucun dedoublonnage par instant ne les reconnaitrait.

        Pour les heures pleines, on garde de chaque cote la version la PLUS
        COMPLETE -- celle qui couvre le plus de secondes."""
        with other._lock:
            src = {k: list(dq) for k, dq in other._series.items()}
            src_hours = {k: dict(v) for k, v in other._hours.items()}
        with self._lock:
            for key, pts in src.items():
                if not pts:
                    continue
                dq = self._series.setdefault(key, deque())
                keep = [(t, v) for t, v in dq
                        if replace_upto is None or t > replace_upto]
                merged = sorted(keep + pts, key=lambda p: p[0])
                dq.clear()
                dq.extend(merged)
                self._prune_locked(key)
            for hkey, e in src_hours.items():
                cur = self._hours.get(hkey)
                if cur is None or e["sec"] > cur["sec"]:
                    self._hours[hkey] = dict(e)
            if len(self._hours) > self.hours_kept:
                for k in sorted(self._hours)[:len(self._hours) - self.hours_kept]:
                    del self._hours[k]
            ts = [dq[0][0] for dq in self._series.values() if dq]
            te = [dq[-1][0] for dq in self._series.values() if dq]
            self.first_t = min(ts) if ts else None
            self.last_t = max(te) if te else None
            self.n_added += sum(len(v) for v in src.values())

    # ---------- Lecture ----------
    def series(self, key, since=None):
        with self._lock:
            dq = self._series.get(key)
            if not dq:
                return []
            if since is None:
                return list(dq)
            return [(t, v) for t, v in dq if t >= since]

    def last(self, key):
        with self._lock:
            dq = self._series.get(key)
            return (dq[-1][0], dq[-1][1]) if dq else None

    def covered_s(self, now=None):
        """Duree reellement couverte par l'historique."""
        with self._lock:
            if self.first_t is None or self.last_t is None:
                return 0.0
            oldest = self.last_t - self.horizon_s
            start = max(self.first_t, oldest)
            end = self.last_t if now is None else max(self.last_t, now)
            return max(0.0, end - start)

    def stats(self, key, now, window_s):
        """Statistiques d'une grandeur sur les window_s dernieres secondes.

        None si rien dans la fenetre : une case vide dit "pas de mesure",
        ce qui est une information ; une valeur recopiee d'il y a une heure
        serait un mensonge.
        """
        meta = TREND_META.get(key)
        if meta is None:
            return None
        kind = meta[3]
        pts = self.series(key, since=now - window_s)
        if not pts:
            return None
        values = [v for _t, v in pts]
        out = {
            "n": len(values), "first": values[0], "last": values[-1],
            "t_first": pts[0][0], "t_last": pts[-1][0],
            "span_s": pts[-1][0] - pts[0][0],
        }
        if kind == "linear":
            out["mean"] = sum(values) / len(values)
            out["min"] = min(values)
            out["max"] = max(values)
            slope = _linear_slope(pts)
            out["slope_per_h"] = None if slope is None else slope * 3600.0
            # Ecart type : dit si la moyenne resume quelque chose de stable
            # ou une bagarre entre 5 et 30 noeuds.
            m = out["mean"]
            out["sd"] = math.sqrt(sum((v - m) ** 2 for v in values) / len(values))
        else:
            mean = _circular_mean_deg(values)
            if mean is None:
                return None
            if kind == "angle_abs":
                mean %= 360.0
            else:
                mean = (mean + 180.0) % 360.0 - 180.0
            out["mean"] = mean
            # Min/max n'ont pas de sens sur un angle (l'ecart entre 359 et 1
            # vaut 2 degres). On rend a la place la DISPERSION angulaire :
            # 0 = tous les releves confondus, 90 = direction indecise.
            r = math.hypot(sum(math.cos(math.radians(v)) for v in values),
                           sum(math.sin(math.radians(v)) for v in values)) / len(values)
            out["spread_deg"] = math.degrees(math.acos(max(-1.0, min(1.0, r))))
            out["min"] = out["max"] = None
            out["sd"] = None
            out["slope_per_h"] = None
        return out

    def table(self, now, windows):
        """{cle: {"instant": v ou None, window_s: stats ou None}} pour toutes
        les grandeurs suivies."""
        out = {}
        for key in TREND_KEYS:
            row = {"instant": None, "instant_t": None}
            lastv = self.last(key)
            if lastv is not None:
                row["instant_t"], row["instant"] = lastv
            for w in windows:
                row[w] = self.stats(key, now, w)
            out[key] = row
        return out

    def buckets(self, key, now, step_s, count):
        """Serie AGREGEE par tranches de step_s, la derniere se terminant a
        'now' : c'est ce que mange un tableau facon Windguru.

        Rend [{"t0","t1","mean","min","max","n"}] du plus ancien au plus
        recent, avec mean=None pour une tranche sans mesure -- un trou doit
        se voir comme un trou."""
        meta = TREND_META.get(key)
        if meta is None or step_s <= 0 or count <= 0:
            return []
        kind = meta[3]
        # Tranches alignees sur l'horloge : une colonne "10:00-10:10" se
        # compare d'un jour a l'autre, "il y a 3 a 13 minutes" non.
        end = math.floor(now / step_s) * step_s + step_s
        edges = [end - (count - i) * step_s for i in range(count + 1)]
        pts = self.series(key, since=edges[0])
        slots = [[] for _ in range(count)]
        for t, v in pts:
            idx = int((t - edges[0]) // step_s)
            if 0 <= idx < count:
                slots[idx].append(v)
        out = []
        for i, vals in enumerate(slots):
            cell = {"t0": edges[i], "t1": edges[i + 1], "n": len(vals),
                    "mean": None, "min": None, "max": None}
            if vals:
                if kind == "linear":
                    cell["mean"] = sum(vals) / len(vals)
                    cell["min"] = min(vals)
                    cell["max"] = max(vals)
                else:
                    m = _circular_mean_deg(vals)
                    if m is not None:
                        cell["mean"] = m % 360.0 if kind == "angle_abs" else (m + 180.0) % 360.0 - 180.0
            out.append(cell)
        return out

    def hourly_distances(self, now=None, count=12):
        """[(debut_heure_epoch, milles, couverture_0_1)] des 'count'
        dernieres heures PLEINES, la plus recente en dernier. L'heure en
        cours y figure aussi, sa couverture disant ou l'on en est.

        La couverture est la part de l'heure reellement mesuree : 0,3 sur
        une heure signifie "18 minutes de flux", et la distance affichee
        n'est alors pas celle de l'heure entiere. Le dire vaut mieux que de
        laisser croire a une heure creuse."""
        now = time.time() if now is None else now
        with self._lock:
            hours = dict(self._hours)
        h_now = _hour_floor_epoch(now)
        out = []
        for i in range(count - 1, -1, -1):
            key = h_now - i * 3600.0
            e = hours.get(key)
            if e is None:
                out.append((key, None, 0.0))
            else:
                elapsed = 3600.0 if key < h_now else max(1.0, now - h_now)
                out.append((key, e["nm"], min(1.0, e["sec"] / elapsed)))
        return out

    def total_distance(self, since=None):
        """Distance cumulee sur le fond depuis 'since' (toutes les heures
        connues si None)."""
        with self._lock:
            return sum(e["nm"] for k, e in self._hours.items()
                       if since is None or k + 3600.0 > since)


# Bornes de plausibilite des grandeurs suivies : celles d'AMBIENT_BOUNDS
# quand elles existent, plus celles des grandeurs de cadran.
_TREND_BOUNDS = dict(AMBIENT_BOUNDS)
_TREND_BOUNDS.update({
    "tws": (0.0, 200.0), "aws": (0.0, 250.0),
    "twa": (-180.0, 180.0), "awa": (-180.0, 180.0),
    "stw": (0.0, 80.0),
})


def _hour_floor_epoch(t):
    """Debut de l'heure pleine LOCALE contenant t. Passe par la date
    complete plutot que par une division : sur les fuseaux a decalage non
    entier (Inde, Marquises, Terre-Neuve) et aux changements d'heure, un
    simple floor(t/3600) ne tombe pas sur une heure ronde locale."""
    lt = time.localtime(t)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, 0, 0, 0, 0, -1))


# =========================================================================
# Orchestrateur
# =========================================================================

class PolarEngine:
    """
    Point d'entree unique, utilise a l'identique par le mode live (UDP)
    et le mode import (rejeu de fichier .log) : ingest_line() ne sait
    pas d'ou vient la donnee, seulement (timestamp, texte brut).

    Chaque instance correspond a UNE PASSE de traitement (une session
    enregistree + son annotation). Les echantillons qu'elle produit sont
    ensuite fusionnes (store.extend) dans l'entrepot cumulatif persistant
    de l'application.
    """

    # En cas de trames du meme type (VHW ou MWV) recues simultanement sur
    # plusieurs voies (deux instruments redondants, un multiplexeur qui
    # duplique...), on considere une voie "silencieuse" pour l'arbitrage de
    # priorite si elle n'a plus rien envoye de ce type depuis ce delai --
    # au-dela, la voie suivante dans l'ordre de priorite prend le relais
    # automatiquement plutot que de rester bloque sur une source coupee.
    PRIORITY_STALE_S = 5.0

    # Delai avant d'abandonner une source EXPLICITEMENT CHOISIE au profit
    # d'une autre. Volontairement bien plus long que PRIORITY_STALE_S :
    # designer une source est une decision, pas une preference, et une voie
    # lente n'est pas une voie en panne. Sur une passerelle reelle, une
    # girouette peut n'emettre son vent apparent que toutes les 10 s (les
    # autres trames du meme type portant un contenu inexploitable) ; avec le
    # seuil court, l'application repassait sur l'autre voie entre deux
    # trames et fabriquait un melange des deux capteurs -- exactement ce que
    # choisir sa source doit empecher.
    SOURCE_FALLBACK_S = 30.0

    # Recul d'horloge au-dela duquel on considere que l'heure du bord a ete
    # CHANGEE (fuseau, heure d'hiver, remise a l'heure) et non qu'une trame
    # est arrivee dans le desordre. Deux minutes : tres au-dessus de tout
    # desordre reseau, tres en dessous du plus petit changement d'heure
    # reel (une demi-heure, sur certains fuseaux).
    CLOCK_JUMP_BACK_S = 120.0

    def __init__(self, smoother=None, store=None, port_priority=None,
                 source_by_type=None, allow_fallback=True):
        self.smoother = smoother or SteadyStateSmoother()
        self.store = store if store is not None else PolarSampleStore()
        self.journal = ConfigJournal()
        self.stats = {
            "lines": 0, "bad_checksum": 0, "unparsed": 0,
            "samples_emitted": 0, "samples_rejected_no_config": 0,
            "lines_deprioritized": 0, "clock_jumps": 0,
        }
        # Dernier instant ingere -- sert UNIQUEMENT a detecter un CHANGEMENT
        # D'HEURE du bord (l'horloge qu'on retarde en changeant de fuseau,
        # l'heure d'hiver, une remise a l'heure). Voir _on_clock_jump.
        self._last_ingest_t = None
        # dernieres valeurs contextuelles (affichage uniquement, pas utilisees pour le calcul)
        self.context = {"gga": None, "vtg": None, "mtw": None, "zda": None, "xdr": {}}
        # Derniere vitesse surface connue (VHW) : necessaire pour recalculer
        # nous-memes le vent vrai a partir du vent apparent -- voir
        # apparent_to_true() et la note en tete de module sur pourquoi on ne
        # fait plus confiance a un MWV,ref=T externe.
        self._last_stw = None
        # Ordre de priorite des voies (ex. ["port1","port2","port3","port4"]),
        # ou None pour desactiver tout arbitrage (comportement historique :
        # tout ce qui arrive est ingere, quelle que soit la voie -- utilise
        # par les appels ingest_line() sans argument 'port', notamment dans
        # les tests qui n'ont pas de notion de voie).
        self.port_priority = list(port_priority) if port_priority else None
        # Source EXPLICITEMENT choisie par type de trame : {"MWV": "port2", ...}.
        # C'est le reglage qui commande, l'ordre de priorite ci-dessus ne
        # servant plus que de repli. Designer sa source nommement vaut mieux
        # que de la deduire d'un classement : sur une passerelle reelle, deux
        # voies emettent le meme type de trame avec des CONTENUS differents
        # (deux girouettes qui ne s'accordent pas, l'une en noeuds l'autre en
        # m/s), et c'est un choix, pas un ordre de preference, qu'on veut
        # exprimer.
        # Valeurs normalisees en LISTE ORDONNEE de voies : la premiere est la
        # source principale, les suivantes ses replis dans l'ordre. La forme
        # "une seule voie" (chaine) reste acceptee -- les reglages ecrits par
        # une version anterieure ne doivent jamais devenir illisibles.
        self.source_by_type = {}
        for styp, val in (source_by_type or {}).items():
            chain = [val] if isinstance(val, str) else list(val or ())
            chain = [p for p in chain if p]
            if chain:
                self.source_by_type[styp] = chain
        # Repli quand la source choisie se tait plus de PRIORITY_STALE_S :
        # mieux vaut une source de secours qu'un trou dans la mesure. Peut
        # etre coupe pour une source unique, non negociable.
        self.allow_fallback = bool(allow_fallback)
        # Le moteur PRODUIT-IL des echantillons ? True par defaut (rejeu d'un
        # fichier, recalcul depuis le tampon, tests : tout ce qui traite une
        # plage connue collecte du debut a la fin). Le mode direct, lui, le
        # met a False entre deux prises -- voir _maybe_emit.
        self.collecting = True
        # Voies qui FOURNISSENT reellement la grandeur (trames exploitables).
        self._last_seen_by_port = {}  # {sentence_type: {port: timestamp}}
        # Voies qui EMETTENT ce type de trame, exploitable ou non : la
        # difference entre les deux est exactement le diagnostic "cette voie
        # parle mais n'apporte rien" (voir measurement_health).
        self._heard_by_port = {}      # {sentence_type: {port: timestamp}}
        # Derniere lecture instantanee et dernier echantillon lisse (stable),
        # exposes pour un affichage "en direct" cote interface -- purement
        # informatifs, jamais utilises pour le calcul de la polaire elle-meme.
        self.last_instant = None   # {"t","twa","tws","stw"}
        self.last_smoothed = None  # {"t","twa","tws","stw"}
        # Lectures instantanees supplementaires, utilisees UNIQUEMENT par le
        # suivi en direct (voir App._build_step_instruments) -- meme principe
        # que last_instant/last_smoothed ci-dessus : purement informatives,
        # jamais utilisees pour le calcul de la polaire. Volontairement
        # limitees aux trois grandeurs qui permettent de DIAGNOSTIQUER une
        # prise en cours : les deux entrees du calcul (vent apparent MWV,
        # vitesse surface VHW) et la vitesse fond (VTG), seule utile comme
        # recoupement -- un loch encrasse se trahit par un STW qui decroche
        # du SOG, panne classique qui ruine silencieusement une polaire.
        self.last_apparent = None      # {"t","awa","aws"} vent apparent brut (MWV ref=R)
        # Meme lecture, mais conservee VOIE PAR VOIE, y compris pour les
        # voies non prioritaires qui n'alimentent pas le calcul : c'est la
        # seule facon de constater qu'un anemometre ment. Deux girouettes qui
        # annoncent des angles differents produisent des polaires
        # differentes, et rien d'autre dans l'application ne le montrerait.
        self.last_apparent_by_port = {}  # {voie: {"t","awa","aws"}}
        self.last_stw_reading = None   # {"t","stw"} vitesse surface brute (VHW)
        self.last_sog_reading = None   # {"t","sog"} vitesse fond (VTG/PEUMA)
        # Vent VRAI mesure par la station meteo du bord ($PEUMA), garde a
        # part : sa direction est referencee au NORD, il ne peut donc jamais
        # servir de TWA. Sert de RECOUPEMENT (c'est lui qui a permis de
        # confirmer l'etalonnage de la girouette) et alimente la fiche.
        self.last_station_wind = None  # {"t","tws","gust","lull","dir"}
        # Derniere position GPS connue (GGA/RMC/PEUMA) -- sert UNIQUEMENT au theme
        # automatique de l'interface (lever/coucher du soleil, voir
        # sun_times) : aucune donnee de polaire n'en depend.
        self.last_position = None      # {"t","lat","lon"} degres signes
        # Historique COURT des lectures, pour l'affichage seul : il permet
        # d'amortir les chiffres montres a l'ecran (voir display_readings)
        # sans toucher a ce qui est enregistre. Un afficheur de bord dont
        # les valeurs sautent a chaque trame est illisible ; la polaire, elle,
        # a son propre lissage, bien plus long et assorti d'un filtre de
        # manoeuvre (voir SteadyStateSmoother). Les deux ne doivent pas etre
        # confondus : l'un sert l'oeil, l'autre la mesure.
        self._disp = {k: deque() for k in ("twa", "tws", "stw", "sog", "awa", "aws")}
        # Guetteur d'arret automatique sur manoeuvre (voir ManeuverWatch) --
        # pose par l'application quand la fonction est activee ; None sinon,
        # et l'ingestion n'y touche alors pas.
        self.maneuver_watch = None
        # Enregistreur de tendances (voir TrendRecorder) -- pose par
        # l'application, partage entre TOUS les moteurs successifs : chaque
        # prise fabrique un moteur neuf, alors que l'historique meteo, lui,
        # ne doit pas repartir de zero a chaque bouton "Demarrer". None =
        # rien n'est enregistre et l'ingestion ne paie rien.
        self.trend = None

    def _resolve_source(self, styp, seen, now):
        """Voie qui doit alimenter le calcul pour ce type de trame, d'apres
        l'etat 'seen' ({voie: dernier instant entendu}). Trois regles, dans
        cet ordre :

        1. une source CHOISIE pour ce type fait foi tant qu'elle parle ;
        2. si elle se tait au-dela de PRIORITY_STALE_S et que le repli est
           autorise, on redescend l'ordre de priorite (ou, a defaut, sur la
           derniere voie entendue) -- un trou dans la mesure serait pire ;
        3. sans source choisie, l'ancien arbitrage par ordre de priorite
           s'applique tel quel.

        Retourne None quand rien ne permet de trancher (aucune voie connue,
        ou aucun arbitrage configure) : l'appelant accepte alors tout, ce qui
        est le comportement voulu sur une installation a source unique."""
        chain = self.source_by_type.get(styp)
        # Une chaine dont AUCUN membre n'a jamais rien donne n'est pas un
        # choix, c'est une erreur de reglage (voie debranchee, renommee,
        # designee depuis une autre installation, ou qui n'apporte rien
        # d'exploitable pour cette grandeur). L'honorer reviendrait a
        # eteindre la mesure pour toujours : on l'ignore et l'on retombe sur
        # ce qui parle. Mieux vaut une source non choisie que pas de mesure
        # du tout -- et l'interface le signale, elle, tres explicitement.
        if chain and not any(key in seen for key in chain):
            chain = None
        if chain:
            # La chaine est PROPRE A CETTE MESURE : le vent peut preferer une
            # voie et la vitesse surface une autre, chacune avec ses propres
            # replis. C'est ce qui remplace l'ancien classement global des
            # voies, qui pretendait qu'une voie etait "meilleure" en bloc
            # alors qu'elle ne l'est que pour ce qu'elle mesure bien.
            for key in chain:
                last_t = seen.get(key)
                if last_t is not None and now - last_t <= self.SOURCE_FALLBACK_S:
                    return key
            if not self.allow_fallback:
                # Chaine entiere muette et repli general interdit : on s'en
                # tient a la source principale, quitte a ne rien recevoir.
                return chain[0]
        if self.port_priority:
            for key in self.port_priority:
                last_t = seen.get(key)
                if last_t is not None and now - last_t <= self.PRIORITY_STALE_S:
                    return key
        if chain and self.allow_fallback and seen:
            # Chaine muette, aucune voie prioritaire fraiche : on se rabat
            # sur la derniere voie entendue, quelle qu'elle soit -- mieux
            # vaut une mesure imparfaite que pas de mesure du tout.
            return max(seen.items(), key=lambda kv: kv[1])[0]
        return None

    # Duree maximale conservee pour l'amortissement d'affichage. Borne haute
    # de ce que l'interface peut demander : au-dela, ce ne serait plus un
    # amortissement mais une moyenne, et le cadran cesserait de suivre le
    # bateau.
    DISPLAY_HISTORY_S = 60.0

    def _note_ground(self, t, cog, sog):
        """Enregistre une mesure de route/vitesse FOND, d'ou qu'elle vienne
        (VTG normalisee ou station proprietaire) : meme grandeur, meme
        traitement -- affichage amorti, derniere lecture connue, et
        alimentation du guetteur de manoeuvre."""
        if sog is not None:
            self.last_sog_reading = {"t": t, "sog": sog}
            self._note_display("sog", t, sog)
        if cog is not None and self.trend is not None:
            # La route fond n'a pas de cadran (elle ne se lit pas d'un coup
            # d'oeil), mais elle a sa ligne dans les statistiques -- et elle
            # doit venir de la source ARBITREE, comme la vitesse fond, pas du
            # premier GPS qui parle.
            self.trend.add("cog", t, cog)
        if self.maneuver_watch is not None and (cog is not None or sog is not None):
            self.maneuver_watch.add(t, cog=cog, sog=sog)

    def _note_display(self, key, t, value):
        """Retient une lecture pour l'amortissement d'affichage, et oublie
        ce qui est sorti de la fenetre maximale."""
        if value is None:
            return
        dq = self._disp[key]
        dq.append((t, value))
        cutoff = t - self.DISPLAY_HISTORY_S
        while dq and dq[0][0] < cutoff:
            dq.popleft()
        # Les statistiques se nourrissent des MEMES valeurs arbitrees que les
        # cadrans : elles ne peuvent donc pas raconter une autre histoire que
        # le suivi en direct (voir TrendRecorder).
        if self.trend is not None:
            self.trend.add(key, t, value)

    def display_readings(self, now, window_s):
        """Valeurs AMORTIES pour l'affichage : moyenne glissante sur les
        window_s dernieres secondes. window_s <= 0 rend les valeurs brutes.

        Les angles (TWA, AWA) sont moyennes CIRCULAIREMENT : au vent arriere,
        le TWA oscille autour de +/-180 et une moyenne arithmetique y
        renverrait 0 -- soit le vent debout, exactement l'oppose.

        Ne retourne que les grandeurs REELLEMENT presentes dans la fenetre :
        une valeur absente reste absente, jamais remplacee par une ancienne."""
        out = {}
        if window_s is None or window_s <= 0:
            for key, dq in self._disp.items():
                if dq:
                    out[key] = dq[-1][1]
            return out
        cutoff = now - min(window_s, self.DISPLAY_HISTORY_S)
        for key, dq in self._disp.items():
            vals = [v for t, v in dq if t >= cutoff]
            if not vals:
                # Rien de frais : on garde la derniere connue plutot que de
                # vider le cadran. La peremption est jugee ailleurs, sur
                # l'horodatage de la lecture brute (voir _update_instruments).
                if dq:
                    out[key] = dq[-1][1]
                continue
            if key in ("twa", "awa"):
                x = sum(math.cos(math.radians(v)) for v in vals)
                y = sum(math.sin(math.radians(v)) for v in vals)
                if x == 0.0 and y == 0.0:
                    continue
                out[key] = math.degrees(math.atan2(y, x))
            else:
                out[key] = sum(vals) / len(vals)
        return out

    def _accept_for_priority(self, styp, port, t, usable=True):
        """True si cette voie doit alimenter le calcul pour ce type de trame a
        cet instant. Les voies ecartees restent comptees dans les statistiques
        (lines_deprioritized) et, pour le vent, restent observables voie par
        voie (last_apparent_by_port) : ecarter une source du calcul ne doit
        jamais revenir a la rendre invisible.

        usable=False : la trame porte bien ce type, mais RIEN d'exploitable
        (un MWV en reference 'T', un VHW sans vitesse...). Une telle trame ne
        doit PAS faire compter la voie comme source vivante de la grandeur.

        C'est un bug reel et grave qui a impose cette distinction : sur une
        passerelle ou une voie n'emet que du MWV,ref=T (une direction de vent
        par rapport au nord, que ce module refuse a dessein -- voir la note en
        tete de module), designer cette voie comme source du vent la faisait
        gagner l'arbitrage a chaque trame, tout en n'apportant jamais une
        seule valeur. La vraie girouette, elle, se faisait ecarter en silence,
        et le vent n'etait plus jamais lu : plus aucun echantillon, sans le
        moindre message. Une voie qui ne DONNE pas la mesure ne peut pas etre
        la source de cette mesure."""
        seen = self._last_seen_by_port.setdefault(styp, {})
        # Voies qui EMETTENT ce type de trame, exploitable ou non : c'est ce
        # qui permet de dire "cette voie parle, mais n'apporte rien" plutot
        # que de laisser une mesure muette sans explication (voir
        # measurement_health).
        self._heard_by_port.setdefault(styp, {})[port] = t
        if usable:
            seen[port] = t
        if port is None or (not self.port_priority and not self.source_by_type.get(styp)):  # noqa: E501
            return True
        active = self._resolve_source(styp, seen, t)
        if active is None:
            # Rien ne permet de trancher : mieux vaut une source non
            # prioritaire que zero donnee.
            return True
        return active == port

    def active_ports(self, now):
        """
        {type_de_trame: voie qui alimente actuellement le calcul} -- version
        LECTURE SEULE de l'arbitrage de _accept_for_priority() (qui, lui,
        memorise au passage la voie entendue et ne peut donc pas servir a un
        simple affichage). Destine au suivi en direct : savoir laquelle de
        deux sources redondantes fait foi en ce moment, et voir le basculement
        se produire quand la voie prioritaire se tait.

        Une voie n'apparait que pour les types de trames reellement recus
        (VHW/MWV) ; la valeur est None si aucune voie n'a jamais emis ce type.
        Quand aucune voie prioritaire n'est fraiche (toutes perimees au-dela
        de PRIORITY_STALE_S), on rapporte la derniere voie entendue : c'est
        l'equivalent, cote affichage, du repli "mieux vaut une source non
        prioritaire que zero donnee" applique par _accept_for_priority().
        """
        out = {}
        for styp, seen in self._last_seen_by_port.items():
            active = self._resolve_source(styp, seen, now)
            if active is None and seen:
                active = max(seen.items(), key=lambda kv: kv[1])[0]
            out[styp] = active
        return out

    def measurement_health(self, styp):
        """Etat de sante d'une grandeur, pour EXPLIQUER un cadran muet.

        Une mesure qui n'arrive pas est le probleme le plus couteux de toute
        l'application : sans explication, on cherche du cote du reseau ou de
        l'instrument alors que la cause est souvent un simple reglage. Ce
        diagnostic separe donc trois choses que l'on confond facilement :

          providers  voies qui FOURNISSENT vraiment la grandeur ;
          talkers    voies qui emettent ce type de trame -- meme sans rien
                     apporter (un MWV en reference 'T', par exemple) ;
          chain_mute voies DESIGNEES comme source qui n'ont jamais rien
                     fourni : la cause la plus frequente, et la seule que
                     l'utilisateur puisse corriger d'un clic.
        """
        usable = self._last_seen_by_port.get(styp, {})
        heard = self._heard_by_port.get(styp, {})
        chain = [k for k in (self.source_by_type.get(styp) or []) if k]
        return {
            "chain": chain,
            "providers": sorted(k for k in usable if k),
            "talkers": sorted(k for k in heard if k),
            "chain_mute": [k for k in chain if k not in usable],
            "mute_talkers": sorted(k for k in heard if k and k not in usable),
        }

    def _check_clock(self, t):
        """Detecte un changement d'heure du bord et remet l'etat temporel
        d'aplomb. Sans cela, un simple recul d'une heure PERDAIT la suite de
        la prise en silence, par trois chemins independants :

          - _last_emit_t du lisseur restait dans le futur : plus aucun
            echantillon emis avant que l'horloge ne le rattrape ;
          - le segment de configuration OUVERT commencait "dans le futur" :
            journal.resolve(t) ne trouvait plus rien, chaque echantillon
            etait rejete "sans configuration" ;
          - fenetres et rearmements (manoeuvre, affichage, tendances)
            melangeaient l'avant et l'apres du saut.

        Une AVANCE de l'horloge, elle, est inoffensive : elle se presente
        comme un simple trou dans le flux, que tout le code sait deja
        traiter."""
        last = self._last_ingest_t
        self._last_ingest_t = t
        if last is None or t >= last - self.CLOCK_JUMP_BACK_S:
            return
        self.stats["clock_jumps"] += 1
        # Fenetres de mesure : reparties a neuf. Cout : les ~15 s de
        # remplissage du lisseur -- contre une heure de prise perdue avant.
        self.smoother.reset()
        if self.maneuver_watch is not None:
            self.maneuver_watch.reset(t)
        # Affichage : on repart de lectures fraiches plutot que de moyenner
        # une heure d'ecart dans une fenetre de quelques secondes.
        for dq in self._disp.values():
            dq.clear()
        # Les "derniere fois entendu" dans le futur figeraient l'arbitrage
        # de sources pendant toute la duree du recul.
        for seen in (self._last_seen_by_port, self._heard_by_port):
            for per_port in seen.values():
                for port_key, when in per_port.items():
                    if when > t:
                        per_port[port_key] = t
        # Les dernieres lectures gardent leur VALEUR mais leur date est
        # ramenee a t : leur fraicheur redevient jugeable.
        for reading in (self.last_instant, self.last_smoothed, self.last_apparent,
                        self.last_stw_reading, self.last_sog_reading,
                        self.last_station_wind, self.last_position):
            if reading is not None and reading.get("t", 0) > t:
                reading["t"] = t
        for reading in self.last_apparent_by_port.values():
            if reading.get("t", 0) > t:
                reading["t"] = t
        # Le segment de configuration OUVERT suit l'horloge : la voilure n'a
        # pas change, c'est l'heure qui a change. Sans cela, resolve(t)
        # rendait None et la prise se vidait en silence.
        for seg in self.journal.all_segments():
            if seg.end is None and seg.start > t:
                seg.start = t
        if self.trend is not None:
            self.trend.clock_jump(t)

    def ingest_line(self, t, raw, port=None):
        self._check_clock(t)
        self.stats["lines"] += 1
        parsed = parse_sentence(raw)
        if parsed is None:
            self.stats["unparsed"] += 1
            return
        talker, styp, fields, ck = parsed
        if ck is False:
            self.stats["bad_checksum"] += 1
            return  # donnee non fiable : ecartee de tout calcul ET de l'affichage contextuel

        if styp == "VHW":
            # Decodage AVANT arbitrage : une VHW sans vitesse lisible ne fait
            # pas de cette voie une source de vitesse surface (voir
            # _accept_for_priority, argument 'usable').
            stw = extract_stw(fields)
            if self._accept_for_priority(styp, port, t, usable=stw is not None):
                self.smoother.add_stw(t, stw)
                if stw is not None:
                    self._last_stw = stw
                    self.last_stw_reading = {"t": t, "stw": stw}
                    self._note_display("stw", t, stw)
            else:
                self.stats["lines_deprioritized"] += 1
        elif styp == "MWV":
            # La trame est DECODEE dans tous les cas : une voie non
            # prioritaire n'alimente aucun calcul, elle doit malgre tout
            # rester observable pour que l'utilisateur puisse comparer ses
            # girouettes entre elles.
            mwv = extract_mwv(fields)
            # Seul un vent APPARENT (ref='R') fait de cette voie une source
            # de vent : une voie qui n'emet que du 'T' n'apporte rien ici, et
            # ne doit surtout pas evincer celle qui apporte quelque chose.
            accepted = self._accept_for_priority(
                styp, port, t, usable=(mwv is not None and mwv[0] == "R"))
            if mwv is not None:
                ref, awa, aws = mwv
                # Seul le vent APPARENT (ref='R') est boat-relatif par
                # construction, sans ambiguite possible -- voir la note en
                # tete de module. On recalcule nous-memes le TWA/TWS a
                # partir de ce vent apparent et de la derniere vitesse
                # surface connue ; toute trame ref='T' est ignoree.
                if ref == "R":
                    if port is not None:
                        self.last_apparent_by_port[port] = {"t": t, "awa": awa, "aws": aws}
                    if accepted:
                        # Expose au tableau de bord Instruments meme sans STW
                        # connue (le cadran "vent apparent" n'en a pas besoin,
                        # contrairement au recalcul du vent vrai ci-dessous).
                        self.last_apparent = {"t": t, "awa": awa, "aws": aws}
                        self._note_display("awa", t, awa)
                        self._note_display("aws", t, aws)
                        if self._last_stw is not None:
                            twa, tws = apparent_to_true(awa, aws, self._last_stw)
                            if twa is not None:
                                self.last_instant = {"t": t, "twa": twa, "tws": tws, "stw": self._last_stw}
                                self._note_display("twa", t, twa)
                                self._note_display("tws", t, tws)
                                self.smoother.add_wind(t, twa, tws)
            if not accepted:
                self.stats["lines_deprioritized"] += 1
        elif styp == "GGA":
            self.context["gga"] = fields
            # $--GGA,hhmmss,lat,N/S,lon,E/W,... -> champs 2-5.
            pos = parse_nmea_latlon(_get(fields, 2), _get(fields, 3),
                                     _get(fields, 4), _get(fields, 5))
            if pos is not None:
                self.last_position = {"t": t, "lat": pos[0], "lon": pos[1]}
        elif styp == "RMC":
            # $--RMC,hhmmss,A,lat,N/S,lon,E/W,... -> champs 3-6 (statut A =
            # position valide ; V = a ignorer).
            status = _get(fields, 2)
            if status is not None and status.strip().upper() == "A":
                pos = parse_nmea_latlon(_get(fields, 3), _get(fields, 4),
                                         _get(fields, 5), _get(fields, 6))
                if pos is not None:
                    self.last_position = {"t": t, "lat": pos[0], "lon": pos[1]}
        elif styp == "VTG":
            self.context["vtg"] = fields
            # La vitesse fond s'arbitre comme les deux autres grandeurs :
            # sur une passerelle qui porte deux GPS, le choix fait dans les
            # reglages doit VRAIMENT trancher. Sans cet appel, la source
            # "Vitesse fond" etait un reglage decoratif : la derniere voie
            # arrivee gagnait toujours.
            sog = extract_sog(fields)
            cog_s = _get(fields, 1)
            try:
                cog = float(cog_s) % 360.0 if cog_s else None
            except ValueError:
                cog = None
            accepted = self._accept_for_priority(
                "VTG", port, t, usable=(sog is not None or cog is not None))
            if accepted:
                self._note_ground(t, cog, sog)
            else:
                self.stats["lines_deprioritized"] += 1
        elif styp == "PEUMA":
            # Station meteo du bord : route et vitesse fond au meme titre
            # qu'une VTG (elle concourt pour cette grandeur, voir
            # SENTENCE_MEASURES), plus tout le contexte -- pression,
            # temperature, humidite, vent vrai de reference, position.
            self.context["peuma"] = fields
            peu = extract_peuma(fields)
            # Elle concourt pour la VITESSE FOND (voir SENTENCE_MEASURES),
            # mais uniquement quand elle la fournit vraiment : une trame
            # tronquee (extract_peuma rend {}) ne fait pas de cette voie une
            # source, et n'evince donc aucun GPS.
            has_ground = peu.get("cog") is not None or peu.get("sog") is not None
            accepted = self._accept_for_priority("VTG", port, t, usable=has_ground)
            if peu.get("lat") is not None and peu.get("lon") is not None:
                # Position en degres decimaux : elle vaut celle d'une GGA
                # pour le theme automatique (lever/coucher du soleil).
                self.last_position = {"t": t, "lat": peu["lat"], "lon": peu["lon"]}
            if peu.get("wind_true_kn") is not None:
                # Vent VRAI de la station, garde a part et JAMAIS injecte
                # dans la polaire : sa direction est referencee au nord, pas
                # a l'etrave. Sert de recoupement (comparaison des
                # girouettes) et alimente la fiche d'une minute archivee.
                self.last_station_wind = {
                    "t": t, "tws": peu["wind_true_kn"],
                    "gust": peu.get("wind_gust_kn"), "lull": peu.get("wind_lull_kn"),
                    "dir": peu.get("wind_dir_deg"),
                }
            if accepted:
                self._note_ground(t, peu.get("cog"), peu.get("sog"))
            elif has_ground:
                self.stats["lines_deprioritized"] += 1
        elif styp == "MTW":
            self.context["mtw"] = fields
        elif styp == "ZDA":
            self.context["zda"] = fields
        elif styp == "XDR":
            groups = fields[1:]
            for i in range(0, max(0, len(groups) - 3), 4):
                typ, val, unit, name = groups[i], groups[i + 1], groups[i + 2], groups[i + 3]
                if typ and val:
                    self.context["xdr"][(typ.strip().upper(), (name or "").strip())] = val

        # Grandeurs d'ambiance (pression, temperatures, humidite, vent de la
        # station...) pour les statistiques : elles ne concernent aucun
        # calcul de polaire, d'ou ce passage a part, tout au bout, et
        # seulement si quelqu'un les ecoute. Vitesse et route fond en sont
        # EXCLUES : elles ont deja ete servies plus haut par la source
        # arbitree (voir _note_ground), et les reprendre ici laisserait
        # n'importe quel GPS secondaire ecrire dans la meme serie.
        if self.trend is not None:
            for key, val in extract_ambient(styp, fields).items():
                if key in ("sog", "cog"):
                    continue
                self.trend.add(key, t, val)

        self._maybe_emit(t)

    def tick(self, t):
        """A appeler periodiquement (mode live) meme sans nouvelle trame, pour ne pas
        manquer une fenetre stable si le flux ralentit."""
        self._check_clock(t)
        self._maybe_emit(t)

    def _maybe_emit(self, t):
        if not self.collecting:
            # Hors acquisition : les trames continuent d'alimenter les
            # lectures instantanees (le suivi en direct doit rester vivant)
            # mais AUCUN echantillon n'est produit. Sans ce garde-fou, les
            # trames qui arrivent entre deux prises -- les voies restent
            # ouvertes pour le tampon glissant -- fabriquaient des
            # echantillons hors de toute acquisition : compteur de rejets qui
            # gonfle, avertissement "aucune configuration active" affiche
            # alors que rien n'enregistre, et surtout des mesures etrangeres
            # qui se seraient retrouvees dans la passe suivante.
            return
        sample = self.smoother.maybe_sample(t)
        if sample is None:
            return
        stw, twa, tws = sample
        # Expose l'echantillon lisse meme s'il finit rejete faute de config
        # (branche ci-dessous) : cote interface, le voir apparaitre "en
        # direct" pendant l'enregistrement aide a remarquer tout de suite
        # un oubli de case voile/moteur, plutot qu'apres coup au traitement.
        self.last_smoothed = {"t": t, "stw": stw, "twa": twa, "tws": tws}
        cfg = self.journal.resolve(t)
        if cfg is None:
            self.stats["samples_rejected_no_config"] += 1
            return
        sails, engines, derive = cfg
        self.store.add(sails, engines, stw, twa, tws, t=t, derive=derive)
        self.stats["samples_emitted"] += 1

    # ---- pilotage live ----
    def set_live_config(self, t, sails, engines, derive=None):
        return self.journal.set_live_config(t, sails, engines, derive)

    # ---- annotation a posteriori (import) ----
    def add_manual_segment(self, start, end, sails, engines, derive=None):
        return self.journal.add_manual_segment(start, end, sails, engines, derive)
