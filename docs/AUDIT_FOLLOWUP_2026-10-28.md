# Suite de l'audit Holding — 28 octobre 2026

Objectif produit : une roadmap suffit dans les autorisations durables. Outils et
modèles sont choisis automatiquement, les blocages réels sont précis et limités
à leur lot, les tâches indépendantes continuent. Aucune auto-certification.

L'audit a couvert les 37 PR #1–#37 et inventorié 1 521 commits accessibles.
Les contrôles historiques statiques ne constituent pas une revue manuelle
exhaustive ni une certification Windows. Les PR fermées restent l'historique
des fonctionnalités ; leurs défauts actuels sont corrigés par de nouveaux commits.

## Corrections préparées

| Constat | Correction et propriétaire | Validation locale |
|---|---|---|
| H14 — restauration SQLite vide avec chemin contenant # | Cette PR : URI échappée avec Path.as_uri, ouverture mode=ro | Régression rouge avant correction, verte après ; données et fichiers créés vérifiés |
| H01 — mutation GitHub Ready/Draft invalide | Cette PR : correctif déjà présent dans #9 et assertion de la requête complète | Tests transport et consommateurs ; aucun appel distant simulé en preuve réelle |
| H13 — tables R&D hors autorité du schéma | #32 : définitions centralisées dans holding/schema.py et apply_schema dans les transactions existantes | SQL conservé, test de schéma et suites R&D |
| H10 — références de gouvernance absentes | #35 : contrats existants, consommateur CI réel et description exacte des déclenchements | Contrôleur et tests de gouvernance |

La preuve feature-scoped existante inclut les tests des correctifs et suit les
nouveaux SHA lorsque la PR est Ready et porte ci:feature-scoped. Les commits
en Draft ne lancent pas la suite automatiquement ; l'ajout explicite du label
reste possible. Aucun nouveau moteur de certification n'est introduit.

## Lots restants — statut explicite

| Lot / constats | Changement à réaliser | Critère de sortie |
|---|---|---|
| Certification — H02/H09 | Consolider #33/#34, conserver une autorité et un agrégateur, configurer les protections main | Tous les contrôles requis réussis sur le HEAD courant ; preuves natives séparées |
| Développeur — H03/H04 | Charger les skills selon la tâche ; contexte par symboles et lectures complémentaires ; boucle d'outils bornée | Correction multi-fichiers impliquant un fichier >12 Kio, outils réellement appelés |
| Maintenance Holding — H07 | Voie distincte pour proposer du code et des tests de Holding avec revue indépendante | Le codeur ne peut ni modifier ses permissions ni fabriquer ses preuves |
| Modèles et rôles — H05 | Raccorder sélection benchmarkée, architecte/auditeur selon le besoin et transitions PR | Objectif → patch → revue → correction → PR → certification sans relance utilisateur |
| Plugins — H06 | Finir les clients MCP, les appels Serena/Semgrep existants et leur consommation | Disponibilité, permissions, appels réels, quotas et fallback vérifiés |
| Parcours produit — H08/H17 | Chat/projets intégrés à l'EXE, saisie longue/import, config résolue automatiquement, STOP direct au checkpoint | Parcours sans paramètres CLI ni choix manuel de modèle ou de plugin |
| Updates et apprentissage — H15 | Raccorder launcher/coordinateur ; commencer par les paquets complets, puis patches vérifiés ; benchmarks authentifiés | Installation/reprise/rollback réels ; aucune promotion sur auto-note |
| Réactivité — H16 | Déporter le hashage des releases hors du thread Qt, observation bornée et annulable | UI réactive sur un vrai paquet Windows, résultats datés et sans jobs concurrents |
| R&D — H12 | Décomposer les grands plans et sourcer les recherches lorsque nécessaire | Aucune collecte, expérience physique ou recherche générale revendiquée sans preuve |
| Release — H11 | Construire et tester le candidat composé final | Windows réel, installation/mise à jour, panne/reprise, RAM de tout l'arbre et vraie mission |

## Ajouts open source retenus pour évaluation

- Développement/CI : Ruff, Hypothesis pour les séquences d'états, pip-audit et Gitleaks.
- Plugins : SDK MCP officiel et Inspector pour l'interopérabilité ; finir Serena
  et Semgrep avant d'ajouter d'autres intégrations.
- Acceptation : essai pywinauto sur l'EXE livré ; pytest-qt si les tests Qt
  existants nécessitent des capacités supplémentaires.
- Environnement reproductible : dépendances déclarées/verrouillées ; uv reste
  une option de développement, sans remplacer le provisioning Windows actuel.

Ces ajouts sont à évaluer, non installés par ce document. Pas de second
orchestrateur, de deuxième registre d'autorisations ni de mémoire graphe sans
gain démontré. Les modèles/services externes gardent leurs propres contraintes.

## État des preuves

Développement, tests locaux, CI au SHA exact, tests Windows réels et fusion sont
des états différents. Les corrections publiées dans une branche restent hors
main jusqu'à leur intégration. Les lots ci-dessus ne sont ni réalisés ni
certifiés par le seul fait d'être documentés.
