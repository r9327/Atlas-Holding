---
name: architecture-impact
description: Verify consumers, existing owners and bounded architectural regression in Holding.
---

# Analyse d'impact

Lire AI_ENTRYPOINT.md et docs/ARCHITECTURE_REGRESSION_GATE.md.
Inspecter imports explicites/dynamiques, UI, worktrees, SQLite, processus, permissions et tests.
Ne pas créer de moteur parallèle ou supprimer un module faute de références textuelles.
Pour Holding: python -B -X utf8 -m tools.architecture_guard --base-ref SHA_BASE, puis tests pertinents.
Pour Dof-Atlas: appliquer son contrat actuel Graphify/Doctor exact-SHA, sans réutiliser une vieille carte.
Une analyse structurelle insuffisante bloque l'édition risquée, pas la mission entière.
