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
8. L'état observé avant mutation est persisté atomiquement avec son empreinte SHA-256.
9. Après un délai minimal, la récupération réobserve GitHub sans muter : elle clôture
   un effet exact déjà visible. Seuls Draft/Ready strictement inchangés libèrent un
   retry borné ; CI et merge restent en inspection tant que leur effet est ambigu.

## Limites explicites

- Aucun token n'est fourni au coordinateur ou stocké dans SQLite.
- Le transport réseau privilégié possède des routes fermées pour Draft/Ready,
  certification et merge exact-SHA. Le worker refuse nativement un fichier token
  Windows qui n'appartient pas à son identité, conserve l'héritage DACL, passe
  par un reparse point ou autorise un principal autre que le compte courant,
  `SYSTEM` et `Administrators`. Ce contrôle porte sur le handle effectivement lu.
  Le worker doit encore être installé sous une identité Windows/ACL dédiée : le
  pont IPC borné, épinglé et sans héritage de secrets est implémenté, mais lancer
  les deux processus sous le même compte ne constituerait pas une isolation de
  secrets.
- Le push possède un coordinateur dédié : worktree propre au SHA exact, observation
  distante fraîche, preuve d'ascendance avant mutation, refspec borné sans force,
  journal `PENDING` préalable et récupération par réobservation sans replay aveugle.
- Un statut `SUCCEEDED` atteste l'effet de synchronisation observé, jamais la qualité produit.
- Un contexte absent (ancienne base), corrompu ou incohérent bloque la récupération.
- Cette PR ne rend pas les PR #1, #3 ou #7 prêtes à fusionner et ne remplace pas leur certification finale.

## Contrats GitHub vérifiés

- [Mutations GraphQL des pull requests](https://docs.github.com/en/enterprise-cloud@latest/graphql/reference/pulls)
  pour Ready/Draft.
- [Merge REST](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
  avec précondition `sha`.
- [Workflow dispatch REST](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
  limité au workflow de certification et à la branche autorisée.
