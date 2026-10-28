# Atlas Holding — point d'entrée IA

Ce document est l'entrée canonique pour les contributions IA au dépôt **Atlas-Holding**. Lire AGENTS.md, ZERO_TRUST_RULES.md et les contrats applicables avant une édition non triviale. Les missions dans Dof-Atlas restent gouvernées par ses propres règles au SHA courant.

## Boucle de travail obligatoire

1. Identifier le SHA candidat, le périmètre, les tests et les propriétaires actuels. Lire le code avant de modifier.
2. Classer le risque LOW / MEDIUM / HIGH / CRITICAL. Consulter .ai/task_profiles.json à titre de recommandation; holding/model_selection.py reste seul responsable de choisir un modèle à partir de benchmarks et de la RAM/VRAM.
3. Pour une modification structurelle dans Holding, inspecter les consommateurs et lancer tools.architecture_guard sur la base Git. Dans Dof-Atlas, appliquer les exigences Graphify/Doctor exact-SHA du dépôt cible. Ne pas prétendre avoir exécuté un moteur absent.
4. Choisir les outils par capacité via config/tool_registry.json, holding/tool_registry.py et holding/mcp_policy.py. Outil local vérifié, puis cache daté, puis réseau explicitement autorisé; sinon BLOCKED / OFFLINE_DEFERRED. Jamais de permission ou budget implicite.
5. Modifier sur une branche/worktree isolé sans mutation de main ou d'état utilisateur hors périmètre. Préserver les contrôles de sécurité et de persistance.
6. Tester localement le changement ciblé, vérifier le diff et lancer git diff --check. Pour un changement IA, lancer python -B -X utf8 -m tools.ai_policy_check et les tests test_ai_policy_check.py. Pour le code structurel, lancer python -B -X utf8 -m tools.architecture_guard --base-ref SHA_BASE.
7. Auditer la logique de chaque commit contre son parent, puis l'intégration complète. Verdict de revue: REVIEW OK / IMPROVEMENTS ADVISED / CHANGES REQUIRED. Ne jamais présenter ce verdict comme une certification.
8. Garder la PR Draft pendant l'édition. Une fois le candidat complet et la revue terminée, passer Ready pour déclencher les contrôles au SHA exact. Toute nouvelle modification rend les preuves antérieures périmées.

## Certification

Voir docs/HOLDING_CERTIFICATION.md. Une validation locale ne vaut pas un succès CI, un succès CI ne prouve pas une exécution Windows réelle, et aucune de ces preuves ne vaut automatiquement certification release. Skipped, BLOCKED et tests non exécutés ne sont jamais PASS.

États de preuve : `CODE_DONE` signifie que le patch et sa revue sont terminés ;
`VALIDATED` qu'une vérification délimitée a réellement réussi au SHA indiqué ;
`CERTIFIED` que toute la chaîne exigée par le périmètre a réussi au même SHA.
`NOT_RUN`, `SKIPPED`, `BLOCKED` ou un résultat d'un autre SHA ne sont jamais
des synonymes de `CERTIFIED`.

## Audit borné des règles

Toute modification des instructions d'agents, profils, skills, plugins, registre, CI ou garde-fous doit examiner les consommateurs directs, commandes existantes, permissions, statut d'implémentation et conflits. Garder une autorité canonique et des pointeurs dans les entrées fournisseur.

Contrats: AGENTS.md (orchestration et sécurité), ZERO_TRUST_RULES.md (règles courtes), docs/TOOLCHAIN.md et docs/LOCAL_MCP.md (outils), docs/HOLDING_CERTIFICATION.md (preuves), docs/QUALITY_RELEASE_GATES.md et docs/WINDOWS_LIVE_ACCEPTANCE.md (acceptation native).
