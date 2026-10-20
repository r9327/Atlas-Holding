# PR 04 — Exécution IA locale de qualité (IMPLEMENTATION PARTIELLE)

## État technique

`holding/model_selection.py` sélectionne désormais automatiquement un modèle
déjà installé pour un rôle, uniquement à partir de benchmarks indépendants liés
au SHA du dépôt, à la suite, à l'environnement et au digest exact du modèle.
Les tests fonctionnels/outils/sécurité/régression et les budgets RAM, VRAM et
contexte sont bloquants. `BALANCED_VERIFIED` accepte une tolérance qualité de
deux points avant de privilégier latence/ressources ; `QUALITY_FIRST` conserve
le meilleur score indépendant. Aucun téléchargement ni démarrage de modèle
n'est effectué par ce composant.

Le runtime Ollama/OpenCode, l'alternance effective des rôles, les watchdogs et
les benchmarks Atlas natifs restent à implémenter et prouver ; la PR reste Draft.

**But :** Ryzen 7 5800X, 32 Go RAM et RTX 3060 Ti 8 Go : meilleur compromis qualité/temps **avec qualité, sécurité et non-régression comme critères bloquants**, modèle local unique chargé à un instant donné, pas d'abonnement obligatoire.

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
- [ ] Profil **par défaut `BALANCED_VERIFIED`** : sécurités et tests d'acceptation comme gates bloquants, puis classement parmi les solutions conformes par qualité démontrée, temps réel, itérations et ressources. Profil optionnel `QUALITY_FIRST` pour les tâches justifiant un effort supplémentaire ; ne jamais conclure à la qualité sur la seule auto-note d'une IA.
- [ ] Modèle devenu incompatible/OOM/timeout : réévaluer puis rollback ; pas de changement silencieux de modèle en plein commit.
- [ ] Profil et résultats visibles depuis Control Center #8 et persistant en SQLite.

## Efficacité adaptative, mémoire et apprentissage (décision utilisateur du 20/10/2026 — À IMPLÉMENTER)

- [ ] Choix automatique des rôles : petite tâche → codeur et tests ciblés ; difficulté, criticité, faible confiance ou échecs → architecte/auditeur/Doctor supplémentaires selon gain mesuré. Ne pas payer le coût multi-agent systématiquement.
- [ ] Routage par benchmark Atlas **reproductible** : succès fonctionnel et tests cachés en prérequis, puis temps jusqu'à résolution, nombre de corrections, consommation CPU/GPU/RAM et taux de régression ; comparaison sur tâches de difficulté comparable et versions/SHAs identifiées.
- [ ] Mémoire technique **sourcée et invalidable** : conserver références aux fichiers/SHAs, diagnostics réussis ou échoués, solutions validées et zones impactées ; réutiliser uniquement les extraits pertinents ; invalider les faits périmés et interdire que la mémoire déclare un test PASS.
- [ ] Après stagnation détectée par #3 : ne pas répéter la même stratégie ; produire un nouveau diagnostic testable, mobiliser outil/rôle différent seulement s'il apporte de l'information, ou passer BLOCKED avec justification et sauvegarde sûre.
- [ ] Apprentissage prudent : préférer une stratégie seulement après amélioration confirmée sur jeux de tests indépendants et absence de régression ; conserver rollback, historique et comparaison avant/après. Aucune « auto-amélioration » fondée sur la satisfaction auto-déclarée du modèle.
- [ ] Mesurer aussi la latence des transitions de modèles et le coût de contexte ; les budgets de temps sont des cibles de pilotage calibrées, jamais des raisons de sauter une vérification requise.
- [ ] Tests : petit correctif sans cascade d'agents, échec répété basculant de stratégie, benchmark qui rejette une option rapide mais défectueuse, et absence d'escalade coûteuse si aucune plus-value démontrée.

**Non certifié** : exigences nouvelles tant que les essais de bout en bout sur Atlas et Windows ne sont pas exécutés.
