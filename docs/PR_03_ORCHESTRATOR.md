# PR 03 — Orchestrateur local offline-first (SPECIFICATION, NON IMPLEMENTEE)

**Objectif :** déposer une Roadmap V3 entière et lancer un moteur qui la découpe en DAG de tâches, exécute les lots prêts sans relances manuelles et persiste chaque étape.

## Architecture
- Package Python minimal `holding/` avec API claire : `roadmap_import`, `scheduler`, `state_store`, `permissions`, `events`, `executor`. Un seul écrivain par worktree cible.
- Import Markdown/JSON/dossier Git, avec origine (repo/path/SHA/hash), identification de tous les critères d'acceptation et audit croisé de couverture ; ne pas confondre Road IA V3 et Road V3 produit.
- DAG vérifié sans cycle, tri AUTO/DELEGATED/REQUIRES_DECISION/BLOCKED/FORBIDDEN ; dépendances, priorité, périmètre, tests, état par lot ; ne pas considérer les anciennes cases cochées comme preuve.
- SQLite en transactions avec WAL, journal d'événements, candidats et commits, checkpoints sûrs, reprise sur crash, snapshots et liens de preuves exact-SHA ; migration versionnée.
- Exécuteur Git isolé : clone canonique, worktree et branche dédiée, validation du remote, refus main/master, dirty user worktree/force-push/destructive deletion, leases et commandes allowlist.
- Gestion des tentatives et des boucles sans progrès ; marquer BLOCKED et poursuivre les tâches indépendantes. Pause/reprise/stop gracieux via API locale, visible par l'UI.
- Aucun service externe obligatoire pour programmer, coder ou valider hors ligne.

## Tests de sortie
- [ ] Import exhaustif avec ambiguïtés et dépendances, 2 lots indépendants, un cycle refusé.
- [ ] Crash/reprise, panne GPU, redémarrage Windows, concurrence et rollback sans corruption ni double travail.
- [ ] Mission sans preuve ne peut passer DONE, données persistantes conformes après plusieurs redémarrages.
- [ ] Refus de main/master, d'un dossier sale et des commandes non autorisées.
- [ ] Contrats d'événements consommables par l'interface #8 et le moteur d'agents #4.

## Dépendances
#1 avant intégration ; #4/#5/#6/#7/#8 utilisent cette API. Pas de CI finale pendant les commits intermédiaires.

## Profil de modèle et intégrité persistante
- [ ] SQLite conserve version, digest, quantification, contexte **mesuré**, ressources matérielles, provenance du modèle et preuve du benchmark local sans stocker de secrets ; la définition de la sélection est `docs/LLM_AUTO_SELECTION.md` de #1.
- [ ] Une mission indique le modèle réellement utilisé à chaque rôle et la possibilité de reprendre/rejouer la vérification après redémarrage ou changement de version.
- [ ] Aucun changement automatique de modèle au milieu d'un checkpoint Git ou d'une écriture atomique ; transition et rollback journalisés.

## Entrée utilisateur unique et roadmaps changeantes

La seule action récurrente de l'utilisateur est **d'envoyer une direction de roadmap**. Le CEO prend les décisions de découpage, priorité, affectation, tests et reprise, sans demander de confirmation par lot. Les opérations GitHub dans le mandat initial sont automatiques ; aucun agent ne peut changer seul ses permissions.

- [ ] Import de roadmap avec identité/source/hash/version immuable ; nouvelle directive pendant l'exécution = diff sémantique, DAG recalculé à checkpoint sûr, tâches existantes préservées et dépendances réévaluées.
- [ ] Nouveaux objectifs, suppressions et contradictions expliqués dans un journal d'arbitrage technique ; aucun abandon silencieux d'un lot ou de ses preuves.
- [ ] Critères de sortie automatiquement couverts par une matrice `exigence → lot → tests → SHA/preuve` ; vérificateur indépendant de couverture, pas seulement une auto-déclaration du CEO.
- [ ] Clôture quand tous les lots sont prouvés `DONE` et aucune vérification bloquante ne reste : rapport final, arrêt de l'ordonnanceur, pas de génération infinie de nouvelles tâches.
- [ ] En cas d'unique décision produit indécidable, enregistrer `REQUIRES_DECISION` mais continuer les autres lots sans sollicitation répétitive.
- [ ] Test de replanification avec 3 lots dépendants et 2 lots déjà fusionnés ; aucune double PR, suppression dangereuse ou perte du journal après reboot.
