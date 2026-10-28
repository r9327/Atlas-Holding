# Historique des contrats de mission

`SafeRoadmapReplanner` reste le propriétaire des modifications de roadmap.
Une révision autorisée d'une mission READY qui n'a pas commencé conserve
désormais son ancien contrat complet dans `mission_spec_history` avant de
remplacer le contrat courant.

L'archive contient l'objectif, les critères et dépendances présents dans le
contrat, sa version d'origine, la version de remplacement, son empreinte SHA-256
et la date d'archivage. L'ajout des tâches, l'archive, la nouvelle version et les
événements sont enregistrés dans la même transaction : un échec annule le tout.

```python
planner = SafeRoadmapReplanner(store)
page = planner.history("mission-id", limit=20)
if page:
    older = planner.history("mission-id", before_version=page[-1].version)
```

La lecture est paginée (1 à 100 contrats), du plus récent au plus ancien, et
vérifie l'empreinte et l'identité avant de rendre un contrat. Elle ne crée pas
de table dans une ancienne base. La première révision après mise à jour installe
la table de façon additive ; les révisions antérieures à cette évolution ne
peuvent pas être reconstituées et ne sont pas inventées.

L'archive n'est pas une commande de restauration ni une preuve de travail
effectué. Un résultat produit pour l'empreinte d'un ancien contrat ne devient pas
valide pour le nouveau contrat. Les consommateurs, notamment les dossiers R&D,
doivent comparer leur empreinte source à celle du contrat courant et présenter
les anciens résultats comme historiques.

Les règles de replanification restent applicables : pas de suppression silencieuse
de tâche, pas de changement d'opération ou de permission, pas de réécriture d'une
mission en cours, avec checkpoint, preuve ou dépendant actif. L'ajout indépendant
de nouvelles tâches préserve les travaux déjà réalisés.
