# Autonomie V2 — F10, inventaire de l'existant avant création

## Implémenté dans ce lot

Le codeur de Holding reçoit **avant la génération** un inventaire limité aux
sources suivies par Git au commit de base exact de la mission. Le scan utilise
`GitWorktrees._git` du superviseur, sans commande générée par le modèle et
sans exécuter le code du dépôt.

- Recherches déterministes de chemins pertinents à partir des critères
  d'acceptation de la roadmap (limite de 24 termes).
- Maximum de 20 000 entrées Git, 12 candidats et 48 Kio par source Python
  inspectée. Les symlinks et sous-modules ne sont jamais des candidats.
- Noms de fonctions/classes extraits de l'AST Python sans import ni exécution.
- Transmission structurée dans `plan_patch.existing_code` avec le commit,
  les chemins et les symboles observés.
- Consigne explicite à l'adaptateur Ollama de préférer l'adaptation du module
  existant à une implémentation parallèle sans justification.
- Si la lecture Git exacte est indisponible ou contradictoire, le codeur ne
  démarre **pas** la génération d'un patch.

**Les correspondances restent des indices, pas des verdicts.** Un chemin ou
un symbole trouvé n'établit ni le propriétaire architectural canonique, ni
un usage effectif, ni la complétude, ni la réussite des tests. L'absence de
correspondance ne prouve pas l'absence de fonctionnalité. Le rapport garde
`functionality_status=NOT_VERIFIED` et `can_skip_implementation=false`.

## PR et code non fusionné

`github_pr_status=NOT_CHECKED` : ce premier lot effectue une lecture du
commit local seulement, **aucune interrogation GitHub** ni assertion qu'il
n'existe pas de PR concurrente. Un futur adaptateur d'inventaire GitHub
authentifié devra corréler PR ouvertes/fermées, branches, commits et diff
avec la même référence de dépôt, puis mesurer la validité des implémentations
sans emprunter leurs certifications.

`consumers_status=NOT_VERIFIED` : l'index de noms n'est pas un graphe
d'appels ou de références exhaustif. La prochaine étape devra réutiliser les
analyses Graphify/Serena disponibles et la cartographie des imports avant
d'affirmer un propriétaire et ses consommateurs.

## Tests et règles

Tests Git réels sur dépôt temporaire : isolation exacte SHA, AST des modules,
chemins interdits, liens symboliques, sortie malformée, scan indisponible.
Tests de bout en bout : le codeur transmet le rapport avant patch, la panne
bloque la génération ; l'adaptateur Ollama rejette les états falsifiant
la disponibilité PR, les benchmarks ou l'autorisation de sauter le travail.

La certification indépendante GitHub et Doctor reste inchangée. La seule
présence d'un ancien module ne provoque jamais un `DONE` ni une fusion.
Ce lot est **une première intégration opérationnelle de F10**, pas la
livraison complète de F10.
