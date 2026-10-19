# PR #8 — Control Center : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Vue **Partage mobile** : débit, plafond Go et données réellement consommées, téléchargements en attente, pause/reprise.
- [ ] Vue **Santé PC** : RAM/VRAM/CPU/disque, consommation de chaque agent, avertissement mémoire et jeu en cours lorsque détectable.
- [ ] Commandes réellement fonctionnelles : pause à checkpoint sûr, reprendre, arrêt du moteur, lever/restreindre une permission, consulter sauvegardes et restaurer avec confirmation.
- [ ] Écran premier lancement « Autorisations » : coder/commits locaux par défaut ; autoriser indépendamment push, Ready/CI et fusion sur le seul dépôt Atlas.
- [ ] Écran « Protection et preuves » montrant dernier snapshot sain, SHA, tests Doctor, status QA/CI et vrai BLOCKED.
- [ ] Expliquer clairement en français les téléchargements lourds, les risques des clés, les plugins en cache et le mode offline.
- [ ] L'UI ne peut forger un état READY/PASS/FINI ; état issu de services et preuves persistants.
- [ ] Tests UI réels de fenêtres petites/HDPI, reprise, saturation de logs, état hors ligne et absence de capteurs thermiques.

La fenêtre fermée ne tue pas le moteur, sauf demande utilisateur d'arrêt explicite.