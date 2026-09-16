# Allure — Générateur de polaires

[![Licence GPL-3.0-or-later](https://img.shields.io/badge/licence-GPL--3.0--or--later-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776AB.svg)](https://www.python.org/downloads/)
[![Windows](https://img.shields.io/badge/plateforme-Windows-0078D6.svg)](#1-installation)

Générateur de polaires de vitesse à partir de trames NMEA0183, avec tampon
d'enregistrement glissant, suivi en direct et entrepôt cumulatif.

**À qui ça sert.** À l'équipage d'un voilier dont la passerelle diffuse ses
trames NMEA0183 sur le réseau du bord (UDP), et qui veut savoir ce que le
bateau *fait vraiment* : Allure écoute les trames, enregistre des passes
annotées par voilure, les accumule sortie après sortie dans un entrepôt, et
en tire des polaires par configuration, une polaire max pour le routage
(`.pol`, `.csv`, TimeZero) et un guide de voilure. Version actuelle :
2.0b — l'historique des livraisons est dans [CHANGELOG.md](CHANGELOG.md).

Python 3.8+, tkinter et matplotlib — Windows en priorité.

---

## 1. Installation

### Python

Il faut **Python 3.8 ou plus récent**, avec **tkinter**.

Sous Windows, installez Python depuis <https://www.python.org/downloads/> et
**cochez impérativement deux cases** dans l'installateur :

- `Add Python to PATH` (sur le premier écran)
- `tcl/tk and IDLE` (sur l'écran « Optional Features »)

Sans la seconde, l'application ne pourra pas s'ouvrir : tkinter est ce qui
dessine toute l'interface. C'est le seul piège de l'installation.

Pour vérifier, ouvrez PowerShell et tapez :

```powershell
python --version
python -m tkinter
```

La première commande doit afficher un numéro de version, la seconde doit
ouvrir une petite fenêtre de test — fermez-la.

### La seule bibliothèque à installer

```powershell
python -m pip install matplotlib
```

C'est tout — les dépendances de matplotlib s'installent automatiquement
avec lui.

Pour vérifier :

```powershell
python -c "import matplotlib, tkinter; print('tout est en place')"
```

### Facultatif : la zone de notification

Pour que la croix de la fenêtre **range** l'application à côté de l'horloge
au lieu de la fermer (elle continue alors de tourner, tampon glissant et
enregistrement compris ; l'extinction réelle se fait par clic sur l'icône
→ *Quitter Allure*) :

```powershell
python -m pip install pystray pillow
```

Sans ces deux bibliothèques, la croix ferme normalement — rien d'autre ne
change. L'option se règle dans Paramètres → *Affichage & fenêtre*.

---

## 2. Les fichiers à mettre sur votre ordinateur

Récupérez le dépôt, au choix :

- bouton vert **Code → Download ZIP** sur la page GitHub, puis décompressez
  l'archive dans un dossier (par exemple `C:\Allure`) ;
- ou, si vous avez git :

```powershell
git clone https://github.com/theptitprince/Allure.git C:\Allure
```

Le programme tient en **quatre fichiers, qui doivent rester ensemble** :

| Fichier | Rôle |
|---|---|
| `allure.py` | l'application : interface, logique d'écran, et lancement |
| `allure_engine.py` | calcul des polaires, sans interface |
| `allure_config.py` | enregistrement des réglages et de l'entrepôt |
| `allure_buffer.py` | tampon d'enregistrement glissant |

Les quatre fichiers sont indispensables. Il n'y a **pas** de lanceur à
part : `allure.py` se lance directement.

Le fichier `LICENSE` contient le texte de la licence du programme (voir
« Licence » en fin de document).

---

## 3. Lancement

En PowerShell, dans le dossier où sont les fichiers :

```powershell
cd C:\Allure
python allure.py
```

**Pour un lancement quotidien, faites-vous un raccourci** (clic droit sur le
Bureau → *Nouveau* → *Raccourci*) dont la cible est :

```
"C:\Chemin\vers\python.exe" "C:\Allure\allure.py"
```

**Et si la fenêtre noire vous gêne**, remplacez `python.exe` par
`pythonw.exe` dans cette même cible : c'est le seul changement, et la
console disparaît. Les deux passent par le même code de démarrage — il n'y
a pas deux comportements à connaître.

Ce qui décide de la présence d'une console, c'est donc **l'exécutable**, et
jamais le nom ou l'extension du fichier lancé. Pour savoir ce qui tourne
*vraiment* à un instant donné, ouvrez la roue dentée → *Sauvegardes* →
**Diagnostic de l'installation** : il affiche l'interprète en cours et dit
s'il y a une console ou non.

**Les voies UDP ne reçoivent plus rien depuis que vous êtes passé à
`pythonw.exe` ?** C'est le piège classique de Windows, et il n'a rien d'une
panne d'Allure : **le pare-feu autorise un EXÉCUTABLE, pas un programme**.
Lancé par `pythonw.exe`, Allure est un exécutable *nouveau* aux yeux du
pare-feu, même si `python.exe` avait été autorisé depuis toujours. Le
socket s'ouvre normalement — aucune erreur, aucune boîte — et les
datagrammes sont jetés en silence avant d'arriver.

Le suivi en direct le dit explicitement : la section *Réception*
affiche l'état réel de chaque voie (`à l'écoute, AUCUNE trame (depuis 45 s)`
contre `1 234 trame(s), dernière en direct`), et au-delà de vingt secondes
de silence sur une voie ouverte, un bandeau nomme la cause, l'exécutable
concerné et l'endroit où l'autoriser : *Paramètres Windows → Réseau →
Pare-feu → Autoriser une application*, en réseau **privé**.

**Et sans console, comment sait-on qu'une erreur est survenue ?** C'est la
contrepartie, et elle est payée : toute erreur inattendue est **écrite dans
`journaux/erreurs.log`** (avec l'heure, la version et la trace complète)
**et annoncée par une boîte de dialogue**. Une application qui se ferme
toute seule sans rien dire est le pire des comportements. Seule exception
volontaire : une erreur dans la boucle de rafraîchissement, qui tourne
deux fois et demie par seconde — elle est consignée **une seule fois**,
sans boîte, sinon vous seriez noyé sous les dialogues ; et la boucle
survit, car c'est elle qui tient le tampon et l'acquisition.

---

## 4. Ce que le programme crée à côté de lui

Rien de tout cela n'est à installer : ces **quatre dossiers** apparaissent
tout seuls à côté des fichiers `.py`.

| Dossier | Contenu | Peut-on le supprimer ? |
|---|---|---|
| `donnees/` | l'**entrepôt cumulatif**, la liste des passes, la corbeille | **Non — c'est votre bien le plus précieux** |
| `reglages/` | vos réglages et la dernière étape visitée | Oui (on repart des valeurs par défaut) |
| `tampon/` | le tampon glissant de trames brutes (~32 Mo pour 48 h) | Oui |
| `journaux/` | les journaux bruts des prises, et `erreurs.log` | Oui — ils s'effacent d'ailleurs tout seuls une fois la prise traitée (le tampon en garde les mêmes trames), sauf si le tampon est désactivé ou si la prise n'a produit aucun échantillon |

**Vous venez d'une version antérieure ?** Vous n'avez rien à faire. Les
versions précédentes écrivaient tout en vrac à côté des `.py` ; au premier
lancement, ces fichiers sont **déplacés automatiquement** dans les bons
dossiers. Un fichier n'est jamais écrasé : si les deux existent, c'est la
version déjà rangée qui l'emporte et l'ancienne reste à la racine, où vous
pourrez l'examiner.

**Sauvegarde.** Le plus simple : roue dentée → *Sauvegardes* → *Exporter
une sauvegarde*. Cela produit un fichier `.json` unique contenant l'entrepôt
et vos passes, à ranger où vous voulez. Vos réglages s'exportent séparément
au même endroit.

**Changement d'ordinateur ou de version.** Recopiez les quatre fichiers du
programme, puis réimportez votre sauvegarde et vos réglages. Une version
récente relit toujours les données de n'importe quelle version antérieure —
c'est un engagement du programme.

---

## 5. L'organisation de l'interface

Trois étapes numérotées — le parcours normal des données :

1. **Acquisition** — l'écran des mesures en direct, **en permanence** : que
   l'on enregistre ou non, on voit ce qui rentre et si c'est exploitable
   (cadran à deux flèches — vent vrai et vent apparent, qui soufflent vers
   le bateau, longueur proportionnelle à la force —, TWA/TWS/STW/SOG en gros
   chiffres, qualité de l'échantillonnage, réception, comparaison des
   girouettes, flux NMEA brut). C'est l'écran qu'on laisse ouvert en
   passerelle. Le bouton **⏱** en haut à droite y ramène depuis n'importe
   où. *Démarrer l'acquisition* demande la configuration (voiles / moteurs /
   dérive, et durée maximale) dans une seule boîte — la sélection précédente
   est reproposée, repartir sur la même voilure est immédiat. *Arrêter*
   envoie la passe **directement dans l'entrepôt** ; l'écran, lui, ne
   change pas : c'est l'état annoncé dans la barre du haut qui change.
2. **Entrepôt** — vos passes s'y accumulent, avec la configuration
   réellement utilisée pour chacune (voiles, moteurs, dérive). Tableau
   triable par clic sur les en-têtes ; la case de la colonne *Incluse*
   inclut/exclut une passe du calcul d'un simple clic.
   Interrogation des archives par date (JJ/MM/AAAA) et heure (HH:MM) : un
   tableau d'une ligne par minute autour de l'horaire, **entrepôt ET tampon
   glissant confondus** (les minutes qu'aucune passe ne couvre ressortent
   en lignes *(tampon)*, reconstruites des trames brutes), détail au clic.
   La date et l'heure se tapent **sans les séparateurs** — `21082026` et
   `1435` deviennent `21/08/2026` et `14:35` — et la recherche se lance à la
   touche Entrée. Séparer soi-même marche aussi : `5/1/2027` donne
   `05/01/2027`. C'est ici que vivent
   aussi l'**import d'un fichier .log** (un fichier, une boîte, une passe de
   plus — ce n'est pas un mode dans lequel l'application bascule), la
   correction a posteriori de l'annotation d'une passe, le **recalcul d'une
   passe depuis le tampon**, et la corbeille.
3. **Polaires** — tracé automatique dès l'arrivée sur l'étape, export
   `.pol` (Expedition) ou `.csv`, et le bouton **Polaire max (routage)…**
   (voir ci-dessous).

Et, en haut à droite, **trois boutons** qui ne sont pas des étapes du
parcours : **⏱** (les mesures en direct), **📈** (les statistiques) et
**⚙** (les paramètres).

### Les statistiques (bouton 📈)

Une page de **lecture**, qui ne produit aucune polaire et n'enregistre rien.
Elle répond à des questions que le reste de l'application ne pose jamais :
*le vent forcit-il ? la pression baisse-t-elle ? ai-je fait plus de route
cette heure-ci que la précédente ?*

Elle fonctionne **sans acquisition** : elle lit le **tampon glissant**, qui
tourne en permanence, et le flux en direct. À l'ouverture de l'application,
le tampon est relu en tâche de fond (bouton *Relire le tampon* pour
recommencer) — sans quoi une « moyenne sur 60 minutes » consultée cinq
minutes après le lancement ne porterait que sur cinq minutes.

Ce que la page **montre** se choisit dans la roue dentée → **Statistiques**
(bouton *Réglages…* en haut de la page pour y aller directement) : les
grandeurs affichées (par défaut l'essentiel — vents réel et apparent, leurs
angles, vitesses surface et fond, pression ; le reste du catalogue se coche
à la demande), les quatre durées de moyenne, la profondeur d'historique, le
tableau par tranches et son pas. La page elle-même ne porte qu'un seul
réglage, celui qu'on tourne l'œil sur le résultat : **l'échelle de temps
des graphes**.

Quatre blocs :

- **Tendances** — la même grandeur sur quatre durées (5 / 10 / 30 / 60 min
  par défaut), plus l'instantané et la pente horaire. C'est l'**écart entre
  les colonnes** qui informe : 18 nœuds sur 5 minutes et 14 sur 60, c'est un
  vent qui forcit. Les angles s'affichent **en flèches**. Une case vide =
  aucune mesure sur cette durée, jamais une vieille valeur recopiée ; une
  grandeur reçue mais non cochée est simplement comptée en pied de tableau.
- **Vent et pression** — deux graphes **séparés**, chacun sa propre échelle,
  mais **un seul axe de temps commun** : zoomer (molette) ou remonter dans
  le passé (cliquer-glisser) déplace les deux à l'identique, et les boutons
  d'étendue (1 h à 24 h) ramènent au direct. L'échelle du vent **part de
  zéro** — c'est le zéro qui donne la proportion — et la pression se gradue
  en **hectopascals entiers**, sur 4 hPa d'amplitude minimum : une variation
  qui tient là-dedans est, à l'échelle d'un baromètre, un trait plat, et
  doit se voir plate. Le vent est tracé en moyenne par tranche avec sa bande
  mini/maxi — les mesures brutes dessinent une pelote où la tendance
  disparaît.
- **Distance sur le fond, heure par heure** — chaque **heure pleine** du
  bord, par intégration de la vitesse fond. Une heure au flux interrompu
  affiche sa couverture (« 5,4 M / 35 % ») ; une coupure n'est jamais
  comblée ; les heures vides d'avant la première mesure sont élaguées.
- **Tableau des dernières heures** *(désactivable)* — une table de prévision
  tournée vers le passé, colorée par la force du vent. Ses lignes suivent
  les grandeurs cochées : suivre le vent réel amène ses rafales et sa
  direction.

**D'où viennent les mesures ?** Des **mêmes valeurs arbitrées** que les
cadrans du suivi en direct : la source de chaque grandeur (quelle voie,
quels replis) se choisit dans *Sources & tampon* et s'applique ici à
l'identique — les statistiques ne peuvent pas raconter une autre histoire
que le suivi en direct. Une valeur physiquement invraisemblable (trame
tronquée) est écartée en silence plutôt que de ruiner une moyenne horaire.

**L'indice de confiance.** Une polaire ne dit pas seulement une vitesse :
elle dit aussi, en creux, *crois-moi*. Or toutes les cases ne méritent pas
la même croyance — une case bâtie sur 200 échantillons pris dans un seul
bord de dix minutes et une case bâtie sur 20 échantillons répartis sur huit
sorties n'ont rien de comparable, et un simple effectif leur donnerait
pourtant la même autorité. L'indice (**A** solide, **B** bonne, **C** indicative, **D**
fragile) repose sur quatre constats, du plus important au moins :

1. **l'indépendance prime sur le nombre** — deux échantillons espacés de dix
   secondes sur le même bord ne sont pas deux mesures, c'est une mesure
   comptée deux fois (même mer, même réglage, même erreur de capteur, même
   courant). Les échantillons sont donc regroupés en **moments** séparés d'au
   moins cinq minutes, et c'est le nombre de moments qui pèse le plus ;
2. **les sorties distinctes valent mieux encore** — d'un jour à l'autre
   changent la mer, le chargement, le courant, l'étalonnage ;
3. **la dispersion** dit si les mesures se recoupent ;
4. **le nombre** compte encore, mais en dernier, et il sature vite.

Et un garde-fou qui vaut mieux que tous les réglages de poids : un
**plafond par nombre de moments**. Quoi qu'annoncent les autres facteurs,
une case observée une seule fois ne dépassera jamais *indicative*. On
n'achète pas la confiance à coups de mesures redondantes — seule la
répétition dans le temps la donne.

L'indice se lit à trois niveaux :

- **par case**, sur l'étape Polaires : la **taille des points** du tracé
  suit la confiance et non le simple effectif, et les exports
  `.csv` portent les colonnes *confiance, niveau, moments indépendants,
  sorties, dispersion* ;
- **par passe**, colonne *Confiance* de l'Entrepôt : une passe se juge sur
  ce qu'elle a **rapporté** — combien de cases différentes elle a nourries,
  avec quelle matière dans chacune. Une prise continue de deux heures n'est
  pas pénalisée (c'est un bon enregistrement), mais deux heures passées dans
  le même bord, si : elles n'apprennent qu'une seule chose. Sélectionnez une
  passe et la ligne du dessous dit **pourquoi** ;
- **par configuration**, sous le tracé : la *maturité* de cette polaire, le
  nombre de cases solides, et surtout **ce qui n'a jamais été mesuré** (« le
  portant, le vent fort ») — c'est-à-dire où aller chercher des données.

**L'indice n'agit sur rien**, sauf si vous le lui demandez. Il n'écarte
aucune case, ne modifie aucune vitesse, ne retire aucune ligne d'aucun
export. Seule la case *Incluse* de l'Entrepôt décide de ce qui entre dans le
calcul.

**Tri automatique à l'entrée** (roue dentée → *Lissage & table*, désactivé
par défaut) : une passe dont la confiance est inférieure à un seuil peut
entrer dans l'entrepôt **décochée** plutôt qu'incluse d'office. Le seuil
**conseillé, 40**, est affiché à côté du champ — c'est la frontière entre
*indicative* (C) et *fragile* (D). Plus haut (60, frontière B/C) vous ne
gardez que les passes bien réparties, utile quand l'entrepôt est déjà
fourni ; plus bas (20) vous n'écartez que les passes vraiment maigres ; à 0
rien n'est jamais écarté. Rien n'est supprimé ni caché : la passe est
entreposée normalement, sa polaire reste consultable, et le compte rendu de
fin de prise dit toujours ce qui a été décidé **et pourquoi**. C'est un
pré-positionnement de case, pas une censure — un clic la réintègre.

**Tout inclure / tout exclure** : une case d'ensemble sous le tableau des
passes bascule les trente d'un coup, avec le compte des passes incluses à
côté. Trier case par case serait un travail de copiste, et l'on veut souvent
repartir de zéro pour n'en garder que quelques-unes.

**Écrêtage par la dérivée** (dans la page Polaire max, désactivé par
défaut). Une polaire est une courbe **physiquement lisse** : la vitesse d'un
bateau ne peut pas grimper de trois nœuds entre 60° et 65°. Une case qui le
prétend ne décrit pas le bateau — elle décrit un surf sur une vague, une
risée ou une poussée de courant, un instant que le routage prendrait pour un
acquis. L'écrêtage borne la **pente** de la courbe (en nœuds par 10° de TWA,
1,5 conseillé) et rabaisse ces pointes.

Trois garde-fous, chacun pour une raison précise :

- **uniquement vers le bas** — un creux n'est pas suspect, et le remonter
  serait inventer une performance que le bateau n'a jamais faite ;
- **jamais aux extrémités** de la courbe, qui n'ont qu'un voisin : les
  borner reviendrait à raboter le près ou le grand largue sur la foi d'une
  seule direction ;
- **une pointe doit dépasser des deux côtés.** Chaque côté est jugé sur ses
  trois cases voisines, la plus permissive faisant foi, puis c'est le côté
  le plus strict qui borne. Ce double mouvement sépare trois situations
  qu'un critère naïf confond : la pointe isolée (écrêtée), le voisin d'un
  **creux** (intact — sans cela une seule mesure basse aberrante rabotait
  toute la courbe autour d'elle), et le **plateau** de deux pointes qui se
  justifient mutuellement (les deux redescendent).

Le nombre de cases rabaissées est annoncé sous la commande, elles portent
une **croix** sur le tracé, et l'export `.csv` les marque `mesure_ecrete`.
Désactivé par défaut, à dessein : boucher un trou *ajoute* là où il n'y
avait rien, écrêter *modifie* une valeur mesurée — ce n'est pas le même
geste, et le second se demande.

**Le zoom et le déplacement, comme sur une image.** La molette agrandit le
dessin, le **glisser** s'y promène, le **double-clic** revient à la polaire
entière. Le zoom se fait **sous le curseur** : le point visé reste où il
est, sans quoi chaque cran chasserait de l'écran ce qu'on cherchait
justement à regarder de plus près.

C'est bien le *cadre du tracé* qui grandit dans la fenêtre, pas l'échelle
des vitesses. Resserrer le rayon aurait été techniquement plus simple, mais
cela ne grossirait ni les graduations ni l'écart entre deux cases voisines
au près, qui est précisément ce qu'on veut lire de plus près. Ici tout
grossit ensemble et ce qui déborde est rogné, exactement comme une
photo qu'on agrandit. Le zoom **survit à un retracé** : cocher une
configuration ne vous rejette pas à la vue de départ. Et l'on ne peut pas
perdre le dessin hors de l'écran — le déplacement est borné.

**Le détail au survol.** Sur le tracé de la polaire max, passer la souris
sur un point affiche sa fiche : cap et vent de la case, vitesse, origine
(mesurée, interpolée, ou pointe écrêtée), voilure gagnante, nombre de
mesures et confiance. Une polaire porte des centaines de cases ; aller
chercher chacune dans un tableau casse le fil de la lecture. Loin de tout
point, l'infobulle s'efface plutôt que de désigner une case au hasard.

**Export TimeZero (MaxSea / Nobeltec TZ).** Le bouton *Exporter TimeZero*
de la page Polaire max écrit **deux fichiers XML d'un seul geste** :

- `..._Wind_Polar.xml` — la polaire de vitesse ;
- `..._Sail_Set.xml` — la **voilure à porter**, par plage de cap et de vent.

Le second est ce qu'Allure a de plus rare à offrir : TimeZero sait afficher
quelle toile porter selon le cap et le vent, et c'est exactement ce que la
polaire max calcule — la configuration gagnante de chaque case. Le routage
reçoit donc non seulement ce que le bateau sait faire, mais **avec quoi il
le fait**, déduit de ce qu'il a réellement fait en mer.

TimeZero ne publie pas la spécification de ses fichiers ; le format a été
relevé sur deux fichiers réels du bord. Trois conventions y sont des pièges
silencieux, et l'export les respecte : le séparateur décimal est une
**virgule** (`10,5` — un point produit un fichier que TimeZero lit sans
broncher mais interprète faux) ; une case sans donnée vaut **`0,1`**, ni 0
ni vide ; et toutes les courbes portent exactement la même liste d'angles.
Les couleurs des voiles reprennent celles de TimeZero, pour que le bateau ne
change pas d'aspect d'un fichier à l'autre.

Une chose doit être réglée une fois : TimeZero ne connaît pas vos codes de
voile. Dans la roue dentée → *Voiles & moteurs*, section **Export
TimeZero**, dites ce qu'est chacune des vôtres — grand-voile (`GV`, 1 à
3 ris, ou `Off` affalée) ou voile d'avant classée par force et par allure.
Une voile laissée sans correspondance est simplement absente du fichier de
voilure ; la polaire de vitesse, elle, n'en dépend pas. Dans TimeZero :
bouton **TIMEZERO → Ouvrir un fichier polaire → Parcourir**.

**Les tronçons : une passe n'est pas un bloc.** Les mesures, elles, sont
déjà atomiques — un échantillon toutes les dix secondes, chacun horodaté.
Ce qui était trop grossier, c'était la **décision** : inclure ou écarter
deux heures d'un coup, alors qu'il suffit souvent d'un quart d'heure de
mauvais (loch encrassé, moteur démarré sans être coché, grain qui passe)
pour gâter le reste.

La fenêtre de détail d'une passe la découpe donc en **tronçons**, calculés
après coup sur les échantillons déjà entreposés — rien de plus n'a été
enregistré. Trois signaux les délimitent : le **changement de configuration**
(frontière absolue, jamais effacée — deux voilures ne sont pas le même
bord) ; le **virement ou changement de cap**, repéré à un TWA qui balaie
plus de 25° en moins de deux minutes ; et l'**interruption** de la mesure.
Ce qui est trop court est recollé au voisin, pour qu'un bord de deux minutes
n'encombre pas la liste. Chaque tronçon annonce son heure, sa durée, son
nombre de mesures, son bord, sa configuration, sa confiance, et **pourquoi
il commence là** — une frontière qu'on ne peut pas expliquer est une
frontière en laquelle on ne peut pas avoir confiance.

Le critère de *vitesse* du changement est essentiel : un bateau qui abat
lentement de 40° à 115° au fil d'une heure ne manœuvre pas, il change
d'allure dans les mêmes conditions. C'est le même moment, et le découper
n'aurait aucun sens — l'application ne le fait pas.

Décochez un tronçon et la polaire de la passe se retrace aussitôt. **Rien
n'est supprimé** : les échantillons restent dans l'entrepôt et redeviennent
comptables dès que vous recochez. Et l'exclusion est enregistrée en **plage
horaire**, pas en numéro de tronçon : le découpage est calculé, pas stocké,
si bien qu'affiner un jour ses règles ne déplacera aucune exclusion
existante sur d'autres mesures.

**Un piège dont il faut être averti** : découper ne crée pas d'information.
Huit tronçons d'une même après-midi, ce ne sont pas huit sorties — même mer,
même étalonnage, même courant, même équipage. Les compter comme indépendants
gonflerait la confiance, soit exactement l'inverse de ce pour quoi elle
existe. La confiance d'un tronçon issu d'une seule sortie est donc
**plafonnée** comme l'est une case observée sur un seul moment : il peut
être bon, il ne peut pas être *solide* à lui tout seul.

**La polaire de chaque passe.** Dans l'Entrepôt, un **double-clic** sur une
passe ouvre la polaire issue de **cette passe uniquement**. L'étape
Polaires, elle, trace l'entrepôt cumulatif — des mois de mesures confondus,
et elle ne peut donc pas répondre à « qu'est-ce que ma sortie d'aujourd'hui
a donné ? ». La fenêtre rappelle la date, la confiance de la passe et une
courbe par configuration (une passe où l'on a changé de voile en porte
plusieurs). Une passe exclue du calcul reste consultable — c'est même
souvent pour décider de l'exclure ou de la réintégrer qu'on vient la
regarder.

**La polaire max exploitable (routage).** Les polaires par configuration
répondent à *« que vaut le bateau sous cette voilure ? »* ; un logiciel de
routage (qtVlm, Adrena, OpenCPN…), lui, veut **une seule table pleine** :
*« que vaut le bateau, point »* — à charge pour l'équipage de porter la
bonne toile. Le bouton **Polaire max (routage)…** de l'étape Polaires ouvre
une page dédiée qui la construit : pour chaque case cap/vent, la
**meilleure vitesse** parmi les configurations cochées l'emporte — et la
page retient **laquelle** a gagné, ce qui donne en prime le **guide de
voilure** (« pour ce cap et ce vent, portez ça »), consultable case par
case ou via le petit chercheur *Quelle voilure ?*. La page est bâtie pour
**tenir dans la fenêtre** : les exports occupent un pied de page sur toute
la largeur, les options et la recherche sont ancrées au bas de la colonne
de gauche — Tk sert les bords avant le centre, rien ne peut donc être
repoussé hors du cadre. Seule la liste des configurations, dont la hauteur
dépend de votre bateau, se donne une barre de défilement, et seulement
au-delà d'une dizaine d'entrées. Sur ce bateau — un
cargo à voile **et** moteur — **le moteur fait partie de la voilure** :
chaque cas de vent appelle un *jeu* voiles/moteur, pas une voile seule.
Les configurations moteur concourent donc **par défaut**, et le guide de
voilure dit aussi *quand passer au moteur*. Pour une polaire voile pure,
décochez-les dans la page — ou changez le défaut dans la roue dentée →
*Lissage & table* (option « Le moteur fait partie de la voilure »). Les
**trous** de la grille sont
bouchés par **interpolation linéaire le long du TWA** (jamais au-delà de la
première ou de la dernière mesure — prétendre une vitesse au vent debout
jamais mesurée serait un mensonge que le routeur prendrait au pied de la
lettre) ; sur le tracé, les points pleins sont mesurés, les points creux
interpolés. L'export **`.pol` est une grille pleine** : les cases jamais
observées y valent 0, ce qu'un routeur lit comme « zone où le bateau ne
marche pas » (le vent debout, typiquement). L'export **`.csv`** donne pour
chaque case la vitesse, son origine (mesurée / interpolée) et la voilure
gagnante.

Un **bandeau d'acquisition** court en haut de toutes les pages : l'état de
la prise, son chrono, la voilure enregistrée, et le bouton démarrer/arrêter.
Une prise qui tourne se voit donc depuis l'Entrepôt comme depuis les
Polaires, et s'arrête sans avoir à aller la chercher.

**La fiche d'une minute archivée.** Dans la recherche, un **double-clic** sur
une ligne ouvre sa fiche complète : les grandeurs de la polaire, le vent
apparent, et les **conditions** — pression, températures, humidité, route et
vitesse fond — moyennées sur une fenêtre centrée (10 min par défaut) relue
dans le tampon glissant. De quoi remplir un journal de passerelle a
posteriori. Le choix des grandeurs et la durée de moyennage se règlent dans
la roue dentée → *Lissage & table* ; le bouton **Cocher ce qui existe à bord** y
coche d'un trait ce que vos voies portent réellement, et décoche le reste —
une case cochée doit promettre une valeur, pas une ligne vide.

**Une seule application à la fois.** Lancer Allure une deuxième fois sur le
même ordinateur ne fait qu'afficher un message : deux exemplaires
écouteraient les mêmes voies UDP et écriraient dans le même entrepôt. Si la
fenêtre semble introuvable, regardez la zone de notification à côté de
l'horloge — elle s'y range peut-être (voir *Affichage & fenêtre*).

**Choisir ses sources.** Une passerelle porte souvent deux fois la même
mesure — deux girouettes, par exemple — qui ne disent pas la même chose.
Dans *Sources & tampon*, donnez d'abord un nom à chaque voie (Station nav, Météo
France, GPS…), puis cliquez **Analyser les voies** : l'application relit le
tampon glissant et dresse la liste de ce que chaque voie porte réellement,
avec sa cadence, son unité et un exemple de valeur. Vous désignez ensuite,
**pour chaque grandeur séparément**, la voie qui fait foi et celle qui prend
le relais si elle se tait. Une passerelle peut très bien porter la meilleure
girouette et le plus mauvais loch : c'est pourquoi l'ordre est propre à
chaque mesure, et non à la voie.

Le tableau se **trie par clic sur les en-têtes** (grouper par trame rassemble
les voies qui portent le même vent — c'est ainsi qu'on les compare) et il est
**actionnable** : un double-clic sur une ligne exploitable désigne cette voie
comme source de la grandeur, l'ancienne devenant automatiquement le repli. La
colonne *Source* rappelle qui fait foi. Voir ce que porte une voie et la
choisir sont ainsi le même geste. Ce que vous désignez est retenu **par
voie**, pas par le nom que vous lui avez donné : renommer un équipement ne
fait pas disparaître le choix. Le choix de la **vitesse fond** tranche lui
aussi pour de bon : avec deux GPS à bord, c'est bien la voie désignée qui alimente
la mesure, et non la dernière arrivée.

**Ce qu'une voie « apporte » se lit en deux temps** : d'abord la trame
elle-même, puis ce qu'elle alimente — `[polaire : vent apparent]`,
`[fiche : pression, humidité…]`, ou les deux. Une trame qui n'entre dans
aucune polaire n'est pas perdue pour autant : la température des XDR ou la
pression de la station remplissent la fiche d'une minute archivée, et le
tableau le dit, au lieu d'annoncer « non exploitée ». Si vous
tentez d'en désigner une comme source, le refus vous rappelle où elle
*est* utilisée.

**La station météo du bord (`$PEUMA`).** Les trames **propriétaires** (celles
qui commencent par `$P`) portent le nom du constructeur et non un code
normalisé ; elles sont affichées sous leur nom entier, et celle de la
station météo de ce bateau est entièrement décodée :

- **pression** au niveau de la station **et** réduite au niveau de la mer —
  sur cette installation, c'est la **seule** source de pression du bord
  (aucune trame MDA/MMB n'y circule) ;
- **température de l'air** et **humidité** (elles recoupent exactement les
  XDR) ;
- **route et vitesse fond** — la vitesse est émise en mètres par seconde et
  convertie automatiquement ; la route concorde à 0,4° près avec celle des
  VTG. La station **concourt donc avec les GPS** pour la grandeur *Vitesse
  fond* : elle se désigne comme source dans le tableau, exactement comme une
  VTG, et elle alimente aussi la détection de manœuvre et le thème
  automatique (par sa position). C'est sans risque : une trame tronquée ne
  fait jamais d'elle une source (voir plus bas), et la vitesse fond ne
  construit **aucune** polaire — elle sert à l'affichage, au recoupement
  STW/SOG et à la détection de manœuvre. Le vent et la vitesse surface,
  eux, restent réservés aux trames normalisées : c'est sur eux que la
  polaire est bâtie ;
- **vent vrai mesuré** : moyen, rafale, mini, et sa direction. Ce vent-là est
  **référencé au nord**, pas à l'étrave : il ne peut donc jamais servir de
  TWA (il faudrait un cap compas instantané que l'installation ne fournit
  pas). En revanche il **arbitre la force du vent** — c'est lui qui a permis
  de confondre la girouette mal étalonnée de ce bateau. Il apparaît pour
  cela dans *Comparaison des girouettes*, au suivi en direct, qui s'affiche
  **même avec une seule girouette** dès lors que la station est là,
  et signale toute girouette qui s'écarte de plus de 25 % de sa mesure.

Toutes ces grandeurs sont proposées dans la fiche d'une minute archivée
(roue dentée → *Lissage & table*). Les trames tronquées — il y en a toujours
dans un tampon réel — sont écartées entières : une trame propriétaire n'a
aucun marqueur de champ, une troncature y décalerait silencieusement tout le
contenu et une pression deviendrait une vitesse.

**Une source désignée qui n'apporte rien ne peut pas éteindre la mesure.**
C'est un piège réel, et il vaut d'être expliqué : une voie qui n'émet que du
`MWV,ref='T'` (une direction de vent par rapport au nord, que l'application
refuse à dessein) **parle** du vent sans jamais en **donner**. Désignée
naïvement comme source du vent, elle remporterait l'arbitrage à chaque
trame ; la vraie girouette serait écartée en silence, le cadran resterait
vide et plus aucun échantillon ne serait produit — sans le moindre message.
Deux règles l'empêchent : une voie ne compte comme source
d'une grandeur que si elle **fournit réellement** cette grandeur, et une
source désignée qui n'a **jamais rien fourni** est ignorée (voie débranchée,
renommée, réglage importé d'une autre installation) plutôt que d'éteindre la
mesure. Dans les deux cas, le suivi en direct affiche un bandeau qui **nomme
la voie fautive et celle qui fournit vraiment**, et dit où corriger. Un
réglage qui ne produit pas ce qu'on en attend doit se signaler.

Attention à la colonne **Cadence** : elle donne la cadence *utile*. Une voie
qui émet du vent deux fois par seconde mais n'y met de l'apparent qu'une
fois sur deux est deux fois plus lente qu'elle n'en a l'air — et c'est cette
cadence-là qui décide du nombre d'échantillons que vous obtiendrez.

Les **Paramètres** s'ouvrent par la **roue dentée en haut à droite** (le
bandeau des étapes s'efface pendant qu'ils sont ouverts). Six catégories,
dans l'ordre du parcours : **Voiles & moteurs**, **Sources & tampon** (type
d'acquisition, voies d'écoute, analyse et choix des sources, tampon
glissant), **Manœuvres** (le filtre d'échantillons *et* l'arrêt automatique
— voir ci-dessous), **Lissage & table** (lissage de la mesure, table
polaire, fiche d'archive), **Affichage & fenêtre** (amortissement, thème,
zone de notification), **Sauvegardes** (entrepôt et réglages).
*Enregistrer les paramètres* ramène à l'étape d'où l'on venait.

**Arrêt automatique sur manœuvre.** Paramètres → *Manœuvres*, désactivé par
défaut. Deux choses distinctes cohabitent sur cette page : le **filtre
d'échantillons** (TWA/STW sur la fenêtre de lissage — il écarte quelques
mesures, silencieusement) et l'**arrêt automatique** — lui détecte que vous
avez **viré de bord ou changé d'allure**, sur la **route fond (COG)** et la
**vitesse fond (SOG)** des trames VTG, donc sans dépendre du
girouette-anémomètre. Quand ses seuils sont franchis (route qui balaie plus
de X°, ou vitesse qui varie de plus de Y % sur la fenêtre d'observation),
Allure **sonne**, affiche un **bandeau rouge visible sur toutes les pages**
et ouvre une boîte : *« Avez-vous manœuvré ? »*. **Oui** : la prise
s'arrête, et les mesures faites **depuis l'instant de détection** sont
jetées (la manœuvre et le temps mis à répondre n'appartiennent pas à la
polaire) — la session part propre dans l'entrepôt. **Non** : fausse alerte,
la prise continue, et la détection repart après un anti-rebond. Fermer la
boîte à la croix ne répond pas : le bandeau reste, on peut répondre plus
tard. Bandeau et boîte posent la même question — le premier qui répond
gagne. Garde-fous : rien ne se déclenche tant que la fenêtre d'observation
n'est pas assez remplie (les premières secondes d'une prise ne sont pas une
manœuvre), la route est ignorée sous 1,5 nd de SOG (le COG d'un GPS à
l'arrêt est du bruit), et l'écart de route est compté **circulairement**
(359° et 1° diffèrent de 2°, pas de 358). Ces seuils se changent à chaud,
même en pleine prise.

**Amortissement des valeurs en direct.** Paramètres → *Affichage & fenêtre* :
les grands chiffres et les flèches du suivi en direct sont moyennés sur cette
durée (5 s par défaut, 0 = valeurs brutes). Sans elle, ils sautent à chaque
trame et deviennent illisibles en mer. Cela ne touche **que l'affichage** :
ni les échantillons enregistrés, ni la détection de manœuvre, ni la polaire
n'en dépendent — le lissage de la *mesure*, lui, se règle dans *Lissage & table*.
Les angles sont moyennés circulairement : au vent arrière, une moyenne
ordinaire renverrait le vent debout.

**Thème clair / sombre.** Paramètres → *Affichage & fenêtre* : clair,
sombre, ou **automatique** — sombre entre le coucher et le lever du soleil,
calculés pour la dernière position GPS reçue (trames GGA/RMC). Le
changement se fait à chaud, même en pleine prise. Sans position connue,
l'automatique reste en clair.

---

## 6. En cas de souci

**« No module named tkinter »** — Python a été installé sans tcl/tk.
Relancez l'installateur, choisissez *Modify*, et cochez `tcl/tk and IDLE`.

**« No module named matplotlib »** — la commande d'installation n'a pas été
exécutée, ou l'a été pour un autre Python. Réessayez avec
`python -m pip install matplotlib` (la forme `python -m pip` garantit qu'on
installe bien pour le Python qui lancera le programme).

**Aucune trame ne rentre en mode direct** — vérifiez dans *Paramètres* que
les voies UDP actives ont les bons numéros de port, et que le pare-feu
Windows autorise Python à recevoir sur le réseau local.

**Le démarrage est long (une invite de commande, puis rien pendant de
longues secondes, voire des minutes).** Le démarrage est chronométré et
**annoncé** : la console affiche chaque phase, et
`journaux/demarrage.log` garde le détail du dernier lancement (aussi résumé
dans ⚙ → *Sauvegardes* → *Diagnostic de l'installation*). Lisez-le : si
l'essentiel du temps part dans « bibliothèques », le coupable est **avant**
la première ligne d'Allure — soit le premier import de matplotlib après une
installation/mise à jour (son cache de polices se reconstruit, une fois),
soit l'**antivirus** qui inspecte les centaines de fichiers de
matplotlib/numpy à chaque lancement : ajoutez le dossier de Python aux
exclusions de l'antivirus, le démarrage redevient court.
Les phases propres à Allure (données, interface, réseau) se comptent en
secondes, même avec un entrepôt fourni.

**Un changement d'heure à bord (fuseau, heure d'hiver, remise à l'heure).**
C'est un événement normal sur un bateau qui traverse des fuseaux, et Allure
l'encaisse sans rien perdre. Ce qui se passe exactement quand
l'horloge **recule** :

- une prise en cours **continue** : le saut est détecté, les fenêtres de
  lissage sont réamorcées (coût : les ~15 secondes de leur remplissage), la
  configuration active suit la nouvelle heure, et la section *Réception*
  affiche « Changement d'heure détecté : fenêtres de mesure réamorcées,
  rien n'est faussé » ;
- le **rejeu d'un journal** (traitement de session, import, recalcul) sait
  distinguer un recul d'une ou deux heures (changement d'heure : les
  instants sont gardés tels qu'écrits) d'un passage de minuit (recul de
  presque 24 h : un jour est ajouté) — confondre les deux enverrait tout ce
  qui suit le changement 23 h dans le futur, hors de toute annotation,
  c'est-à-dire à la perte ;
- le **tampon glissant** ne purge jamais au-delà de ce que les données
  racontent : même une horloge avancée par erreur de dix jours ne peut pas
  lui faire supprimer les dernières 48 h de flux enregistré ;
- les **tendances** (page 📈) jettent les mesures horodatées « dans le
  futur » plutôt que de les mélanger aux fenêtres — on perd au pire une
  heure d'affichage de tendances, jamais une mesure de polaire.

Une **avance** de l'horloge, elle, se présente comme un simple trou dans le
flux et ne demande rien de particulier. Seule vraie ambiguïté restante,
inhérente au problème : pendant l'heure d'automne qui existe **deux fois**
(2 h → 3 h → on recule → 2 h), les archives de ces deux heures portent les
mêmes horodatages et se lisent mélangées — aucun format à base d'heure
locale ne peut faire mieux.

**La page Statistiques (📈) reste presque vide** — elle vit du tampon
glissant. Vérifiez d'abord qu'il est actif (*⚙ > Sources & tampon*) : sans
lui, les statistiques ne portent que sur ce qui est arrivé depuis le
lancement, et la page le dit. Ensuite, cliquez sur *Relire le tampon* : le
message qui suit indique combien de trames ont été relues et jusqu'à quelle
heure. Une ligne absente du tableau des tendances a deux causes possibles :
la grandeur n'est pas cochée dans *⚙ > Statistiques* (le pied de tableau
compte alors ce qui est reçu mais masqué), ou elle n'existe simplement pas
sur votre passerelle (la profondeur, par exemple, si aucun sondeur n'émet).

**Des angles de près qui semblent trop serrés** — c'est presque toujours
la girouette, pas le calcul. Quand deux voies annoncent du vent apparent,
le Suivi en direct affiche le bloc *Comparaison des girouettes* : chaque
voie y montre le TWA qu'elle **donnerait** si elle était la source.
Quelques degrés d'écart sur l'angle apparent, ou 15 % sur la force, se
traduisent par une dizaine de degrés d'écart sur le TWA — et c'est
uniquement la source retenue (marquée ▸) qui construit votre polaire. Pour
trancher, comparez au vent vrai que votre passerelle calcule elle-même,
puis désignez la bonne voie dans *Sources & tampon*.

**Réparer des passes déjà enregistrées sur la mauvaise voie.** Changer de
source n'agit que sur les prises à venir : les échantillons déjà entreposés
gardent l'angle calculé au moment de la prise. Mais tant qu'une passe reste
couverte par le tampon glissant, ses trames brutes sont encore là :
sélectionnez-la dans l'Entrepôt et cliquez **Recalculer depuis le tampon**.
Tout est rejoué avec les réglages du moment — rien n'est estimé, rien n'est
extrapolé. L'annotation (voiles/moteurs/dérive) est conservée, le compte
rendu chiffre ce qui a changé, et l'ancienne version part dans la Corbeille
d'où elle revient d'un clic. Au-delà de la fenêtre conservée, l'application
refuse plutôt que de produire un résultat tronqué — d'où l'intérêt d'une
rétention généreuse si vous naviguez plusieurs jours d'affilée.

**Aucun échantillon produit après une session** — l'application affiche un
diagnostic précis qui distingue les trois causes possibles : rien reçu,
reçu mais aucune configuration cochée, ou reçu mais jamais assez stable.
Le fichier brut de la session est toujours conservé dans ce cas : vous
pouvez le réimporter après avoir corrigé le réglage.

---

## 7. Licence

Allure est un **logiciel libre**, distribué sous la licence
**GNU General Public License, version 3 ou ultérieure** (GPL-3.0-or-later).

Copyright © 2026 ETDEL.

Concrètement, vous pouvez utiliser le programme librement, y compris à bord
et dans un cadre professionnel, l'étudier, le modifier et le redistribuer —
à condition que toute version redistribuée, modifiée ou non, reste sous la
même licence, avec son code source et la mention de copyright. Le programme
est fourni **sans aucune garantie** : les polaires qu'il produit vous aident
à régler le bateau, elles ne remplacent ni votre jugement ni vos documents de
navigation.

Le texte intégral de la licence est dans le fichier `LICENSE` du dépôt et
sur <https://www.gnu.org/licenses/gpl-3.0.html>. Chaque module source
(`allure.py`, `allure_engine.py`, `allure_config.py`, `allure_buffer.py`)
porte l'en-tête de licence correspondant.

---

*Allure — ETDEL 2026 — GPL-3.0-or-later*
