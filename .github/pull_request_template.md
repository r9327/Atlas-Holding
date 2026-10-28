## Objectif et périmètre
Décrire précisément le changement, les exclusions, les propriétaires canoniques et les consommateurs vérifiés. Garder Draft tant que le lot n'est pas complet.

## Preuves au SHA candidat
- [ ] Diff et logique de chaque commit revus, puis cohérence globale de la PR vérifiée.
- [ ] Tests ciblés réellement exécutés : commandes / environnement / résultats et SHA documentés.
- [ ] `python -m tools.governance_check --json` PASS, si applicable.
- [ ] Graphify exact-SHA et contrôle d'architecture réalisés pour une modification structurelle, sinon documenter BLOCKED.
- [ ] Contrats plugins / secrets / persistance contrôlés si impactés.
- [ ] CI universelle Linux + Windows réellement PASS sur HEAD final (après Ready).
- [ ] Workflows de domaine et essais Windows réels requis exécutés ou explicitement NOT_RUN / BLOCKED.
- [ ] Aucun SKIPPED / NOT_RUN requalifié en PASS, aucune ancienne certification réutilisée.

## Risques, limites, suites
Préciser les validations impossibles, dépendances externes, changements de droits et travaux distincts. Un résultat « validation ciblée » n'est pas une certification complète.

Voir `AI_ENTRYPOINT.md` et `HOLDING_CERTIFICATION.md`.