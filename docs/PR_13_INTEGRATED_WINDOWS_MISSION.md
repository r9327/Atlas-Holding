# PR #13 - Preuve Windows de mission locale

Candidat **nonrelease** issu du bootstrap #2 et du moteur/agent #11.
Aucun test ici ne vaut autorisation de pousser ou de modifier le vrai
depot r9327/Dof-Atlas.

## CI sur SHA exact

Le workflow windows_integrated_validation.yml valide :

1. Suite complete Python 3.13 sous Ubuntu et Windows, et analyse AST.
2. Construction des trois vrais EXE et test de leurs protocoles natifs.
3. Installation de ces EXE avec ACL Windows natives, choix de disque et
   configuration moteur schema v3, refus de faux clone Git et d'un clone sale.
4. Un vrai AtlasHoldingAgent.exe lance par LocalProcessAgent, qui appelle
   LocalCodeWorker et produit un commit Git avec bail, grants et SQLite.
   Dans la CI, la reponse de generation de code vient d'un **faux Ollama
   local deterministe**. La verification independante lit un AST Python :
   fonction answer sans parametre, retourne exactement entier 42, aucun
   autre fichier modifie. Main reste inchange et la mission reste RUNNING,
   sans simuler un PASS Doctor ni un push GitHub.
5. Emballage du setup avec SHA des trois EXE et execution du self-test.

Ce test prouve une chaine technique agent natif -> worker -> commit autorise ;
il ne prouve **pas** qu'un vrai LLM code correctement.

## Essai optionnel avec modele Ollama reel sur Windows

Le script tests/native_windows_code_mission_smoke.py accepte deux modes.
Par defaut, le modele est simule pour CI. Avec --model-json il utilise un
modele deja installe et epingle par digest selon /api/tags de Ollama local.

Exemple de JSON en chemin absolu : 
    {"schema":1,"model":"nom-exact","digest":"64-hex-du-tags","num_ctx":8192,"timeout":300}

Commande PowerShell (sur machine equipee, a partir du checkout Holding) :
    python -B -X utf8 tests/native_windows_code_mission_smoke.py --agent "C:\chemin\AtlasHoldingAgent.exe" --model-json "C:\chemin\model.json"

Le vrai modele travaille alors **exclusivement dans un nouveau depot Git
temporaire**. Aucune PR n'est ouverte, pas de push, aucune commande de tests
dans du code genere : uniquement controle AST non executable. Un modele qui
ne satisfait pas exactement le contrat rend un echec explicite.

## Verrous de release

Modele reel et qualite benchmarkee, tests UI Windows visibles, gestion de
redemarrage et upgrade, pression RAM/VRAM Ollama mesuree independamment,
Doctor et CI preuves exact-SHA, GitHub broker authentifie et PR brouillon.
Ne jamais confondre un commit local avec une mission autonome certifiee.

## Static QA is deliberately strict (non-executing)

The isolated coding fixture permits only the exact Python AST of
`def answer(): return 42`, allowing formatting and comments only.
Decorators, annotations, default expressions, all argument kinds,
extra statements and imports are rejected without ever executing the
model-generated file. This is a **narrow acceptance fixture**, not a
general-purpose replacement for Doctor or real project tests.

## Local evidence receipt, separate from release certification

Both the default deterministic stub and the optional real Ollama model
may supply `--receipt-json` pointing to an **absolute new JSON file**.
Example for a real-model test:

```powershell
python -B -X utf8 tests/native_windows_code_mission_smoke.py --agent "C:\\chemin\\AtlasHoldingAgent.exe" --model-json "C:\\chemin\\model.json" --receipt-json "C:\\chemin\\nouvelle-preuve.json"
```

The receipt records agent/model SHA-256, isolated Git base and candidate
commit SHAs, elapsed time and independently checked AST. It clearly marks
`source: LIVE_PINNED_OLLAMA` or `DETERMINISTIC_CI_STUB`, and **always**
states `release_ready: false`, `doctor_status: NOT_EXECUTED` and
`github_push_status: NOT_PERMITTED`.

The frozen native agent's Windows Job peak is recorded **only when actually
measured**. The separately running Ollama server RAM/VRAM remain `null`
(unmeasured), not a misleading zero. Evidence is local, unsigned and
nonrelease; no existing receipt can be silently overwritten. It contains
no generated code or credentials. The CI checks this schema for the stub
rather than mislabeling it as real model proof.


## 22 octobre — observation facultative du vrai modèle local

En mode \`--model-json\` uniquement, le banc de mission enregistre les relevés
\`GET http://127.0.0.1:11434/api/ps\` du **modèle demandé par son nom exact**
au cours de la génération. L'interrogation est en lecture seule, sans proxy,
avec timeout, plafond de taille de réponse, intervalle borné et thread arrêté
à la fin. Le rapport nonrelease comprend :

- \`ollama_allocation_observations\` : nombre de relevés où ce modèle était chargé ;
- \`ollama_allocation_failed_polls\` : erreurs ou timeouts de relevé ;
- \`ollama_loaded_model_bytes_observed_high\` : maximum des tailles chargées observées ;
- \`ollama_loaded_model_vram_bytes_observed_high\` : maximum des tailles GPU déclarées observées.

**Ces champs ne mesurent ni le maximum de RSS du serveur Ollama ni un pic
réel de mémoire GPU.** Les champs \`ollama_ram_peak_bytes\` et
\`ollama_vram_peak_bytes\` restent donc \`null\`, et zéro relevé veut dire
valeur inconnue, jamais zéro consommation. Ces valeurs ne sont pas
attestées contre un serveur local éventuellement contrefait : le digest de
\`/api/tags\` et \`/api/ps\` provient du même service. Une expérimentation
qualité réelle doit encore valider plusieurs tâches, les échecs, les ressources
par processus et la promotion par le Doctor/CI du vrai dépôt Atlas.


## 22 octobre — contrôle durable de la transition QA

Après le commit Git local, le simulateur Windows enregistre maintenant un
`StateStore.checkpoint(...)` sous bail et constate `QA_PENDING` sur le
SHA exact. Il tente ensuite volontairement d'injecter un verdict `PASS`
textuel non authentifié ; ce verdict est refusé, et la mission reste
`QA_PENDING`. Le reçu indique `mission_state:
QA_PENDING_AWAITING_PRIVILEGED_DOCTOR` et `doctor_status: NOT_EXECUTED`.

**Il n'y a pas encore de Doctor réel sur cette mission factice.** Le véritable
Doctor Atlas, sa QA exacte et la certification FULL du dépôt source devront
être réalisés sur un candidat Atlas isolé, avec une identité Windows
restreinte et des preuves officielles. Ni le commit, ni le contrôle AST, ni
la valeur `QA_PENDING` ne suffisent pour certifier ou fusionner le code.


## Validation du digest charge et grant Doctor local

Les releves facultatifs `/api/ps` ne comptent un modele comme charge que si
son nom **et son digest exact** correspondent au modele choisi dans le JSON
d'installation. Si le nom est identique mais l'empreinte differe ou manque,
la lecture echoue, le compteur d'echecs augmente et les valeurs peuvent
rester inconnues. Aucun releve d'un autre modele ne doit se voir attribuer
une mesure RAM/VRAM. Ce controle ne prouve toujours pas un pic GPU systeme.

Le test Windows de mission ajoute ensuite une tentative de lancer le Doctor
scoped **sans** grant `local_test` (seuls `code` et `commit` sont permis).
Le superviseur la refuse explicitement : la mission demeure `QA_PENDING` et
aucun faux verdict `PASS` n'est emis. C'est un test du refus de permission,
non une certification du vrai Doctor.


## Diagnostic du modèle depuis le vrai EXE Windows

Le moteur compilé `AtlasHoldingEngine.exe` expose désormais un diagnostic
supplémentaire **sans interpréteur Python utilisateur** :

```powershell
& "C:\\chemin\\AtlasHoldingEngine.exe" --check-model-health --config "C:\\chemin\\installation\\config\\engine.json"
```

Il valide d'abord l'empreinte des vrais binaires, les fichiers d'arguments
épinglés et le clone Git canonique propre, puis démarre **seulement** le vrai
`AtlasHoldingAgent.exe` en action `health` pour vérifier le modèle exact
dans `/api/tags`. Aucun début de mission, changement de dépôt, démarrage
du Control Center, ouverture de base SQLite, push GitHub ni inférence
`/api/chat` n'est permis. Le résultat signale explicitement
`ollama_inference: NOT_TESTED`, `doctor_qa: NOT_EXECUTED` et
`github_push: NOT_PERMITTED`. Une erreur ou un digest changé renvoie
`local_model_health: REJECTED` (code retour 2).

Le smoke Windows utilise **les trois vrais EXE installés** et simule uniquement
l'API Ollama `/api/tags`, avec une contre-épreuve modifiant son digest.
Cette preuve certifie le câblage natif de diagnostic et le refus sur dérive
d'empreinte ; elle ne prétend pas avoir fait tourner un véritable LLM.

## 22 octobre — Kit Windows de vraie inférence, sans Python

La CI Windows construit un **quatrième EXE**, strictement nonrelease :
`AtlasHoldingAcceptance.exe`. Il contient le harnais de mission test dans
l'exécutable PyInstaller : aucun interpréteur Python utilisateur n'est requis.

Le pipeline doit lancer son `--self-test`, puis mener le flux natif :
`AtlasHoldingAcceptance.exe` → `AtlasHoldingAgent.exe` → faux service Ollama
local déterministe → permission `code`/`commit` → worktree Git jetable →
commit réel → contrôle AST exact → `QA_PENDING` (sans verdict Doctor).

**Seulement après** tous les tests Windows et l'autotest du setup, GitHub
Actions publie pendant 3 jours un artefact explicitement appelé
`NONRELEASE-Windows-Ollama-Acceptance-<run-id>`, contenant :
`AtlasHoldingAcceptance.exe`, `AtlasHoldingAgent.exe`,
`Run-HoldingLiveAcceptance.ps1`, `nonrelease-manifest.json`, `README.md`.

Sur le PC de l'opérateur, le lanceur PowerShell :
- vérifie les SHA-256 des deux EXE et le SHA exact du candidat ;
- détecte le **modèle explicitement sélectionné** via `127.0.0.1`, sans
  proxy, et épingle son digest sans téléchargement ;
- crée un fichier de modèle temporaire en UTF-8 et un nom de reçu unique ;
- exécute le modèle **réel** dans un Git **temporaire seulement**, puis vérifie
  la source, le commit et le reçu `LIVE_PINNED_OLLAMA` ;
- ne demande aucune permission GitHub, aucun mot de passe ni connexion cloud ;
- ne touche pas au vrai dépôt Atlas. Le résultat est un essai de compétence
  ponctuel, pas un PASS Doctor, une release ou un benchmark.

La CI ne peut pas certifier la qualité d'un modèle qui n'est **pas installé
sur le runner**. Cette distribution est un banc d'essai facultatif pour
collecter les premières preuves réelles. La release signée et le vrai setup
ne sont PAS diffusés : l'installateur de CI reste construit avec un manifeste
de simulation et n'est pas destiné à l'utilisateur.

### Validation de la distribution PowerShell elle-même

Le test natif supplémentaire `tests/native_windows_live_kit_smoke.py`
exécute le **véritable script PowerShell livré dans l'archive**, après sa
construction et en utilisant `AtlasHoldingAcceptance.exe` et
`AtlasHoldingAgent.exe` gelés. Un service HTTP factice est lié exclusivement
à `127.0.0.1:11434`. Le scénario atteste :

- la lecture du manifeste et des empreintes SHA des deux EXE ;
- la sélection exacte du nom du modèle et l'épinglage de son digest ;
- la création du JSON temporaire, l'appel natif, le commit Git jetable et
  le reçu `QA_PENDING` nonrelease ;
- la suppression du JSON de modèle temporaire en sortie ;
- la modification volontaire d'un SHA dans le manifeste provoquant un refus
  **avant** toute nouvelle requête Ollama et tout nouveau dossier de preuve ;
- la restauration du manifeste initial avant publication.

**Cette épreuve reste SIMULÉE** ; son PASS ne dit pas que le LLM a réellement
fonctionné sur le matériel de l'utilisateur. Elle complète le test séparé
facultatif à lancer sur Windows avec un vrai modèle installé.

Première distribution testée sur `424e00b762c728d1d5b082d7f39bcf20f9fbbf83` :
CI Windows+Ubuntu PASS (#39730074863), artefact GitHub Actions
`NONRELEASE-Windows-Ollama-Acceptance-39730074863` (environ 19,9 Mo,
expiration le 25/10/2026). Les corrections postérieures doivent être
recertifiées sur leur **propre SHA**.


## 22 octobre — preuves négatives et intégrité du kit complet

Le kit nonrelease épingle désormais dans son manifeste **les quatre fichiers**
utilisateur (2 EXE, script PowerShell, README). Le script refuse une empreinte
altérée avant la création de preuves et avant toute requête réseau vers
l'API Ollama locale. Le smoke Windows exécute réellement le script pour
chacun des quatre refus, et vérifie le nombre de requêtes au serveur factice.
La CI exerce aussi le cas HTTP 503 lors de génération, le remplacement du
digest après sélection du modèle, le nettoyage des pins et l'absence de
reçu sur échec. La publication affiche enfin le digest SHA-256 de l'archive
GitHub Actions pour une comparaison indépendante avant exécution.

**Ce sont des contrôles de distribution nonrelease et de comportements sur
API simulée** : ils ne prouvent ni la qualité du vrai modèle ni une signature
Authenticode.


### 22 octobre — répétition de l'essai Ollama sans privilèges

Le lanceur Windows nonrelease accepte `-Trials 1..5`. Chaque essai
utilise un nouveau dépôt Git jetable et un reçu individuel épinglé au
digest Ollama exact. La session récapitulative n'est publiée qu'après la
réussite de **tous** les essais, avec les empreintes et durées de chacun ;
aucune donnée ne peut conclure à une comparaison multi-modèles, Doctor ou
release. Le test Windows exécutant le vrai script et les vrais EXE vérifie
**3/3 sur serveur Ollama simulé**, les SHA des trois reçus et la
contre-épreuve **1 essai réussi puis HTTP 503 au deuxième** : aucun bilan
PASS dans ce dernier cas. Essai avec un véritable LLM Windows encore requis.


### 22 octobre — double interpréteur Windows pour le kit

La CI valide `Run-HoldingLiveAcceptance.ps1` avec `pwsh` (PowerShell 7)
**et** avec `powershell.exe` (version 5.x native à Windows). Chaque shell
exerce le même kit des deux EXE compilés, 3 essais Git/AST, altérations
d'empreinte, HTTP 503, digest changé et 1 réussite suivie d'un échec.
Le kit reste nonrelease : le vrai modèle et les performances GPU du PC
utilisateur ne sont pas simulables par cette validation.


### Contre-épreuves QA : modèle remplacé et décorateur malveillant

Le nouveau smoke Windows de l'archive fournie exercera la chaîne réelle
PowerShell → EXE d'acceptation → agent EXE sur API Ollama **simulée**, sur
PowerShell Windows 5.1 et PowerShell 7. Deux scénarios supplémentaires :

- Modèle portant le bon digest au premier essai, remplacé ensuite : le second
essai est refusé **avant le prochain /api/chat**, le premier reçu individuel
reste consultable mais aucun résumé de session `PASS` n'est créé.
- Réponse d'agent qui propose `def answer(): return 42` avec un décorateur
malveillant effectuant un effet de bord **à l'import** : l'analyseur indépendant
rejette l'AST complet sans importer ni exécuter le code. Un fichier témoin
hors du worktree doit rester absent, et aucun reçu/session `PASS` n'est admis.

Ces tests ne font tourner que des modèles **fictifs** en CI. Un résultat
réel avec Ollama installé, l'isolation de l'IA et Doctor Atlas restent à
certifier sur un environnement Windows utilisateur. La PR reste en brouillon.
