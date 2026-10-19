# PR #4 — Agents locaux : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` et `docs/LLM_AUTO_SELECTION.md` de la PR #1.

- [ ] Jeux de tâches de qualité conservant **une part de tests cachés aux modèles** : fonctions, comportement, injection de prompt, tool calling, refus d'un faux PASS.
- [ ] Séparation des rôles et évaluation indépendante : jamais de note donnée uniquement par le LLM évalué.
- [ ] Changement de modèles/quantification/context window : requalification et rollback sur baisse de qualité ou OOM.
- [ ] Un modèle lourd à la fois, paramètres de ressources observables et limites de pression système.
- [ ] Pause/réduction d'activité lors des sessions de jeu et reprise à checkpoint sûr.
- [ ] Refus technique d'exécuter toute requête d'agent modifiant ses propres permissions, Doctor/CI, secrets ou règles sans procédure de maintenance humaine.
- [ ] Journal probant des raisons d'un refus/BLOCKED au lieu de boucle infinie de tentatives.

Ces gates s'ajoutent aux essais sur du vrai code Atlas, pas à la place des tests.