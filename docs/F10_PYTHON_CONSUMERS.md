# F10 — Consommateurs Python de l'existant

Cette étape complète l'inventaire exact-SHA des fichiers et symboles déjà
ajouté dans Holding. Pour chaque module Python sélectionné par pertinence,
le superviseur recherche des **imports statiques** dans un échantillon borné
de sources du même arbre Git validé.

L'analyse ne charge jamais le module inspecté : elle lit le contenu au SHA
figé avec `git show` et analyse l'AST Python uniquement. Elle reconnaît les
formes `import app.feature`, `from app.feature import X`,
`from .feature import X` et `from . import feature`.

Les résultats sont attachés au champ `candidates[].importers` du rapport
`existing_code`, puis transmis au modèle dans le flux `plan_patch` existant.
La consigne de travail rappelle qu'un changement touchant un module importé
demande une revue de ses consommateurs **avant** de créer ou refactoriser du
code.

Bornes : au maximum 24 sources Python analysées (priorité au même dossier),
48 Kio par source, 12 chemins de consommateurs par candidat, 12 candidats.
Les arbres très volumineux n'impliquent pas de parcours récursif supplémentaire
d'assets : les chemins proviennent de l'inventaire Git F10 déjà borné.

L'état `PARTIAL_AST_IMPORT_SCAN` est volontairement explicite : les
références dynamiques, plugins chargés à l'exécution, imports conditionnels
et projets non Python ne sont pas couverts exhaustivement. **Zéro import trouvé
ne veut pas dire zéro consommateur réel**. Ce relevé n'accorde aucun droit
de refactoring, de suppression, de fusion ou de certification.

Tests ciblés : import absolu et relatif, import d'un module parent, absence
de détection dynamique, isolation des modifications non committées, borne
de fichiers, et rejet d'une fausse preuve d'exhaustivité dans Ollama.

La phase suivante devra réutiliser Graphify et les analyses multi-langages
disponibles pour approfondir les dépendances avant d'en tirer un verdict
automatique. Ce lot ne déclare pas F10 intégralement terminé.

## Correctifs de portée de l'échantillonnage

- Les imports relatifs exécutés dans `app/__init__.py` sont résolus à partir
  du paquet `app` (et non du répertoire parent).
- Lorsqu'il existe plus de 24 sources possibles, un unique `git grep -l -F`
  au **même SHA immuable** préclasse les fichiers susceptibles d'importer
  les candidats. Les résultats bruts de grep ne suffisent jamais : seuls les
  vrais imports AST sont retenus.
- Une panne de cette préselection revient au scan borné précédent ; elle ne
  constitue **pas une preuve de non-utilisation**.
- La limite de 24 fichiers AST et de 12 importeurs par module reste inchangée.
  Un consommateur absent du rapport peut encore exister hors de l'échantillon.
