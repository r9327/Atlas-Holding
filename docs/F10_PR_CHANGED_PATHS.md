# F10 — Chemins modifiés par une PR ouverte au SHA exact

Cette étape ajoute au **courtier GitHub isolé existant** une nouvelle
observation en lecture seule : `observe_pr_paths(repository, number, head_sha)`.

Le courtier réalise trois requêtes bornées :
1. Vérifier que la PR est ouverte, appartient au dépôt de base demandé et
   pointe sur le SHA attendu.
2. Lire les noms des 100 premiers fichiers modifiés via l'API GitHub,
   **sans contenu de patch, commentaires ni secrets**.
3. Revérifier le SHA et le dépôt de la PR pour rejeter les modifications
   concurrentes survenues pendant l'observation.

Les chemins sont validés, dédupliqués et limités à 20. Les noms sensibles
(identifiants, secrets, clés privées) ne sont pas relayés au modèle. Une PR
de 100 fichiers ou dont les chemins ont été filtrés est déclarée
`PR_PATHS_PARTIAL`, jamais « complète ». La liste vide reste partielle.
Le reste est `PR_PATHS_BOUNDED`, qui confirme **uniquement** l'observation
de la première page de métadonnées à ce SHA.

Le rapport traverse le protocole IPC déjà utilisé par l'authentification
GitHub, avec revalidation de l'identité, des champs et de l'absence de
permission de sauter une mission. Le modèle codeur ne peut pas appeler
directement GitHub et ne reçoit aucun jeton.

Cela prépare la corrélation des fichiers d'une PR concurrente avec les
critères de la roadmap et l'inventaire local exact-SHA. Le raccordement
automatique à la sélection des PR pertinentes n'est **pas encore fait**
dans ce lot. Une correspondance de fichiers ne certifie ni la fonctionnalité
d'une PR, ni sa qualité, ni une autorisation de la fusionner.

Tests : changement de tête avant/après lecture, mauvaise identité dépôt,
états et fichiers malformés, clés privées filtrées, pagination bornée, IPC
et absence de secrets dans les réponses. La certification Windows/Ubuntu
du dépôt et une réception native ACL restent distinctes.
