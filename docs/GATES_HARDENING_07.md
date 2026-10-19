# PR #7 — Réseau mobile et opérations GitHub : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Connexion réputée stable **par service cible**, reprise des requêtes et quotas API ; éviter relance sur chaque microcoupure. Forfait mobile illimité : aucun cap artificiel en Go.
- [ ] Comparaison du journal local et de `origin/main` avant tout push ; ne pas réécrire ni force-push une branche divergente sans traitement explicite.
- [ ] Probes Context7/Firecrawl/GitHub ciblées sur les dettes de vérification externe ; cache avec preuves et limites de données.
- [ ] Push, PR Draft, Ready/CI et fusion utilisent un **mandat Autopilot complet consenti une fois**, persistant et limité au dépôt/opérations/SHA/tests ; aucun consentement par PR, aucun token donné au LLM.
- [ ] CI seulement quand PR **complète** et toutes preuves requises présentes ; pas de certification à chaque commit ou reconnexion.
- [ ] Retries idempotents et borne maximale de tentatives sur réseau intermittent / erreur 5xx / 429 / quota.
- [ ] Aucune publication de secrets, logs sensibles, données privées ou snapshots de configuration dans la PR.
- [ ] Test d'un faux « reconnecté », réseau limité, token révoqué, CI stale et SHA changé.

S'il manque une autorisation, état `REQUIRES_DECISION` explicite, poursuite des autres tâches.
## Contrat utilisateur prioritaire
Voir [le profil d'autonomie](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) dans la PR #1. **La seule action récurrente de l'utilisateur est de fournir la ligne directrice des roadmaps**. La Holding prend en charge choix, exécution, revues, corrections, synchronisation et fusion dans le mandat initial révoquable ; les seuls blocages humains sont les décisions réellement hors délégation ou opérations sensibles.
