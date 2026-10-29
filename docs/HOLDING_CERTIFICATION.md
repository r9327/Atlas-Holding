# Atlas Holding — niveaux de preuve et certification

Processus: branche isolée, PR Draft, développement et tests ciblés, revue de chaque commit et du diff final, passage Ready, contrôles sur le SHA final, fusion uniquement selon les protections applicables. Toute évolution du SHA invalide les preuves précédentes.

## Niveaux distincts

- LOCAL_VERIFIED : commandes réellement exécutées, SHA, environnement, portée et résultats archivables. Un test non exécuté est UNKNOWN et un test bloqué est BLOCKED.
- PR_CI_VERIFIED : réussite de `Holding Certification / Required` au SHA exact. Le workflow `holding-pr-certification.yml` agrège la politique IA, la suite Python Linux/Windows et le garde architectural. Un job skipped ne vaut pas PASS.
- NATIVE_WINDOWS_VERIFIED : véritables essais Windows nécessaires au lot, installation, UI, Ollama, sécurité, reprise réseau, ressources et crash avec mesures et logs.
- RELEASE_CERTIFIED : toutes les conditions pertinentes de docs/QUALITY_RELEASE_GATES.md et docs/WINDOWS_LIVE_ACCEPTANCE.md sont prouvées sur le candidat exact, avec approbations requises. Une PR verte ne suffit pas.

## Sources de vérité

Les politiques des plugins sont dans config/tool_registry.json, holding/tool_registry.py et holding/mcp_policy.py. Le contrôle tools.ai_policy_check atteste leur cohérence statique; il ne teste pas la disponibilité du hub ni la santé des serveurs.

Le garde architecture actuel est tools/architecture_guard.py. Atlas Doctor, AI Context et Graphify de Dof-Atlas doivent être exécutés selon les contrats et le SHA de Dof-Atlas pour une mission sur ce dépôt. Holding n'en possède pas de duplicata certifiant.

## Rapport de revue

Conserver SHA base/candidat, fichiers, risque, tests et logs réellement observés, contrôles non exécutés, vérification de chaque commit, revue de l'intégration et risques résiduels. Utiliser REVIEW OK, IMPROVEMENTS ADVISED ou CHANGES REQUIRED uniquement pour la logique, jamais pour signaler une certification.

Les paramètres GitHub de protection de branche et les checks requis sont externes au dépôt. La protection de `main` doit exiger `Holding Certification / Required`; un fichier workflow seul ne rend pas ce check obligatoire. L'agrégateur ne certifie ni l'installateur natif, ni une release, ni une preuve privilégiée.
