# Atlas Holding — regeneration de l'index AI Context sans hooks (prototype)

## Origine du besoin

La vraie PR #17 a exerce un commit documentaire jetable et les trois actions
Doctor sur le SHA exact. Les resultats sont **plan PLANNED_LOCAL, check REVIEW,
validate REVIEW**. Le diagnostic de Doctor `validate` a constate :
`Agent/AI Context preflight failed: committed context index is stale or missing`.

Atlas regenere normalement `.ai/context_index.json` dans un hook pre-commit
qui invoque son propre code Python. **Holding ne doit jamais executer un
hook modifiable par l'agent ou par le depot cible**. Le moteur `GitWorktrees`
neutralise donc deja les hooks et la signature GPG lors du commit.

## Nouvelle solution experimentee

Le superviseur Git de Holding reconstruit directement le format canonique
Dof-Atlas `tools.ai_context` schema 2 a partir du Git tree **staged**
(`write-tree` / `ls-tree`). Il reprend les vrais champs
`schema_version`, `source`, `excluded_top_level` et `entries`, en
excluant `.ai` pour eviter l'autoreference.

Ce mecanisme est reserve a `r9327/Dof-Atlas` si le vrai scope map existe
dans le depot canonique. Il refuse son absence dans le worktree candidat,
les liens symboliques, un index non suivi, les chemins dangereux et une
demande directe de l'agent d'ecrire `.ai/context_index.json`. Le contenu
genere est stage par le superviseur avec les seuls fichiers explicitement
acceptes, puis un commit local est cree en conservant la desactivation des
hooks Git. Le moteur relit ensuite le contenu du blob de commit pour
verifier son exactitude.

**Autorite limitee** : le commit de l'index prouve une empreinte de tree,
pas la validite du code, des tests, de Graphify ou une certification Doctor.
Les gardes de provenance restent obligatoires. L'index n'est jamais obtenu
a partir d'un JSON choisi par le modele.

## Verification

Le banc de tests utilise de **vrais depots Git temporaires**, sous
Windows et Linux, et verifie notamment que les modifications sont
uniquement dans le worktree autonome, que le commit inclut l'index
regenere, qu'aucun chemin `.ai` n'entre dans la source d'empreinte,
que l'agent ne peut pas demander une fausse regeneration et que les
symlinks/metadata supprimees sont bloques.

Cette PR est un prototype de synchronisation de confiance, non une
publication Windows et non une permission de passer au travers des gates
du vrai Atlas. Une prochaine verification doit confronter le resultat
aux sources exactes de `tools.ai_context check` sur un clone jetable et
un vrai check Doctor, avant toute integration sur la branche principale.
