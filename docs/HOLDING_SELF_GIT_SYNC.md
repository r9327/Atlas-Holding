# Synchronisation du code source Atlas Holding

Cette commande **ne concerne que** le checkout Git `r9327/Atlas-Holding` sur le PC. Elle est indépendante des missions de la Holding sur `r9327/Dof-Atlas`, du broker GitHub des agents et de la mise à jour de `AtlasHoldingSetup.exe` / des EXE installés.

Depuis **la racine du dépôt Holding** :

```powershell
python -m holding.self_git_sync status    # 100 % hors-ligne
python -m holding.self_git_sync check     # vérifie uniquement le distant, sans pull/push
python -m holding.self_git_sync check --json
python -m holding.self_git_sync sync
# ou explicitement :
python -m holding.self_git_sync pull
python -m holding.self_git_sync push
```

`status` est **hors ligne** : branche, HEAD, changements non commités. `check` relève **à la demande** les SHA distant/local et les compteurs `ahead`/`behind` pour le Control Center, même si des fichiers de travail sont modifiés ; il ne fait jamais de `merge` ou `push`. Si les SHA sont identiques, `check` n'effectue **aucun téléchargement Git** (un seul `ls-remote`) ; si le distant a changé, un `fetch` de contrôle d'ascendance actualise uniquement les métadonnées Git, pas le dossier de travail. `sync`, `pull` et `push` vérifient l'origine GitHub attendue, effectuent une observation distante et affichent les SHA **avant et après**. `sync` effectue un fast-forward si GitHub est en avance, un push non forcé uniquement sur branche de travail si le local est en avance, ou ne fait rien si déjà aligné. Pour la future interface, ajouter `--json`.

Règles : **pas de `reset --hard`, `--force`, écrasement, staging ou commit automatique**. `main` est en lecture seule côté publication : travailler sur une branche, puis passer par une PR et sa CI. Un checkout sale, détaché, un remote suspect, une divergence ou une coupure réseau bloquent les mutations et conservent le travail. Résoudre explicitement le conflit ou commiter les changements locaux avant de relancer. En cas de push réseau ambigu, réexécuter `status` puis `sync` ; le nouvel état distant est revérifié et aucun push forcé n'est tenté.

Ce mécanisme ne doit **pas** être lancé automatiquement pendant qu'un agent écrit des fichiers ni pendant une mise à jour de l'EXE. La synchronisation autonome du travail des agents est implémentée séparément dans le flux `sync_worker`/GitHub à preuves exact-SHA.

Tests : `python -m unittest discover -s tests -p test_self_git_sync.py -v`. Ils utilisent uniquement des dépôts Git temporaires locaux : aucune écriture dans GitHub ni dans DOFUS Atlas.
