# Atlas Holding — carte compacte du dépôt

Cette carte n'est pas une deuxième autorité. Les règles permanentes sont `AGENTS.md`, `AI_ENTRYPOINT.md`, `HOLDING_CERTIFICATION.md` et les documents spécialisés dans `docs/`.

## Navigation / responsabilités

- `holding/engine.py`, `holding/service.py`, `holding/daemon_lease.py`, `holding/state_store.py` : orchestration, verrouillage, reprise, source SQLite.
- `holding/roadmap_*.py`, `holding/project_*.py`, `holding/autonomy.py` : lots, dépendances et autorisations.
- `holding/executor.py`, `holding/code_worker.py`, `holding/git_*.py`, `holding/github_*.py` : opérations de code, preuves et frontière de mutation GitHub.
- `holding/qa_*.py`, `holding/doctor_*.py`, `holding/atlas_*.py`, `holding/closure.py` : planification, preuve et vérifications.
- `holding/tool_registry.py`, `holding/mcp_*.py`, `config/tool_registry.json` : inventaire des capacités, permissions et plugins MCP.
- `holding/control_center_*.py`, `holding/windows_*.py`, `holding/update_*.py` : UI et runtime Windows, installation, mise à jour.
- `tests/`, `tools/`, `.github/workflows/` : tests, garde-fous et CI.

## Préflight par lot

Contrôler HEAD/worktree et les règles du dépôt réellement modifié. Chercher les appels et consommateurs dans la version courante ; identifier l'owner canonique et les tests. Pour un changement structurel, obtenir un graphe exact-SHA et corroborer avec les sources (sinon BLOCKED). Ne pas réécrire l'architecture pour de simples changements de documentation.

Le catalogue de capacités est déclaratif : le succès du validateur n'est pas la preuve qu'un exécutable, un modèle ou un serveur MCP est installé.

## Vérifications utiles

```sh
python -B -X utf8 -m tools.governance_check --json
python -B -X utf8 -m unittest discover -s tests -p 'test_governance_check.py' -v
python -B -X utf8 -m unittest discover -s tests -p 'test_tool_registry.py' -v
# Avant une modification structurelle, contre la base : 
python -B -X utf8 -m tools.architecture_guard --base-ref <SHA_BASE>
```

La CI globale de certification ne se lance normalement qu'après Ready for review ; une nouvelle modification invalide la preuve précédente et redéclenche la validation du SHA courant.