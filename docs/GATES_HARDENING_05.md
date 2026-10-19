# PR #5 — MCP et provenance : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Serveurs MCP versionnés et permissions effectives vérifiées côté exécuteur, jamais simplement par la déclaration de l'agent.
- [ ] Chaque plugin a quotas **de service et de crédits**, profondeur/crawl maximum, délai de requête et stratégie de repli offline ; pas de plafond Go mobile artificiel.
- [ ] Plugin externe non fiable : prompt injection dans un résultat Web ne peut appeler le shell, écrire dans un dépôt ou demander une nouvelle permission.
- [ ] Catalogue et caches stockent URI/source/date/hash/version, et signalent un résultat stale ; pas de fausse disponibilité de Firecrawl/Context7 hors ligne.
- [ ] Secrets stockés hors Git/logs/LLM ; tous les requêtes distantes sont limitées au périmètre autorisé, avec redacteur de données.
- [ ] Tests de faux serveur MCP hostile, source incohérente, quota nul, authentification cassée et ressources saturées ; hub reste réparable.

Ne jamais lancer le cluster Docker Firecrawl complet automatiquement si cela met en péril la RAM/VRAM de l'inférence locale.
## Contrat utilisateur prioritaire
Voir [le profil d'autonomie](https://github.com/r9327/Atlas-Holding/blob/feat/windows-local-holding-v0/docs/USER_AUTONOMY_PROFILE.md) dans la PR #1. **La seule action récurrente de l'utilisateur est de fournir la ligne directrice des roadmaps**. La Holding prend en charge choix, exécution, revues, corrections, synchronisation et fusion dans le mandat initial révoquable ; les seuls blocages humains sont les décisions réellement hors délégation ou opérations sensibles.
