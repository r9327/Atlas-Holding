# PR #5 — MCP et provenance : gates supplémentaires (non implémentés)

Référence : `docs/OPERATIONAL_READINESS.md` de la PR #1.

- [ ] Serveurs MCP versionnés et permissions effectives vérifiées côté exécuteur, jamais simplement par la déclaration de l'agent.
- [ ] Chaque plugin a quota, budget de données, profondeur/crawl maximum, délai de requête et stratégie de repli offline.
- [ ] Plugin externe non fiable : prompt injection dans un résultat Web ne peut appeler le shell, écrire dans un dépôt ou demander une nouvelle permission.
- [ ] Catalogue et caches stockent URI/source/date/hash/version, et signalent un résultat stale ; pas de fausse disponibilité de Firecrawl/Context7 hors ligne.
- [ ] Secrets stockés hors Git/logs/LLM ; tous les requêtes distantes sont limitées au périmètre autorisé, avec redacteur de données.
- [ ] Tests de faux serveur MCP hostile, source incohérente, quota nul, authentification cassée et ressources saturées ; hub reste réparable.

Ne jamais lancer le cluster Docker Firecrawl complet automatiquement si cela met en péril la RAM/VRAM de l'inférence locale.