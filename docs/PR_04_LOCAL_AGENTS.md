# PR 04 — Exécution IA locale de qualité (SPECIFICATION, NON IMPLEMENTEE)

**But :** Ryzen 7 5800X, 32 Go RAM et RTX 3060 Ti 8 Go : qualité supérieure à la vitesse, modèle local unique chargé à un instant donné, pas d'abonnement obligatoire.

## Pipeline des rôles
- CEO (décision de planification), architecte, codeur, auditeur, testeur/Doctor, intégrateur, **en alternance**, chacun avec contrat de sortie JSON strict et historique traçable.
- Adapteur Ollama local et exécution OpenCode contrôlée ; healthchecks réels, détection des modèles installés, VRAM/RAM/context window, versions/quantification et mémoire consommée.
- Modèles sélectionnés sur tests comparatifs de vrais lots Atlas ; pas de promesse qu'un 27B quantifié tient intégralement en VRAM ni qu'il est meilleur sur toutes les tâches.
- Contextes courts alimentés par AI Context et extraits pertinents ; réemploi de cache épinglé, résumés sourcés, pas d'injection de toutes les règles dans chaque prompt.
- L'auditeur doit produire observations ciblées sur diff, pas "LGTM" générique ; le testeur utilise des commandes de tests véritables. Les agents ne délivrent aucun PASS par eux-mêmes.
- Timeout, annulation sûre, checkpoint, reprise après plantage du modèle, budget de tours, watchdog de répétitions ; aucun processus IA ne peut installer un plugin ou modifier les permissions.
- Réduire les erreurs en autorisant une deuxième revue par **modèle distinct facultatif** après benchmark, sans 2 modèles lourds simultanés.

## Acceptation
- [ ] Deux modèles/rôles alternent sans épuisement RAM/VRAM ; un seul agent écrivain modifie le worktree.
- [ ] Tâche de codage bornée, auditeur signale un défaut injecté, correction testée et journalisée.
- [ ] Simuler réponse malformée, hallucination de fichier, tool call interdit, boucle, timeout et crash.
- [ ] Aucun résumé agent ne transforme un test FAIL en PASS ; preuves et sorties liées à tâche/SHA.

## Dépendances
#1 règles + API orchestrateur #3 ; connecteurs #5 et Doctor #6 à brancher ensuite.
