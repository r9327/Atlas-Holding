---
name: holding-governance
description: Audit borné des règles IA, capacités plugins, preuves et certification dans Atlas Holding.
---

# Holding governance

1. Lire `AI_ENTRYPOINT.md`, `AGENTS.md`, `ZERO_TRUST_RULES.md` et
   `docs/QUALITY_RELEASE_GATES.md`.
2. Inspecter les consommateurs réels, `config/tool_registry.json` et
   `holding/tool_registry.py` avant de modifier une règle ou une capacité.
3. Confirmer qu'un plugin est présent, autorisé, compatible avec les quotas
   et adapté à la tâche ; ne pas confondre registre et runtime.
4. Établir les preuves requises, le SHA exact, les limites d'environnement
   et les checks qui resteront NOT_RUN. Pour les dépôts cibles, leur propre
   Doctor/Graphify et leurs contrats priment.
5. Appliquer un diff limité ; lancer
   `python -B -X utf8 tools/ai_governance_guard.py` et
   `python -B -X utf8 -m unittest discover -s tests -p "test_ai_governance_guard.py" -v`.
6. Revoir chaque commit, la PR entière, et classifier séparément la logique,
   les tests et la certification. Pas de label CERTIFIED fictif.
