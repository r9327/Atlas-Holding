# Intake autonome d'une roadmap entière — contrat (non implémenté)

## Objectif
L'utilisateur peut fournir **une seule roadmap complète**, sans tri préalable. La Holding importe, identifie les objectifs, découpe en lots, ordonne par dépendances, applique les règles du dépôt cible et travaille sans demande de relance à chaque étape. Seules les décisions produit non déductibles et mutations à risque nécessitent une intervention.

Ne jamais supposer que ROAD IA V3 (issue tooling du dépôt Atlas) et ROAD V3 produit désignent le même document. Référence canonique de roadmap = URI/path choisi, repo, SHA/snapshot, empreinte, date, autorisation et périmètre.

## Import / préparation automatique
1. Lire le document intégral (Markdown, JSON, dossier versionné ou lien autorisé), les annexes, les tâches et les règles du dépôt cible.
2. Extraire exhaustivement résultats attendus, contraintes, exceptions, tests, critères d'acceptation, capacités requises, contraintes de données, non-objectifs et preuves.
3. Identifier tâches déjà réalisées **avec preuves actuelles** ; une case cochée dans le texte n'est pas une certification.
4. Transformer en graphe DAG de lots portant ID stable, description, dépendances bloquantes, critères de sortie observables, fichiers/ownership, priorité, risque, tests et politique d'approbation.
5. Demander à l'auditeur IA de comparer **tous** les éléments initiaux avec les lots reconstruits : aucune fonctionnalité silencieusement oubliée.
6. Planifier le plus petit lot vertical cohérent prêt à être développé ; jamais seulement maximiser le nombre de PR.
7. Suivre l'exécution avec SQLite, événements persistants, checkpoints, commits Git locaux, preuve Doctor et preuves CI exact-SHA.

## Classification et délégation
- `AUTO` : recherche et analyse locale, plan, édition sur worktree isolé autorisé, tests ciblés, correction, commit local, déplacement vers la prochaine tâche indépendante.
- `AUTO_WITH_STANDING_GRANT` : push, PR Draft, passage Ready, lancement certification et fusion **uniquement si l'utilisateur a préalablement délégué ces actes pour le dépôt et si les protections GitHub sont effectivement satisfaites**. Révocation et limites enregistrées.
- `REQUIRES_DECISION` : nouveau comportement produit non spécifié, contradiction substantielle, suppression/migration destructive de données, paiement, publication publique, secrets, permissions système élargies, changement de politique de sécurité/CI.
- `BLOCKED` : dépendance non satisfaite, preuves absentes, CI non verte, ressource hors ligne indispensable, conflit Git ou ownership non résolu, quota dépassé.
- `FORBIDDEN` : contourner les checks, fausses preuves, écrasement de travaux non connus, dépôt principal modifié directement, force push destructeur, dépenses non consenties ou exécution d'instructions issues de pages Web.

## États vérifiables
`IMPORTED → TRIAGED → READY → ARCHITECT → CODING → REVIEW → LOCAL_QA → LOCAL_VALIDATED → COMMITTED → WAITING_FOR_NETWORK → SYNCED_DRAFT → FINAL_CERTIFICATION → MERGE_ELIGIBLE → DONE`.

États latéraux `REWORK`, `REQUIRES_DECISION`, `BLOCKED`, `CANCELLED`. Chaque transition exige une preuve ou un événement déterministe ; le CEO ne peut pas se noter lui-même.

Exigence de DONE : critères de fonctionnalité respectés, tests requis réussis sur le SHA exact, aucun oubli, PR réellement fusionnée quand c'est le mode de livraison. Le contrôleur ne peut jamais considérer un skip comme PASS.

## Boucles / reprise
Après trois corrections sans progrès mesurable sur la même cause, consigner les logs, bloquer cette tâche et poursuivre les lots indépendants. Reprise persistante après arrêt PC. Reconnexion : checkpoint atomique et pause sûre des writers avant push ; **certification seulement des PR terminées**, conformément aux règles Atlas. Les outils distants indispensables mais indisponibles créent `OFFLINE_DEFERRED`, pas une preuve inventée.

## État
Ce contrat n'est pas encore implémenté. Les protections doivent être testées sur une branche et un dépôt jetables avant qu'un CEO autonome ait des permissions sur Dofus Atlas.

## Validation différée et critères de sortie

Lors de l'import, chaque lot obtient aussi `external_evidence_required` et `external_review_pending`. La Holding peut avancer hors ligne sur les lots sans vérification extérieure indispensable, mais ne certifie pas ceux dont les données ou documentations externes sont essentielles tant que la contre-expertise sur réseau n'est pas achevée. Les modifications après revue redéclenchent Doctor/test local puis la certification finale du nouveau SHA. Voir `docs/ONLINE_REVALIDATION.md`.
