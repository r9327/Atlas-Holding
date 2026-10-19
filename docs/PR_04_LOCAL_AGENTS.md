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

## Routage automatique par qualité, pas par nom de modèle

La Holding ne doit jamais demander à l'utilisateur de choisir lui-même « Qwen / Devstral / X paramètres ». Consommer le **profil matériel et les modèles validés** produits par #2. Choisir les modèles selon le contrat `docs/LLM_AUTO_SELECTION.md` de #1 : qualité et conformité aux tests avant rapidité ; tâche et licences compatibles ; aucun coût d'API externe ; un modèle lourd à la fois ; CPU offload et contexte réellement mesurés.

- [ ] Benchmarks locaux reproductibles sur worktrees jetables : correctif rouge→vert, appel d'outil, respect des garde-fous, relecture d'anomalies et gestion d'incertitude.
- [ ] Chaque score s'appuie sur des preuves non générées par les IA : tests, Doctor, diff et critères d'acceptation.
- [ ] Routeur qui désigne codeur/auditeur automatiquement ; un deuxième modèle (facultatif) seulement si avantage validé, téléchargement autorisé et RAM suffisante.
- [ ] Score prioritaire sur qualité, sécurité comme gate bloquant ; vitesse ne départage qu'en dernier pour le profil `QUALITY_FIRST`.
- [ ] Modèle devenu incompatible/OOM/timeout : réévaluer puis rollback ; pas de changement silencieux de modèle en plein commit.
- [ ] Profil et résultats visibles depuis Control Center #8 et persistant en SQLite.
