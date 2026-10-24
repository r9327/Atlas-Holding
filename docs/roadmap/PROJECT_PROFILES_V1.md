# Profils universels de projets (22/10/2026)

## But
Une seule Holding, plusieurs dépôts cibles. **Ne pas forker l'orchestrateur pour DOFUS**, et ne pas demander au propriétaire de modifier Python pour ajouter un projet.

## Contrat
- Manifeste de profil versionné `project-profile/v1`, identifié par nom/dépôt/branche autorisée, chemins locaux, commandes de test/build explicites, adaptateurs Doctor/Graphify/AI Context disponibles et critères de sortie.
- Profil `dofus-atlas` livré par adaptateur ; nouveau profil générique Python/Qt (et ultérieurement autres langages) sans dépendance à Atlas.
- Les tâches héritent du profil et du SHA exact ; état SQLite cloisonné par projet ; interdit de recycler preuve, caches, permissions ou clés d'un projet sur un autre.
- Sélection automatique des plugins disponibles avec refus explicite si nécessaires et absents ; préférer absence de capacité à un faux PASS.
- Autorisations Git bornées par dépôt/branche/worktree ; interdiction d'éditer `main`, rollback par candidat, quotas indépendants et identités Git contrôlées.
- Import/export JSON canonique, migrations versionnées et validation stricte des commandes, chemins et variables ; jamais de commande arbitraire issue du Web.

## Tests
- Profil valide Atlas ; profil simple Python fictif indépendant avec tests/build ; profil malformé refusé.
- Deux projets concurrents : isolation de la mémoire, du cache, du travail Git et des décisions.
- Refus de traversée `../`, commande non autorisée et mauvais repo ; cycle de validation sans réseau.
- GUI #8 affiche projet/profil et provenance sans démarrer les modèles.

## Dépendances
- Réutiliser PR Holding #3/#4/#6/#7/#8/#11/#12, pas un deuxième service.
- Draft : **spécification de chantier uniquement** jusqu'à l'implémentation et tests Windows.
