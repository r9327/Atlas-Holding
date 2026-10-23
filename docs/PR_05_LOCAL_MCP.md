# PR 05 — Hub MCP local plugins-first (SPECIFICATION, NON IMPLEMENTEE)

**But :** les IA recherchent systématiquement la meilleure *capability* chez un plugin spécialisé et travaillent en mode offline-first. Distinguer serveur installé sur le PC et capacité réellement disponible sans Internet.

## Intégrations
- Hub local stdio avec registry versionné, discovery des capabilities sans exposer 200 outils inutiles à l'IA ; permissions lecture seule par défaut et activation au cas par cas.
- Priorité : plugin local vérifié → serveur auto-hébergé pertinent → snapshot/cache local versionné → remote MCP autorisé avec quota → fallback natif fiable → OFFLINE_DEFERRED.
- Connecteurs locaux AI Context/Graphify/Doctor/Git et adaptation OpenCode ; vérification réelle des exécutables et contrats plutôt qu'une liste statique.
- Context7 : client MCP sur PC et cache documentaire avec version/URL/date/expiration ; **les nouvelles docs restent réseau-dépendantes**.
- Firecrawl : client local + scrape/map/recherche à crédits contrôlés. Autohébergement Docker/Playwright/queues/DB en profil avancé **optionnel** ; les nouvelles pages ne sont pas disponibles hors réseau même en self-host. Alternative légère ciblée seulement si légale et fiable.
- Cache de pages sourcées et contrôlées ; propagation de provenance/URLs/IDs, pas de contenu externe traité comme instruction système.
- Quotas à zéro dépense, secrets hors Git, healthcheck et statuts READY_LOCAL / READY_ONLINE / CACHED_ONLY / OFFLINE_DEFERRED / QUOTA_EXHAUSTED / AUTH_REQUIRED / BLOCKED.

## Sortie
- [ ] Tests d'un plugin fictif local, plugin indisponible, mauvais schéma, quotas nuls et permission interdite.
- [ ] Context7/Firecrawl offline : jamais de fausse réponse prétendue fraîche ; cache timestamp explicite.
- [ ] Injection de prompt hostile dans une page collectée n'élève aucune permission.
- [ ] Fermeture du hub n'interrompt pas la DB ni ne corrompt le worktree ; pas de clés dans logs.
- [ ] Test sur machine RAM contrainte : aucun serveur Firecrawl lourd lancé par défaut.

## Dépendances
#1 et API #3. Consommer les contrats `docs/LOCAL_MCP.md` et `docs/TOOLCHAIN.md` de #1 sans recopier un second règlement.
# État d'implémentation

Un noyau MCP local moderne est désormais implémenté dans `holding/mcp_stdio.py`
et lançable avec `python -m holding.mcp_entry` : JSON-RPC 2.0 borné sur stdio,
framing une ligne/un message, `server/discover` MCP `2026-07-28`, `tools/list`,
`tools/call`, erreurs bornées, ordre déterministe et arrêt propre sur EOF. Le seul
outil livré est `holding.health`, local et en lecture seule. Il ne contacte aucun
service et n'exécute aucun plugin.

Cette base ne constitue pas encore le hub complet : les adaptateurs Context7 /
Firecrawl, le cache durable, les délais/processus isolés et la comptabilité
SQLite restent à brancher et tester. Aucune capacité réseau n'est revendiquée.

Contrats officiels suivis :
- https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio
- https://modelcontextprotocol.io/specification/2026-07-28/server/discover
- https://modelcontextprotocol.io/specification/2026-07-28/server/tools


## Contrat exécutable des arguments JSON-RPC — 23 octobre 2026

Le serveur MCP stdio refuse désormais toute invocation tools/call dont les
arguments ne satisfont pas un **sous-ensemble effectivement validé** du JSON
Schema affiché dans tools/list. Les schémas sont copiés à l'initialisation,
sans références réseau, expressions régulières embarquées, code exécutable ou
résolution de références ; une déclaration incompatible empêche le démarrage
de l'outil concerné.

Mots-clés couverts : type, properties, required, additionalProperties, items,
minLength/maxLength, minItems/maxItems et enum. Les booléens ne sont pas des
entiers. Les collections et profondeurs sont bornées. Les handlers ne reçoivent
jamais un appel refusé par ce contrat. Les exceptions d'un outil ayant reçu
une entrée valide restent masquées dans un résultat isError, sans fuite de
secret, sans basculement en « succès ».

Ce lot est **local et sans réseau**. Il ne prouve ni que Firecrawl/Context7
sont installés ni qu'un outil distant a été exécuté. Le branchement des vrais
exécuteurs, leur état d'installation et les quotas durables restent des lots
distincts à certifier après intégration.

Les tests couvrent les champs obligatoires, refus des clés inattendues,
validation des tableaux, bornes, types et enum, schémas non supportés,
non-exécution des handlers et masquage des erreurs.
