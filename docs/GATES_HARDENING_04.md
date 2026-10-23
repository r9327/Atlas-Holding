# PR #4 — Agents locaux : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` et `docs/LLM_AUTO_SELECTION.md` de la PR #1.

- [ ] Jeux de tâches de qualité conservant **une part de tests cachés aux modèles** : fonctions, comportement, injection de prompt, tool calling, refus d'un faux PASS.
- [ ] Séparation des rôles et évaluation indépendante : jamais de note donnée uniquement par le LLM évalué.
- [ ] Changement de modèles/quantification/context window : requalification et rollback sur baisse de qualité ou OOM.
- [ ] Un modèle lourd à la fois, paramètres de ressources observables et limites de pression système.
- [ ] Protection RAM/VRAM/CPU avec checkpoint sûr sous surcharge critique ; pas de détection des jeux puisque l'utilisateur ne lance pas la Holding pendant qu'il joue.
- [ ] Refus technique d'exécuter toute requête d'agent modifiant ses propres permissions, Doctor/CI, secrets ou règles sans procédure de maintenance humaine.
- [ ] Journal probant des raisons d'un refus/BLOCKED au lieu de boucle infinie de tentatives.

Ces gates s'ajoutent aux essais sur du vrai code Atlas, pas à la place des tests.
## Autonomie sans micro-validations
Contrat utilisateur source de vérité : [docs/USER_AUTONOMY_PROFILE.md](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) (PR #1). La seule action récurrente est la **ligne directrice des roadmaps** ; premier onboarding et autorisations durables bornées au dépôt une fois, puis exécution/tri/corrections/certification/fusion sans demander « continue ». Aucun plafond Go inventé ni surveillance des jeux ; préserver sécurité, permissions, ressources et quotas financiers réels.
