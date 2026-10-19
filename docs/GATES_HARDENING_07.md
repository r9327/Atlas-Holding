# PR #7 — Réseau mobile et opérations GitHub : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Connexion réputée stable **par service cible** et budget de données avant sync ; éviter relance sur chaque microcoupure.
- [ ] Comparaison du journal local et de `origin/main` avant tout push ; ne pas réécrire ni force-push une branche divergente sans traitement explicite.
- [ ] Probes Context7/Firecrawl/GitHub ciblées sur les dettes de vérification externe ; cache avec preuves et limites de données.
- [ ] Push, PR Draft, Ready/CI et fusion nécessitent des **autorisations initiales distinctes**, persistantes, bornées au dépôt et révocables ; aucun token donné au LLM.
- [ ] CI seulement quand PR **complète** et toutes preuves requises présentes ; pas de certification à chaque commit ou reconnexion.
- [ ] Retries idempotents et borne maximale de tentatives sur réseau intermittent / erreur 5xx / 429 / quota.
- [ ] Aucune publication de secrets, logs sensibles, données privées ou snapshots de configuration dans la PR.
- [ ] Test d'un faux « reconnecté », réseau limité, token révoqué, CI stale et SHA changé.

S'il manque une autorisation, état `REQUIRES_DECISION` explicite, poursuite des autres tâches.