# F10 — Raccordement du relevé GitHub au codeur local

Ce lot raccorde la lecture des PR de #53 au préflight du codeur livré par
#52. Il **réutilise la même instance de `GitHubBrokerProcessControl`**
déjà construite par le bootstrap de `daemon_entry` pour la santé GitHub
et la synchronisation. Aucun jeton GitHub n'entre dans l'adaptateur Ollama,
dans les prompts ou dans les sources inspectées.

## Chemin de données

1. Le bootstrap de confiance installe un seul courtier si et seulement si
   la configuration de compte GitHub a été validée.
2. `HoldingService.from_local_agent` transmet ce courtier optionnel à
   `LocalCodeWorker`, sans créer de rôle d'exécution supplémentaire.
3. Le codeur établit d'abord un arbre Git local exact-SHA, puis demande
   un relevé des PR ouvertes via `observe_open_prs(repository)`.
4. Les informations PR (numéro, head/base SHA, branche, Draft, fork) sont
   transmises comme **données non fiables** de recherche/adaptation ;
   aucune description/commentaire remote n'est inclus.
5. L'adaptateur Ollama, qui reste **autonome et exécutable en script sans
   import du package Holding**, revalide son JSON borné et ajoute au
   contexte la consigne d'examiner l'éventuel recouvrement.

En l'absence de compte configuré, l'observation est `NOT_CHECKED`.
Si le courtier ne répond pas, elle est `UNAVAILABLE`. Ces états ne doivent
pas être transformés en « aucune PR ouverte ». Une observation réussie
indique `OPEN_PRS_OBSERVED` ou `PAGINATION_NOT_VERIFIED`. Les informations
du codeur n'établissent **ni la réussite des fonctionnalités ni l'autorisation
de sauter une mission** ; les permissions et les certificats exact-SHA sont
identiques à avant.

Le contrôle est limité à 20 PR exposées parmi les 100 premières observées.
Le prochain lot pourra comparer les diff et les chemins des PR aux critères,
les fermer après intégration seulement sous autorisation, et cartographier
les consommateurs via les outils d'architecture existants.

## Vérification

Tests du vrai `LocalCodeWorker` avec un courtier factice : la PR est
disponible avant le patch ; l'indisponibilité est signalée au modèle.
Tests du protocole Ollama : rejet de fausses preuves, mauvais dépôt,
contradictions de pagination et état de délégation illégitime.
Certification complète indépendante CI au dernier SHA obligatoire.
