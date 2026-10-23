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


## Isolation native Windows du hub interne (23 octobre 2026)

- `holding/mcp_windows_job.py` : Job Object Windows non nommé, limites
  `KILL_ON_JOB_CLOSE`, `JOB_MEMORY_LIMIT` et `ACTIVE_PROCESS_LIMIT`.
  Le processus démarre **suspendu** avant toute exécution : ouverture du job,
  paramètres de limites, création suspendue, rattachement par
  `AssignProcessToJobObject`, reprise contrôlée du thread principal. Si
  une étape échoue, le processus est tué et le lancement est refusé.
- La sonde `holding/mcp_hub_probe.py` utilise cette voie sous Windows ;
  elle n'est plus autorisée à lancer le hub hors Job Object. Sous Linux,
  le lancement habituel reste borné par la requête, le délai et la sortie.
  La commande est fixée à l'interpréteur Python épinglé par SHA-256, pour
  le module installé `holding.mcp_entry`, pas à un outil arbitraire.
- Valeurs par défaut : plafond de **256 Mo pour le job** et **4 processus**
  au maximum. Les tests Windows vérifient la vraie appartenance au job,
  les limites Win32, la réponse de découverte, la fermeture et le refus
  des faux binaires et répertoires. Le test Linux vérifie le refus explicite
  de tout fallback vers un « Job Object » fictif.
- **Limites :** Windows Job Objects assurent le confinement de processus et
  des ressources, **pas une sandbox de sécurité ni une suppression d'accès
  réseau**. L'installation et les ACL privées, le contrôle d'identité du
  binaire réellement autorisé, la validation des permissions et la
  supervision du processus parent restent obligatoires. Les plugins tiers
  ne sont pas encore exécutés par cet adaptateur.


### Preuve d'état native, sans faux statut de plugin

`ManagedHubProcess.observe()` interroge directement
`QueryInformationJobObject` et vérifie que les limites Windows restent
effectivement activées. Elle renvoie l'état du processus (RUNNING/EXITED),
le PID observé, l'heure de lecture et les plafonds mémoire/processus lus
dans le noyau. Après fermeture, l'état devient CLOSED et les limites
ne sont plus présentées comme activement vérifiées.

Cette observation **ne prouve pas la santé du plugin**, l'existence d'une
session d'agent ou la disponibilité du réseau. Des tests natifs attestent
également que tout échec de rattachement au Job ou de reprise du thread
fait disparaître l'enfant suspendu, sans exécution non supervisée.


## Windows ACL privées pour le bootstrap et le cache (23 octobre 2026)

Un contrôle natif `holding/mcp_windows_acl.py` lit les SID et DACL Win32 via
`GetNamedSecurityInfoW`, `GetAce` et `EqualSid`. Avant **et après**
la lecture du manifeste, son propre fichier et le dossier susceptible de
le remplacer doivent appartenir à l'utilisateur courant, SYSTEM ou au
groupe Administrateurs, sans droit d'écriture accordé à un SID non fiable.
Les DACL NULL, ACE non pris en charge, ACL énormes et reparse points
sont rejetés. Windows peut accorder des droits complets à `OWNER RIGHTS`
(S-1-3-4) : ils sont admis uniquement comme droits du propriétaire **déjà
vérifié** (utilisateur courant/SYSTEM/Administrateurs), jamais comme preuve
qu'un propriétaire inconnu est fiable. Sur POSIX, les vérifications UID/mode
préexistantes restent actives.

Le bootstrap Windows protège également le fichier SQLite du cache et son
dossier, même si l'accès aux données se fait en mode SQLite read-only. Sans
cette vérification, un autre utilisateur aurait pu altérer à la fois le
contenu d'une page et le hash stocké pour lui faire passer le contrôle
d'intégrité. Un échec est bloquant, sans fuite des chemins ou contenus vers
le protocole MCP.

Les tests Windows configurent des ACL privées via SID explicites, puis
attribuent volontairement un droit d'écriture à Everyone sur le manifeste,
son dossier ou le cache : **aucun outil MCP ne doit être exposé dans ces cas**.
Cela ne remplace pas la politique d'installation Windows, la protection
des binaires au moment de leur exécution et le confinement des vrais plugins.


## Session MCP persistante et journal d'observations (23 octobre 2026)

Le superviseur dispose désormais de deux composants **locaux**, non exposés
aux prompts d'agents :

- `holding/mcp_hub_session.py` conserve un processus MCP interne unique
  dans une session réutilisable, lancée depuis l'interpréteur SHA-256 épinglé
  et le véritable répertoire du package. Sous Windows, ce processus reste
  sous Job Object (256 Mio/4 processus par défaut) ; sous POSIX, le
  superviseur peut tuer son groupe de processus. Chaque vérification
  `server/discover` a un délai et une lecture bornée à 64 Kio. Une réponse
  invalide, trop volumineuse ou silencieuse **ferme** la session et interdit
  de la présenter comme `RESPONSIVE`.
- `holding/mcp_hub_journal.py` conserve les dernières **128 observations**
  dans SQLite sans chemins locaux ni secrets. Une validation réussie
  `RESPONSIVE` n'est valable que pendant 30 secondes. Après cette limite
  l'API de lecture renvoie `STALE` sans conserver le protocole prétendument
  opérationnel. Les nouveaux événements sont transactionnels, chronologiques,
  contrôlés et accessibles en ouverture SQLite strictement read-only.

Les tests incluent : deux échanges de découverte dans le **même vrai
processus**, disparition du processus entre deux lectures, expiration,
réponse non conforme, dépassement de sortie, timeout et arrêt de groupe,
puis le journal relu dans un second lecteur, l'éviction, les horodatages
incorrects, le refus des preuves altérées et les interdictions d'écriture.

**Frontière de confiance explicite :** ce journal appartient au
superviseur local et n'est pas un certificat cryptographique transmis à
l'interface. Avant d'en afficher des états dans le Control Center, le
moteur doit vérifier son installation/ACL, puis projeter les observations
à travers l'IPC HMAC existant de la PR #11. Même `RESPONSIVE` prouve
seulement le hub interne, jamais que Doctor, Graphify, Firecrawl,
Context7 ou un autre plugin tiers est actif.
