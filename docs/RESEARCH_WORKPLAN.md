# Dossier R&D vers feuille de route vérifiable

Après le chat et le premier dossier R&D (PR #27), le modèle local
propose jusqu'à huit tâches de conception et développement, avec critères
testables et dépendances explicites.

Les tâches classées `code` sont ajoutées à la roadmap existante lorsque
leurs dépendances sont des tâches de code transmises au moteur ou des calculs
numériques/RC effectivement enregistrés et vérifiés. Les empreintes des calculs
directement utilisés sont attachées au contrat de la tâche. Le scheduler ne les exécute
que si le dépôt, l'exécuteur et les permissions `code` sont déjà vérifiés.
Une proposition d'IA ne donne jamais de permission, de preuve Doctor ni de
certification de fin de projet.

Les calculs numériques bornés et les circuits RC configurés sont exécutés par
leurs adaptateurs existants. Le coordinateur revisite les deux moteurs jusqu'à
stabilisation du graphe borné à huit tâches, y compris RC → numérique → RC.
Les paramètres demeurent ceux du plan : il n'y a pas de substitution automatique
des résultats dans les paramètres de la tâche suivante.

Une erreur du simulateur RC diffère ce circuit et ses descendants sans empêcher
les travaux indépendants d'avancer. Chaque circuit est tenté une seule fois par
passage ; le motif reste journalisé sans doublon. Le bouton Reprendre / actualiser
permet une nouvelle tentative après correction du simulateur. Une corruption
de résultat enregistré et Pause/STOP bloquent toujours la reprise.

Les tâches `research`, `hardware`, `external` et les simulations sans adaptateur
ou sans prérequis satisfaits restent dans le plan en attente. Elles ne sont pas
silencieusement ignorées : le journal SQLite conserve leur nombre et le
plan enregistré permet de les consulter.

Le plan est stocké avant son transfert vers le moteur (`PREPARED`), puis
marqué `STAGED` après l'ajout des tâches. Si le processus est interrompu,
une reprise utilise le même plan et les IDs identiques sans redemander au
modèle ni écraser les missions en cours.

Le projet initial R&D constitue une **provenance**, pas une dépendance
`DONE` artificielle. Les tests indépendants restent nécessaires avant
toute certification. Ce changement n'exécute pas d'expériences physiques,
de CAO et ne garantit ni la qualité d'un modèle réel ni la validité expérimentale
des calculs. Le transfert de tâches débloquées reste idempotent après une coupure
entre l'import au moteur et le checkpoint du plan.
