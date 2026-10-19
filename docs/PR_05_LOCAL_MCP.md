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
