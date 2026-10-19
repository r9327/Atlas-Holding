# PR 06 — Liaison fiable à Dofus Atlas (SPECIFICATION, NON IMPLEMENTEE)

## But
Brancher le moteur Holding aux outils **déjà existants** d'Atlas sans les dupliquer. Les règles du dépôt cible au SHA exact sont la seule autorité de qualité.

## Avant toute édition
- `git rev-parse HEAD`, `git status --short`, branche, remote et worktree isolé ; lire `AI_ENTRYPOINT.md`, `AGENTS.md`, `ZERO_TRUST_RULES.md`, `DEVELOPMENT_GUARDRAILS.md` et autres contrats effectivement concernés.
- `tools.ai_context status` puis route/impact sur chemins connus ; `tools.atlas_doctor agent plan` et profils de qualité selon le périmètre.
- Travail structurel : Graphify exact-SHA / worktree, inspection des consommateurs, confirmation source/tests. Aucun contournement si preuve absente.
- Procédure: plan → modification ciblée → tests module → `agent check` → `agent validate` ; rapport machine complet, sans skip déguisé.
- Qt / Guide / progression / mémoire / réseau : sélectionner vérifications spécifiques Doctor UI ou scénarios Windows selon les contrats réels. Pas de faux screenshot.
- L'adaptateur produit un passeport local (SHA, actions exécutées, états, tests, artefacts et blocages). **VALIDATED_LOCAL ≠ CERTIFIED_FULL**.
- Protéger les fichiers utilisateur, le runtime installé, les hooks et les données persistantes.

## Tests de sortie
- [ ] Exécution hors ligne des tests ciblés sur un clone de démonstration Atlas jetable.
- [ ] SHA périmé/dirty worktree -> refus propre, et Graphify manquant sur refactor -> BLOCKED.
- [ ] Test réel qui échoue -> correction ciblée puis nouveaux résultats, sans réécriture des tests.
- [ ] Changement UI exige capture Doctor UI réelle; test environnement absent reste explicitement NON TESTÉ.
- [ ] Les budgets RAM/latence existants ne sont jamais ajustés par le modèle pour obtenir PASS.

## Dépendances
#1 + #3 + #4 + #5. Les outils doctor/graphify restent dans r9327/Dof-Atlas, la Holding n'en maintient pas une copie forkée.
