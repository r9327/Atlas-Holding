# Atlas Holding

Orchestrateur local et offline-first de missions de développement sur des dépôts autorisés, avec agents, worktrees Git, contrôles QA, plugins MCP soumis à permissions et preuves de certification.

**Contribution au dépôt Holding :** commencer par [AI_ENTRYPOINT.md](AI_ENTRYPOINT.md), puis [AGENTS.md](AGENTS.md) et [ZERO_TRUST_RULES.md](ZERO_TRUST_RULES.md). Les états de validation et les exigences de merge sont décrits dans [PHASE_CERTIFICATION.md](PHASE_CERTIFICATION.md).

**Outillage existant :** [chaîne d'outils](docs/TOOLCHAIN.md), [politique MCP](docs/LOCAL_MCP.md), [registre déclaratif](config/tool_registry.json) et [contrôle architectural](tools/architecture_guard.py). Les outils déclarés ne sont pas nécessairement installés. Atlas Doctor s'exécute dans le dépôt Dof-Atlas, pas comme une commande Holding.

**État réel :** une CI Python réussie ne certifie pas l'EXE ou son autonomie Windows. Les exigences d'acceptation restent dans [docs/QUALITY_RELEASE_GATES.md](docs/QUALITY_RELEASE_GATES.md) et [docs/OPERATIONAL_READINESS.md](docs/OPERATIONAL_READINESS.md).

Contrôle local ciblé : `python -B -X utf8 -m unittest discover -s tests -p 'test_contribution_contracts.py' -v`.
