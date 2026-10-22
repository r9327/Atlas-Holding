# PR #8 — Control Center : gates supplémentaires (partiels)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Vue **Téléchargements & réseau** : volumes, progression, accès aux logs et reprise après coupure ; aucun plafond Go artificiel (forfait mobile illimité).
- [ ] Vue **Santé PC** : RAM/VRAM/CPU/disque, consommation de chaque agent et avertissements de ressources ; aucune détection de jeu requise.
- [ ] Commandes réellement fonctionnelles : pause à checkpoint sûr, reprendre, arrêt du moteur, lever/restreindre une permission, consulter sauvegardes et restaurer avec confirmation.
- [ ] Écran initial « Autopilot complet pour Atlas » : consentement unique et révocable, incluant selon mandat push, Ready/CI et fusion sur le seul dépôt Atlas ; pas de demande de validation pour chaque PR.
- [ ] Écran « Protection et preuves » montrant dernier snapshot sain, SHA, tests Doctor, status QA/CI et vrai BLOCKED.
- [ ] Expliquer clairement en français les téléchargements lourds, les risques des clés, les plugins en cache et le mode offline.
- [ ] L'UI ne peut forger un état READY/PASS/FINI ; état issu de services et preuves persistants.
- [ ] Tests UI réels de fenêtres petites/HDPI, reprise, saturation de logs, état hors ligne et absence de capteurs thermiques.

La fenêtre fermée ne tue pas le moteur, sauf demande utilisateur d'arrêt explicite.
## Contrat utilisateur prioritaire
Voir [le profil d'autonomie](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) dans la PR #1. **La seule action récurrente de l'utilisateur est de fournir la ligne directrice des roadmaps**. La Holding prend en charge choix, exécution, revues, corrections, synchronisation et fusion dans le mandat initial révoquable ; les seuls blocages humains sont les décisions réellement hors délégation ou opérations sensibles.
