# Roadmap de réalisation — Atlas Holding (pas encore livré)

## 0 — Contrats et protections
- [ ] Portail d'installation Windows vérifié en environnement propre, téléchargement demandé une fois et reprenable.
- [ ] Lecture des règles du dépôt cible et catalogue de capabilities.
- [ ] Vérification de la racine Git, interdiction de travailler sur main/master et worktrees distincts.
- [ ] Permissions d'outils bornées au dépôt et commandes autorisées par l'exécuteur.
- [ ] Vérification des entrées/sorties et injection de prompts externes.

## 1 — Noyau autonome hors ligne
- [ ] SQLite transactionnel : missions, états, reprises après crash, dépendances, tentatives, coûts, evidence et journal.
- [ ] CEO qui planifie puis délègue successivement architecte, codeur, relecteur, Doctor.
- [ ] Ollama/OpenCode locaux configurés sans abonnement payant ; profils de contexte adaptés 8 Go VRAM / 32 Go RAM.
- [ ] Checkpoints à points sûrs, aucun commit si tests bloquants en échec.
- [ ] Stratégie de corrections bornée et détection de boucles répétitives.

## 2 — Outils locaux
- [ ] AI Context / Graphify exact-SHA / Doctor plan-check-validate, testés sur une branche jetable.
- [ ] Doctor UI / tests Qt selon diff ; journal des sorties exactes sans faux PASS.
- [ ] Interface Windows montrant progression sourcée, agent actif, utilisation CPU/RAM/VRAM, preuves, erreurs et boutons pause/reprise/stop.

## 3 — Plugins et réseau
- [ ] Pilote MCP à catalogue explicite, outils non disponibles visibles, scope et budget.
- [ ] Context7 : consultation conditionnelle versionnée ; cache documenté.
- [ ] Firecrawl : recherche/scrape/crawl bornés, provenance, quota, file offline ; aucune collecte automatique superflue.
- [ ] GitHub : auth utilisateur via GitHub CLI, vérification des droits, push idempotent, PR Draft/Ready, CI exact SHA, récupération logs.
- [ ] Tester interruption réseau/retour réseau pendant édition, push, test et attente CI.

## 4 — Autonomie longue durée et livraison
- [ ] Roadmap lisible avec graphe de dépendances et critères de sortie par lot.
- [ ] Contrôles d'intégrité : jamais forcer un merge rouge ou masquer un test.
- [ ] Installer Windows en un clic (bootstrap signé/sha vérifié lorsqu'approprié), MAJ, rollback, désinstallation et stockage disque configurable.
- [ ] Windows E2E sur clone jetable : 2 lots indépendants, 1 FAIL puis réparation, mode hors ligne puis reconnexion, certification et blocage correct.
- [ ] N'autoriser l'exécution sans surveillance sur le vrai Atlas qu'après ces validations.

## Interdictions
Aucun shell général, plugin MCP, token ou installation système confié à un LLM sans contrôle. Les étapes ci-dessus sont des **TODO**, pas des capacités déjà fonctionnelles.

## 0B — plugins locaux en priorité + import de roadmap intégrale
- [ ] Import complet et audit de couverture de roadmap, lots/DAG/règles et niveaux d'autonomie ; voir `docs/ROADMAP_INTAKE.md`.
- [ ] Hub MCP local avec serveur stdio, discovery des outils, permissions, vérification santé et configuration OpenCode ; voir `docs/LOCAL_MCP.md`.
- [ ] Plugins locaux Doctor, AI Context, Graphify et Git prêts même sans réseau ; vrais tests hors ligne.
- [ ] Cache documentaire versionné (sources et hash) pour Context7/Firecrawl ; extraction Web légère locale facultative.
- [ ] Firecrawl auto-hébergé comme profil Docker facultatif, démarrage contrôlé selon pression mémoire ; ne pas confondre client local et collecte offline.
- [ ] Interface Plugins : local/online/cache/indisponible + usage quotas + permissions.
