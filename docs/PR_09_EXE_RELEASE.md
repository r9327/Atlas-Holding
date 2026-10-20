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
