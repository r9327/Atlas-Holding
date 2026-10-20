# PR 11 — Intégration autorisation, orchestration et synchronisation

Cette PR Draft vérifie ensemble les contrats développés séparément dans :

- PR #1 : `998382229aa0091b34e712b55e731198e8745939` ;
- PR #3 : `bf4eb6277c3de1bc2111f6d6920089d2fbcfb571` ;
- PR #7 : `9c5448cc6af2345acb17bdca5dc3fb3dfa7b16fb`.

## Flux intégré

1. Une décision issue du standing grant est sérialisée en reçu canonique.
2. La santé GitHub doit être `ONLINE`, récente et persistée sous le lease courant.
   Une sonde authentifiée fermée interroge uniquement `/user` dans le broker,
   ne restitue que `github:<login>` et exige deux observations concordantes.
   Une identité inattendue ou un rejet explicite bloque ; les erreurs réseau et
   limitations temporaires suivent le backoff SQLite borné sans fabriquer `ONLINE`.
3. L'état actuel de la PR est observé et lié au dépôt, numéro, branche et SHA exacts.
4. Le gate produit une intention bornée par le reçu, le compte et la policy attendue.
   Le grant GitHub complet est conservé comme BLOB avec son empreinte dans le
   schéma SQLite v5 ; il est réauthentifié à chaque reçu exact-SHA et une
   révocation désactive atomiquement les scopes locaux et GitHub.
5. Le journal SQLite écrit `PENDING` avant l'appel privilégié.
6. L'opération est réobservée après mutation avant de devenir `SUCCEEDED`.
7. Une issue réseau ambiguë reste `PENDING` et impose une inspection ; elle n'est jamais rejouée.
8. L'état observé avant mutation est persisté atomiquement avec son empreinte SHA-256.
9. Après un délai minimal, la récupération réobserve GitHub sans muter : elle clôture
   un effet exact déjà visible. Seuls Draft/Ready strictement inchangés libèrent un
   retry borné ; CI et merge restent en inspection tant que leur effet est ambigu.
10. Le worker runtime `push` résout un unique worktree issu de dépendances déjà
    certifiées, émet un reçu frais et ne marque la mission `DONE` que si le journal
    contient l'effet distant `SUCCEEDED` au même dépôt, branche, SHA et idempotency key.
    Une attente réseau ne consomme pas le budget, une divergence bloque et une issue
    ambiguë place la mission en récupération sans second push.

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
- Le worker `push` existe mais le bootstrap multi-rôle ne l'installe pas encore à
  côté du worker de code par défaut. Le routeur multi-rôle et le hook de bootstrap
  trusted `enable_git_push` sont implémentés avec capacités dynamiques : un rôle
  indisponible n'est pas réclamé et ne bloque pas un autre rôle sain. Le fichier
  d'installation v1 n'apporte toutefois pas encore l'identité/policy GitHub ni
  la configuration épinglée du broker à `enable_git_push` ; l'activation dans
  le daemon installé reste donc incomplète, même si la sonde authentifiée et son
  hystérésis sont désormais implémentées.

## Contrats GitHub vérifiés

- [Mutations GraphQL des pull requests](https://docs.github.com/en/enterprise-cloud@latest/graphql/reference/pulls)
  pour Ready/Draft.
- [Merge REST](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
  avec précondition `sha`.
- [Workflow dispatch REST](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
  limité au workflow de certification et à la branche autorisée.
