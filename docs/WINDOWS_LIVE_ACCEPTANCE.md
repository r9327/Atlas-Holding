# Atlas Holding — Essai local Ollama sous Windows (NONRELEASE)

Ce kit permet d'essayer le **véritable AtlasHoldingAgent.exe** contre un
modèle Ollama déjà installé sur ton PC, **sans installer Python**. Les fichiers
sont issus de la CI du SHA GitHub exact indiqué par
`nonrelease-manifest.json`. Ce kit n'est **ni** `AtlasHoldingSetup.exe`,
**ni** une version stable, ni une certification de la roadmap entière.

## Prérequis

- Windows 10/11 et Git pour Windows installé dans le PATH ;
- Ollama lancé localement sur `127.0.0.1:11434`, avec un modèle existant ;
- le kit entier extrait dans un dossier local propre ;
- exécution sous un compte Windows standard, sans jetons GitHub, clés de
  signature, secrets ou privilèges administrateur.

Le lanceur ne télécharge aucun modèle, n'envoie rien à des services cloud et
ne modifie **pas** le vrai dépôt `r9327/Dof-Atlas`. Il crée un dépôt Git
**temporaire jetable**, autorise uniquement `code` et `commit`, demande au
modèle un petit fichier Python, effectue un commit local et vérifie sa
structure AST **sans exécuter le code produit**. Il refuse de lancer Doctor
sans permission `local_test`. Pas de push, pas de PR, pas d'accès au vrai Atlas.

## Lancer la preuve

Après avoir installé Git et Ollama, voir les modèles avec `ollama list`.
Dans une console PowerShell standard ouverte sur le dossier extrait :

```powershell
.\Run-HoldingLiveAcceptance.ps1 -Model "nom-exact-du-modele"
```

Si la politique locale empêche l'exécution des scripts, inspecter
`Run-HoldingLiveAcceptance.ps1` avant de l'exécuter selon les règles de
sécurité de Windows. Ne désactive pas la sécurité système de manière
permanente pour essayer ce prototype.

Paramètres facultatifs :

```powershell
.\Run-HoldingLiveAcceptance.ps1 -Model "nom-exact" -NumCtx 4096 -TimeoutSeconds 600 -EvidenceDirectory "D:\AtlasHoldingEssais"
```

Le script vérifie le SHA-256 exact **des deux EXE, du lanceur PowerShell et de ce README** contre le manifeste,
demande la liste `/api/tags` **uniquement via loopback, sans proxy**, fixe le
digest du modèle choisi, puis exécute la mission via le vrai EXE de test
`AtlasHoldingAcceptance.exe`. Les fichiers JSON de preuve nonrelease
sont stockés par défaut dans `%LOCALAPPDATA%\AtlasHolding\AcceptanceEvidence`.
Le JSON d'épinglage temporaire est supprimé en fin d'essai. Aucun reçu
préexistant ne peut être écrasé.

## Signification d'un PASS

`LIVE_MODEL_LOCAL_ACCEPTANCE_PASS` veut seulement dire : **ce modèle a
produit une réponse utilisable dans cette unique mission jetable**, la
structure du fichier a été vérifiée statiquement, et le commit local a été
créé. Le vrai modèle est sollicité via `/api/chat` ; contrairement à la
simulation CI, la réponse n'est pas prédéfinie.

Le reçu indique notamment `source: LIVE_PINNED_OLLAMA`,
`mission_state: QA_PENDING_AWAITING_PRIVILEGED_DOCTOR`,
`doctor_status: NOT_EXECUTED`, `github_push_status: NOT_PERMITTED`, et
`release_ready: false`. Ce n'est pas un benchmark multi-missions ni une
attestation QA indépendante. Les tailles du modèle chargé fournies par
`/api/ps` ne mesurent pas le vrai pic RSS du serveur Ollama, ni son pic
VRAM GPU. RAM / VRAM effectives restent non certifiées.

## Vérifier la provenance

Dans GitHub Actions, choisir la CI `Integrated Windows installer and local
agent` correspondant à la PR #13, sur le SHA exact du manifeste, puis
télécharger l'artefact nommé
`NONRELEASE-Windows-Ollama-Acceptance-<run-id>`.

Les SHA-256 dans le manifeste servent à détecter les fichiers modifiés
après la construction. Ce n'est **pas une signature Authenticode**, ni une
preuve contre la compromission de la CI. L'artefact expire après 3 jours.
L'installateur généré avec un manifeste de simulation n'est **pas distribué**
dans ce kit.

Pour une future release : essais sur PC réel, isolation OS du code proposé,
Doctor/CI sur le vrai dépôt, benchmarks RAM/VRAM et modèles, installation et
upgrade fiables, et signature officielle restent nécessaires.


## Intégrité de l'archive complète (nonrelease)

La CI Windows calcule aussi le SHA-256 de l'archive téléchargée, sous la
ligne de log `NONRELEASE_ACCEPTANCE_ARTIFACT_DIGEST=sha256:...`. Vérifier
**avant toute extraction/exécution** que ce digest correspond au ZIP obtenu
depuis l'exécution GitHub Actions qui a testé le même SHA :

```powershell
(Get-FileHash -Algorithm SHA256 ".\\NONRELEASE-Windows-Ollama-Acceptance-XXXX.zip").Hash.ToLowerInvariant()
```

Le manifeste interne doit ensuite contenir exactement quatre empreintes :
l'agent, le binaire d'acceptation, `Run-HoldingLiveAcceptance.ps1` et
`README.md`. Un décalage bloque le lanceur **avant tout accès à Ollama**.
Cela protège contre les corruptions et erreurs de distribution lorsqu'on
part d'une CI et d'un digest fiables, **pas** contre une CI compromise ou
un attaquant remplaçant ensemble le script, le manifeste et les preuves.
Ne considérer ce kit ni comme signé ni comme une version de production.

La CI exerce également les refus si `/api/chat` renvoie une erreur 503 ou
si le digest du modèle change entre le choix PowerShell et la revérification
par l'agent natif : aucun reçu de succès n'est créé, le JSON temporaire
est supprimé, et aucune PR ni aucun push n'est effectué.


## Essais répétables du modèle (nouveau contrôle nonrelease)

La commande accepte désormais `-Trials` entre **1 et 5** (défaut : 1).
Exemple :

```powershell
.\\Run-HoldingLiveAcceptance.ps1 -Model "nom-exact" -Trials 3 -NumCtx 4096 -TimeoutSeconds 600
```

Chaque essai lance une **nouvelle mission Git jetable indépendante**, avec
le même modèle épinglé par digest et la même petite tâche. Chacun génère
un `receipt-<session>-<num>.json` qui conserve les SHA et la durée. Une
fois **tous les essais réussis**, un `session-<id>.json` est créé en mode
sans écrasement, avec nombre d'essais, durées et SHA-256 des reçus.
**Si un essai échoue, la session globale n'est jamais marquée PASS** et le
JSON d'épinglage temporaire est effacé ; les reçus d'essais déjà réussis
sont conservés pour diagnostic. Une interruption ne crée jamais de bilan
positif non mérité.

Cette répétition détecte certains comportements instables sur la même tâche
mais **n'est pas un benchmark comparatif de qualité** :
`model_quality_benchmark: NOT_CERTIFIED`, `independent_doctor_qa:
NOT_EXECUTED` et `release_ready: false`. Elle n'inclut pas la RAM/VRAM
réelle d'Ollama ni les contraintes d'un projet de production.
