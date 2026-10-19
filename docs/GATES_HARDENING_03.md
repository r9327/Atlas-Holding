# PR #3 — Orchestrateur et stockage : gates supplémentaires (non implémentés)

Référence de politique : PR #1, `docs/OPERATIONAL_READINESS.md`.

- [ ] Contrats d'autorisation persistants, par **dépôt exact**, permission et expiration : lecture, codage, commit, push, Ready/CI, fusion distincts.
- [ ] Moteur de permissions **indépendant du LLM** : l'agent ne peut ni modifier les gates, ni autoriser son propre passage, ni réécrire Doctor/CI pour gagner.
- [ ] Checkpoint atomique état SQLite + références Git, suivi des opérations en vol ; reprise sans double application après crash ou redémarrage Windows.
- [ ] Sauvegarde DB avant migration/upgrade ; restauration démontrée, pas une simple archive.
- [ ] Quotas de disque/cache/logs avec purge contrôlée uniquement des artefacts détenus par Holding ; préserver les worktrees utiles.
- [ ] Gestion CPU/RAM/disque, pression GPU, watchdog d'agent/timeout sans boucle infinie ; pause sûre si jeu prioritaire détecté.
- [ ] Le moteur continue hors ligne sans l'UI, mais **ne démarre pas un travail sur un dépôt non autorisé**.
- [ ] Tests de sandbox : prompt malveillant, conflit worktree, suppression de données, modification de politique et tokens.

Ces gates doivent être automatisés et prouvés sur environnement jetable.