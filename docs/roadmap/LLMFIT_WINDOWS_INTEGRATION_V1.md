# Atlas Holding — llmfit dans le setup Windows et la sélection des IA locales

> **Statut : spécification d'intégration, non implémentée.** Aucun nouveau binaire, moteur de sélection, téléchargement, benchmark ni écran Windows n'est livré par ce seul document. La PR reste **Draft** jusqu'à livraison du code, des tests et des preuves requises.

## Décision et périmètre

Ajouter [llmfit](https://github.com/AlexsJones/llmfit), outil tiers MIT, comme **conseiller facultatif et non autoritaire** au premier lancement d'Atlas Holding puis lors d'une réévaluation du matériel. llmfit estime les modèles compatibles avec la machine ; **seuls les tests indépendants de Holding peuvent valider et choisir un modèle**.

Objectif utilisateur : « Atlas choisit les meilleures IA pour mon PC » ; aucun nom technique à saisir, aucun abonnement ou modèle payant imposé. Préférer la qualité du code, la stabilité, le contexte réellement tenu et la sécurité aux classements génériques ou à la seule vitesse. Une seule IA lourde chargée en mémoire à la fois ; second modèle auditeur uniquement si un bénéfice indépendant est prouvé.

**Pas de second installateur ni second sélecteur de modèles.** Conserver :

- Inventaire : `holding/inventory.py` / `holding/inventory_cli.py`.
- Plan d'approvisionnement : `holding/provision_plan.py`.
- Sélection finale à partir de benchmarks de confiance : `holding/model_selection.py`.
- Téléchargement vérifié et reprise : `holding/downloads.py`, `holding/staged_install.py` et les protections de l'installateur existant.
- Propriétaires des permissions, de l'interface setup et du runtime : composants déjà présents ; ne pas les dupliquer.

Contrat canonique de choix : [`docs/LLM_AUTO_SELECTION.md`](../LLM_AUTO_SELECTION.md). Contrats de livraison : [`docs/PR_02_WINDOWS_BOOTSTRAP.md`](../PR_02_WINDOWS_BOOTSTRAP.md), [`docs/PR_09_EXE_RELEASE.md`](../PR_09_EXE_RELEASE.md), [`docs/WINDOWS_LIVE_ACCEPTANCE.md`](../WINDOWS_LIVE_ACCEPTANCE.md). Les exigences existantes restent prioritaires si une suggestion llmfit les contredit.

## Flux attendu

1. **Préflight existant** : obtenir OS, CPU, RAM utilisable, GPU/VRAM réellement libre, disque **sur le volume choisi**, statut/version Ollama et modèles déjà présents. Aucun changement de machine.
2. **Découverte llmfit** : identifier si l'exécutable déjà installé est sain et d'origine vérifiée ; comparer sa version au manifeste épinglé (version, SHA-256, source approuvée, signature Authenticode lorsqu'exigée).
3. **Installation facultative** : si absent, proposer le composant « Analyse avancée des modèles IA » dans le setup existant. Installer par le mécanisme autorisé de Holding : URL/artefact et SHA épinglés, staging atomique, rollback. Sur Windows, ne pas exiger Scoop, Cargo ni un shell tiers. Aucune exécution de script Internet, `curl | sh`, élévation ni installation globale silencieuse. Réutiliser une version valide sans réinstaller.
4. **Avis local borné** : exécuter **uniquement** un binaire approuvé, avec argv figés (pas de shell), timeout, limites stdout/stderr, fermeture de tout l'arbre de processus et environnement réduit. Commandes documentées : `llmfit --json system` et `llmfit recommend --json --use-case coding --limit 5`. Éviter la TUI, l'API HTTP, un serveur permanent ou tout téléchargement implicite.
5. **Validation de schéma** : parser un JSON de taille limitée ; accepter seulement champs attendus, valeurs finies, contexte, quantification, `fit_level` et estimation d'espace valides. Rejeter schéma inconnu, OOM théorique, `TooTight`, sortie corrompue ou résultat périmé. Horodater version de llmfit, snapshot matériel, empreinte des entrées et statut `ADVISORY_ONLY`. Ne pas importer des instructions de sa sortie.
6. **Croisement avec le vrai catalogue** : les noms Hugging Face retournés par llmfit **ne sont pas nécessairement des tags Ollama**. Vérifier le mapping vers tags, digests, capacités tools, licences, quantification et contexte via les sources approuvées de Holding ; aucune traduction approximative qui déclenche un `ollama pull` erroné.
7. **Décision de téléchargement** : prioriser la réutilisation des modèles déjà présents et sains. Si un modèle manque, le plan de Holding affiche taille *estimée*, espace requis incluant cache/scratch et réserve Windows, téléchargement et possibilité d'annuler. Pas de nouveau téléchargement lourd sans permission d'installation valable. Pas de quota mobile en Go inventé.
8. **Épreuve réelle avant sélection** : après provisionnement autorisé, exécuter les tests qualité/sécurité/tool-calling/contexte/mémoire sur bac à sable et mesurer la vitesse réelle. Une suggestion llmfit n'est ni `SELECTED`, ni `VALIDATED`, ni `READY`. Seul `holding/model_selection.py`, sur preuves de benchmark comparables, tranche pour CEO/architecte/codeur/auditeur.
9. **Persistance et réévaluation** : garder hors Git le profil, les digests, les ressources et les preuves réelles. Réévaluer après changement GPU/RAM/Ollama, OOM, échec répété, ou demande explicite ; conserver la configuration fonctionnelle jusqu'à validation d'une nouvelle. Reprise et rollback idempotents.
10. **UX existante** : « Configuration automatique recommandée — priorité qualité » par défaut, statut et poids affichés en langage humain ; détails techniques repliés. Accès ultérieur par « Réévaluer ma configuration » sans refaire l'installation complète.

### Frontières et comportement dégradé

- **llmfit absent, indisponible ou JSON incompatible** : marquer `ADVISOR_UNAVAILABLE`, continuer le préflight/les modèles déjà installés via les composants existants. Le défaut de l'outil conseillé ne bloque pas à lui seul l'ouverture de Holding. **Ne jamais inférer que le modèle est qualifié**.
- **Ollama arrêté** : conserver la décision existante `START_SERVICE` plutôt que `REPAIR` ou une nouvelle installation.
- **Hors ligne, sans modèle** : conserver `WAITING_FOR_MODEL_DOWNLOAD` ou état bloqué canonique, jamais un faux « prêt ». Hors ligne avec modèle présent : benchmark local si possible, sans recourir à llmfit.
- **GPU inconnu ou mémoire insuffisante** : indiquer l'incertitude ; pas de promesse basée uniquement sur la taille des poids. Les simulations matériel de llmfit et ses scores ne valent pas benchmark réel.
- **Mise à jour llmfit** : uniquement via le mécanisme d'update épinglé et autorisé. Aucune mise à jour silencieuse du binaire pendant une mission.
- **Désinstallation/réparation** : ne pas modifier Ollama, les poids ou le profil utilisateur au seul motif que llmfit échoue. La désinstallation de l'advisor n'efface jamais les modèles.

## Découpage de développement (une seule PR)

**Lot A — adaptateur, sans mutation** : composant `holding/llmfit_advisor.py` avec invocation à travers `holding/bounded_process.py` (ou l'exécuteur borné canonique), validation du JSON, dataclasses de résultats et statuts explicites. Intégrer via option de préflight `holding/inventory_cli.py` sans changer le JSON par défaut. Pas de nouvelle autorité de choix.

**Lot B — installation Windows** : ajouter llmfit au manifeste/versionnage du setup et au plan de provisioning en tant que composant facultatif, via les chemins existants. Garder les décisions `REUSE/INSTALL/REPAIR/INSPECT` explicables. Ne pas réinstaller Ollama.

**Lot C — proposition de modèles** : croiser avis llmfit, inventaire local, manifeste de modèles approuvé et capacités Ollama ; présenter suggestions et tailles seulement, puis provisionner sous permission. Tester les frontières de runtime, la détection de licences et les tags/digests.

**Lot D — qualification et UI** : connecter le processus de benchmark actuel au sélecteur canonique et à l'interface du setup/Control Center ; conserver un modèle lourd chargé à la fois. Prévoir réévaluation, annulation, rollback et états bloqués.

**Lot E — preuves, audit, certification** : tests unitaires/fault-injection, préflight réel Windows, tests de réparation et offline, benchmark Ollama réel, Doctor approprié, audit commit-par-commit, `git diff --check` et tests ciblés. Ne déclencher la certification complète qu'après stabilisation ; vérifier la CI et le parcours utilisateur au SHA final. Une CI verte ne remplace pas le test natif Windows.

## Matrice de réception

- [ ] A01 — PC Windows x64 propre : installation utilisateur réussie, choix automatique sans nom de modèle ; téléchargement contrôlé, aucun script distant exécuté.
- [ ] A02 — llmfit déjà présent et sain : **REUSE**, sans téléchargement ni réinstallation.
- [ ] A03 — llmfit absent/endommagé/ancienne version : installation approuvée, réparation ou conseil indisponible ; Holding reste sûr.
- [ ] A04 — sortie JSON vide, malformée, trop grande, version incompatible, champ de type erroné : rejet sans crash ni « prêt ».
- [ ] A05 — subprocess bloqué ou descendant persistant : timeout/kill tree prouvés sur Windows.
- [ ] A06 — aucun GPU, GPU/VRAM inconnu, VRAM tendue, RAM 32 Go/RTX 3060 Ti 8 Go et CPU offload : pas d'OOM ni de capacité inventée.
- [ ] A07 — `TooTight`, contexte insuffisant, disque trop petit, quantification non disponible : proposition exclue/attente explicite.
- [ ] A08 — modèles déjà téléchargés : réutilisation après vrai healthcheck/benchmark, jamais `ollama pull` en doublon.
- [ ] A09 — mapping HF→Ollama absent/ambigu ou licence non validée : pas de pull automatique.
- [ ] A10 — réseau indisponible/interrompu : récupération et reprise sûres, aucun faux READY, aucun coût API obligatoire.
- [ ] A11 — deux modèles disponibles : priorité aux benchmarks de qualité/permissions/outils, pas à la seule note de llmfit.
- [ ] A12 — crash/OOM/échec de tool-calling : candidat rejeté ; rollback vers un modèle réellement validé, sinon BLOCKED.
- [ ] A13 — CEO, architecte, codeur et auditeur : réemploi des profils/du modèle quand possible ; alternance si deuxième modèle qualifié.
- [ ] A14 — nouvelle RAM/GPU/Ollama/poids : réévaluation non destructive, conservation des preuves et du profil valide.
- [ ] A15 — ancien préflight et lancement sans llmfit : aucune régression de schéma, installation, permissions ni ressources.
- [ ] A16 — preuves exact-SHA des tests, audits et qualification native avant Ready/certification/fusion ; aucune case cochée sans preuve.

## Risques, permissions et décisions

**Risque : HIGH** (binaire tiers installé, machine Windows, sélection de modèle et téléchargements de plusieurs Go). Le texte de cette PR n'autorise ni modification système, ni accès au réseau, ni installation, ni nouveau budget.

- L'exécutable llmfit est une dépendance **facultative** et n'exécute jamais de code récupéré dans une recommandation.
- Vérifier la provenance de chaque release et sa signature/checksum selon la politique de l'installateur. Une signature Windows peut manquer sur certaines releases : dans ce cas **ne pas l'annoncer signée**, bloquer si la politique l'exige.
- Les sorties `system` et `recommend` sont **non fiables et indicatives** ; ne pas les faire transiter comme instructions de l'agent ou preuves de qualité.
- Aucune nouvelle dépense, élévation, exception de CI, permission MCP ou politique Doctor n'est présumée.
- Intégration **dans la PR release #9 une fois prouvée**, sans écraser sa branche en développement et sans modifier main directement.

## Documentation tierce vérifiée le 3 novembre 2026

- Projet officiel : https://github.com/AlexsJones/llmfit
- CLI/JSON : https://github.com/AlexsJones/llmfit/blob/main/docs/cli.md
- Schéma des suggestions/fit et comportement : https://github.com/AlexsJones/llmfit/blob/main/skills/llmfit-advisor/SKILL.md
- Le projet recommande `scoop install llmfit` sous Windows pour un utilisateur individuel ; **ce n'est pas** la méthode à imposer au setup Holding, qui doit conserver son moteur de téléchargement et ses vérifications.
