---
name: root-cause-debugging
description: Diagnostic ciblé et changement minimal sur l'architecture existante.
---

Lire `AI_ENTRYPOINT.md`. Reproduire le défaut, suivre les appels, localiser la responsabilité canonique et comparer tests/code avant changement. Préserver comportements non concernés, persistance et frontières d'autorisation. Ne pas créer d'owners ou routeurs parallèles. Avant un refactor, contrôler les consommateurs et l'analyse `tools.architecture_guard` ; utiliser Graphify quand le contrat du dépôt concerné l'exige. Exécuter les tests ciblés et comparer le résultat au comportement attendu.
