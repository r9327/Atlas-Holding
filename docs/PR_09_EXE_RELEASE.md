# PR 09 — EXE Windows installable et sortie finale (IMPLEMENTATION PARTIELLE)

## État technique

`holding/release_evidence.py` valide un manifeste exact-SHA sans construire ni
publier de release. Chaque hash **et taille** d'artefact, chaque preuve de gate
Windows et l'éventuelle preuve de signature doivent correspondre à une
observation externe du superviseur ; les déclarations auto-signées du manifeste
et les clés JSON dupliquées sont refusées.

L'installateur, sa construction reproductible, la machine Windows vierge et le
scénario E2E restent à réaliser. Aucun EXE ni PASS natif n'est revendiqué ici.

**Livrable :** utilisateur télécharge un fichier depuis GitHub Releases, double-clique pour installer Atlas Holding, choisit son disque, télécharge/configure les outils nécessaires en une fois, démarre le Control Center puis la roadmap.

## Gate bloquant ajouté : paire runtime native et sécurité Windows

Le contrôle `holding/release_evidence.py` refuse désormais de qualifier une
release sans **trois artefacts observés indépendamment** (noms, SHA-256, tailles) :
`AtlasHoldingSetup.exe`, `AtlasHoldingControlCenter.exe` et
`AtlasHoldingEngine.exe`. Un simple setup PyInstaller ou une fenêtre qui
s'ouvre sans moteur ne suffisent pas.

La publication exige également les SHA exacts des dépendances
`pr1` à `pr8`, plus `pr11` (intégration moteur/GUI) et `pr12`
(garde-fous architecture). Une liste fournie par erreur sans ces PR est
refusée avant toute qualification.

La validation exige aussi de véritables preuves externes `PASS` pour les
gates `windows-clean-install`, `native-runtime-ipc`,
`windows-acl-isolation`, `offline-after-provisioning` et
`e2e-roadmap`. La liste imposée ne peut plus omettre les gates IPC/ACL :
le validateur refuse même un appel lui fournissant moins d'exigences.

**Important :** les tests unitaires du validateur emploient des fichiers et
empreintes de démonstration uniquement pour vérifier sa logique ; ils ne
fournissent **aucune** preuve réelle de publication. Le run Windows
non-release de la PR #11 valide actuellement les exécutables congelés, mais
ne prouve pas une installation Windows neuve, l'isolation ACL ni une roadmap
multi-agent autonome. Cette PR demeure en Draft.

## Staging natif des trois exécutables (non-release)

Le nouveau module `holding/native_candidate_stage.py` prépare **sans écraser
les installations existantes** un dossier candidat contenant uniquement
`AtlasHoldingSetup.exe`, `AtlasHoldingControlCenter.exe`,
`AtlasHoldingEngine.exe` et `candidate-index.json`.

Le staging vérifie les fichiers sources réels par SHA-256/taille, les copie
dans un répertoire temporaire sur le volume de destination, **rehache** les
copies ainsi que les sources avant publication du dossier, puis écrit un
index de provenance lié au SHA du checkout et à toutes les dépendances requises.
Un verrou exclusif interdit les assemblages concurrents sur le même nom.
Chemins relatifs, liens symboliques, fichiers absents, index dupliqué,
binaire modifié et configuration trop vieille sont rejetés.

Le futur installateur peut appeler `verify_native_candidate(...,
expected_source_sha=..., expected_upstream_shas=...)` pour recontrôler les
binaires avant adoption. Le champ `release_ready` est **toujours false** :
cette étape n'installe rien, ne confère aucune signature et ne fabrique aucun
PASS d'installation. La véritable certification Windows, les ACL et les
scénarios IA/roadmap restent obligatoires.

## Implémentation
- Packaging reproductible sur runner Windows à partir de code versionné, versions figées, bundles/licences vérifiés, ressources Qt et vrais assets ; chemins sans installation Python préalable côté utilisateur.
- Créer `AtlasHoldingSetup.exe` + manifest/hashes SHA-256 + guide court, dans une Release GitHub. EXE non signé possible -> avertissement SmartScreen explicite, **pas** de fausse signature ; publication testée sur Windows propre.
- Séparer le petit setup du téléchargement potentiellement volumineux des poids IA, avec téléchargement reprenable, accord utilisateur, choix du stockage et mode hors ligne après provisionnement.
- Réunir les composants PR #1–#8 uniquement lorsque leurs contrats sont validés, sans intégrations simulées. Vérifier bootstrap, service, plugins, agents, Doctor, dashboard, GitHub sync et cycle de correction.
- Pipeline de CI sur les SHA candidats complets, smoke Windows sans service payant, test E2E sur clone Git factice, build et release contrôlés ; aucune CI FULL pendant développement/commit intermédiaire.
- Mise à jour, réparation, rollback, désinstallation, droits standard, chemins espaces/accents, sécurité des secrets, antivirus et dépendances réseau ; documentation de limitations et provenance.

## Scénario E2E bloquant
1. Setup depuis Windows vierge avec choix disque.
2. Installer config et modèles téléchargés / repris après panne de réseau.
3. Lancer l'app, importer une roadmap multi-lots, effectuer un changement local via agent autorisé.
4. Le relecteur trouve un défaut puis Doctor fait échouer un test ; correction locale et preuves.
5. Retrouver Internet, exécuter contre-expertise, synchroniser PR Draft puis certification sur SHA final.
6. En cas de CI rouge : correction et recertification, puis fusion uniquement si conditions et délégation remplies.
7. Reprise après crash à chaque phase et zéro perte/altération des sauvegardes Atlas.

## Sortie vérifiable
- [ ] EXE réellement construit, téléchargé depuis Release et testé sur Windows propre.
- [ ] Un seul installateur interactif et rapport d'installation ; aucun "tout installé" artificiel.
- [ ] Tests UI/E2E Windows + vérification SHA256 du bundle et de l'archive.
- [ ] Phase Certification/contrats Atlas jamais contournés, 0 € facturé par défaut.

## Dépendances
#1–#8. Cette PR est la **dernière** à certifier/fusionner. Aucune Release publiée avant preuve E2E.

## Gate E2E supplémentaire — choix IA automatique sans jargon
- [ ] Installer un Windows neuf puis préparer la Holding **sans demander de nom de modèle** : détecter matériel, recommander « priorité qualité », autorisation globale initiale de télécharger, provisionner, tester et affecter automatiquement les modèles. Forfait mobile illimité : pas de plafond Go imposé.
- [ ] Reprendre un modèle existant sans re-téléchargement ; exécuter un véritable benchmark d'intégration dont les résultats / preuves conduisent à la sélection (pas d'auto-notation par l'IA).
- [ ] Panne réseau pendant gros téléchargement, OOM du modèle choisi ou échec tool calling : fallback ou blocage explicite, aucun « tout prêt » fictif ; configuration survivant au redémarrage.
- [ ] Contrôler l'onglet IA/Plugins de l'UI : nom technique facultatif, statut du modèle réel, liens aux tests et mémoire utilisée ; Windows propre, 8 Go VRAM / 32 Go RAM test ou simulation documentée des limites.
- [ ] La règle canonique de décision est `docs/LLM_AUTO_SELECTION.md` depuis #1 ; interfaces #2/#3/#4/#8 réellement intégrées.

## Gate E2E : l'utilisateur ne fait que diriger les roadmaps

- [ ] Après **une seule** installation/connexion initiale et délégation GitHub bornée à Atlas, l'utilisateur soumet une roadmap entière. CEO choisit le plan, LLM, plugins, lot et tests ; aucun nom de modèle ni PR à choisir.
- [ ] Au moins trois lots terminés / PR successives entièrement traités **sans nouveau clic « continue », approbation par commit/PR, ou demande de certification** ; tous les SHA/test Doctor/CI restent exacts.
- [ ] Simuler conflit Git, problème de sources Web, erreur CI et décision indécidable : correction autonome dans le mandat ; sinon blocage ciblé visible et poursuite d'autres lots, aucune autorisation inventée.
- [ ] Connexion mobile illimitée, coupure Internet et espace disque faible : progression et reprise, mais pas de plafond de Go inventé.
- [ ] L'utilisateur ne joue pas pendant l'exécution de la Holding : surveillance matérielle générale plutôt que détection des jeux.

Voir `docs/USER_AUTONOMY_PROFILE.md` en PR #1. Gates non implémentés.

## Gate E2E : changement de directive et clôture autonome

- [ ] Pendant que deux agents se passent le relais sur une mission, l'utilisateur dépose une mise à jour de la **ligne directrice** ; la Holding termine le checkpoint courant, conserve les preuves, reconstruit les dépendances et poursuit sans intervention technique.
- [ ] La matrice `exigence → lot → test → SHA / PR` permet de prouver l'absence d'objectif oublié ; le rapport final correspond à la version de roadmap réellement active.
- [ ] À 100 % **réel** : tous lots requis certifiés, aucune dette externe bloquante, aucun conflit Git / CI non résolu ; génération du rapport final et arrêt normal de la boucle sans tâche inventée.
- [ ] Si une opération humaine réellement non délégable subsiste, le rapport indique `PARTIEL / BLOQUÉ` et les raisons ; jamais « terminé » mensonger.
- [ ] Redémarrer l'EXE après clôture ne rejoue pas les anciennes PR ; nouvelle roadmap ou modification déclenche uniquement le différentiel nécessaire.

Se référer à `ROADMAP.md` de la PR #1 ; le test porte sur un vrai exécutable Windows, pas sur une maquette.

## Répétition native multi-PR des vrais EXE (21 octobre 2026)

Le workflow `.github/workflows/native_packaging_rehearsal.yml` assemble, sur un runner Windows, les sources setup, moteur et Control Center du `main` certifié au SHA `fcbabe0da38c22860e724f719d88ddfb1ea3742e`. Il compile les deux vrais exécutables, mesure leurs SHA-256, les embarque dans `AtlasHoldingSetup.exe` puis lance le self-test natif du setup et celui des deux composants. Les entrées sont épinglées à ce SHA immuable, leurs checkouts vérifiés et aucun artefact n'est publié comme release.

Le label `ci:package-native` déclenche ce test séparé. Cette épreuve vérifie le **câblage d'emballage et l'extraction réelle**, mais **pas** l'installation complète sur un PC vierge, l'ACL native, le lancement des agents ou une roadmap autonome. La PR reste Draft et les gates de sortie demeurent obligatoires.

**Extension de la répétition :** après avoir produit le setup avec les vrais EXE, le runner exécute la fonction de staging du `main` certifié sur un chemin Windows contenant des espaces et des accents, puis lance les deux vrais EXE depuis leur dossier de version. Il teste l'absence de copie avant autorisation, la réutilisation de fichiers conformes et le refus d'écraser un moteur corrompu. Ce contrôle teste le lancement *depuis le disque choisi*, sans faire croire à une inscription de service, un raccourci, une ACL validée ou un parcours IA autonome.

## Assemblage Windows trois EXE sur SHAs distincts (non-release)

Le workflow `.github/workflows/native_three_binary_assembly.yml` réunit les
composants exacts et préalablement testés du `main` certifié sur un même
**runner Windows natif**. Il compile la paire Qt/moteur autonome,
vérifie l'autotest de chacun, exige un vrai échange IPC signé **entre les
deux exécutables compilés**, puis construit `AtlasHoldingSetup.exe` avec
leurs octets et leurs SHA-256 embarqués. Le `--self-test` du setup doit
revérifier les ressources extraites, sans installation de Python chez
l'utilisateur.

L'assemblage relève de nouveau les trois empreintes depuis les fichiers
réellement copiés, et écrit `cross-pr-nonrelease-evidence.json` comprenant
les SHA des deux checkouts du `main` certifié, tailles/empreintes observées et
`release_ready: false`. L'entrée de test du manifeste des dépendances est
**explicitement non téléchargeable et fictive** : ce workflow ne simule
pas une machine Windows vierge, une IA productive, un démarrage depuis
l'installateur ou la sécurité ACL de l'app installée. Il ne publie rien
dans GitHub Releases et nettoie les fichiers temporaires.

Le même runner compile aussi l'agent natif, l'embarque dans le setup, applique
de vraies ACL Windows à une installation jetable sur un chemin avec espaces et
accents, inscrit la configuration moteur v3, vérifie les pins Git/modèle et
exécute les sondes de santé du moteur installé. Le reçu conserve séparément
`native_acl_enrollment_smoke: PASS` et `installed_agent_health_smoke: PASS`,
sans convertir ces smokes jetables en certification de machine vierge.

Le manifeste embarqué n'est plus une fixture : le runner télécharge les ZIP
officiels MinGit et OpenCode, vérifie leurs SHA-256 éditeurs, dérive le
manifeste portable v3 depuis ces octets et confirme la disponibilité exacte
d'`Ollama.Ollama@0.32.8` dans WinGet. Le bundle testé est conservé trois jours
comme artefact GitHub `NONRELEASE-AtlasHolding-Windows-Candidate-*`, avec le
SHA-256 de l'archive émis par GitHub Actions. Il reste explicitement distinct
d'une GitHub Release et sert à obtenir la future preuve sur machine vierge.
Un second job, sur une nouvelle VM Windows 2025, retélécharge cet artefact,
revérifie les trois empreintes et tailles, supprime l'environnement Python du
processus puis exécute `AtlasHoldingSetup.exe --self-test`. Il lance ensuite le
même moteur de provisionnement que le bouton d'autorisation du wizard, avec
des chemins neufs contenant espaces et accents : téléchargement des dépendances
officielles épinglées, contrôles natifs, ACL, copie rehachée du Control Center,
du moteur et de l'agent. Le mode d'acceptation exige une ligne de commande
fermée et produit par création exclusive un reçu `NONRELEASE_FRESH_WINDOWS_PROVISIONING`.
Les deux reçus conservent `windows_clean_install_certified: false` : cette
contre-épreuve prouve une installation réelle automatisée depuis le binaire
téléchargé, pas les clics du wizard, le redémarrage Windows, le modèle final ni
la roadmap complète.

Ce test est volontairement séparé de la certification finale : réaliser
l'installation interactive depuis l'artefact publié sur une machine Windows
vierge et exécuter une roadmap complète restent des gates bloquants.

Le label `ci:live-ollama` déclenche aussi, sur le HEAD exact de cette PR, trois
inférences successives avec un vrai Ollama Windows épinglé et trois commits Git
jetables distincts. Cette preuve lie modèle, digest, agent EXE et reçus, mais
conserve `Doctor: NOT_EXECUTED`, `GitHub push: NOT_PERMITTED` et
`release_ready: false` : elle valide la chaîne d'inférence native, pas la
roadmap Atlas autonome.

L'assemblage exécute également le véritable `AtlasHoldingAgent.exe` sur une
roadmap locale jetable de trois lots dépendants. Une autorité QA séparée de
fixture signe un FAIL exact-SHA sur le deuxième lot ; le moteur reprend le même
worktree, produit un nouveau commit corrigé, consomme ensuite trois PASS signés
et ne clôt la couverture que lorsque chaque critère est lié à son SHA. Le reçu
`native-three-lot-roadmap.json` est conservé dans l'artefact candidat. Il porte
explicitement `target: DISPOSABLE_LOCAL_GIT_NOT_REAL_ATLAS`,
`canonical_doctor_status: NOT_EXECUTED`, `github_push_status: NOT_PERMITTED` et
`release_ready: false` : cette épreuve valide l'ordonnancement, la réparation et
la clôture persistante, mais pas trois PR GitHub ni le Doctor canonique Atlas.



## Accès au chat R&D depuis le binaire Windows gelé (candidat nonrelease)

Le candidat Windows embarque désormais l’interface PySide6 `ProjectChatWindow` dans `AtlasHoldingControlCenter.exe`, via **un mode d’exécution séparé**, sans joindre les capacités privées de lecture/commande du tableau de bord. La vérification `--project-chat-self-test` instancie les véritables widgets R&D depuis le binaire gelé ; l’assemblage natif échoue si ce mode ou ses dépendances sont absents.

**Parcours utilisateur sans commande :** après une installation consentie, un modèle Ollama réellement épinglé et le lancement du moteur, le setup propose le bouton **« Ouvrir le chat R&D »**. Ce bouton vérifie l'identité et l'ACL des exécutables, la configuration de modèle installée et la base SQLite, puis démarre **un autre processus** `AtlasHoldingControlCenter.exe --project-chat` avec `stdin` fermé. Aucun secret du tableau de bord n'est transféré. Un démarrage de fenêtre ne prouve pas la santé de l'agent ou le succès d'un projet.

Pour diagnostic, le même mode peut s'ouvrir directement en ligne de commande depuis le **chemin réel installé** :

```powershell
& "C:\\Atlas Holding\\runtime\\<version>\\AtlasHoldingControlCenter.exe" --project-chat --chat-db "C:\\Atlas Holding\\data\\engine.sqlite" --chat-repo "r9327/Dof-Atlas" --chat-model-config "C:\\Atlas Holding\\agents\\AtlasHoldingModel.json"
```

Les exemples de chemins sont illustratifs ; le bouton se sert des emplacements versionnés et du modèle **déjà choisis** dans l'installateur. Il n'invente ni modèle ni permission de chat. Un raccourci autonome hors installateur, le redémarrage Windows, la réparation/restauration, la mesure de RAM et le parcours complet à modèle réel restent à certifier avant une release.


## Audit de provenance du GUI gelé — correction de l'assemblage natif

Un smoke test natif exact-SHA a **échoué** sur le commit `38252a606b88269f038e200850f15966f7af001f` (run [40841605027](https://github.com/r9327/Atlas-Holding/actions/runs/40841605027)) : l'interface compilée provenait uniquement de `main@fcbabe0da38c22860e724f719d88ddfb1ea3742e`, avant l'ajout du mode R&D de cette PR, donc le véritable EXE retournait 2 à `--project-chat-self-test`.

**Correctif `1d058050e4ca` :** l'assemblage construit désormais séparément le moteur, l'adaptateur et le runner d'acceptation depuis le `main` certifié ; le **Control Center depuis le candidat #9 exact-SHA** ; et le setup à partir du même candidat avec les deux EXE déjà hachés. Le reçu contient l'origine explicite `source_shas.release_candidate_viewer`, en plus de `certified_main_runtime`. Le test R&D est exécuté sur **les octets de l'exécutable réellement embarqué**. L'ancien PASS d'assemblage ne validait pas les changements UI de cette PR.

Cette correction ne vaut pas à elle seule PASS : reconstruire puis revalider intégralement sur le nouveau SHA avant toute promotion. Le parcours utilisateur sans paramètres, les permissions à modèle réel et la publication restent bloqués.


## Preuve R&D après installation fraîche (candidate nonrelease)

La validation native du candidat prend désormais en charge le **fichier `AtlasHoldingControlCenter.exe` effectivement installé**, et non seulement les octets gelés dans le répertoire de build. Sur le runner Windows distinct, sans Python transmis par le setup, elle : (1) installe les vrais fichiers de la candidate depuis l'artefact GitHub Actions, (2) recalcule l'identité du viewer depuis les empreintes reçues, (3) vérifie sa provenance explicite sur le SHA exact du candidat et les octets SHA-256 installés, (4) exécute `--project-chat-self-test` en environnement Qt hors écran sans modèle Ollama, sans capacité IPC, puis (5) inscrit `installed_frozen_rd_chat: PASS` **uniquement** si le processus se termine avec succès. Ce contrôle est ajouté par `338b88c3060c`.

Il reste une **preuve nonrelease** de présence et d'initialisation Qt du chat installé. Il ne certifie pas un nouveau projet R&D, le choix automatique d'un modèle, un redémarrage Windows réel, le contrôle RAM complet ou un installateur signé. Les résultats d'une ancienne CI ne sont pas transférables au nouveau commit.


## Audit de provenance du moteur installé — 29 octobre 2026

Le lot de reprise Git/SQLite a révélé une autre lacune de packaging : le moteur `AtlasHoldingEngine.exe` était jusqu'ici construit sur `main@fcbabe0da38c22860e724f719d88ddfb1ea3742e`, tandis que la correction `holding/recovery.py` n'existait **que sur le candidat PR #9**. Une suite Python verte ne garantit pas que le correctif soit effectivement distribué.

- `fee4401f98e0` : moteur **et** Control Center gelés depuis le candidat exact-SHA. L'adaptateur `AtlasHoldingAgent.exe` et l'acceptance runner restent construits depuis `main` épinglé. Les hashes des binaires finaux sont toujours mesurés sur les octets réels.
- `a84097f3d53c` : le reçu doit porter `release_candidate_viewer == release_candidate_engine == head_sha` et `certified_main_agent == fcbabe0da38c22860e724f719d88ddfb1ea3742e`. Le runner Windows rejette toute autre provenance lors de la réinstallation.

**Portée :** les tests Windows natifs doivent de nouveau être exécutés avant de déclarer le nouveau moteur réellement testé. Aucune ancienne preuve d'assemblage n'est transférée au nouveau SHA. La sécurité des permissions et le gate final de release restent inchangés ; le reboot machine, le modèle réel, l'autonomie complète et la RAM ne sont pas certifiés par ces deux commits.


## Avancement preuve modèle réel et comptabilité RAM Windows (NONRELEASE)

**Inférence locale réelle :** [run GitHub Actions 40851812109](https://github.com/r9327/Atlas-Holding/actions/runs/40851812109), `ci:live-ollama`, **PASS** sur le candidat `f7576674402a9dc3884273cb22228fad76f7f518`. Le runner Windows installe un Ollama épinglé, utilise le modèle CPU de test `qwen2.5-coder:1.5b`, exécute trois essais d'agent compilé dans des dépôts Git jetables avec trois commits distincts. Il ne s'agit **pas** d'une preuve de qualité d'un modèle de production : ni vrai Doctor Dofus Atlas, ni push GitHub, ni mesure RAM/VRAM du modèle, ni autonomie end-to-end. Cette preuve se rapporte à ce SHA précédent, et ne certifie pas à elle seule les nouveaux commits de mesure.

**Comptabilité de la RAM de Holding :** nouveau module `holding/windows_tree_ram.py` comptant réellement les processus Windows sous les racines PID du moteur et du viewer, leurs sous-processus PyInstaller/IPC ainsi que les outils lancés, par snapshots Win32 natifs. Les noms d'exécutables d'inférence Ollama connus sont exclus du **budget Holding**, mais enregistrés séparément ; les autres enfants, même inconnus, restent imputés à Holding. Une erreur d'accès à une mémoire de descendant est bloquante, pas assimilée à zéro. `tests/native_windows_runtime_smoke.py` mesure la période où les deux vrais EXE communiquent avec une capacité authentifiée, et refuse l'absence d'échantillons des deux racines. Le reçu `native-frozen-ipc-ram.json`, signé par sa somme SHA256 dans les preuves nonrelease, est vérifié avant publication de l'artefact GitHub Actions.

**Limites explicitement non couvertes :** fenêtre courte d'IPC plutôt qu'une longue mission de codage IA, échantillonnage de working set susceptible de rater des pics entre échantillons, compte des pages partagées potentiellement en double, pas de certification du plafond **400 Mo en codage ordinaire**, pas de bench cumulatif heures/Doctor/Graphify, pas de test machine Windows de l'utilisateur. Les seuils de production de `QUALITY_RELEASE_GATES.md` restent **à cocher uniquement après une vraie mission**. Le nouveau contrôle constitue l'instrumentation de preuve et une première mesure, pas une approbation RAM.
