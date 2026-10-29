# Autonomie V2 — F02 : préflight de mission (premier raccordement)

Cette fonctionnalité **inspecte**, sans ouvrir d'exécuteur, un état SQLite existant
et un inventaire machine. Elle complète `holding.inventory_cli` : elle **ne**
choisit aucun modèle, ne démarre pas Ollama, n'accorde aucune permission, ne
déclenche ni installation ni écriture Git.

## Appel

```powershell
python -B -m holding.inventory_cli --install-target "C:\AtlasHolding" --plan --state-db "C:\AtlasHolding\data\state.sqlite" --mission-id "mission-01"
```

`--state-db` et `--mission-id` sont liés et exigent `--plan`.
Le chemin SQLite doit être absolu et exister. Aucun état vide n'est créé.
L'ancien export sans paramètres de mission reste inchangé.

## Ce qui est vérifié

- État et opération de la mission, exigences de décision produit et épuisement
  du budget de tentatives.
- Dépendances : elles ne sont satisfaites que si la tâche est `DONE` **et**
  si les SHA candidat et preuve correspondent. Une tâche simplement marquée
  `DONE` n'est pas suffisante.
- Autorisation durable limitée au dépôt et à l'opération, avec expiration et
  révocation, via `StandingGrant.allows`.
- Pause, arrêt demandé et présence d'un autre écrivain déjà `RUNNING`.
- Dépendance réseau déclarée, sans prétendre vérifier la connectivité.

`PRECHECK_PASSED` signifie uniquement « aucun obstacle détecté dans **les
champs consultés** » : ce n'est ni `READY` ni une certification. Les champs
`worker_validation`, `model_benchmark` et `network_verification` restent
`NOT_RUN`. Le plan de provisioning peut recommander de démarrer un service
existant, mais ce CLI ne l'exécute pas.

## Limites et suite

Ne vérifie pas encore la santé du worker, les plugins effectivement chargés,
les benchmarks indépendants de modèle, l'environnement Windows réel, les
autorisations du transport GitHub ni les sources du dépôt au SHA cible.
Le daemon et ses gates restent les autorités pour toute exécution. L'UI
devra afficher la provenance de ces états sans transformer un préflight en PASS.
La suite de F02 doit raccorder les contrôles effectifs au moteur et à ses
consommateurs, puis tester la reprise après interruption.

## Premier raccordement au Control Center (schéma 6)

Le moteur calcule **un préflight de la prochaine mission READY affichée**, par
ordre de priorité, dans la **même transaction de lecture** que la projection
SQLite des tâches. Il publie l'avis via le canal IPC authentifié existant.
Le Control Center affiche l'identifiant et les causes (permission manquante,
dépendance non prouvée, pause, besoin réseau non testé). Il ne reçoit jamais
de chemin SQLite, secret ni pouvoir d'exécution supplémentaire.

La lecture des instantanés v1 à v5 reste compatible : dans ces cas aucun
préflight n'est affiché. Une coupure ou une observation périmée signale
explicitement que l'avis n'est plus actualisé. `PRECHECK_PASSED` reste un
**précontrôle consultatif**, jamais une preuve de worker, de modèle ou de
permission GitHub ; les contrôles indépendants du moteur restent obligatoires.

Le bandeau indique uniquement le premier lot READY ; ce n'est pas encore un
diagnostic exhaustif de toutes les missions ou de tous les outils. Aucune
mutation du schéma SQLite ou du projet utilisateur n'est réalisée.
