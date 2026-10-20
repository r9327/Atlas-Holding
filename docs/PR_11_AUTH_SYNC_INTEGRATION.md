# PR 11 — Intégration autorisation, orchestration et synchronisation

Cette PR Draft vérifie ensemble les contrats développés séparément dans :

- PR #1 : `998382229aa0091b34e712b55e731198e8745939` ;
- PR #3 : `bf4eb6277c3de1bc2111f6d6920089d2fbcfb571` ;
- PR #7 : `9c5448cc6af2345acb17bdca5dc3fb3dfa7b16fb`.

## Flux intégré

1. Une décision issue du standing grant est sérialisée en reçu canonique.
2. La santé GitHub doit être `ONLINE`, récente et persistée sous le lease courant.
3. L'état actuel de la PR est observé et lié au dépôt, numéro, branche et SHA exacts.
4. Le gate produit une intention bornée par le reçu, le compte et la policy attendue.
5. Le journal SQLite écrit `PENDING` avant l'appel privilégié.
6. L'opération est réobservée après mutation avant de devenir `SUCCEEDED`.
7. Une issue réseau ambiguë reste `PENDING` et impose une inspection ; elle n'est jamais rejouée.

## Limites explicites

- Aucun token n'est fourni au coordinateur ou stocké dans SQLite.
- Le transport GitHub privilégié concret reste à implémenter hors du processus écrivain.
- Le push reste bloqué tant qu'une preuve de non-divergence Git dédiée n'est pas fournie.
- Un statut `SUCCEEDED` atteste l'effet de synchronisation observé, jamais la qualité produit.
- Cette PR ne rend pas les PR #1, #3 ou #7 prêtes à fusionner et ne remplace pas leur certification finale.
