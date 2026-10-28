# Intake autonome d'une roadmap entière — contrat (non implémenté)

## Objectif
L'utilisateur peut fournir **une seule roadmap complète**, sans tri préalable. La Holding importe, identifie les objectifs, découpe en lots, ordonne par dépendances, applique les règles du dépôt cible et travaille sans demande de relance à chaque étape. Seules les décisions produit non déductibles et mutations à risque nécessitent une intervention.

La finalité d'un projet est fixée par son auteur. Le planificateur doit chercher comment la réaliser, sans veto moral subjectif sur un objectif légitime ou simplement atypique. Il distingue la faisabilité, les contraintes légales/sécurité, les permissions d'action et les capacités réellement disponibles. Un obstacle concret appelle une explication vérifiable, une alternative et la poursuite des lots indépendants.

Ne jamais supposer que ROAD IA V3 (issue tooling du dépôt Atlas) et ROAD V3 produit désignent le même document. Référence canonique de roadmap = URI/path choisi, repo, SHA/snapshot, empreinte, date, autorisation et périmètre.

## Objectif produit transmis aux agents

L'import de roadmap accepte maintenant un champ facultatif `project_goal` (texte UTF-8 non vide, au maximum 4096 octets), au niveau supérieur du JSON, ou `Project goal:` / `Objectif:` avant les missions dans le Markdown strict. Le moteur conserve une copie de cet objectif avec les critères de chaque mission ; le codeur le transmet au modèle local comme **donnée de contexte**, jamais comme permission. Pour un plan issu de `roadmap_contract`, le même champ facultatif doit être présent **à l'identique** dans le plan vérifié et dans les missions opérationnelles ; le gate refuse toute modification ou omission de l'objectif par l'agent. Les anciennes roadmaps restent valables sans ce champ. L'objectif ne modifie ni les règles du dépôt, ni les contrôles Doctor, ni les permissions. Les modifications d'un objectif sur des missions déjà démarrées nécessitent une nouvelle planification sans écraser leur historique. Le modèle ne doit pas interpréter cette direction comme une preuve ou une certification.

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

Un lot `BLOCKED` ou `REQUIRES_DECISION` ne rend **pas toute la roadmap inexécutable**. Le plan complet reste l'inventaire de référence ; l'import opérationnel ne peut inclure que les lots autorisés indépendants, sans dépendance directe ou transitive sur un lot restreint. Les lots exclus et leurs dépendants doivent rester visibles dans le suivi de la roadmap et nécessitent une décision ou une replanification avant exécution. La prévisualisation `verify-plan` expose `importable_lots` et `deferred_lots` sans accorder de permission ; l'affichage unifié de ces lots dans le Control Center reste à implémenter. L'importeur ne transforme jamais un lot bloqué en `READY`.

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
