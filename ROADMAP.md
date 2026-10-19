# Atlas Holding — ROADMAP CANONIQUE V0

**Source de vérité du projet Holding**. Objectif utilisateur : depuis une GitHub Release, télécharger `AtlasHoldingSetup.exe`, double-cliquer, choisir un disque, laisser le setup inspecter / installer / configurer les outils locaux puis piloter des agents IA et plugins via un Control Center Windows. La Holding doit importer toute une roadmap de développement et travailler hors ligne avec reprise fiable, puis contre-expertiser et synchroniser quand Internet revient.

**Statut réel au 19/10/2026 : SPECIFICATIONS UNIQUEMENT.** Les neuf PR [#1 à #9](https://github.com/r9327/Atlas-Holding/pulls) sont en Draft et ne constituent pas un EXE utilisable. Aucun modèle n'est installé sur la machine par le seul fait que cette roadmap existe. Les cases ci-dessous sont des critères à satisfaire, **pas des états de livraison**.

## Parcours cible « je télécharge, je clique »

1. Ouvrir les Releases du dépôt `r9327/Atlas-Holding` et télécharger **un seul** `AtlasHoldingSetup.exe` indépendant de Python sur la machine de l'utilisateur.
2. Le setup lit la configuration Windows (CPU, GPU/VRAM, RAM, disques, espace disponible, connexion, privilèges) et **détecte la présence ET le bon fonctionnement** de Git, Python/venv si nécessaire, Ollama, OpenCode, modèles téléchargés et serveurs MCP. Ne jamais réinstaller sans raison.
3. Afficher un plan d'installation transparent : « déjà prêt », « à réparer », « à installer », « optionnel », « nécessite Internet/connexion utilisateur », avec **volume de téléchargement estimé** avant de lancer un LLM.
4. L'utilisateur choisit l'emplacement initial ; la Holding choisit automatiquement le modèle compatible avec 3060 Ti 8 Go/32 Go et lance les téléchargements nécessaires dans l'autorisation d'installation globale donnée une seule fois. Le setup exécute les installations officielles épinglées, valide hashes/versions/healthchecks, réessaie et reprend les téléchargements interrompus.
5. Les secrets et préférences restent hors dépôts Git. Auth GitHub ou services tiers uniquement lorsque nécessaire, via procédure sécurisée ; les permissions système ou changements irréversibles exigent un accord clair. Aucune clé n'est incluse dans le binaire ou les logs.
6. Le setup clone/choisit **le dépôt canonique** `r9327/Dof-Atlas`, lit ses instructions et prépare des worktrees isolés. Ne jamais lancer automatiquement un writer sur `main` ou un clone utilisateur sale.
7. Afficher le Control Center : santé des modèles/plugins, agents, progression réelle, Doctor/Graphify/AI Context, stockage, consommation système, tâches bloquées, bouton pause/reprendre/stop, journal lisible.
8. L'utilisateur choisit sa roadmap exacte et lance le CEO : planification, découpage exhaustif, agents alternés, tests Doctor **locaux** et sauvegarde persistante.
9. Connexion retrouvée : checkpoint sûr, contre-expertise externe ciblée (Context7/Firecrawl/GitHub), corrections locales si nécessaire, push des commits et PR Draft ; **une CI finale pour une PR entièrement terminée**, sur son dernier SHA. Fusion sous protections et délégation requises.
10. Windows redémarre, l'application ou un plugin échoue, ou le réseau disparaît : la Holding retrouve l'état persistant sans double mutation ni perte du travail.

**Limite physique :** un tout premier téléchargement de modèle ou de dépendance indisponible localement exige Internet. « Fonctionne hors ligne » signifie **après provisioning** ; les MCP distant lancés depuis le PC ne rendent pas la documentation ou les pages Web accessibles sans Internet. Firecrawl auto-hébergé reste un composant avancé optionnel, jamais installé par défaut sur 32 Go RAM.

## Lots de construction et ordre

| Lot | PR | Fonction attendue | Dépend de |
|---|---|---|---|
| 0 | [#1](https://github.com/r9327/Atlas-Holding/pull/1) | Règles, sécurité, architecture, catalogue capabilities, contrat exact-SHA | — |
| 1A | [#3](https://github.com/r9327/Atlas-Holding/pull/3) | CEO, roadmap entière, DAG, SQLite, worktrees sûrs, reprise après crash | #1 |
| 1B | [#2](https://github.com/r9327/Atlas-Holding/pull/2) | Détection/installation/réparation Windows et modèles avec choix du disque | #1 |
| 2A | [#4](https://github.com/r9327/Atlas-Holding/pull/4) | IA alternées locales, programmation, revue contradictoire, limites de tokens/ressources | #3 |
| 2B | [#5](https://github.com/r9327/Atlas-Holding/pull/5) | Hub MCP plugins-first : locaux puis caches puis distants autorisés | #3 |
| 3A | [#6](https://github.com/r9327/Atlas-Holding/pull/6) | Atlas Doctor, AI Context, Graphify exact-SHA, vrais tests | #4, #5 |
| 3B | [#7](https://github.com/r9327/Atlas-Holding/pull/7) | Réseau retrouvé, contre-expertise, synchro GitHub/PR/CI, reprise | #6 |
| 3C | [#8](https://github.com/r9327/Atlas-Holding/pull/8) | UI PySide6 indépendante, outils, ressources, preuves et boutons utiles | #3, puis interfaces #4/#5/#7 |
| 4 | [#9](https://github.com/r9327/Atlas-Holding/pull/9) | Packaging Setup.exe, Windows tests E2E, artefact GitHub Release | #1–#8 |

Des lots indépendants peuvent être développés séparément, mais jamais deux agents modifiant les mêmes fichiers/worktrees. La PR #9 est la **dernière** à déclarer terminée. Décomposition détaillée : [docs/DELIVERY_TO_EXE.md](docs/DELIVERY_TO_EXE.md).

## Critères de sortie globale (tous bloquants)

- [ ] **EXE réel** téléchargeable sur GitHub Release avec SHA-256 et traçabilité de build ; fonctionne sur Windows x64 propre sans Python préalable.
- [ ] Installation automatique **idempotente** : ce qui existe et fonctionne est conservé ; version incompatible réparée ou migrée proprement ; échecs affichés et récupérables ; choix du disque.
- [ ] Installation Ollama, OpenCode, Git, Python et serveurs MCP adéquats avec vérification des versions, permissions et healthchecks ; provisionnement modèles avec consentement sur téléchargements volumineux.
- [ ] Clone ciblé r9327/Dof-Atlas et règles réelles au SHA courant, zéro mutation de `main` ; aucune donnée utilisateur détruite.
- [ ] Roadmap entière importée et couverte sans oubli ; CEO choisit par dépendances ; scripts/outils spécialisés priorisés, contexte externe non fiable.
- [ ] Plusieurs rôles d'IA se succèdent sur un modèle local compatible avec RAM/VRAM, un seul writer, aucune décision de PASS par génération textuelle.
- [ ] Doctor et Graphify locaux effectifs avec vrais tests, preuves exact-SHA et non-régression ; contrôle UI/Windows quand pertinent.
- [ ] L'interface affiche états et preuves réelles, contrôle pause/reprise, reste réactive ; le daemon survit à la fermeture de l'UI.
- [ ] Réseau interrompu / revenu : travail sauvegardé, vérification différée utile seulement, GitHub idempotent, CI à fin de PR, correction puis nouvelle certification si nécessaire.
- [ ] Quotas/devises : dépense de services payants **désactivée par défaut**, besoins d'auth et impossibilités rendus explicites. Jamais de faux succès.
- [ ] Test de bout en bout Windows propre : installation, deux lots, un test FAIL, correction, reconnexion, PR, certification du SHA final, redémarrage et reprise.
- [ ] Publication bloquée tant qu'un gate de sécurité ou de validation est rouge ou non exécuté.

## Sources de référence

- [AGENTS.md](AGENTS.md) : règles communes de la Holding.
- [docs/LOCAL_MCP.md](docs/LOCAL_MCP.md) : plugins locaux, caches, permis et contraintes.
- [docs/ROADMAP_INTAKE.md](docs/ROADMAP_INTAKE.md) : ingestion autonome d'une roadmap.
- [docs/ONLINE_REVALIDATION.md](docs/ONLINE_REVALIDATION.md) : revue Internet différée.
- [docs/AUTOPILOT_PLAN.md](docs/AUTOPILOT_PLAN.md) : inventaire de tâches techniques.
- [docs/DELIVERY_TO_EXE.md](docs/DELIVERY_TO_EXE.md) : organisation des PR.

## Discipline des PR

**Draft pendant le développement**, tests ciblés et Doctor local avant toute certification. Une seule certification finale au candidat complet, puis re-certification sur un nouveau SHA uniquement si correction. Aucune interprétation d'un `skipped` comme `PASS`. Une documentation seule ne constitue pas l'implémentation.

## Sélection LLM sans connaissance technique requise

La Holding doit **choisir elle-même une configuration IA locale privilégiant la qualité du code**, à partir du matériel et de tests comparatifs, sans demander à l'utilisateur de sélectionner un nom de modèle. Elle réutilise les modèles installés et n'autorise de gros téléchargements qu'après affichage de leur volume et accord. La comparaison de modèles et l'affectation codeur/auditeur doivent être fondées sur des benchmarks/tests **mesurés**, pas sur des descriptions marketing. Voir [docs/LLM_AUTO_SELECTION.md](docs/LLM_AUTO_SELECTION.md) (contrat partagé par PR #2, #4, #8 et #9).

- [ ] Premier lancement « configuration recommandée — priorité qualité » sans question technique sur les noms des modèles.
- [ ] Matériel réellement mesuré, choix compatible prouvé, poids installés réutilisés et gros téléchargements consentis.
- [ ] Bascule/réévaluation sans perte de préférences et refus d'un modèle non validé.

## Expérience sans pilotage humain récurrent

**Seule action récurrente : transmettre la ligne directrice des roadmaps.** Le CEO importe les objectifs, structure les lots, choisit modèles et plugins, travaille, teste, revoit, commite, puis synchronise/certifie/fusionne **dans les délégations durables choisies une fois pendant l'installation**. Aucune confirmation manuelle routinière, aucun « continue ». Les décisions réellement hors périmètre restent visibles sans arrêter les autres lots. Voir `docs/USER_AUTONOMY_PROFILE.md` (règle prioritaire pour ce profil).

Le partage mobile de l'utilisateur est **illimité** : aucune limite quotidienne en Go à imposer. Afficher tailles et progression et permettre la reprise en cas de coupure ; continuer à respecter espace disque, quotas API/Firecrawl et politique zéro dépense. La Holding n'est pas lancée pendant les sessions de jeu : inutile de développer une reconnaissance des jeux ; seules les protections système générales restent nécessaires.
