# PR #2 — Windows bootstrap : gates supplémentaires (non implémentés)

Se reporter au contrat commun [OPERATIONAL_READINESS.md](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/OPERATIONAL_READINESS.md) de la PR #1.

- [ ] Connexion mobile **illimitée** : aucun plafond de Go ; volumes et espace estimés affichés, autorisation globale unique de provisioning au premier setup puis installation automatique des modèles recommandés.
- [ ] Pause/reprise et checksum d'un gros téléchargement sur partage interrompu, sans re-téléchargement complet indu.
- [ ] Inventaire et réutilisation des composants réels, installation/réparation atomique et idempotente ; ne jamais casser Python/Git/Ollama installés pour d'autres apps.
- [ ] Sauvegarde et rollback automatique après mise à jour échouée, plus test réel de restauration.
- [ ] Vérification de provenance, signature lorsqu'elle existe et hash des paquets ; aucune commande `curl|sh` ou script réseau non contrôlé.
- [ ] Profil **qualité auto** pour LLM sans nom à saisir, et permissions GitHub expliquées en français lors de l'onboarding.
- [ ] Téléchargement initial requis s'il manque un modèle : état explicite si offline, pas d'IA inventée.
- [ ] Ne jamais effacer des poids Ollama ou dossiers partagés lors d'une désinstallation sans confirmation explicite.

Aucune de ces cases n'est validée actuellement ; tests sur Windows propre requis.
## Autonomie sans micro-validations
Contrat utilisateur source de vérité : [docs/USER_AUTONOMY_PROFILE.md](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) (PR #1). La seule action récurrente est la **ligne directrice des roadmaps** ; premier onboarding et autorisations durables bornées au dépôt une fois, puis exécution/tri/corrections/certification/fusion sans demander « continue ». Aucun plafond Go inventé ni surveillance des jeux ; préserver sécurité, permissions, ressources et quotas financiers réels.
