# Atlas Holding — point d'entrée des agents IA

Lire ce fichier puis `AGENTS.md`, `AI_CONTEXT.md`, `HOLDING_CERTIFICATION.md` et les contrats relatifs au lot **avant toute modification**. Ce contrat concerne le développement de Holding elle-même ; pour un dépôt piloté par Holding, ses règles locales prévalent.

## Cycle obligatoire

1. Constater le SHA exact, la branche, le statut Git et les droits effectifs. Ne jamais écrire dans `main`, écraser un worktree utilisateur ou supposer qu'un plugin est installé.
2. Délimiter le besoin, les critères d'acceptation, les consommateurs, les données persistantes, les risques et les tests existants. Utiliser `AI_CONTEXT.md` pour naviguer dans les propriétaires canoniques.
3. Avant un changement structurel : analyser graphe, cycles et consommateurs au SHA exact (Graphify s'il est disponible) ; à défaut, suspendre le refactor risqué. `tools/architecture_guard.py` détecte des régressions statiques, mais ne remplace pas une preuve runtime.
4. Choisir des outils par capacité : registre déclaré `config/tool_registry.json`, plugin local vérifié, cache identifié, service distant autorisé avec quota, puis repli sûr. Respecter `docs/PLUGIN_GOVERNANCE.md`.
5. Implémenter un micro-lot sur branche isolée. Distinguer développeur et auditeur ; relire chaque commit puis la PR complète, sans supprimer ni contourner des tests.
6. Exécuter les tests ciblés réels, la vérification de politique ci-dessous, et les autres gates applicables. Conserver les erreurs et les NOT_RUN ; une sortie IA n'est jamais une preuve.
7. Ouvrir la PR en Draft pendant l'édition ; passer Ready seulement sur un candidat final. Laisser la certification GitHub, notamment les tests Windows, valider le SHA exact. Ne pas fusionner ou déclarer CERTIFIED sur une simple CI partielle.

```sh
git rev-parse HEAD
git status --short
python -B -X utf8 -m tools.governance_check --json
python -B -X utf8 -m unittest discover -s tests -p 'test_governance_check.py' -v
# Selon le périmètre : tests ciblés et architecture_guard --base-ref <SHA_BASE>
```

## Verdict et escalade

- `CODE_DONE` : code et revue présents, tests pas encore nécessairement passés.
- `VALIDATED` : tests locaux pertinents exécutés et PASS avec SHA et portée vérifiés.
- `CERTIFIED` : toutes les preuves obligatoires de ce périmètre, dont natives Windows si nécessaires, ont effectivement réussi sur le SHA candidat.
- `BLOCKED` : permissions, capacité, provenance, tests ou preuves insuffisants. Ne jamais convertir un SKIPPED, un NOT_RUN ou un échec en PASS.

Un contrôle de gouvernance réussi ne certifie ni l'EXE, ni Ollama, ni les intégrations MCP en conditions réelles.