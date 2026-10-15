# Journal des versions

Toutes les livraisons d'Allure, de la plus récente à la plus ancienne.

## Comment se lit un numéro de version

`X.Y` puis une **lettre** : `2.0a`, `2.0b`, `2.0c`…

- Le **chiffre** change quand l'application change de visage — une nouvelle
  étape, un nouveau format de fichier, une refonte.
- La **lettre** suffit pour une livraison mineure — une correction, un
  ajustement d'affichage, un réglage de plus.

Le numéro vit à un seul endroit dans le code, `APP_VERSION` en tête de
`allure_config.py`. Il est destiné à l'humain : il identifie une version
installée et rattache une donnée à la version qui l'a produite, mais il ne
pilote **aucun** comportement du programme.

À ne pas confondre avec `BACKUP_FORMAT_VERSION`, qui décrit le *format* des
fichiers et ne bouge que si celui-ci change réellement. C'est cette
séparation qui protège le contrat de compatibilité : une version récente
relit toujours les données de n'importe quelle version antérieure.

---

## 2.2b

Allure dit désormais qui l'a fait et sous quelle licence : la mention
d'auteur s'affiche sur toutes les pages, et une page *A propos* rassemble
les avis légaux.

### Ajouté

- **`NOTICE`**, à la racine du dépôt : les conditions additionnelles de la
  licence (article 7 de la GPL v3), en français et en anglais. Toute copie
  ou version modifiée doit garder, visible, la mention « Allure, par ETDEL —
  https://github.com/theptitprince/Allure » ; une version modifiée doit le
  dire, et ne pas se présenter comme l'original.
- **Paramètres → *A propos***, septième et dernière catégorie : la mention
  et la version, un bouton *Ouvrir la page du projet*, et les avis légaux
  dans un texte sélectionnable — licence GPL v3 ou ultérieure et ses
  conditions additionnelles, absence de garantie, où lire `LICENSE` et
  `NOTICE`. Ces textes sont écrits dans le code : l'exe autonome, qui
  n'embarque ni `LICENSE` ni `NOTICE`, affiche les mêmes.
- Les quatre modules `.py` et les deux scripts `.bat` portent, sous leur
  en-tête de licence, la mention et le renvoi à `NOTICE`.

### Modifié

- Le **bandeau d'en-tête**, sur toutes les pages, affiche la mention
  « Allure, par ETDEL — https://github.com/theptitprince/Allure » au lieu
  de « by ETDEL 2026 » ; le bas de la catégorie *Sauvegardes* aussi.
- README, en français et en anglais : la section *Licence* présente les
  conditions additionnelles (article 7), le tableau des fichiers gagne
  `NOTICE`, et les Paramètres comptent sept catégories au lieu de six.

---

## 2.2a

La dérive disparaît, les propulsions se cochent, et tout l'affichage a été
passé en revue — à 100, 125 et 150 %, du petit écran au grand.

### Modifié

- **Polaires : les propulsions se cochent.** La colonne de gauche est une
  seule liste défilante où chaque propulsion a sa case : *Voiles seules*,
  puis *Voiles + 1ME*, *Voiles + 2ME*, *Voiles + 1SB*… — une par
  configuration moteur déclarée dans les Paramètres (*Voiles & moteurs*),
  d'office et dans le même ordre ; une combinaison enregistrée ensemble
  (*Voiles + 1ME + 1SB*) a aussi la sienne. Une propulsion encore jamais
  mesurée garde sa case, grisée. Sous chaque propulsion cochée viennent
  ses voilures, en retrait, cochées comme on les avait laissées. On combine
  donc librement — voiles seules *et* voiles + 1SB, par exemple. *Tout
  confondu* disparaît : il suffit de tout cocher.
- La liste tient en **une seule colonne** dont chaque libellé passe à la
  ligne au lieu d'être coupé (les deux colonnes d'avant se tronquaient), et
  la colonne s'**élargit à la souris** par la poignée qui la sépare du
  tracé ; sa largeur est retenue.
- Le choix des propulsions — cochées comme décochées — est retenu d'un
  lancement à l'autre ; une propulsion qui apparaît ensuite prend le défaut
  du réglage « Le moteur fait partie de la voilure ». Le choix fait en 2.1b
  est repris.
- **La dérive est supprimée**, partout : plus de choix Haute / Basse au
  démarrage d'une acquisition, à l'import d'un `.log` ni dans la correction
  d'une passe, plus de colonne dans l'Entrepôt ni dans les exports (le
  `.csv` par configuration n'a plus que voiles et moteurs). Les données
  existantes se relisent sans rien perdre : deux configurations qui ne
  différaient que par la dérive sont réunies.
- La fenêtre principale s'ouvre à une taille adaptée à l'écran et au zoom
  de Windows, et retrouve au lancement suivant la taille et la position
  qu'on lui avait laissées.

### Corrigé

- **Revue complète de l'affichage**, page par page, à 100, 125 et 150 % et
  sur des écrans de 1040 à 1920 px de large : une quarantaine de textes
  coupés, de boutons écrasés et de listes sans ascenseur. En particulier :
  - *Paramètres* : le tableau des voies se cale sur son contenu, avec un
    ascenseur horizontal et, dessous, le texte complet de la ligne choisie ;
    les listes Voiles et Moteurs ont un ascenseur ; la grille TimeZero suit
    les voiles ajoutées ou retirées ; conseils, états et diagnostic
    s'affichent en entier.
  - *Acquisition* : compteurs, alertes des voies et bandeau de manœuvre
    passent à la ligne (« Non, fausse alerte » n'est plus écrasé) ; le
    cadran grandit avec le zoom ; la fenêtre de choix de la configuration
    range ses cases en grille.
  - *Entrepôt* : colonnes des passes et des archives à la bonne largeur,
    ascenseur horizontal au besoin ; panneau de correction en grille ;
    fenêtre *Polaire de la passe* avec sa légende à droite du cercle et son
    bouton *Fermer* toujours visible.
  - *Polaires* : les exports passent sur deux lignes plutôt que d'être
    rognés, la légende des vents reste dans la figure, la fiche d'un point
    tient dans l'écran avec un ascenseur.
  - Les fenêtres secondaires restent dans l'écran et prennent le thème
    sombre, ascenseurs compris.
- **Démarrage** : l'application mettait près de 20 secondes à s'afficher
  (une zone défilante se remettait en page des centaines de fois) ; elle
  s'ouvre maintenant en une seconde environ.
- Entrepôt, archives : la ligne *(tampon)* décalait ses valeurs d'une
  colonne.
- Tronçons d'une passe : une plage déjà écartée n'est plus fondue dans un
  tronçon retenu quand le découpage a changé depuis.
- Corbeille : la version d'avant un recalcul se restaure correctement, même
  après un relancement.
- Paramètres : une liste de moteurs vide (voilier sans moteur) est acceptée
  au lieu de revenir à la liste par défaut.
- Au-delà de dix vents, les couleurs se répètent : le trait (Polaires) ou
  le point creux (polaire de la passe) distingue désormais les courbes.
- Des messages renvoyaient à une étape *Enregistrement* et à un bouton
  *Traiter la session* qui n'existent plus.

---

## 2.1b

Les polaires se choisissent en deux temps, et la molette fait enfin
défiler ce qu'on vise.

### Modifié

- **Polaires : d'abord la propulsion, ensuite les voilures.** En tête de la
  colonne de gauche, parmi les propulsions présentes dans l'entrepôt :
  *Voiles seules*, une famille de moteurs chacune — déduite des codes
  moteur, chiffres retirés : *Voiles + ME*, *Voiles + SB*, avec en dessous
  ce que chacune regroupe (« ME = 1ME, 2ME ») —, et *Tout confondu* dès
  qu'il y en a deux. La liste, rebaptisée *Voilures en concurrence*, ne
  montre plus que les voilures de la propulsion choisie, toutes cochées la
  première fois. Le choix est retenu d'un lancement à l'autre, les coches
  le temps de la séance ; les codes C1, C2… ne changent pas d'une
  propulsion à l'autre.
- Le réglage « Le moteur fait partie de la voilure » (Paramètres →
  *Lissage & table*) fixe désormais la propulsion proposée à l'ouverture :
  *Tout confondu* s'il est coché, *Voiles seules* sinon. Le modifier
  remplace le dernier choix retenu, même sans repasser par les Polaires.

### Corrigé

- **Molette.** Elle fait défiler la zone sous la souris, et elle seule.
  Dans l'Entrepôt, elle faisait défiler tantôt la liste des passes, tantôt
  la page, tantôt les deux à la fois. Une liste en butée ne passe plus la
  main à la page ; une liste qui tient entière la laisse défiler ; la
  molette sur l'ascenseur d'une liste ne fait défiler qu'elle. Maj +
  molette garde son défilement horizontal. Les petits pas d'un pavé
  tactile se cumulent au lieu d'être perdus.
- **Ascenseur invisible** de la liste des voilures (Polaires) : une zone
  défilante étroite ne laissait plus de place à son ascenseur.
- **Bandeau des étapes** : après un passage par les Paramètres, il revenait
  sous la barre d'acquisition au lieu de reprendre sa place au-dessus.

---

## 2.1a

L'étape Polaires change de visage : elle affiche désormais **directement la
polaire max**, et le tracé y a enfin toute la place. Avec beaucoup de
configurations, l'ancienne page empilait sous le cercle la liste
« C1 = … », le guide de voilure et la légende des vents — et la polaire se
réduisait jusqu'à devenir illisible.

### Modifié

- **L'étape Polaires est la polaire max.** Pour chaque case cap/vent, la
  meilleure vitesse parmi les configurations cochées l'emporte. Une seule
  cochée — un double-clic dans la liste suffit — et c'est sa polaire à
  elle : l'ancien affichage par configuration (menu déroulant) et la
  fenêtre séparée « Polaire max (routage)… » disparaissent, tout se fait au
  même endroit.
- **Le tracé garde toute la hauteur**, quel que soit le nombre de
  configurations. Leur liste vit dans la colonne de gauche, avec sa propre
  barre de défilement ; la légende des vents passe à droite du cercle ; le
  guide de voilure s'affiche à la place du tracé (boutons *Polaire* /
  *Guide de voilure*), et non plus dessous.
- **Codes de configuration stables.** C1, C2… sont affichés dans la liste
  et ne changent plus quand on coche ou décoche : décocher C2 ne renomme
  plus C3. Le guide, l'infobulle, la recherche et la fiche d'un point
  emploient les mêmes codes. (C'est le rang de la configuration dans
  l'entrepôt, la plus fournie d'abord : ajouter, inclure ou exclure une
  passe peut encore les renuméroter.)
- **Exports.** Les boutons « .pol / .csv (configuration sélectionnée) »
  disparaissent : pour une seule voilure, cochez-la seule et utilisez les
  exports du pied de page, qui proposent alors son nom de fichier. Le
  `.pol` est dans ce cas, comme pour la polaire max, une **grille pleine** :
  trous bouchés si l'option est cochée (elle l'est d'office), 0 dans les
  cases jamais mesurées — là où l'ancien export les laissait vides.
  « .csv (toutes les configurations) » devient « .csv (une polaire par
  configuration) », à droite du pied de page, avec son indice de confiance.
- La **taille des points** suit l'indice de confiance (celui de la
  configuration gagnante de la case), et la **maturité** d'une
  configuration s'affiche en tête de page quand elle est seule cochée —
  ce qu'offrait l'ancien affichage par configuration.
- La page suit l'entrepôt : inclure ou exclure une passe, importer,
  recalculer ou restaurer met la liste et le tracé à jour. L'ancienne
  fenêtre gardait la table de son ouverture.

### Ajouté

- **Masquer un vent** : un clic sur une valeur de la légende masque sa
  courbe, un second la rend. L'échelle ne bouge pas.
- Boutons *Tout cocher* / *Tout décocher* pour les configurations.
- `installer_dependances.bat` : installe pour la commande `python` (et
  affiche son chemin), vérifie tkinter, installe matplotlib (et pystray +
  pillow, facultatifs), fait le bilan.
- `construire_exe.bat` : construit `Allure.exe`, autonome, avec l'icône
  d'Allure (disque bleu, voile blanche), à côté de `allure.py` dont il
  partage les données.
- L'icône d'Allure sur la fenêtre et dans la barre des tâches, au lieu de
  la plume de Tk.
- README : section *Installation hors ligne* (installer matplotlib ou
  PyInstaller sans internet à bord).
- `.gitignore` : les dossiers de données propres à chaque poste, les
  fichiers laissés par les versions antérieures et ceux de la construction
  de l'exe ne sont plus proposés au versionnage ; `.gitattributes` : les
  `.bat` sont toujours livrés en fins de ligne Windows.

---

## 2.0b

Le tri de l'entrepôt était **bien** appliqué au calcul — polaires,
polaire max et exports écartaient déjà ce qu'il fallait — mais rien de ce
qui s'affichait autour ne le disait. Tous les compteurs de l'Entrepôt
continuaient d'annoncer le total brut, ce qui donnait toutes les raisons de
croire que décocher une passe ou un tronçon ne servait à rien. Cette
livraison remet l'affichage d'accord avec le calcul, et corrige au passage
une exclusion qui, elle, n'avait réellement pas lieu.

### Corrigé

- **Tronçons sans horodatage réellement écartés.** Une plage horaire ne peut
  rien dire d'un échantillon qui n'a pas d'heure : décocher le tronçon qui
  les portait faisait baisser le résumé « X mesure(s) retenue(s) » sans rien
  retirer du calcul. Ces échantillons ont désormais leur propre tronçon
  (au lieu d'être fondus dans le dernier tronçon daté, qu'ils faisaient
  mentir) et s'écartent par un drapeau dédié, enregistré avec la passe.
  Ne concerne que des données anciennes ou importées d'ailleurs — une prise
  directe et un import `.log` sont toujours horodatés.
- **Bandeau de l'Entrepôt.** Il comptait tout l'entrepôt, passes exclues et
  tronçons décochés compris. Il affiche maintenant les échantillons
  **retenus** sur le total accumulé, et le nombre de configurations
  réellement en jeu.
- **Colonne « Échantillons ».** Elle affichait le compte figé au moment du
  traitement, qui ne bougeait jamais. Elle affiche maintenant ce qui est
  retenu, et `retenus / contenus` dès qu'un tronçon a été décoché.
- **Colonne « Confiance » et colonnes Voiles/Moteurs/Dérive.** Elles étaient
  calculées sur toutes les mesures de la passe, tronçons écartés compris :
  une passe dont on venait d'écarter tous les bords au moteur continuait
  d'afficher « moteur » et sa note d'origine. Elles ne décrivent plus que
  les mesures retenues (avec repli sur la passe entière quand il n'en reste
  aucune, pour que la ligne continue de dire ce qu'elle était).
- **Détail de la passe sélectionnée.** Rappelle le total contenu quand des
  tronçons ont été écartés.
- Les plages écartées sont répercutées sur l'entrepôt **avant** le relevé
  qui alimente le tableau, et non après : une restauration de corbeille ou
  un import de sauvegarde ne peut plus afficher l'état d'avant.

### Ajouté

- `PolarSampleStore.count_included()` et `sample_count_for_session()` — ce
  que l'entrepôt *exploite*, à côté de ce qu'il *contient*. La seconde était
  déjà appelée par la restauration d'un recalcul, sans exister : ce dialogue
  annonce maintenant le bon nombre.
- Ce journal des versions.

---

## 2.0a

Première version publiée sous le nom **Allure**.

Générateur de polaires NMEA0183 : écoute UDP et suivi en direct, tampon
glissant de trames brutes, enregistrement de passes annotées par voilure,
import de fichiers `.log`, entrepôt cumulatif avec corbeille, indice de
confiance par case / par passe / par configuration, découpage d'une passe en
tronçons, polaires par configuration et polaire max pour le routage, exports
`.pol`, `.csv` et TimeZero (polaire + guide de voilure).
