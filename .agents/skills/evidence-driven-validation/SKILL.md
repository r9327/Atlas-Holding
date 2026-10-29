---
name: evidence-driven-validation
description: Report exact-SHA tests, logic review and native-versus-CI evidence for Holding.
---

# Preuves avant validation

Lire AI_ENTRYPOINT.md, ZERO_TRUST_RULES.md et docs/HOLDING_CERTIFICATION.md.
Classer le risque, choisir les tests ciblés et relever le SHA exact.
Lancer tools.ai_policy_check pour l'IA/MCP et tools.architecture_guard pour le structurel.
Relire chaque commit contre son parent, puis le diff complet et les effets indirects.
Séparer preuves exécutées, skipped, non exécutées et BLOCKED.
Garder les PR Draft pendant le développement. CI finale et preuves Windows sont distinctes.
