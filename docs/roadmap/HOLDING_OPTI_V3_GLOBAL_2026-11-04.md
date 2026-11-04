# Holding Opti V3 — audit exhaustif et cahier des charges exécutable

> **Statut : SPEC_ONLY / NOT_RUN.** Ce document versionné appartient à la PR Draft issue de [l'issue #62](https://github.com/r9327/Atlas-Holding/issues/62). Il ne correspond à **aucune optimisation implémentée** ni aucun benchmark réalisé. Base étudiée : `main@7813063b416ebaf945c0ededbfb8fdd448909a29`.

## Périmètre

Optimiser le runtime Windows local **Python, PySide6, SQLite, Ollama, MCP, Git, QA et agents** ; augmenter les missions réellement terminées tout en diminuant temps, tokens, contexte, mémoire, CPU, IO et erreurs. Toutes les bonnes pratiques pertinentes doivent être imposées **aussi au futur code** à travers les propriétaires canoniques existants et des tests ciblés. Les règles web hors objet ne justifient aucune dépendance serveur.

À relire avant développement : `AGENTS.md`, `AI_ENTRYPOINT.md`, `ZERO_TRUST_RULES.md`, `docs/HOLDING_CERTIFICATION.md`, `docs/TOOLCHAIN.md`, `docs/LOCAL_MCP.md`, `docs/ARCHITECTURE_REGRESSION_GATE.md`, `docs/QUALITY_RELEASE_GATES.md`, `docs/WINDOWS_LIVE_ACCEPTANCE.md`. Appliquer `tools.ai_policy_check` pour IA/plugin et `tools.architecture_guard` pour refactor Holding ; appliquer Graphify/Doctor exact-SHA dans les dépôts cibles concernés. Distinction absolue entre code, tests, CI, vrais essais Windows et release.

## Audit des 31 règles

Les statuts sont **issus d'un contrôle statique de sources**, pas d'un test natif. Chaque ligne HOPT-001…031 devra disposer d'un état confirmé, de fichiers, tests, coût utilisateur, benchmark avant/après ou `NOT_APPLICABLE` motivé.

| ID | Règle | Statut initial | Code/propriétaire à examiner | Adaptation ou absence d'objet |
| --- | --- | --- | --- | --- |

| HOPT-001 | Skeletons et progression visible | PARTIAL | `holding/project_chat_ui.py` | États de génération et premier résultat, sans faux pourcentage |
| HOPT-002 | Images adaptées WebP/AVIF | N/A | Qt images | Décoder à la taille/UX uniquement si futur écran images |
| HOPT-003 | Éviter doublons appels API | PARTIAL | `holding/mcp_cache.py`, `holding/document_cache.py` | Dédupliquer appels identiques sécurisés avec SHA et TTL |
| HOPT-004 | Debounce des filtres et recherches | N/A | `holding/control_center_ui.py` | Debounce seulement pour filtres réellement lourds |
| HOPT-005 | Pagination des listes et réponses | PARTIAL | `holding/control_center_projection.py` | Pagination stable des missions, historique, journal |
| HOPT-006 | CDN assets statiques | N/A | Windows offline | Écarter tout CDN obligatoire |
| HOPT-007 | Tâches lourdes hors UI | APPLIED | `holding/project_chat_ui.py`, `holding/bounded_process.py` | Qt libre ; queue/worker bornés, contrôle STOP/cancel |
| HOPT-008 | Élimination requêtes N+1 | PARTIAL | `holding/control_center_projection.py` | Regrouper SQL pour preuves UI mesurées, compte de requêtes |
| HOPT-009 | Compression HTTP | N/A | IPC/MCP | Comparer compression de cache/IPC seulement si utile |
| HOPT-010 | Redis partagé | N/A | `holding/state_store.py` | Écarter Redis ; SQLite locale owner |
| HOPT-011 | Dépendances JavaScript inutiles | N/A | imports Python | Lazy imports et dépendances Python facultatives |
| HOPT-012 | Timeouts externes | PARTIAL | `holding/ollama_protocol.py`, `holding/mcp_policy.py` | Délai global, retries et erreurs structurées par service |
| HOPT-013 | Streaming IA | ABSENT | `holding/ollama_protocol.py` | Streaming LLM progressif facultatif ; aucun JSON partiel appliqué |
| HOPT-014 | UI optimiste | N/A | commits/permissions | Mutation seulement après preuve/rollback |
| HOPT-015 | Images hors écran différées | N/A | Qt images | Lazy assets Qt conditionnel |
| HOPT-016 | Parallélisme indépendant | PARTIAL | `holding/process_agent.py`, `holding/service.py` | Lectures concurrentes seulement si ressources et permissions le permettent |
| HOPT-017 | Index SQLite mesurés | PARTIAL | `holding/schema.py`, `holding/state_store.py` | EXPLAIN + index ciblés, pas de sur-indexation |
| HOPT-018 | Tracing corrélé OpenTelemetry | ABSENT | `holding/learning.py`, preuves | Tracing local mission→tool→modèle→Doctor→GitHub, exporter seulement si autorisé |
| HOPT-019 | Proximité serveur/base | N/A | SQLite | SQLite locale |
| HOPT-020 | SSR/SSG | N/A | Qt/PySide6 | Écarter SSR |
| HOPT-021 | Scripts tiers différés | N/A | importations Python | Retarder plugins facultatifs, pas de script JS |
| HOPT-022 | Découpage JavaScript | PARTIAL | imports Python | Équivalent : lazy Python Qt et loaders de modules |
| HOPT-023 | Virtualisation listes Qt | PARTIAL | `holding/control_center_ui.py` | Tester QTableWidget sur gros historiques, éventuellement QAbstractItemModel |
| HOPT-024 | Réduction SELECT * | PARTIAL | `holding/state_store.py` | Audit SELECT * et parité des décodeurs |
| HOPT-025 | Pool DB côté serveur | N/A | SQLite WAL | Pas de pool serveur ; mesurer locks/transactions |
| HOPT-026 | Rendus UI ciblés | APPLIED | `holding/control_center_ui.py` | Réutiliser cellules Qt, éviter repaint complet |
| HOPT-027 | Cache-Control et cache local versionné | PARTIAL | `holding/mcp_cache.py`, `holding/document_cache.py` | Cache local vérifié, borné, versionné et invalidation exacte |
| HOPT-028 | Équilibrage de charge | N/A | `holding/engine.py` | Conserver un writer Git, pas répartir les mutations |
| HOPT-029 | Optimisation WOFF2 fonts | N/A | polices Qt | Polices natives ; aucune exigence WOFF2 |
| HOPT-030 | Monitoring/perf réelle | PARTIAL | `holding/ollama_observation.py` | RAM processus et LLM distinctes, tokens et latence p95/p99 locaux, PostHog opt-in |
| HOPT-031 | Scénarios PC/réseau contraints | PARTIAL | `holding/resource_guard.py`, `docs/WINDOWS_LIVE_ACCEPTANCE.md` | Windows bas débit/low spec, téléchargement/reprise, charge modèle réelle |

### Bilan statique

- **2 règles déjà appliquées** au niveau mécanisme : #7 workers hors UI, #26 cellules Qt mises à jour de manière ciblée.
- **13 partiellement appliquées** : ne pas les déclarer terminées.
- **2 absentes** : #13 streaming IA et #18 tracing distribué/OTel. Une alternative locale peut être préférable à OTel ou PostHog.
- **14 sans objet au sens web** ; examiner uniquement leurs équivalents natifs.
- Le code vérifié utilise `stream:false` pour `/api/chat` avec patch JSON complet. Le streaming ne doit pas provoquer d'écriture de patch incomplet.
- `StateStore.__init__` exécute `PRAGMA integrity_check` à l'ouverture ; `claim_next` et le snapshot UI balaient des missions/preuves : **hypothèses de profilage**, pas bugs confirmés.

## Optimisations supplémentaires — HOPT-032 à HOPT-047 (issues #62)


- **HOPT-032** — Contexte LLM borné, sélection sémantique du code et suppression des doublons de tokens, sans perte de prérequis ni d'identité de tâche.
- **HOPT-033** — Benchmark de modèles local indépendant : qualité, tool calling, sécurité, latence, contexte utilisable, RAM/VRAM et critères comparables exact-SHA ; llmfit seulement consultatif.
- **HOPT-034** — Streaming facultatif et interruptible pour interface et génération, sans appliquer de JSON/patch incomplet et sans déranger le propriétaire actuel de la persistance.
- **HOPT-035** — Profilage des traitements et des requêtes SQLite, index planifiés selon EXPLAIN, contention, integrity_check à l'ouverture, récupération ciblée des missions et audit de N+1 dashboard.
- **HOPT-036** — Backpressure par modèle, mission, worker, MCP et réseau : limites de files, nombre de jobs, réessais exponentiels avec jitter et budgets, déduplication/idempotence, annulation sûre.
- **HOPT-037** — RAM arbre complet Holding et CPU/IO/VRAM mesurés : repos <=250 Mio, normal 250–350 Mio, plafond <=400 Mio hors inférence IA ; audit lourd et Ollama séparés.
- **HOPT-038** — Pilotage stable multi-heure avec freeze UI, arrêt/reprise, fuite de workers/timers/signaux, absence de doublons Git et respect strict Pause/STOP.
- **HOPT-039** — Observabilité locale sensible : corrélation mission→agent→modèle→outil→Doctor→GitHub, erreurs, tokens, temps avant résultat, p50/p95/p99, coût et overhead ; aucun secret ni export automatique.
- **HOPT-040** — Règles permanentes applicables aux nouvelles PR et nouveaux modules dans les propriétaires canoniques AI_ENTRYPOINT/AGENTS, architecture_guard et QA existants, avec fixtures de régression conditionnelles et sans nouvelle CI lourde par commit.
- **HOPT-041** — Tests natifs Windows offline/instable/disque lent, installation/reprise, Ollama réel et 1/plusieurs projets ; différencier tests unitaires, CI, natif et release, preuves sur SHA exact.
- **HOPT-042** — Évaluation ciblée Ponytail, Addy Osmani agent-skills, OmniRoute, Perplexity, PostHog, Playwright, agent harness et Harness.io, avec statut réellement installé/actif/planifié et décision de réutilisation ou rejet.
- **HOPT-043** — Architecture fournisseurs LLM extensible local-first : utiliser Ollama maintenant ; prévoir providers cloud seulement comme interface et capacités, sans frais, secrets ou API activée implicitement ; comparer routage existant avant OmniRoute.
- **HOPT-044** — Réemploi caches locaux MCP/documentaires, cache de sources/version et mesures cold/warm, invalidation atomique, SHA et provenance ; ne jamais annoncer un résultat périmé comme frais.
- **HOPT-045** — Protection des données et droits : aucune télémétrie PostHog sans opt-in, aucun paiement Perplexity/LLM sans consentement, moindre privilège MCP, isolation exécuteur, pas de nouveau moteur équivalent.
- **HOPT-046** — Compatibilité des chantiers existants : PR #9 setup/release, #60 OSS, #61 llmfit ; ne pas modifier leur périmètre sans coordination des owners et audit des diffs.
- **HOPT-047** — Preuves de fin par lot : baseline + A/B comparable, tests ciblés, revue de chaque commit + audit global, architecture_guard/ai_policy_check selon risque, CI Holding exacte et vrais essais Windows séparés.

## Outils externes et Harness

| Outil | État dans Holding observé | Décision V3 |
| --- | --- | --- |

| **Ponytail** | Non démontré comme plugin installé ; équivalent interne `tools/architecture_guard.py` | Adapter principes anti-complexité sans nouveau propriétaire ; benchmark de faux positifs |
| **Addy Osmani agent-skills (24 + méta-skill)** | Trois skills locaux spécifiques déjà présents ; pas de pack installé prouvé | Reprendre uniquement pratiques utiles de performance, contexte, code review, sources, debugging |
| **OmniRoute** | Absent du registre canonique vérifié | Évaluer pour interface providers future ; aucun service cloud payant, ni gateway systématique maintenant |
| **Perplexity** | Non intégré | Éventuelle capacité de recherche Web publique, facultative, consentement réseau/coût/provenance ; offline sinon |
| **PostHog** | Non intégré | Pas de télémétrie silencieuse ; traces locales d'abord, export/opt-in plus tard uniquement si validé |
| **Playwright** | Déclaré comme option browser.e2e dans `docs/TOOLCHAIN.md`, pas de preuve d'exécution | Utilisable pour pages web, ne remplace pas pytest-qt/pywinauto ou UI Qt native |
| **Agent harness** | Existant partiel : `holding/engine.py`, `service.py`, `state_store.py`, `process_agent.py`, QA | Améliorer reprise, délégation, limites et preuves existantes ; pas d'orchestrateur parallèle |
| **Harness.io** | Non intégré | Pas de seconde CI/CD tant que GitHub Actions couvre le besoin ; ne pas confondre avec agent harness |
| **llmfit** | PR #61 Draft SPEC_ONLY | Conseiller local optionnel, propriétaire du choix final `model_selection.py` et mesures réelles |
| **Open source 21 projets** | PR #60 Draft, catalogue documentaire | Réutiliser étude de #60, comparer coût/efficacité, ne pas installer 21 outils par défaut |
| **Installateur Windows** | PR #9 Draft | Tests/mesures et absence de régression V3, propriétaire du setup reste #9 |


Ces projets ne sont **pas** implicitement autorisés à installer, modifier une machine, consommer des tokens payants, collecter des données ou contacter Internet. Les capacités réseau optionnelles et fournisseurs de modèles doivent rester désactivés par défaut. Ollama est prioritaire, avec abstraction minimale des fournisseurs cloud pour une éventuelle activation future **sans refonte, mais pas d'API payante maintenant**.

## Budgets et preuves

**Holding hors processus d'inférence IA** : repos ≤250 Mio, fonctionnement normal 250–350 Mio, pic ordinaire ≤400 Mio selon `docs/QUALITY_RELEASE_GATES.md`. Les campagnes Doctor/Graphify lourdes et l'inférence Ollama sont mesurées séparément, sans dissimuler leurs processus. Mesurer RAM RSS/process-tree, CPU, IO, VRAM réellement observée, temps au premier résultat, latence complète, p50/p95/p99, prompt/response tokens, consommation et réussite sans régression.

**Scénarios déterminants :** 1 / 100 / 1 000 missions selon capacité réelle ; migrations, corruption et contention SQLite ; 10/20/50 cycles UI, une longue session, surcharge du modèle, arrêt au milieu d'un appel, crash/restart, offline/reconnexion, quota épuisé, 1 seul writer, tests sûrs de retry et de checkpoint. Utiliser exactement le même modèle, digest, corpus, `num_ctx`, source et conditions matérielles avant/après. Les modèles seuls ne peuvent pas se donner un PASS.

## Garde-fous obligatoires sur tout futur code

- **Nouvelle IA/modèle** : évaluer qualité et tool calling avant adoption, preuve SHA/version/context, num_ctx et RAM/VRAM bornés, pas deux modèles lourds.
- **Nouvelle requête SQLite** : chemin chaud profilé, index EXPLAIN, colonnes strictement utiles, transactions courtes, migration/idempotence.
- **Nouvelle vue Qt** : thread UI libre, limitation des tâches, pas de listes reconstruites sans nécessité, fermeture et invalidation ciblées.
- **Nouveau cache** : limite octets/objets, source SHA+version, TTL/fraîcheur et invalidation atomique, secret interdit.
- **Nouveau MCP** : priorité local vérifié, permission minimale, quotas/timeouts/réponse bornés, provenance, tests d'injection, offline sain.
- **Nouvelle mission/workflow** : backpressure, arrêt/reprise sûre, pas de mutation parallèle Git, pas de preuve auto-attribuée.
- **Nouveau fournisseur externe** : interface locale extensible, aucun coût ni telemetry ni secret activé par simple présence.
- Tester par **fixtures de régression injectées** que le contrôle remonte le vrai défaut et ne bloque pas des règles `NOT_APPLICABLE`. Pas de second moteur ni de FULL sur chaque commit.

## Une PR, lots de réalisation

**A — baseline / ownership** : audit à SHA exact, métriques et comparateurs, cartographie 31 règles/16 compléments, lecture des PR actives.  
**B — LLM / contexte / MCP** : suppression doublons vérifiés, tokens et prompt context, outils, gestion charge, streaming facultatif si utile.  
**C — SQLite / runtime / Qt** : requêtes et index après profilage, cycle workers, lifecycle UI et pression mémoire.  
**D — Observabilité et qualification** : traçabilité locale per-mission/agent/tool/model, benchmark vrai matériel sans exports secrets.  
**E — Avenir** : contrats canoniques, tests de garde conditionnels, règles performance anti-régression utiles à **toutes futures PR**.  
**F — fin** : tests ciblés pendant Draft, diff + revue logique commit par commit puis globale, `Holding Certification / Required` exact SHA une fois Ready ; essai natif Windows distinct.

## Frontières de PR

- **#9 Release/Windows** : ne pas coder la distribution en doublon.
- **#60 OSS** : 21 projets recensés uniquement ; intégrer les bons candidats dans leurs owners à l'issue d'une vérification.
- **#61 llmfit** : advisor optionnel SPEC_ONLY ; sélection finale = `holding/model_selection.py`, benchmark réel et consentement utilisateur.
- **Atlas #367** : optimiser Atlas dans son dépôt, pas le déplacer dans Holding ; réutiliser son protocole Doctor sans le dupliquer.

## Suivi

Chaque HOPT-001…047 doit passer à `VERIFIED`, `IMPLEMENTED`, `BLOCKED` ou `NOT_APPLICABLE` avec propriétaire, test, preuve, SHA, éventuel A/B, et statut Windows. Aucune ligne fermée avec seulement une promesse. **Aucun code/runtime/benchmark n'a été modifié ou exécuté lors de l'écriture de ce dossier.**
