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
allure_buffer.py
================
Tampon d'enregistrement GLISSANT des trames NMEA brutes : conserve en
permanence les N dernieres heures de flux (48 h par defaut, reglable),
purge automatiquement au-dela, et sait relire n'importe quelle plage
date/heure a l'interieur de la fenetre conservee.

Motivation -- ce tampon change la nature de l'outil : jusqu'ici il fallait
avoir pense a cliquer sur "Demarrer l'enregistrement" AVANT le bord
interessant. Avec un tampon permanent, la question ne se pose plus : tout
est deja enregistre, et on decoupe apres coup les plages qui valent la
peine. Le meme tampon alimente le journal passerelle horaire.

Choix de conception
-------------------

1. FICHIERS HORAIRES TOURNANTS plutot qu'un seul gros fichier reecrit en
   permanence. Purger revient a supprimer les fichiers trop vieux (aucune
   reecriture, aucun risque de corrompre l'historique en cours d'ecriture),
   et relire une plage n'ouvre que les heures concernees et non 48 h de
   flux. Un arret brutal de l'application ne peut couter au pire que la
   derniere ligne en cours d'ecriture.

2. LE MEME FORMAT DE LIGNE que les journaux de session existants
   ("HH:MM:SS.mmm [portX] $TRAME"), avec la DATE PORTEE PAR LE NOM DU
   FICHIER (nmea_AAAAMMJJ_HH.log). Deux consequences voulues :
     - allure_engine.replay_log_lines() relit ces fichiers tels quels, sans
       la moindre adaptation ;
     - l'horodatage devient ABSOLU (date + heure), ce qui corrige la
       limite connue des .log importes, qui ne portaient qu'une heure sans
       date (voir App._sample_absolute_epoch dans allure.py).

3. HEURE LOCALE, comme les journaux de session existants -- c'est l'heure
   du bord, celle dans laquelle l'utilisateur raisonne et remplit son
   journal passerelle. Le flux NMEA transporte par ailleurs l'heure UTC
   (trame ZDA), disponible dans les trames elles-memes pour qui en a
   besoin.

Ce module ne connait ni Tkinter ni UDP : il recoit des lignes deja
recues (write) et rend des lignes (iter_range). Testable sans interface.

Allure -- ETDEL 2026
"""

import os
import re
import threading
import time

# nmea_AAAAMMJJ_HH.log -- la date est dans le nom, l'heure du debut de
# tranche aussi (voir la note 2 en tete de module).
_FILE_RE = re.compile(r"^nmea_(\d{4})(\d{2})(\d{2})_(\d{2})\.log$")

# Meme motif que allure_engine._LOG_LINE_RE, redefini ici pour que ce module
# reste utilisable seul (et pour ne pas dependre d'un detail interne d'un
# autre module).
_LINE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+\[(\w+)\]\s+(\$.*)$")

DEFAULT_RETENTION_H = 48
MIN_RETENTION_H = 1
MAX_RETENTION_H = 720  # 30 jours -- garde-fou, pas une recommandation


def _hour_key(t):
    """(annee, mois, jour, heure) locale d'un horodatage epoch."""
    lt = time.localtime(t)
    return (lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour)


def _hour_start_epoch(key):
    """Epoch du debut de la tranche horaire (heure locale).

    L'indicateur d'heure d'ete est laisse a -1 : c'est a la bibliotheque de
    trancher selon la date, sans quoi les tranches situees de part et
    d'autre d'un changement d'heure seraient decalees d'une heure."""
    y, mo, d, h = key
    return time.mktime((y, mo, d, h, 0, 0, 0, 0, -1))


def _file_name(key):
    y, mo, d, h = key
    return f"nmea_{y:04d}{mo:02d}{d:02d}_{h:02d}.log"


def parse_file_name(name):
    """(annee, mois, jour, heure) d'un nom de fichier de tampon, ou None si
    ce n'est pas un fichier de tampon (le dossier peut contenir autre
    chose : on ignore, on ne se plaint pas)."""
    m = _FILE_RE.match(os.path.basename(name))
    if not m:
        return None
    y, mo, d, h = (int(g) for g in m.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31 and 0 <= h <= 23):
        return None
    return (y, mo, d, h)


def format_line(t, port, raw):
    """Ligne de tampon a partir d'un horodatage epoch. Format identique a
    celui des journaux de session (voir la note 2 en tete de module)."""
    lt = time.localtime(t)
    ms = int((t - int(t)) * 1000)
    return f"{lt.tm_hour:02d}:{lt.tm_min:02d}:{lt.tm_sec:02d}.{ms:03d} [{port}] {raw}\n"


class NmeaBuffer:
    """
    Tampon glissant. Ecrit depuis les fils d'ecoute UDP (write() est donc
    protege par un verrou), relu depuis l'interface.

    Toutes les methodes de lecture tolerent un dossier absent, vide ou
    contenant des fichiers etrangers : le tampon ne doit jamais empecher
    l'application de demarrer.
    """

    def __init__(self, directory, retention_h=DEFAULT_RETENTION_H):
        self.directory = directory
        self.retention_h = self._clamp_retention(retention_h)
        self._lock = threading.Lock()
        self._fh = None
        self._open_key = None
        self._last_purge_key = None
        self.bytes_written = 0
        self.lines_written = 0
        self.write_errors = 0

    # ---------- Reglages ----------
    @staticmethod
    def _clamp_retention(hours):
        try:
            h = int(hours)
        except (TypeError, ValueError):
            return DEFAULT_RETENTION_H
        return max(MIN_RETENTION_H, min(MAX_RETENTION_H, h))

    def set_retention(self, hours, now=None):
        """Change la duree conservee. Reduire la duree purge immediatement
        l'excedent -- l'utilisateur qui passe de 48 h a 6 h s'attend a
        recuperer la place tout de suite, pas au prochain changement
        d'heure."""
        with self._lock:
            self.retention_h = self._clamp_retention(hours)
        self.purge(now if now is not None else time.time())

    # ---------- Ecriture ----------
    def write(self, t, port, raw):
        """Ajoute une trame. Appele depuis les fils d'ecoute UDP : doit
        rester bref et ne JAMAIS lever d'exception -- une erreur d'ecriture
        (disque plein, dossier supprime sous les pieds) est comptee puis
        ignoree, car faire tomber le fil d'ecoute couterait aussi
        l'enregistrement en cours, bien plus precieux que le tampon."""
        try:
            with self._lock:
                key = _hour_key(t)
                if key != self._open_key:
                    self._roll_locked(key)
                if self._fh is None:
                    return False
                line = format_line(t, port, raw)
                self._fh.write(line)
                self._fh.flush()
                self.bytes_written += len(line)
                self.lines_written += 1
            return True
        except Exception:
            self.write_errors += 1
            self._close_quietly()
            return False

    def _roll_locked(self, key):
        """Bascule sur la tranche horaire 'key'. Appele avec le verrou."""
        self._close_locked()
        try:
            os.makedirs(self.directory, exist_ok=True)
            self._fh = open(os.path.join(self.directory, _file_name(key)), "a", encoding="utf-8")
            self._open_key = key
        except OSError:
            self._fh = None
            self._open_key = None
            self.write_errors += 1
            return
        # Purge au changement d'heure seulement : inutile de parcourir le
        # dossier a chaque trame (plusieurs par seconde).
        if self._last_purge_key != key:
            self._last_purge_key = key
            self._purge_locked(_hour_start_epoch(key))

    def _close_locked(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._open_key = None

    def _close_quietly(self):
        try:
            with self._lock:
                self._close_locked()
        except Exception:
            pass

    def close(self):
        with self._lock:
            self._close_locked()

    # ---------- Inventaire ----------
    def files(self):
        """[(cle_horaire, chemin)] triees du plus ancien au plus recent."""
        try:
            names = os.listdir(self.directory)
        except OSError:
            return []
        out = []
        for name in names:
            key = parse_file_name(name)
            if key is not None:
                out.append((key, os.path.join(self.directory, name)))
        out.sort()
        return out

    def size_bytes(self):
        total = 0
        for _key, path in self.files():
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    def span(self):
        """(epoch_debut, epoch_fin) reellement couverts par le tampon, ou
        None s'il est vide. Les bornes sont celles des TRAMES REELLEMENT
        PRESENTES, pas les bornes theoriques des tranches horaires : c'est la
        seule reponse honnete a "de quand a quand ai-je des donnees ?".

        Les extremes sont pris par MINIMUM et MAXIMUM sur la premiere et la
        derniere tranche, et non sur la premiere/derniere ligne ecrite : en
        fonctionnement normal les lignes arrivent dans l'ordre, mais rien ne
        le garantit (une trame reinjectee apres coup, un horodatage repris
        d'une source externe), et une seule ligne dans le desordre suffisait
        a rendre un debut posterieur a la fin -- avec pour consequence de
        declarer "hors tampon" des donnees parfaitement presentes."""
        files = self.files()
        if not files:
            return None
        start = None
        for key, path in files:
            ts = [t for t, _p, _r in self._iter_file(key, path)]
            if ts:
                start = min(ts)
                break
        end = None
        for key, path in reversed(files):
            ts = [t for t, _p, _r in self._iter_file(key, path)]
            if ts:
                end = max(ts)
                break
        if start is None and end is None:
            # Tranches presentes mais toutes illisibles/vides : on retombe
            # sur les bornes theoriques plutot que de pretendre le tampon vide.
            return (_hour_start_epoch(files[0][0]), _hour_start_epoch(files[-1][0]) + 3600.0)
        if start is None:
            start = end
        if end is None:
            end = start
        return (start, end) if start <= end else (end, start)

    # ---------- Relecture ----------
    def _iter_file(self, key, path):
        """(epoch, port, trame) d'un fichier de tranche horaire."""
        y, mo, d, h = key
        try:
            fh = open(path, "r", encoding="utf-8", errors="replace")
        except OSError:
            return
        with fh:
            for line in fh:
                m = _LINE_RE.match(line.rstrip("\n"))
                if not m:
                    continue
                hh, mm, ss, frac, port, raw = m.groups()
                hh = int(hh)
                day_shift = 0
                # Garde-fou pour la ligne ecrite juste apres minuit mais
                # encore dirigee vers le fichier de 23 h (et l'inverse) :
                # sans cela, sa date serait fausse d'un jour entier.
                if h == 23 and hh == 0:
                    day_shift = 1
                elif h == 0 and hh == 23:
                    day_shift = -1
                base = time.mktime((y, mo, d + day_shift, hh, int(mm), int(ss), 0, 0, -1))
                yield base + int(frac) / 1000.0, port, raw

    def iter_range(self, t_from, t_to):
        """(epoch, port, trame) pour toutes les trames de la plage, dans
        l'ordre chronologique. Bornes incluses."""
        if t_from > t_to:
            t_from, t_to = t_to, t_from
        for key, path in self.files():
            file_start = _hour_start_epoch(key)
            # Une tranche horaire couvre au plus [debut, debut + 1 h] ; on
            # elargit d'une minute pour absorber une ligne de bordure
            # (voir le garde-fou de minuit dans _iter_file).
            if file_start > t_to + 60 or file_start + 3600 < t_from - 60:
                continue
            for t, port, raw in self._iter_file(key, path):
                if t_from <= t <= t_to:
                    yield t, port, raw

    def extract_to_log(self, t_from, t_to, dest_path):
        """Ecrit la plage demandee dans un fichier .log autonome, au format
        des journaux de session -- directement reexploitable par le mode
        import et par allure_engine.replay_log_lines(). Retourne le nombre
        de lignes ecrites."""
        n = 0
        with open(dest_path, "w", encoding="utf-8") as fh:
            for t, port, raw in self.iter_range(t_from, t_to):
                fh.write(format_line(t, port, raw))
                n += 1
        return n

    # ---------- Purge ----------
    def purge(self, now=None):
        """Supprime les tranches horaires sorties de la fenetre conservee.
        Retourne le nombre de fichiers supprimes."""
        with self._lock:
            return self._purge_locked(now if now is not None else time.time())

    def _purge_locked(self, now):
        files = self.files()
        # La purge se mesure a l'horloge, mais JAMAIS au-dela de ce que les
        # donnees elles-memes racontent : si l'heure du bord vient d'etre
        # avancee (changement de fuseau, remise a l'heure d'une machine dont
        # la pile etait morte, annee mal reglee corrigee d'un coup), un
        # cutoff calcule sur la seule horloge pouvait declarer perime le
        # tampon ENTIER et le supprimer -- une perte de donnees reelle pour
        # une erreur d'horloge. On borne donc la reference par la fin de la
        # tranche la plus recente : quoi qu'ait fait l'horloge, les
        # dernieres retention_h heures DE FLUX ENREGISTRE survivent.
        if files:
            newest_end = _hour_start_epoch(files[-1][0]) + 3600.0
            now = min(now, newest_end)
        cutoff = now - self.retention_h * 3600.0
        removed = 0
        for key, path in files:
            # On compare la FIN de la tranche : un fichier n'est perime que
            # lorsque meme sa derniere seconde est sortie de la fenetre,
            # sinon on jetterait des donnees encore demandees.
            if _hour_start_epoch(key) + 3600.0 <= cutoff:
                if key == self._open_key:
                    continue  # jamais le fichier en cours d'ecriture
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
        return removed

    def clear(self):
        """Vide entierement le tampon (fichier courant compris). Retourne le
        nombre de fichiers supprimes."""
        with self._lock:
            self._close_locked()
            removed = 0
            for _key, path in self.files():
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
            return removed
