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
