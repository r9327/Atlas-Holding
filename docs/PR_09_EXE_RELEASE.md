# PR 09 — EXE Windows installable et sortie finale (SPECIFICATION, NON IMPLEMENTEE)

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
