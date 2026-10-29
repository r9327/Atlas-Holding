# Holding Autonomie V2 — backlog approuvé du 28 octobre 2026

Objectif : fournir une direction, puis laisser Holding choisir, exécuter, vérifier et reprendre les lots autorisés sans microgestion.

Les 16 idées retenues sont F01–F16. F17/F18 sont deux compléments proposés dans cette PR. Les statuts sont des états de développement, jamais une certification. Les correctifs de fiabilité restent dans #38 ; le parcours R&D repose sur la chaîne #25–#32.

## État de cette PR

Cette PR livre le backlog complet et un premier composant F02 : `python -B -m holding.inventory_cli --install-target CHEMIN_ABSOLU --plan`. Il compose l’inventaire et le plan de provisioning existants, sans installation, nouvelle permission ou choix de modèle certifié. `--online` exprime une hypothèse du caller ; `network_verification` reste `NOT_RUN`. Sans `--plan`, le JSON historique est conservé.

Le préflight complet par mission (repo, grants, plugins et UI), comme les autres capacités, reste à réaliser. Aucun des 18 lots n’est déclaré DONE ou READY par la présence de ce document.

## Capacités et propriétaires canoniques

| ID | Fonctionnalité | Dépendances | Propriétaire existant | État |
|---|---|---|---|---|
| F01 | Conversation continue par projet | F15 | project_chat / roadmap_replan | À RÉALISER |
| F02 | Préflight de faisabilité et capacités | — | inventory / provision_plan / tool_registry | PARTIEL : export du plan machine seulement |
| F03 | Suivi vivant par résultat | F02 | control_center_projection / project_chat | À RÉALISER |
| F04 | Changement de stratégie après stagnation | F02, F07 | qa_repair / learning / model_selection | À RÉALISER |
| F05 | Retour au dernier état validé | F06 | sqlite_backup / recovery / update_switch | À RÉALISER |
| F06 | Banc d’essai jetable des capacités | F02 | git_worktree / model_selection / code_intelligence | À RÉALISER |
| F07 | Mémoire des erreurs vérifiées | — | learning / state_store / preuves existantes | À RÉALISER |
| F08 | Bilan utile et notifications sobres | F03 | closure / control_center_projection | À RÉALISER |
| F09 | Critères de réussite avant édition | — | roadmap_contract / roadmap_import | À RÉALISER |
| F10 | Détection de l’existant avant création | F09 | ai_context / Graphify / Git / registre | À RÉALISER |
| F11 | Petites livraisons utilisables | F09, F10 | roadmap_contract / coverage / closure | À RÉALISER |
| F12 | Gestion des dépendances entre PR | F11, F06 | sync_coordinator / GitHub / preuves | À RÉALISER |
| F13 | Reproduction et régression des bugs | F06 | qa_scoped / qa_repair / tests du projet | À RÉALISER |
| F14 | Réparation de l’environnement | F02, F06 | provision_runtime / native_health / grants | À RÉALISER |
| F15 | Mémoire des décisions produit | — | roadmap_replan / historiques de contrats | À RÉALISER |
| F16 | Arrêt du travail devenu inutile | F01, F03, F09 | roadmap_replan / control / git_journal | À RÉALISER |
| F17 | Replay d’une panne | F13, F07 | git_journal / events / recovery | À RÉALISER |
| F18 | Réemploi de résultats prouvés | F07, F10, F06 | document_cache / preuves / profils | À RÉALISER |

## Critères de sortie par capacité

### F01 — Conversation continue par projet

Ajouter une demande à un projet existant, avec filiation et historique, sans exiger un nouveau dépôt.

- Une précision après livraison crée une révision liée, sans écraser le contrat précédent.
- Une reprise après crash ne duplique ni demande ni mission.
- Le contexte ancien pertinent est sourcé ; les tâches non touchées continuent.

### F02 — Préflight de faisabilité et capacités

Vérifier automatiquement les prérequis avant une mission et préparer les actions couvertes par les grants.

- Distinguer installé, sain, autorisé, disponible et benchmarké.
- Une API Ollama arrêtée propose un démarrage, pas une réinstallation.
- Un prérequis absent bloque seulement les lots dépendants ; aucun faux READY.

### F03 — Suivi vivant par résultat

Présenter le livrable, les preuves, les blocages précis et la prochaine action dans un écran cohérent.

- Les statuts reflètent SQLite et le SHA observé, sans pourcentage inventé.
- Pas de travail lourd sur le thread Qt.
- Les détails techniques sont consultables sans saturer la vue normale.

### F04 — Changement de stratégie après stagnation

Changer de diagnostic, outil ou modèle après des échecs sans progrès mesurable.

- Détecter les tentatives équivalentes et borner leur budget.
- Une nouvelle stratégie doit s’appuyer sur une information nouvelle.
- Aucun changement silencieux de modèle pendant une écriture ; checkpoint et traçabilité.

### F05 — Retour au dernier état validé

Restaurer un état vérifié en préservant les données et travaux utiles.

- Tester une restauration réelle et une mise à jour défectueuse sur copie jetable.
- Ne pas faire de reset destructeur sur un worktree utilisateur.
- Vérifier schémas, sources et empreintes avant reprise ; garder les versions utiles.

### F06 — Banc d’essai jetable des capacités

Éprouver un modèle, plugin ou changement sur un dépôt isolé avant usage réel.

- Le codeur corrige un défaut injecté et l’auditeur détecte une anomalie.
- Tester timeout, réponse malformée, indisponibilité et fin des processus.
- Les preuves identifient version, digest, environnement et suite ; aucune auto-note.

### F07 — Mémoire des erreurs vérifiées

Réutiliser les diagnostics, solutions et tests validés en invalidant les faits périmés.

- Conserver cause, fichiers, SHA, test et preuve de correction.
- Ne pas recycler un fait lorsque le code ou l’environnement pertinent change.
- La mémoire n’accorde jamais un PASS ni un droit d’exécution.

### F08 — Bilan utile et notifications sobres

Informer sur les résultats et décisions nécessaires, sans journal bavard ni relances de routine.

- Un bilan distingue code livré, validation ciblée, CI, fusion et release.
- Notifier une intervention nécessaire une fois, avec cause et prochaine action.
- Ne pas notifier chaque appel d’outil ; continuer les lots indépendants.

### F09 — Critères de réussite avant édition

Transformer l’objectif en critères stables et vérifiables avant de coder.

- Chaque exigence promise possède un ID et un test ou une preuve attendue.
- Poser zéro à trois questions seulement si elles changent réellement le résultat.
- Un changement de direction conserve la source et le diff des exigences.

### F10 — Détection de l’existant avant création

Chercher les fonctionnalités présentes, les PR et leurs implémentations partielles avant d’ajouter.

- Relever le propriétaire canonique et les consommateurs pertinents.
- Distinguer code présent, PR non fusionnée et fonctionnalité réellement opérationnelle.
- Une proposition de doublon est remplacée par réutilisation ou adaptation justifiée.

### F11 — Petites livraisons utilisables

Découper les gros objectifs en versions cohérentes intégrables.

- Chaque lot produit un résultat vérifiable et possède ses dépendances.
- Les interfaces transversales sont exercées avant accumulation de branches.
- Aucun lot n’est supprimé silencieusement pour présenter le chantier comme fini.

### F12 — Gestion des dépendances entre PR

Ordonnancer les PR, détecter les conflits et retester leur composition.

- Respecter les bases de PR empilées et préserver les commits uniques.
- Une nouvelle composition invalide les preuves incompatibles avec son SHA.
- Fusion uniquement sous délégation et selon les règles applicables, jamais sur du vert périmé.

### F13 — Reproduction et régression des bugs

Partir d’un log, d’une erreur ou d’une capture pour reproduire puis empêcher le retour du défaut.

- Séparer observation, hypothèse et reproduction confirmée.
- Obtenir un test rouge avant correction quand la reproduction le permet.
- Ne pas présenter une analyse statique comme une correction visuelle Windows vérifiée.

### F14 — Réparation de l’environnement

Réparer les dépendances défectueuses dans les autorisations durables.

- Réutiliser les composants sains et ne pas écraser une installation utilisateur.
- Éprouver les sources/pins et garder reprise/rollback.
- Une réparation non autorisée ou payante devient une décision ciblée, pas un blocage global.

### F15 — Mémoire des décisions produit

Conserver les choix explicites de l’utilisateur avec portée, source et possibilité de révision.

- Distinguer décision utilisateur, défaut recommandé et hypothèse du modèle.
- Une nouvelle instruction applicable remplace la préférence précédente avec historique.
- Aucun mélange de décisions ou de données privées entre projets.

### F16 — Arrêt du travail devenu inutile

Réévaluer les tâches quand l’objectif change sans casser une opération atomique.

- Arrêter au checkpoint sûr les tâches obsolètes.
- Préserver les livrables et preuves encore utiles.
- Ne pas annuler une tâche indépendante ni réutiliser une preuve pour un contrat modifié.

### F17 — Replay d’une panne

Rejouer sur environnement jetable les étapes nécessaires à une panne à partir d’un journal minimal.

- Journal sans secrets : versions, entrées autorisées, états et empreintes.
- Replay interdit par défaut sur le dépôt ou les données utilisateur réelles.
- Le replay conserve les différences d’environnement et ne fabrique aucune preuve native.

### F18 — Réemploi de résultats prouvés

Réutiliser un résultat coûteux seulement si son identité et ses préconditions restent valides.

- Clé incluant sources, contrat, versions, environnement et portée du projet.
- Une dépendance changée ou une preuve expirée provoque un recalcul ciblé.
- Aucune propagation de secrets ni certificat emprunté à un autre projet.

## Ordre de réalisation recommandé

1. Fiabilité #38, schéma #32 et consolidation de la certification #33–#37.
2. Fondations F02/F09/F10/F15, puis raccordement de la boucle d’outils et de la sélection des modèles.
3. Conversation et retour utilisateur F01/F03/F08, avec petites livraisons F11.
4. Sandbox, diagnostic et adaptation F06/F13/F04, mémoire F07 et réparation F14.
5. Orchestration des PR F12, rollback F05 et invalidation F16.
6. Replay F17 et réemploi prouvé F18, après mesure de leur gain réel.

Les dépendances du plan sont techniques ; elles n’accordent aucune permission. Chaque lot est encore TRIAGED, y compris F02 qui possède une première brique. Une feuille de route entière doit être traitée sans demandes répétées de go, mais les opérations sensibles hors délégation restent isolées.

## Plan machine et décomposition en PR

Le fichier `HOLDING_AUTONOMY_V2_2026-10-28.plan.json` suit le contrat `holding.roadmap_contract` existant : 18 exigences, 18 lots, couverture exhaustive et graphe sans cycle. Il est lié au SHA source et à l’empreinte de ce document ; après changement de la source/base, il doit être régénéré. Il ne crée ni mission opérationnelle, ni grant, ni statut de réussite.

Un lot peut être subdivisé en plusieurs PR cohérentes, en conservant son ID d’exigence. Une PR de code doit nommer le résultat réellement livré, ses consommateurs et ses tests. Ne pas empiler 18 nouvelles PR de documentation qui dupliquent celle-ci.

## Mesures d’efficacité

Mesurer succès fonctionnel, régressions, temps jusqu’à résolution, interventions demandées, appels/outils, répétitions inutiles, RAM/VRAM et coût réel. Préférer une capacité seulement si elle améliore le résultat ou évite une erreur démontrée. Ne pas choisir sur auto-note du modèle.

## Outils et limites

Réutiliser les mécanismes existants. Ruff/Hypothesis/pip-audit/Gitleaks concernent d’abord le développement ; SDK/Inspector MCP l’interopérabilité ; Serena/Semgrep doivent être raccordés et éprouvés avant extension. Les outils sont déclenchés selon la tâche, pas tous à chaque mission. Aucun framework d’agents, base de mémoire ou service payant supplémentaire n’est imposé.

L’installation Windows, les tests natifs, le SHA exact, les permissions et la séparation des preuves restent ceux des contrats existants. Un plugin ou modèle indisponible appelle une alternative permise et la poursuite des autres lots. Les limites réelles d’un fournisseur ne doivent pas être présentées comme une impossibilité intrinsèque du projet.
