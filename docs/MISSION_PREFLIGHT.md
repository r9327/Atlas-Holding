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

## Tableau de bord authentifié (F02 — raccordement v6)

Le moteur expose `ControlPlane.mission_preflight(mission_id, now_epoch=...)`,
puis `project_dashboard_snapshot` publie les diagnostics dans son **unique
snapshot signé/authentifié**. Les calculs sont réalisés depuis la même
transaction SQLite de lecture que les états des tâches : pas de requête
réseau, aucun accès à SQLite depuis le processus Qt, pas de nouveau moteur.

Le Control Center présente un onglet **Préflight** en lecture seule.
Au maximum 20 missions `READY` sont détaillées, dans l'ordre de la
roadmap visible ; `preflight_total_ready` donne le nombre réel de missions
`READY` consultées, même quand toutes ne tiennent pas dans l'onglet.
Le tableau affiche les grants présents et les blocages tels qu'observés,
sans prétendre que la capacité de travail est disponible.

Le protocole est passé à `schema_version=6` pour rendre les nouveaux champs
explicites. Le lecteur conserve la compatibilité avec les snapshots v1–v5 ;
les champs v6 ont un schéma strict, leurs missions doivent être `READY`
et leurs identifiants correspondre exactement à l'ordre affiché. Les claims
`executes_actions`, benchmark ou worker réussis sont rejetés. Les
modifications de ce document et la PR ne constituent ni test natif Windows
du produit installé, ni qualification de la release #9.

**À développer ensuite** : contrôles de santé réels du worker et du hub MCP,
benchmark modèle sur environnement actuel, liaison aux capacités de la
mission, expiration des observations et éventuel détail/pagination pour
plus de 20 missions. Le préflight reste consultatif ; aucune preuve CI
ou permission n'est accordée automatiquement.
