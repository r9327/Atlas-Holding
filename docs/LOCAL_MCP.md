# Local MCP Fabric — contrat d'architecture (non implémenté)

## Décision : plugins-first, offline-first

La Holding doit disposer d'un **hub MCP local** (processus exécuté sur Windows avec IPC/stdin-stdout) que le CEO et chaque agent peuvent interroger. L'agent choisit par capability, pas par nom vague de plugin. Le hub vérifie l'état de chaque outil et les permissions AVANT l'appel. L'interface montre READY_LOCAL, READY_ONLINE, CACHED_ONLY, OFFLINE_DEFERRED, UNINSTALLED, AUTH_REQUIRED, QUOTA_EXHAUSTED ou BLOCKED.

Ordre de sélection par capability :
1. Plugin spécialisé **local et fiable** présent dans le dépôt cible (Doctor Atlas, Graphify, AI Context), ou MCP local disponible, dans la bonne version et avec permission.
2. Plugin MCP local offrant un service auto-hébergé réellement disponible.
3. Adaptateur local lisant un cache fiable et versionné, **sans faire passer une copie périmée pour une recherche à jour**.
4. Plugin distant autorisé, si Internet et crédits gratuits disponibles.
5. Fallback ciblé et vérifiable utilisant des outils natifs (Git, Python AST, docs installées). Ne jamais remplacer un test de Doctor par le jugement d'un LLM.
6. Si la donnée fiable manque : mettre la seule sous-tâche en OFFLINE_DEFERRED/BLOCKED, et continuer les autres lots.

Un agent n'active **que les MCP nécessaires à son rôle et sa tâche**. Trop de tools dans le contexte d'un modèle local réduit la qualité : exposer une sélection minimale via hub. Les serveurs MCP ne reçoivent aucun token GitHub ni clé d'un service tiers sans besoin exact.

## Disponibilité effective

| Capacité | En local et hors ligne | Conditions |
|---|---|---|
| AI Context / Graphify / Doctor | OUI | Python 3.13, dépendances installées et graphe exact SHA/worktree ; certains tests Doctor exigent Windows ou des données |
| Git, branches, worktrees, historique | OUI | Contrôle des chemins, aucune mutation de main |
| Fichiers + recherche + docs Python installées | OUI | Lecture limitée au clone autorisé |
| Ollama / agent code | OUI | Poids téléchargés et environnement prêt ; un seul modèle lourd à la fois |
| GitHub | Git local OUI ; API, PR, CI NON | Réseau stable + authentification externe |
| Context7 officiel lancé comme serveur MCP local | Serveur OUI, **catalogue vivant NON** | Auth/service Context7 requis pour les recherches actuelles ; documentation locale exportée/cache épinglé pour travail sans réseau |
| Firecrawl auto-hébergé | Service local possible ; **nouvelles pages Web NON** sans Internet | Docker + API + workers + Playwright + Redis + RabbitMQ + PostgreSQL ; gourmand en ressources |
| Firecrawl distant via MCP local | Client local possible ; collecte distante NON sans Internet | Compte/clé, crédit restant, autorisation réseau |
| Extraction Web légère locale (Playwright/HTML) | Code local OUI ; nouvelles pages NON sans réseau | Alternative pour tâches simples en ligne, jamais présenter comme Firecrawl complet |

## Stratégie spécifique machine utilisateur

Ryzen 7 5800X / 32 Go RAM / RTX 3060 Ti 8 Go VRAM, priorité à la qualité :
- LLM 24–30B quantifié peut déborder sur la RAM système ; mesurer RAM, VRAM et contexte réel. Aucun modèle précis n'est supposé installé ni rapide.
- Le hub MCP et SQLite restent légers ; ne pas lancer une grappe Docker Firecrawl complète par défaut pendant une inférence lourde.
- **Firecrawl local complet** = profil installation facultatif (phase ultérieure), arrêté lorsque le modèle demande trop de mémoire. **Firecrawl client MCP local**, cache local, et extraction légère sont les valeurs par défaut.
- Téléchargements initiaux et mises à jour demandent un réseau et peuvent peser lourd sur un partage mobile ; afficher taille, version, espace disponible, hash et possibilité de reprendre, sans téléchargement automatique géant.

## Automatisation de l'installation
L'installateur final doit détecter Python, Git, runtimes nécessaires, Ollama, OpenCode, MCP SDK/clients, Atlas Doctor/Graphify/AI Context, navigateur d'extraction et état des authentifications. Installer uniquement des paquets vérifiés/épinglés depuis des sources autorisées. Sauvegarder profils, versions, cache et secrets dans un répertoire utilisateur hors des deux dépôts Git. Générer une configuration OpenCode avec **serveurs MCP locaux** en fonction de la version détectée ; ne pas recopier aveuglément une config obsolète.

L'UI devra afficher un onglet **Plugins** : nom, fonction, origine officielle, version, empreinte, réseau requis, droits (lecture, édition, mutations externes), crédit/quotas, date du cache, santé et rôle autorisé. Boutons Tester, Réparer, Installer, Désactiver ; les installations/mises à jour système requièrent un accord explicite.

## Politique de confiance et essais avant autonomie
- Pas de `npx -y` dynamique à chaque mission : installation initiale contrôlée et version figée.
- Serveurs MCP locaux en mode read-only pour discovery/audit ; écritures exclusivement par exécuteur allowlist et branche dédiée.
- Les sorties externes sont traitées comme données non fiables (prompt injection), avec plafond de taille et source/version/date.
- Épreuves : 100 % hors ligne ; connexion intermittente ; mauvais cache ; quota 0 ; modèle saturant la RAM ; serveur MCP malveillant ; conflits Git ; validation Doctor en échec. Tout échec doit produire un statut clair, aucun faux PASS.
- Il n'y a **pas encore** de hub MCP ni d'installateur opérationnel dans cette PR. Ce document est un contrat de construction, pas une annonce de livraison.

Sources d'intégration à consulter au moment de l'installation :
- Firecrawl self-host : https://github.com/firecrawl/firecrawl/blob/main/SELF_HOST.md
- Context7 MCP : https://context7.com/docs/resources/developer
- OpenCode MCP : https://dev.opencode.ai/docs/fr/mcp-servers/
