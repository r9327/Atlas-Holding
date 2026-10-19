# PR #9 — Exécutable Release : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

Avant publication de `AtlasHoldingSetup.exe` :
- [ ] Machine Windows vierge : installer, choisir disque, détecter outils déjà présents, configurer LLM automatiquement, vérification santé sans nom de modèle saisi.
- [ ] Forfait mobile illimité : pas de cap Go ; consentement global initial pour les téléchargements de la configuration recommandée, affichage des tailles et reprise après coupure.
- [ ] Simuler Windows/agent crash + restauration complète depuis sauvegarde, y compris SQLite/worktree et permissions.
- [ ] Simuler RAM/VRAM saturée et pression disque, contrôler pause/réduction de charge sans perte d'état ; pas de détection de jeu.
- [ ] Tentatives d'élévation MCP et contournement de Doctor/CI bloquées techniquement, même si l'agent insiste.
- [ ] Test de modèle sur cas cachés aux modèles + rejet de faux PASS et retour arrière après requalification ratée.
- [ ] Exécutable et dépendances vérifiés (hash SHA-256/version/provenance), migration/réparation/retrait sur machine Windows.
- [ ] Mandat Autopilot complet unique, explicite, borné au dépôt pour push/Ready/fusion et révocable ; pas de micro-validation par PR.
- [ ] Installer sans Python préinstallé, puis utilisation réellement offline après provisioning.
- [ ] Aucune Release « certifiée » avec test Windows/Doctor/UI obligatoire non exécuté.

Un simple installateur qui ouvre l'interface n'est pas la fin du projet.
## Contrat utilisateur prioritaire
Voir [le profil d'autonomie](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) dans la PR #1. **La seule action récurrente de l'utilisateur est de fournir la ligne directrice des roadmaps**. La Holding prend en charge choix, exécution, revues, corrections, synchronisation et fusion dans le mandat initial révoquable ; les seuls blocages humains sont les décisions réellement hors délégation ou opérations sensibles.
