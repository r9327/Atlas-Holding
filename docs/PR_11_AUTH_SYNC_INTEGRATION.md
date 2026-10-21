# PR 11 — Intégration autorisation, orchestration et synchronisation

Cette PR Draft vérifie ensemble les contrats développés séparément dans :

- PR #1 : `998382229aa0091b34e712b55e731198e8745939` ;
- PR #3 : `bf4eb6277c3de1bc2111f6d6920089d2fbcfb571` ;
- PR #7 : `9c5448cc6af2345acb17bdca5dc3fb3dfa7b16fb`.

## Flux intégré

1. Une décision issue du standing grant est sérialisée en reçu canonique.
2. La santé GitHub doit être `ONLINE`, récente et persistée sous le lease courant.
   Une sonde authentifiée fermée interroge uniquement `/user` dans le broker,
   ne restitue que `github:<login>` et exige deux observations concordantes.
   Une identité inattendue ou un rejet explicite bloque ; les erreurs réseau et
   limitations temporaires suivent le backoff SQLite borné sans fabriquer `ONLINE`.
3. L'état actuel de la PR est observé et lié au dépôt, numéro, branche et SHA exacts.
4. Le gate produit une intention bornée par le reçu, le compte et la policy attendue.
   Le grant GitHub complet est conservé comme BLOB avec son empreinte dans le
   schéma SQLite v5 ; il est réauthentifié à chaque reçu exact-SHA et une
   révocation désactive atomiquement les scopes locaux et GitHub.
5. Le journal SQLite écrit `PENDING` avant l'appel privilégié.
6. L'opération est réobservée après mutation avant de devenir `SUCCEEDED`.
7. Une issue réseau ambiguë reste `PENDING` et impose une inspection ; elle n'est jamais rejouée.
8. L'état observé avant mutation est persisté atomiquement avec son empreinte SHA-256.
9. Après un délai minimal, la récupération réobserve GitHub sans muter : elle clôture
   un effet exact déjà visible. Seuls Draft/Ready strictement inchangés libèrent un
   retry borné ; CI et merge restent en inspection tant que leur effet est ambigu.
10. Le worker runtime `push` résout un unique worktree issu de dépendances déjà
    certifiées, émet un reçu frais et ne marque la mission `DONE` que si le journal
    contient l'effet distant `SUCCEEDED` au même dépôt, branche, SHA et idempotency key.
    Une attente réseau ne consomme pas le budget, une divergence bloque et une issue
    ambiguë place la mission en récupération sans second push.
11. Le bootstrap installé accepte toujours le schéma v1 local-only. Son schéma
    v2 exige en bloc l'identité GitHub, le SHA de policy, la commande du broker,
    tous ses pins de fichiers, son spool absolu et son timeout. Il refuse les
    packages Python partiellement épinglés, revérifie les empreintes au chargement
    puis avant chaque IPC, active le rôle `push` et alimente sa santé via le broker.
12. L'observation Git distante et le push exact-SHA traversent aussi ce même IPC.
    Le broker reconstruit son propre `GitWorktrees` depuis sa configuration trusted,
    résout l'unique `source_id`, refuse force/protected/dirty/divergent et conserve
    le credential Git hors du daemon principal.
13. Le Control Center dispose d'une projection transactionnelle réelle de SQLite
    vers son schéma d'affichage borné. Une mission avant commit conserve un SHA
    nul ; une mission `DONE` sans preuve durable correspondante devient
    `DONE_UNVERIFIED` à l'écran. Les détails d'événements ne traversent pas cette
    frontière afin de ne pas exposer un secret accidentellement journalisé.

## Nouveau canal Control Center réel (21 octobre 2026)

- `holding/dashboard_ipc.py` : serveur TCP **bouclé sur 127.0.0.1 uniquement**, autorisation HMAC-SHA256 sur requêtes et réponses, nonce aléatoire à chaque requête avec anti-replay borné, messages binaires plafonnés à 2 Mio, délais courts et quatre clients concurrents au maximum. Aucune désérialisation pickle, aucun endpoint de mutation, commande shell ou accès SQLite depuis le client.
- `HoldingService.enable_dashboard_ipc(secret=..., engine_id=...)` : serveur en lecture seule activable par le **bootstrap de confiance après démarrage**, maintenu indépendamment des fenêtres Qt puis arrêté avant fermeture du moteur. Le secret est fourni par le superviseur, pas par une roadmap ou le modèle.
- `holding/control_center_live.py` : première fenêtre PySide6 qui récupère les projections SQLite **authentifiées et validées** de façon asynchrone, sans bloquer l'interface ; en cas de perte de connexion, affiche explicitement l'état comme non actualisé. La fermeture de la fenêtre n'arrête pas le moteur.
- `tests/test_dashboard_ipc.py` et `tests/test_control_center_live.py` : vrais échanges TCP/SQLite, mauvais secret et moteur, réponse frauduleuse, rejeu, limites de taille, disponibilité après fermeture et déconnexion UI.

**Ce canal n'est pas encore le setup Windows final :** son port et son secret doivent être provisionnés par le service d'installation avec ACL natives, identité de processus et stockage protégés. Aucun secret de démonstration ne doit être utilisé en production. Le lancement automatique du viewer, les commandes authentifiées Pause/Reprendre/Stop, les preuves graphiques et le cycle de vie complet de l'EXE restent à intégrer/tester. Il n'existe aucun mode de secours donnant directement SQLite à l'UI.

## Commandes locales authentifiées et états durables (21 octobre 2026)

- `DashboardControlClient` utilise un **secret distinct de la consultation** : HMAC portant sur l'action Pause/Reprendre/Stop, horodatage borné, nonce anti-rejeu mémorisé en quantité limitée et accusé de réception authentifié. Le serveur limite les connexions simultanées, ne rejoue **jamais** automatiquement une commande dont l'effet est ambigu et rejette toutes les autres opérations (notamment Reset Stop, GitHub et exécution de code).
- `HoldingService.enable_dashboard_ipc(..., control_secret=...)` n'active ce pouvoir **que si le bootstrap installé lui fournit explicitement cette deuxième clé**. Sans clé, le canal demeure strictement en lecture. Le secret de lecture ne peut ni déclencher une mutation, ni devenir la clé d'écriture.
- Les commandes sont traduites par le serveur trusted en `ControlPlane.pause/resume/request_stop` et donc journalisées par l'état SQLite. STOP ne peut pas être annulé par Resume : seul le chemin trusted prévu pour un nouveau démarrage peut réinitialiser ce verrou.
- Schéma de projection **v2** : les booléens durables `paused` et `stop_requested` sont observés dans la même transaction que les missions et journaux. L'ancien v1 reste consultable sans inventer une valeur ; les écrans ne rendent les boutons actifs qu'après réception d'un état v2 authentifié et lorsque la commande est légale.
- L'interface `LiveControlCenterWindow` transmet les commandes sur son exécuteur asynchrone, n'interrompt jamais le thread Qt, exige confirmation explicite avant STOP, et passe en lecture seule pendant une commande ou une déconnexion.
- Tests réels : `tests/test_dashboard_controls.py`, `tests/test_control_center_live.py`, `tests/test_control_center_state.py` et `tests/test_control_center_projection.py` vérifient la distinction des secrets, l'absence d'accès direct SQLite, les rejets/mutations, l'anti-rejeu, les transitions d'interface et l'ancien protocole.

**Gates de livraison toujours ouverts :** installation native de l'identité Windows/ACL, distribution protégée des deux secrets (jamais via ligne de commande de processus ni dans des logs), bootstrap/launch du viewer installé, scénarios de reboot et arrêt, E2E EXE sur machine propre, RAM process-tree et release certifiée. Un HMAC sur loopback n'isole pas des programmes malveillants lancés sous la même identité Windows.

## Point d'entrée autonome de la fenêtre (21 octobre 2026)

- `python -B -m holding.control_center_entry --port <port> --engine-id <identifiant>` est un point d'entrée Qt réel **réservé au lanceur installé de confiance**. Le lanceur doit transmettre exactement 64 octets sur son pipe privé `stdin` (clé de consultation puis clé de commandes, toutes deux indépendantes), ou 32 octets avec `--readonly`. EOF obligatoire pour borner la lecture.
- Les secrets ne sont **jamais acceptés dans argv, variables d'environnement, chemin de fichier, URL ou logs**. Une trame tronquée, des clés identiques, un terminal interactif ou un port invalide bloque avant toute ouverture de Qt. Le mode lecture seule ne fabrique jamais de capacité d'écriture.
- `tests/test_control_center_entry.py` couvre le parsing strict des capacités, l'absence de divulgation des secrets, le mode lecture seule et les erreurs pré-Qt.
- **Prochaine étape à ne pas confondre avec ce lot :** le setup natif doit démarrer les processus de confiance avec un pipe OS protégé, générer des clés aléatoires fraîches à chaque session, contrôler ACL et identité de l'utilisateur, leur distribuer les capacités, puis tester l'EXE sans Python préinstallé.

## Pont lanceur installé → service → GUI (21 octobre 2026)

- `holding/dashboard_session.py` établit une **session locale réelle** : vérification SHA-256 juste avant exécution de l'exécutable GUI installé (et d'un éventuel script explicitement épinglé pour les tests), lancement via `subprocess.Popen(shell=False, close_fds=True)` sans identifiant dans les arguments / variables d'environnement, clés aléatoires uniques par session, transmission par un pipe stdin anonyme et fermé immédiatement après l'envoi.
- `HoldingService.launch_dashboard()` lie le démarrage GUI au démon qui détient déjà son lease et son port IPC, sans interférer avec la boucle d'agents ; `HoldingService.close()` termine proprement l'enfant borné puis le serveur. Si l'exécutable a été altéré ou l'envoi échoue, la session est bloquée et l'écouteur est fermé ; aucune fausse preuve de lancement.
- `Installation.load()` accepte maintenant le **schéma v3 opt-in** avec objet `dashboard` strict : `executable`, `executable_sha256`, `script`, `script_sha256`, `readonly`. Le dépôt cible, les politiques et les clés GitHub ne proviennent jamais des roadmaps. Les schémas v1 local et v2 broker GitHub restent compatibles ; v3 peut désactiver GitHub explicitement sans désactiver le Control Center.
- `daemon_entry.main()` lance la fenêtre vérifiée après démarrage du moteur, uniquement dans une session utilisateur Windows interactive (Session 0 rejetée). Cela n'est pas un démarrage de bureau depuis un service Windows Session 0.
- `tests/test_dashboard_session.py` teste un vrai sous-processus Python autonome qui authentifie le serveur IPC et effectue Pause via la clé reçue sur stdin, le mode lecture seule, une substitution de script, un binaire altéré et le nettoyage à la fermeture. `tests/test_daemon_entry.py` teste le câblage du schéma v3 et l'exécution de l'enfant.

**Toujours interdit d'annoncer une release** : aucun compte Windows/service séparé et ACL native ne sont établis par le pont Python, les clés ne sont protégées qu'à l'intérieur de la session du lanceur, et le binaire Windows final de la GUI / l'installateur / parcours Windows vierge ne sont pas encore certifiés. Les scripts Python sont réservés aux harness/tests : le mode release doit livrer et épingler un véritable exécutable Windows signé ou explicitement non signé, puis valider son lancement interactif réel.

## Limites explicites

- Aucun token n'est fourni au coordinateur ou stocké dans SQLite.
- Le transport réseau privilégié possède des routes fermées pour Draft/Ready,
  certification et merge exact-SHA. Le worker refuse nativement un fichier token
  Windows qui n'appartient pas à son identité, conserve l'héritage DACL, passe
  par un reparse point ou autorise un principal autre que le compte courant,
  `SYSTEM` et `Administrators`. Ce contrôle porte sur le handle effectivement lu.
  Le worker doit encore être installé sous une identité Windows/ACL dédiée : le
  pont IPC borné, épinglé et sans héritage de secrets est implémenté, mais lancer
  les deux processus sous le même compte ne constituerait pas une isolation de
  secrets.
- Le push possède un coordinateur dédié : worktree propre au SHA exact, observation
  distante fraîche, preuve d'ascendance avant mutation, refspec borné sans force,
  journal `PENDING` préalable et récupération par réobservation sans replay aveugle.
- Un statut `SUCCEEDED` atteste l'effet de synchronisation observé, jamais la qualité produit.
- Un contexte absent (ancienne base), corrompu ou incohérent bloque la récupération.
- Cette PR ne rend pas les PR #1, #3 ou #7 prêtes à fusionner et ne remplace pas leur certification finale.
- Le routeur multi-rôle et le hook trusted `enable_git_push` ont des capacités
  dynamiques : un rôle indisponible n'est pas réclamé et ne bloque pas un autre
  rôle sain. Le schéma d'installation v2 les câble au broker épinglé ; le v1
  demeure volontairement local-only et ne peut pas muter GitHub.
- Le code ne peut pas prouver à lui seul l'identité Windows qui lance la commande
  broker. Une installation réelle doit encore exécuter ce broker sous l'identité
  et les ACL dédiées décrites ci-dessus. Le schéma v2 route désormais le credential
  API et le credential Git vers cette frontière, mais l'identité/ACL effective reste
  un prérequis de déploiement natif, pas une propriété prouvable par un test Python.
- La projection SQLite et le transport HMAC vers PySide6 sont réels ; l'installation
  des permissions et secrets natifs Windows de ce canal n'est pas encore finalisée.
  Aucune lecture directe de la base par l'UI n'est autorisée.

## Contrats GitHub vérifiés

- [Mutations GraphQL des pull requests](https://docs.github.com/en/enterprise-cloud@latest/graphql/reference/pulls)
  pour Ready/Draft.
- [Merge REST](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
  avec précondition `sha`.
- [Workflow dispatch REST](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
  limité au workflow de certification et à la branche autorisée.
