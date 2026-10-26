# Garde-fou anti-complexite (complementaire)

Ce controle **complemente**, sans les reimplementer, Doctor, Graphify, la
certification Windows, les tests metier et les budgets RAM.

Il compare les fichiers Python de `holding/` au commit de base Git exact.
Une nouvelle PR est refusee en presence de :
- nouveau cycle d'import Python statique ;
- copie exacte et substantielle d'une fonction dans un autre module (AST,
  corps >= 14 lignes et >= 65 noeuds, petits adaptateurs exclus) ;
- nouvel import direct de stockage/execution Git privilegiee dans
  `control_center_ui.py` (l'UI reste une vue, les commandes passent
  par une interface authentifiee) ;
- nouveau module parallele suffixe `_v2`, `_legacy`, `_old`, `_new`,
  `_copy` ou `_backup`.

Executer avant la certification finale :

```bash
python -B -m unittest discover -s tests -p test_architecture_guard.py -v
python -B -m tools.architecture_guard --base-ref origin/main
```

Le workflow `Holding Architecture Regression` s'execute sur la PR lorsqu'elle
passe Ready for review ou recoit le label `ci:validate`, **pas a chaque commit**.

Limites :
- Les imports dynamiques, signaux Qt, callbacks, subprocess et fuites de workers
  ne sont pas prouvables avec ce scanner AST : utiliser les validations
  runtime existantes et des tests Windows reels.
- Ne pas supprimer/renommer du code sur la seule base d'une relation Graphify.
- Un warning indicatif n'est pas un bug et ne justifie pas un nouveau moteur.
- Le code existant hors scope n'est pas reformatte ni refondu.
- Le workflow et sa PR restent non certifies tant que les tests exact-SHA
  n'ont pas ete executes. Le required check doit etre active dans les
  regles GitHub apres integration ; un workflow YAML ne suffit pas.
- Les branches deja ouvertes doivent integrer cette politique avant leur
  certification finale pour en beneficier.
