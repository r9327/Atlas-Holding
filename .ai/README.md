# Routage de tâches IA — Holding

task_profiles.json est une table de recommandations de rigueur, et non une implémentation de sélection de modèle ou un niveau de certification. La sélection exécutable reste dans holding/model_selection.py avec ses benchmarks, rôles et limites mémoire. Les profils BALANCED_VERIFIED et QUALITY_FIRST correspondent à ses profils connus.

L'autorité outils/plugins est config/tool_registry.json, validé par holding/tool_registry.py et holding/mcp_policy.py. Un tool déclaré n'est pas nécessairement installé, autorisé ou disponible hors ligne.

Contrôle statique: python -B -X utf8 -m tools.ai_policy_check. Ce contrôle ne certifie ni Doctor, ni Graphify, ni Ollama, ni les serveurs MCP.
