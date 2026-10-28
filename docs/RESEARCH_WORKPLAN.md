# Dossier R&D vers feuille de route vérifiable

Après le chat et le premier dossier R&D (PR #27), le modèle local
propose jusqu'à huit tâches de conception et développement, avec critères
testables et dépendances explicites.

Seules les tâches classées `code` dont toutes les dépendances sont aussi
du code sont ajoutées à la roadmap existante. Le scheduler ne les exécute
que si le dépôt, l'exécuteur et les permissions `code` sont déjà vérifiés.
Une proposition d'IA ne donne jamais de permission, de preuve Doctor ni de
certification de fin de projet.

Les tâches `research`, `simulation`, `hardware` et `external` restent
dans le plan en attente des adaptateurs correspondants. Elles ne sont pas
silencieusement ignorées : le journal SQLite conserve leur nombre et le
plan enregistré permet de les consulter.

Le plan est stocké avant son transfert vers le moteur (`PREPARED`), puis
marqué `STAGED` après l'ajout des tâches. Si le processus est interrompu,
une reprise utilise le même plan et les IDs identiques sans redemander au
modèle ni écraser les missions en cours.

Le projet initial R&D constitue une **provenance**, pas une dépendance
`DONE` artificielle. Les tests indépendants restent nécessaires avant
toute certification. Ce changement n'exécute pas d'expériences physiques,
de CAO ou de simulations et ne garantit pas la qualité d'un modèle réel.
