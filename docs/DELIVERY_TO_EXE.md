# Atlas Holding — Plan de livraison de l'EXE (V0)

Ce document pilote l'ordre des PR **créées en Draft**. Les PR #2–#9 contiennent actuellement des **contrats d'implémentation**, pas du code fini ; toutes les tâches doivent être réalisées et testées avant fusion. Les composants `docs/LOCAL_MCP.md`, `docs/ROADMAP_INTAKE.md` et `docs/ONLINE_REVALIDATION.md` sont des spécifications.

## Registre des PR

| PR | Livrable concret | Dépendances | Gate de fin |
|---|---|---|---|
| [#1](https://github.com/r9327/Atlas-Holding/pull/1) | Contrats, permissions, repo canonique, catalogue outils | Aucune | Contrats cohérents, tests de sécurité et validations de leurs consommateurs |
| [#2](https://github.com/r9327/Atlas-Holding/pull/2) | Bootstrap Windows : installer/détecter/configurer Ollama, Python, Git, OpenCode, choix disque, reprise | #1 | Installation, réparation et mode offline testés sur Windows |
| [#3](https://github.com/r9327/Atlas-Holding/pull/3) | CEO, import de roadmap entière, DAG, SQLite, worktrees, checkpoints, contrôle des permissions | #1 | Tests d'import, de crash/reprise, de sécurité Git et de transactions |
| [#4](https://github.com/r9327/Atlas-Holding/pull/4) | IA locales alternées (architecte, codeur, auditeur) + validation structurée des réponses | #1, #3 | Correction d'un bug injecté et absence de faux PASS |
| [#5](https://github.com/r9327/Atlas-Holding/pull/5) | Hub plugins MCP local, Context7/Firecrawl en cache/online, budgets et droits | #1, #3 | Tests offline, quotas, injection externe et capacités réelles |
| [#6](https://github.com/r9327/Atlas-Holding/pull/6) | Adaptateur Atlas : AI Context, Graphify exact-SHA, Doctor local, UI/Qt | #1, #3, #4, #5 | Contrats et tests réels sur clone jetable d'Atlas |
| [#7](https://github.com/r9327/Atlas-Holding/pull/7) | Reconnexion : revue Internet différée, push PR, logs CI, correction/recertification | #1, #3, #4, #5, #6 | Épreuves réseau intermittent, SHA et permissions |
| [#8](https://github.com/r9327/Atlas-Holding/pull/8) | Control Center PySide6 : progression réelle, plugins, agents, pause/stop/reprise | #1, #3 ; interfaces #4/#5/#7 | Captures et scénarios réels, fermeture UI sans tuer moteur |
| [#9](https://github.com/r9327/Atlas-Holding/pull/9) | Packaging Windows, `AtlasHoldingSetup.exe`, tests E2E, release | #1–#8 | EXE Windows téléchargé/installé/testé, sortie certifiée |

## Ordre recommandé

1. Fusionner #1 **seulement lorsqu'elle contient les fondations techniques validées**.
2. Développer #3, et préparer #2 en parallèle sans partage de fichiers.
3. Sur les contrats stabilisés de #3 : #4 et #5 (pas deux writers sur le même worktree).
4. Brancher #6, puis #7. #8 peut être développé en parallèle avec contrats de service figés.
5. Intégrer, tester et certifier #9 en dernier.

Les branches #2–#9 sont initialement basées sur `main`, non sur une branche empilée : rebaser/synchroniser sans force-push destructeur après fusion des dépendances.

## Règles communes de sortie
- Un seul auteur de modifications au même instant pour un worktree donné ; autres tâches indépendantes possibles.
- Mode hors ligne par défaut. Plugins locaux spécialisés prioritaires ; les outils réseau sont utilisés et revérifiés lorsque disponibles sans abonnement obligatoire.
- En développement : tests ciblés Doctor, PAS de FULL par commit ; PR en Draft. Ready uniquement quand implémentation, relecture, intégration et tests sont terminés.
- Certification réelle sur le SHA final, puis fusion uniquement si les politiques du dépôt et les autorisations convenues l'admettent.
- Jamais d'accès en écriture à `main`, pas de modification de données utilisateur, pas de désactivation de checks pour obtenir vert.
- Une PR en Draft contenant seulement la spécification ne vaut **aucun avancement fonctionnel**.
- Aucun release public, signature revendiquée ni exécutabilité Windows proclamée sans artefact binaire et test Windows de bout en bout.

## Définition du lancement utilisateur
Le parcours accepté doit être : ouvrir la Release → télécharger un Setup EXE → choisir un disque → détecter/installer les dépendances approuvées → provisionner un modèle au téléchargement reprenable → démarrer une interface claire → importer la roadmap → exécuter les agents et vérifications locales → retrouver réseau → contre-expertiser et certifier uniquement les PR complètes.

Les installations système, l'accès réseau, les permissions GitHub de mutation et les grands téléchargements initiaux doivent être autorisés lors de l'installation initiale ; ensuite, la Holding peut travailler selon ces **autorisations durables bornées** et révocables.
