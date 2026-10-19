# PR #9 — Exécutable Release : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

Avant publication de `AtlasHoldingSetup.exe` :
- [ ] Machine Windows vierge : installer, choisir disque, détecter outils déjà présents, configurer LLM automatiquement, vérification santé sans nom de modèle saisi.
- [ ] Forfait mobile avec budget de données très inférieur au téléchargement prévu : consentement/cap explicite et reprise après coupure.
- [ ] Simuler Windows/agent crash + restauration complète depuis sauvegarde, y compris SQLite/worktree et permissions.
- [ ] Simuler RAM/VRAM saturée et jeu lancé, contrôler pause/réduction de charge sans perte d'état.
- [ ] Tentatives d'élévation MCP et contournement de Doctor/CI bloquées techniquement, même si l'agent insiste.
- [ ] Test de modèle sur cas cachés aux modèles + rejet de faux PASS et retour arrière après requalification ratée.
- [ ] Exécutable et dépendances vérifiés (hash SHA-256/version/provenance), migration/réparation/retrait sur machine Windows.
- [ ] Autorisations permanentes bornées au dépôt respectées pour push/Ready/fusion et révocables par UI.
- [ ] Installer sans Python préinstallé, puis utilisation réellement offline après provisioning.
- [ ] Aucune Release « certifiée » avec test Windows/Doctor/UI obligatoire non exécuté.

Un simple installateur qui ouvre l'interface n'est pas la fin du projet.