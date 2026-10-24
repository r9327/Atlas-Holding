# PR 07 — Synchronisation réseau et validation différée (IMPLEMENTATION PARTIELLE)

## État technique

La branche contient les preuves FULL exact-SHA, l'émetteur séparé, les gates
d'autorisation GitHub, l'hystérésis par service et l'idempotence des tentatives.
Le push exige désormais une topologie fraîche issue d'un fetch : HEAD local au
SHA autorisé, candidat contenant `origin/main`, et branche distante absente ou
ancêtre du candidat. Une divergence est bloquée ; aucun force-push n'est produit.

Restent notamment à brancher les exécuteurs privilégiés réels, le checkpoint du
writer et les probes Context7/Firecrawl avec leur journal de dettes externes.

**But :** connexion mobile intermittente. Travail 100 % local en absence de réseau ; dès reconnexion fiable, vérifier hypothèses externes, réconcilier GitHub, pousser et certifier les PR **complètes**.

## Pipeline
- Probe réel par service nécessaire (GitHub/Context7/Firecrawl) + hystérésis contre flapping ; aucun simple "Wi-Fi connecté".
- Demander un checkpoint sûr au writer, suspendre nouvelles modifications, puis revue des dettes de vérification externe par tâche, SHA et type de source.
- Context7 pour API/versions, Firecrawl pour pages et données métier réellement utilisées, GitHub pour branches/prérequis/diff. Données nouvelles datées, comparées à la preuve locale ; impact réinjecté comme tâche corrective.
- `git fetch` et comparaison avec main avant push ; aucun rebase destructeur, aucun force-push implicite, gestion des conflits sur worktree protégé.
- Push idempotent, PR Draft pendant développement, Ready seulement quand périmètre et tests locaux terminés ; une Phase Certification au dernier SHA, réexécution sur SHA corrigé si FAIL.
- Polling CI limité et resilient ; logs/artifacts associés au SHA exact ; PASS/FAIL/PENDING/BLOCKED. Fusion seulement si toutes les protections GitHub et garde-fous Atlas vérifiés **et permission déléguée explicite**.
- Après synchronisation, reprendre l'ordonnancement local ; offline ne bloque pas les autres lots indépendants.

## Tests à réussir
- [ ] Connexion coupée pendant l'édition, pendant le push, pendant CI et pendant un téléchargement externe.
- [ ] Service Internet joignable mais token invalide, quota épuisé, API 5xx, branch diverged.
- [ ] Plugin externe contredit une hypothèse offline : mission corrective + Doctor local puis nouveau SHA/CI.
- [ ] PR incomplète ne déclenche pas FULL ; résultat d'un SHA ancien jamais réutilisé.
- [ ] Reconnexion répétée ne recrée pas de PR ou commit et ne relance pas inutilement la CI.

## Dépendances
#1, #3, #4, #5, #6. Contrat `docs/ONLINE_REVALIDATION.md` de #1. Aucune diffusion de tokens à l'IA.
