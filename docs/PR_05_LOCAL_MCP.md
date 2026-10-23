# PR 05 — Hub MCP local plugins-first (implémentation partielle vérifiable)

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


## Cache documentaire offline-first vérifiable (23 octobre 2026)

- `holding/mcp_cache.py` fournit un cache SQLite borné à **64 documents**
  et **32 Kio UTF-8 par document**, avec `source_uri` HTTPS, version, date
  d'observation, date d'expiration et SHA-256 du contenu vérifié à l'écriture
  puis à chaque lecture. Chaque insertion et éviction est atomique.
- La lecture distingue `CACHED_ONLY`, `STALE` et `MISSING`. Seul un
  cache valide et non expiré délivre du texte ; un cache périmé ne fait
  jamais croire à une réponse réseau récente. Les erreurs de corruption
  arrêtent la lecture sans exposer la moindre donnée défectueuse.
- `holding.mcp_entry.router(offline_cache=...)` est un **point d'injection
  du bootstrap de confiance uniquement**. Le hub stdio par défaut n'annonce
  que `holding.health` ; il n'invente aucune installation ou configuration.
  Lorsque le superviseur lui fournit explicitement un cache provisionné,
  `holding.cache_lookup` devient disponible en **lecture seule**.
  L'ingestion documentaire reste une API réservée au composant local de
  confiance et n'est pas exposée par `tools/call`.
- Réponses annotées `UNTRUSTED_EXTERNAL`, `offline=true`,
  `has_live_network=false`. Le texte collecté n'est jamais une instruction,
  et aucune commande, requête Web, SQL ou capacité de modification de
  dépôt n'est déduite d'un document.
- Contrôles supplémentaires : refus d'URL HTTP/file, utilisateur ou
  mot de passe intégrés à l'URL, paramètres d'authentification, fragment,
  vieux snapshot remplaçant un récent, horodatages futurs et contenu
  dont l'empreinte ne correspond pas.
- Tests : relecture après réouverture SQLite, injection hostile inerte,
  réponses fraîches/périmées/absentes, corruption, rollback, taille maximale,
  éviction des anciennes versions, absence du tool quand non configuré et
  aucune écriture via MCP.

**Limites restantes :** le hub n'effectue **aucune nouvelle collecte Web** ;
un véritable connecteur Firecrawl/Context7, un ingester authentifié,
des sessions MCP isolées, la vérification du binaire installé, les quotas
persistés et la validation Windows de ces adaptateurs sont à développer.
Le cache local ne garantit pas la véracité du contenu distant, seulement
l'intégrité des octets et leur provenance déclarée.


## Inventaire des binaires MCP épinglés (23 octobre 2026)

- `holding/mcp_local_inventory.py` vérifie **sur demande** le SHA-256
  des fichiers locaux préalablement épinglés par le bootstrap authentifié.
  Aucun inventaire du disque entier, lancement de processus ou tentative
  de connexion à un serveur distant. Maximum 32 entrées explicites,
  fichiers réguliers et lectures en blocs de 64 Kio, binaires de taille
  bornée et refus des liens symboliques.
- Les résultats `BINARY_VERIFIED`, `HASH_MISMATCH`, `MISSING`,
  `NOT_REGULAR_FILE`, `CHANGED_DURING_READ` et `UNREADABLE`
  désignent **l'intégrité des octets du binaire**, jamais la disponibilité
  d'une session, l'autorisation de commandes ni le bon fonctionnement
  du protocole MCP. Les chemins absolus du PC ne sont pas exposés.
- Lorsque le superviseur fournit explicitement des
  `PinnedLocalPlugin`, le routeur rend disponible
  `holding.local_plugin_inventory` en lecture seule. Sans provisionnement,
  il ne prétend pas découvrir ou installer de plugins.
- Tests sur binaire existant, binaire modifié, absent, dossier,
  lien symbolique (si disponible), doublons, limite d'inventaire et
  déclaration de binaires non fiables.

Une vérification SHA seule ne constitue pas un healthcheck complet :
un lancement isolé avec timeout, les versions de serveur attestées,
les permissions par capability et le branchement du véritable
exécuteur réseau restent les étapes suivantes.


## Démarrage réel du hub MCP par le superviseur (23 octobre 2026)

Le processus `python -m holding.mcp_entry` fonctionne par défaut en mode
**santé locale seulement**, sans création de base de données, inventaire
du PC, plugin distant, secret ni commande d'exécution.

Un superviseur de confiance peut désormais lancer :

```text
python -m holding.mcp_entry --bootstrap-manifest <absolute-private-file.json>
```

Le manifeste JSON est strictement validé : `schema_version: 1`,
`cache_database` (chemin absolu ou null) et `pins` (liste au plus
32 entrées `name`, `declared_version`, `executable` absolu,
`expected_sha256`). Les champs inconnus, JSON à clés dupliquées,
liens symboliques, chemins relatifs et configurations de taille excessive
sont refusés avant l'ouverture du hub.

Sous POSIX, le fichier et son répertoire parent doivent appartenir
à l'utilisateur courant et ne pas être modifiables par les groupes ou
autres utilisateurs. **Sous Windows, le superviseur doit garantir
explicitement les ACL NTFS privées avant de lancer le processus** :
les attributs fournis par `stat` en Python ne remplacent pas une
vérification des ACL Windows.

Le cache déjà provisionné est ouvert en **SQLite mode=ro avec
PRAGMA query_only**. Il n'est ni créé ni mis à jour par le reader ;
une erreur de manifeste, une base manquante ou une incohérence
d'installation interrompt le démarrage (code 2), sans fuite de
chemins locaux, arguments sensibles ou stack traces sur stdout.
`tools/call` ne peut pas changer le manifeste, écrire dans
la base, changer un digest épinglé ni lancer un exécutable.

Les tests de bout en bout lancent le véritable module comme sous-processus
Python en stdin/stdout sur les runners Linux et Windows. Ils vérifient
les tools réellement annoncés et appelables, la non-exécution des
binaires, les bases absentes et les manifestes hostiles.

**Limite:** ce support est un contrat de lancement autonome du hub,
pas encore le câblage d'un installeur AtlasHoldingSetup.exe ;
la configuration et les ACL doivent être fournies par le
superviseur Windows lors de l'intégration des PR.


## Sonde processus réelle du hub interne (23 octobre 2026)

- `holding/mcp_hub_probe.py` permet au **superviseur de confiance** de
  vérifier la réponse protocolaire réelle du **seul hub interne Atlas Holding**.
  L'interpréteur Python doit correspondre à une empreinte locale SHA-256 et
  être exactement l'interpréteur courant. Le programme lancé est fixé dans
  le code : `python -B -m holding.mcp_entry`. Aucun argument d'outil, chemin
  d'exécutable arbitraire ou décision émise par le modèle ne peut remplacer
  cette commande.
- Une requête `server/discover` unique attend le protocole
  `2026-07-28`, l'identité `atlas-holding-local`, les capabilities bornées
  et un seul message JSON valide, sans doublon de clés. Le contrôle impose
  une sortie maximum de 64 Kio, un timeout de 0,25 à 10 s, `shell=False`,
  une fermeture du processus et un environnement minimal **sans clés API**.
- Les résultats `PROTOCOL_RESPONSIVE`, `TIMEOUT`, `BAD_PROTOCOL`,
  `OUTPUT_LIMIT`, `BINARY_UNVERIFIED`, `BINARY_CHANGED` ou
  `LAUNCH_FAILED` sont des constats limités. La sonde ne demande
  `tools/call` à aucun plugin externe.
- Des tests exécutent le processus interne réellement sur les runners et
  simulent des réponses falsifiées, des sorties trop grandes, de fausses
  empreintes et un environnement contenant des secrets.

Le répertoire d'exécution est désormais contraint au paquet réellement installé :
une copie hostile de `holding/mcp_entry.py` dans un autre dossier est refusée
avant création du moindre sous-processus. Test de régression associé.

**Limite de sécurité importante :** cette sonde n'est pas un bac à sable
d'exécutables tiers ; la protection ACL de l'installation Windows et la
supervision de processus par Job Objects restent obligatoires avant tout
lancement arbitraire de plugin externe. `PROTOCOL_RESPONSIVE` atteste
uniquement la découverte du hub, **pas** la disponibilité de Firecrawl,
Context7, Doctor ou des autres services.
