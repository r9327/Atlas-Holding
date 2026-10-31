# Atlas Holding — sélection et intégration de projets open source GitHub
_Date de recherche : 31 octobre 2026 — cadrage documentaire, **aucune intégration ni installation réalisée**._

## Objectif et périmètre

Transformer la recherche GitHub en **backlog exhaustif, priorisé et vérifiable** pour augmenter la qualité effective des agents, leur compréhension du code, l'usage des outils, la capacité de diagnostic/correction, les tests autonomes, l'observabilité, la sécurité, la validation Windows et la R&D.

**21 projets candidats identifiés et tracés**, plus les composants déjà détenus/prévus (Doctor, Graphify, AI Context, Semgrep, pip-audit, Ollama/OpenCode, Context7 et Firecrawl). Les outils servent le **moteur Holding existant** : pas de deuxième orchestrateur, de deuxième catalogue de permissions, de deuxième Doctor ni de mémoire parallèle. L'évaluation n'est pas une installation, un benchmark ni une décision de fusion.

Autorités à conserver : [AGENTS.md](../../AGENTS.md), [AI_ENTRYPOINT.md](../../AI_ENTRYPOINT.md), [ZERO_TRUST_RULES.md](../../ZERO_TRUST_RULES.md), [Autonomie V2 et F01–F18](HOLDING_AUTONOMY_V2_2026-10-28.md), [audit du 28/10](../AUDIT_FOLLOWUP_2026-10-28.md), [toolchain](../TOOLCHAIN.md), [MCP local](../LOCAL_MCP.md), [preuves d'efficacité IA](../QUALITY_EFFECTIVENESS_ACCEPTANCE.md). La PR release [#9](https://github.com/r9327/Atlas-Holding/pull/9) reste indépendante, en Draft et sans preuve native supplémentaire attribuée à ce document.

## Critères de décision — applicables à chaque candidat

1. Identifier **la responsabilité canonique** et les consommateurs existants ; lire code et tests réels au SHA cible ; déterminer s'il manque une implémentation, seulement un câblage, ou rien.
2. Vérifier le dépôt GitHub officiel, dernier tag/version, activité réelle, licence **par composant et par version**, dépendances transitives, publication/empreintes et compatibilité Windows/Python avant tout ajout. La visibilité de GitHub ne garantit pas qu'une licence est adaptée au binaire distribué. Ne pas substituer des étoiles GitHub à une évaluation.
3. Comparer sur une **suite Atlas versionnée**, sur worktree jetable, à une baseline **sans outil**, avec mêmes SHAs, modèles, prompts, tâches, ressources et critères cachés de validation.
4. Mesurer : tâches correctement résolues, anomalies détectées, faux diagnostics, erreurs de tool calls, temps par mission, temps d'analyse, tokens/contexte, RAM/VRAM de l'arbre de processus, temps au repos, coût réel, reprises après panne et faux PASS (éliminatoires).
5. Ne retenir un outil que s'il couvre un manque concret ou démontre un gain net sans régression. Sinon : **DEFER/REJECT avec justification**, sans ajouter de dépendance.
6. Les outils restent désactivés/non installés par défaut tant que compatibilité, permissions, provenance et contrôles ne sont pas prouvés. Un avis d'outil ne remplace jamais Doctor ou la preuve exacte au SHA.
7. Exécution à la demande par **capability** via `holding/tool_registry.py`, `holding/mcp_policy.py`, `holding/mcp_*` et l'exécuteur canonique ; READ seul quand suffisant. Pas de lancement permanent, de téléchargement automatique arbitraire ni d'appel payant (budget payé 0 € par défaut).
8. Conserver la cible de la PR release : **400 Mio maximum hors inférence IA pour l'arbre de processus** lors des missions concernées, avec mesures natives séparées et rejet si régression significative. Le chiffre est une cible de validation, pas un résultat obtenu.

État commun au 31/10 : **PROPOSÉ / NON INTÉGRÉ PAR CETTE PR / TESTS NON EXÉCUTÉS**. La présence de certains adaptateurs dans le code ne vaut pas utilisation opérationnelle qualifiée.

## Catalogue complet — 21 projets, sans omission

### P0 — comprendre, agir et vérifier (7)

| ID | Projet source | Gains envisagés et périmètre strict | Propriétaire canonique à réutiliser | Sortie vérifiable |
|---|---|---|---|---|
| OSS-01 | [Serena](https://github.com/oraios/serena) | Recherche symbolique, références, déclaration, aperçu de code ; **finaliser l'adaptateur déjà présent**, d'abord lecture seule | `holding/code_intelligence.py`, `holding/code_worker.py`, hub MCP | Appels réellement observés sur gros fichiers et plusieurs consommateurs ; comparaison temps/tokens/précision et tests d'absence de droits d'édition |
| OSS-02 | [Pydantic AI / Evals](https://github.com/pydantic/pydantic-ai) | Retenir uniquement les **Evals** utiles aux modèles/agents, pas remplacer l'orchestrateur | `holding/model_selection.py`, `holding/learning.py`, `holding/proofs.py`, F06 | Corpus Atlas versionné, tests cachés, traces, différence mesurée codeur/auditeur ; aucune auto-notation ni faux PASS |
| OSS-03 | [MCP Python SDK officiel](https://github.com/modelcontextprotocol/python-sdk) | Câbler discovery, client, transports et schémas au **hub existant** | `holding/mcp_*`, `holding/tool_registry.py`, `holding/mcp_policy.py` | Appels réels et refus permission/timeout/injection ; arrêt propre et statut offline/online fidèle |
| OSS-04 | [Hypothesis](https://github.com/HypothesisWorks/hypothesis) | Tests génératifs de séquences : STOP, restart, reprise, états, permissions, échec outil, SQLite | `tests/`, `holding/recovery.py`, `holding/state_store.py`, F13/F17 | Tests de propriétés reproductibles, graine enregistrée, contre-exemples persistés ; zéro double mutation et corruption silencieuse |
| OSS-05 | [ast-grep](https://github.com/ast-grep/ast-grep) | Recherche de motifs AST, changements structurels limités ; **complément**, pas remplaçant de Graphify/Serena/Semgrep | `holding/code_intelligence.py`, F10 | Détection de variantes de bugs avec précision mesurée ; pas de rewrite implicite ni faux positif bloquant non justifié |
| OSS-06 | [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | Instrumenter **les événements existants**, corrélation mission→agent→tool→Doctor→Git/PR, coûts/temps | `holding/learning.py`, `holding/proofs.py`, `holding/control_center_projection.py`, F03/F07 | Même correlation ID, SHA et run IDs à travers une panne/reprise ; export borné, sans secrets ; mesure de surcharge/RAM |
| OSS-07 | [MCP Inspector](https://github.com/modelcontextprotocol/inspector) | Test d'interopérabilité des outils/serveurs MCP sur environnement jetable ; ne pas installer comme service permanent | `tests/`, `holding/mcp_*`, F06 | Schémas/handshakes/erreurs d'un serveur légitime et malformé vérifiés, sous permissions strictes |

### P1 — qualité du delivery, sécurité, performance et R&D (11)

| ID | Projet source | Gains envisagés | Propriétaire canonique / condition de sélection | Sortie vérifiable |
|---|---|---|---|---|
| OSS-08 | [zizmor](https://github.com/zizmorcore/zizmor) | Scanner les risques dans GitHub Actions | Workflows `.github/workflows/` et QA ; résultats spécifiques, remédiation avant adoption de règle | Identifier et corriger une fixture workflow vulnérable sans changer les permissions en douce |
| OSS-09 | [pywinauto](https://github.com/pywinauto/pywinauto) | Essais d'installation, setup, contrôles de fenêtre et reprise **sur vrai Windows** | `tests/native_*`, `holding/windows_*`, gates release #9 | Installation/annulation/reboot/rollback sur installation isolée, traces et captures natives |
| OSS-10 | [pytest-qt](https://github.com/pytest-dev/pytest-qt) | Événements, signaux, blocages UI PySide6 | Tests UI existants et `holding/control_center_ui.py` / `holding/project_chat_ui.py` | Interaction pause/reprendre/saisie et thread Qt non bloqué ; plus-value par rapport aux tests existants |
| OSS-11 | [MarkItDown](https://github.com/microsoft/markitdown) | Conversion PDF/Office en texte exploitable pour Documentaliste R&D | `holding/document_*`, F01 et R&D ; plugins/formats optionnels | Extraction de corpus connu DOCX/XLSX/PPTX/PDF avec limites, provenance, tailles et comparaison au parseur existant |
| OSS-12 | [OSV-Scanner](https://github.com/google/osv-scanner) | Audit des dépendances et vulnérabilités | QA/dépendances ; comparer avec pip-audit retenu auparavant pour éviter deux scanners redondants | Détecter une dépendance de fixture connue ; distinguer résultat cache hors ligne/données actuelles |
| OSS-13 | [Ruff](https://github.com/astral-sh/ruff) | Analyse et formatage Python rapides | QA et tests existants ; ne jamais modifier massivement le style ni réécrire des fichiers sans besoin | Baseline warnings, comparaison coûts et non-régression ; diff limité |
| OSS-14 | [Gitleaks](https://github.com/gitleaks/gitleaks) | Détecter l'ajout de secrets et tokens | `holding/github_mutation_gate.py`, contrôle des commits/CI | Fixture secret bloquée et vraie source saine non bloquée ; logs eux-mêmes expurgés |
| OSS-15 | [py-spy](https://github.com/benfred/py-spy) | Profilage échantillonné des processus Python lents | `holding/measurement_cli.py`, Doctor et mesures Windows | Identifier un goulot réel avec surcharge de profilage chiffrée ; permission d'attachement explicite |
| OSS-16 | [uv](https://github.com/astral-sh/uv) | Installation/cache des dépendances de développement, reproductibilité | `holding/provision_*`, `holding/managed_toolchain.py` ; **pas de remplacement aveugle** de setup Windows | Mesure avant/après installation propre et incrémentale, pins/hashes et rollback |
| OSS-17 | [Pint](https://github.com/hgrecco/pint) | Contrôle des unités, conversions et dimensions pour calculs R&D | `holding/research_simulation.py`, `holding/research_measurement.py` | Rejeter dimensions incohérentes, cas de référence vérifiables, séparation hypothèses/mesures |
| OSS-18 | [sqlite-vec](https://github.com/asg017/sqlite-vec) | Recherche sémantique locale dans retours validés et corpus | `holding/learning.py`, `holding/document_cache.py`, F07/F18 ; **option stricte** | Démontrer un rappel supérieur à recherche texte/FTS sur corpus test et un fonctionnement sûr Windows ; sinon REJECT |

### P2 — projets de référence, **pas de remplacement du moteur** (3)

| ID | Projet source | Décision par défaut | Étude éventuellement utile | Test d'entrée |
|---|---|---|---|---|
| OSS-19 | [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | **RÉFÉRENCE SEULEMENT** | Gestion de conversations/actions et outils isolés ; réutilisation d'une idée ponctuelle si prouvée | Mesurer un avantage net sur la boucle `code_worker` sans remplacer Holding ni ses permissions |
| OSS-20 | [Aider](https://github.com/Aider-AI/aider) | **COMPARATIF OPTIONNEL** | Comparatif de codeur Ollama face à OpenCode, sur le même corpus et matériel | Adoption seulement si amélioration mesurée ; jamais deux writers concurrents |
| OSS-21 | [LangGraph](https://github.com/langchain-ai/langgraph) | **RÉFÉRENCE SEULEMENT** | Techniques de checkpoint et reprise à confronter à `holding/engine.py`, `holding/state_store.py` | Réutilisation conceptuelle si un scénario de crash non couvert est démontré ; pas de deuxième orchestrateur/SQLite |

**Composants complémentaires déjà répertoriés, sans créer des entrées concurrentes :** [Semgrep](https://github.com/semgrep/semgrep) (adaptateur de règles consultatives existant), [pip-audit](https://github.com/pypa/pip-audit), AI Context, Graphify, Doctor Atlas, Context7, Firecrawl, Ollama et OpenCode. Priorité au **câblage et à la qualification de l'existant** avant l'ajout d'un remplaçant.

## Lots de réalisation conseillés — dépendances, propriétaires, preuves

Chaque lot est une **proposition technique distincte de cette PR documentaire**. Groupements possibles en PR cohérentes après vérification des chevauchements ; ne pas ouvrir 21 PR vides.

### OS-L01 — préflight et diagnostic des capacités (prérequis)
- Liens : F02/F06/F09/F10, H03/H04/H06 ; `holding/inventory.py`, `holding/tool_registry.py`, `holding/code_intelligence.py`, `holding/mcp_*`.
- Réaliser l'inventaire sur un vrai Windows : outil présent/versionné, hash, licence, disponibilité, permissions, dépendances, coût et lieu d'installation. Cartographier les consommateurs et tâches de chaque outil. Ne pas confondre déclaration et READY.
- Baseline par mission Atlas **avant** tout outil nouveau : temps, tokens, résultats, pic RAM/VRAM et taux de réussite. Attacher SHA, environnement et corpus.
- **Sortie** : preuve du préflight et de la baseline ; absence de doublon d'owner ; garde-fous inchangés.

### OS-L02 — compréhension du code et consommation réelle d'outils
- Liens : OSS-01/05 + Semgrep existant ; F10/F13 ; H03/H04/H06. Dépend de OS-L01.
- Finaliser **Serena en lecture seule** pour symboles, références et lecteurs >12 Kio ; évaluer ast-grep sur motifs précis et garder Semgrep comme avis de scan. Coder via exécuteur autorisé, pas par ces outils.
- Tester changement de fonction publique et consommateurs multi-fichiers ; résultat attendu établi indépendamment ; sorties adversariales et indisponibilité du serveur.
- **Sortie** : traces des vraies invocations (pas mock seul), taux de détection, faux positifs, coûts de contexte et régressions.

### OS-L03 — hub MCP effectif
- Liens : OSS-03/07 ; F02/F06, H06. Dépend de OS-L01 ; peut avancer en parallèle d'OS-L02.
- Raccorder le SDK officiel au client/serveur existant, Inspector pour tests seulement ; schémas stricts, discovery par mission, timeout, fermeture de process tree, quota, offline et grants.
- Ne pas modifier `config/tool_registry.json` pour proclamer `implemented=true` sans comportement réellement exécuté et validé.
- **Sortie** : un serveur MCP testé, un faux serveur refusé, droits read-only effectifs, aucune commande arbitraire ni secret transmis.

### OS-L04 — évaluation modèle/agents, anti-boucles et qualité
- Liens : OSS-02/04 ; F04/F06/F07/F13/F17, H05 ; `model_selection`, `qa_repair`, `learning`. Dépend des baselines OS-L01 ; bénéficie de OS-L02/03.
- Pydantic Evals employé **comme test harness optionnel**, pas comme agent maître. Hypothesis pour états persistants/crash/reprises ; corpus Atlas avec tâches UI, launcher, code multi-fichiers, changements structurels, permissions et injection.
- Tester codeur seul vs codeur+auditeur, détection d'une faute injectée, red→green, stagnation et changement de stratégie ; calibrer sur qualité des livrables et vérifications indépendantes.
- **Sortie** : preuves de succès/échec reproductibles, zéro faux PASS, aucun choix de modèle basé sur une simple auto-note.

### OS-L05 — corrélation et suivi de mission
- Liens : OSS-06/15 ; F03/F07/F08, H16. Dépend de OS-L01 et du journal existant.
- Ajouter une couche d'instrumentation **des événements canoniques**, pas un second journal ou stockage de vérité : mission, task, attempt, tool, timings, cache, tokens, SHAs, Doctor, GitHub, erreurs, retries, pics de process tree.
- Tester panne/reprise, données manquantes et bornage de volume ; utiliser py-spy **uniquement à la demande** sur un ralentissement reproductible.
- **Sortie** : diagnostic de bout en bout lisible dans le Control Center avec overhead et rétention mesurés, aucun secret dans les traces.

### OS-L06 — qualité, sécurité et Windows natif
- Liens : OSS-08/09/10/12/13/14/16 ; audit H11/H15 ; PR release #9 ; peut avancer par outils indépendants après OS-L01.
- Scanner GitHub Actions avec zizmor, secrets avec Gitleaks, vulnérabilités avec **un outil principal à choisir entre pip-audit et OSV-Scanner** ; Ruff en mode advisory préalable.
- Évaluer pywinauto sur vrais flux de setup Windows, pytest-qt seulement si meilleur que les tests Qt existants ; uv optionnel pour environnement de dev, pas remplacement de l'installateur.
- **Sortie** : fixtures vulnérables/positives-négatives, Windows réel et preuves exactes ; aucune annonce `release_ready` fondée sur ces seuls outils.

### OS-L07 — R&D et connaissances locales
- Liens : OSS-11/17/18 ; F01/F07/F18 et R&D #25–#32. Dépend de OS-L01.
- Ajouter les formats MarkItDown seulement lorsqu'ils manquent au `holding/document_*` ; garantir extraction bornée, provenance, méfiance face au prompt injection, pas de téléchargement silencieux.
- Pint : opérations dimensionnelles connues et tests de référence ; ne jamais présenter une simulation hypothétique comme une mesure ou une expérience.
- sqlite-vec : n'évaluer qu'après preuve que FTS/document_cache ne suffisent plus ; index reconstruit à partir des données canoniques, invalidation SHA/source, stockage local et confidentialité.
- **Sortie** : corpus de références, tests de formats/unité, benchmark de recherche et contrôle de ressources ; rejeter sans gain mesuré.

### OS-L08 — étude comparative sans migration
- Liens : OSS-19/20/21 ; dépend des baselines OS-L01/04.
- Étudier sans installer par défaut les concepts OpenHands SDK/LangGraph ; Aider seulement en agent isolé sur worktree jetable et comparaison équitable.
- **Sortie** : tableau des gains/coûts/risques avec décision ADOPT_COMPONENT / KEEP_EXISTING / DEFER / REJECT et sources vérifiées. Aucun changement de moteur sans mandat et preuve.

### Graphe d'ordre minimal

```text
OS-L01 Baseline + préflight
  ├─ OS-L02 Serena / AST / Semgrep ─┐
  ├─ OS-L03 MCP SDK / Inspector ────┤
  ├─ OS-L05 Instrumentation ────────┤
  ├─ OS-L06 Qualité / Windows ──────┤
  └─ OS-L07 R&D ────────────────────┤
                                    ↓
                         OS-L04 Evals / Hypothesis
                                    ↓
                         OS-L08 Études comparatives
```

Les modules indépendants avancent même si un outil manque. L'ordre relatif OS-L04/OS-L05 peut être ajusté si l'instrumentation est nécessaire pour mesurer un test ; chaque lot conserve ses critères d'entrée.

## Critères d'acceptation transversaux (OS-AC01 à OS-AC14)

- [ ] OS-AC01 : tous les **21 IDs OSS-01 à OSS-21** figurent une seule fois, avec URL officielle, owner canonique, priorité, stratégie et test.
- [ ] OS-AC02 : inventaire réel des outils et de leurs statuts ; aucun outil déclaré installé/READY à partir de sa seule présence en roadmap.
- [ ] OS-AC03 : baseline Atlas versionnée, worktree isolé, résultats cachés et comparaison objective avant/après par outil.
- [ ] OS-AC04 : lecture des règles et consommateurs au SHA courant ; aucun nouveau moteur, registre, cache ou preuve parallèle.
- [ ] OS-AC05 : Serena/Semgrep/Graphify/Doctor interagissent selon leurs rôles ; aucun résultat consultatif ne remplace une certification.
- [ ] OS-AC06 : tests MCP (vrai appel, schéma faux, quota 0, timeout, offline, serveur malveillant, permissions READ, nettoyage process).
- [ ] OS-AC07 : reprise après crash/STOP et correction de boucle stérile sans doublon de commit, mutation ni checkpoint.
- [ ] OS-AC08 : injection d'une faute et validation cachée : l'auditeur/Doctor bloque un patch incorrect malgré un texte IA positif.
- [ ] OS-AC09 : dossier de preuves exact-SHA lié à chaque action ; `NOT_RUN`, `SKIPPED`, `BLOCKED` et tests d'autre SHA jamais promus en PASS.
- [ ] OS-AC10 : métriques CPU/RAM/VRAM, latence, temps, tokens, tailles des traces, taux d'échec et overhead mesurés ; respecter les budgets de la PR #9 lorsque concernés.
- [ ] OS-AC11 : scan de sécurité et chaînes d'approvisionnement, licences vérifiées par version, consentements sensibles bornés, secrets jamais dans logs ou git.
- [ ] OS-AC12 : tests PySide6 et véritable setup Windows uniquement sur environnement supporté ; aucune CI simulée présentée comme certification native.
- [ ] OS-AC13 : formats documentaires et unités scientifiques vérifiés ; calcul hypothétique et mesure physique restent distincts ; index optionnel justifié.
- [ ] OS-AC14 : décision écrite ADOPT / ADAPT_EXISTING / DEFER / REJECT pour chaque candidat avec gain net ou motif d'absence de gain, et revue du diff + consommation réelle avant fusion.

## Discipline de développement des lots futurs

- **Cette PR est une inscription documentaire** (risque LOW), **pas l'intégration des 21 projets**. Aucun binaire installé, test runtime lancé, benchmark réalisé ou release certifiée.
- Chaque lot de code : lire HEAD exact, code et tests, faire diagnostic d'overlap PR, travailler sur branche dédiée et un seul writer par worktree ; tests ciblés/Doctor local, revue commit par commit, diff intégré ; CI complète seulement sur candidat stable au dernier SHA selon [certification](../HOLDING_CERTIFICATION.md).
- Les demandes de permissions (installation, réseau, MCP écrivant, credentials, push, release) sont distinctes ; pas de grant implicite ni nouveaux secrets. La validation de sécurité est factuelle et bornée, jamais un jugement moral subjectif sur le projet.
- Ne pas modifier les critères des tests pour fabriquer un PASS. Ne pas déclarer fonctionnel un outil simplement listé.
- Ne pas intervenir sur #9 tant que ses vrais critères Windows, sécurité et distribution ne sont pas satisfaits.

## Audit croisé avec DOFUS ATLAS — réutiliser le pont déjà implémenté

- **PR associée** : [Dof-Atlas #312](https://github.com/r9327/Dof-Atlas/pull/312), catalogue et affectation de **29 candidats** aux modules Atlas ; issue documentaire [#311](https://github.com/r9327/Dof-Atlas/issues/311).
- **Constat code vérifié (ne pas recréer !)** : `holding/atlas_doctor_runner.py` existe déjà, invoque `tools.atlas_doctor agent plan/check/validate --json` dans un sous-processus borné et contrôle le SHA du checkout/worktree. `holding/atlas_doctor_evidence.py` valide `atlas-doctor-agent/v1`, les statuts, les sorties et la portée. **La commande Doctor a déjà son adaptateur direct** ; lui ajouter un passage MCP supplémentaire ne serait pas un gain.
- Le SDK MCP et Inspector restent candidats pour d'**autres** outils sans adaptateur canonique, sous `holding/mcp_policy.py` et `holding/tool_registry.py`. Ne pas déclarer `local_mcp_hub.implemented=true` sans intégration vérifiée ; à la base actuelle la validation impose `implemented=false`.
- Répartition : **Holding** conserve le contrôleur d'agents, les règles de permission, les évaluations inter-projets, la recherche documentaire et le hub MCP ; **Atlas** conserve Qt, Doctor, Graphify, les données/Guides, les profils, le benchmark RAM/CPU, l'installateur et l'UI Unity.
- Pour py-spy, pytest-qt, pywinauto, Hypothesis et les autres recouvrements, **un outil peut être partagé comme dépendance de développement**, mais **chaque dépôt garde ses tests, son owner et son verdict au SHA exact**. Aucun ajout à l'EXE Atlas depuis Holding.
- **Manque à prouver** : tests end-to-end d'AtlasDoctorRunner sur vrai Windows, timeout/kill tree, état offline, contrôle d'identité SHA, rapport erreur et import de preuves ; comparaison du coût du pont existant. Le fait que l'adaptateur soit présent ne signifie pas que son exécution native actuelle est certifiée.
- **Audit de la PR #60** : les 21 entrées sont présentes, mais les 8 lots restent **NOT_IMPLEMENTED** par cette PR documentaire. Le raccordement n'est pas 21 cases `READY` ; seules les capacités déjà codées et testées peuvent être exposées. Rechercher au moment de chaque lot les PR qui chevauchent `holding/code_worker.py`, `holding/mcp_*`, `holding/learning.py` et `holding/qa_*`.

**Règle de jonction :** échanges inter-dépôts via contrats/protocoles vérifiés (`atlas-doctor-agent/v1`), jamais import d'Atlas comme bibliothèque dans le processus Holding ni duplication des services de l'autre dépôt.

## Sources et fraîcheur

- Répertoires GitHub officiels : liens de chaque ligne ; métadonnées et activité consultées le **31/10/2026**. Ces métadonnées évoluent ; les versions, dépendances et licences doivent être **revalidées avant intégration**.
- État de Holding : `main@7813063b416ebaf945c0ededbfb8fdd448909a29`, documents ci-dessus. La roadmap Autonomie V2 a son **propre plan hashé** : ne pas éditer son texte/JSON existant sans régénérer leurs liens et leur couverture.
- Le découpage OS-L01 à OS-L08 **complète F01–F18** ; il ne les remplace pas et ne crée pas de nouveaux certificats, services ou registres.
