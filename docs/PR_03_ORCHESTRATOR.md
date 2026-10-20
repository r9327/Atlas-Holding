# PR 03 — Orchestrateur local offline-first (IMPLÉMENTATION EN COURS, NON CERTIFIÉE)
## État réel de l'implémentation (20/10/2026)

La spécification ci-dessous reste le contrat de sortie. Les modules Python existent sur cette PR Draft et ne doivent **pas** être considérés certifiés avant l'exécution du lot complet de tests locaux et Windows.

- **Codé** : `holding/autonomy.py` (DAG itératif), `state_store.py` (SQLite/WAL, grants, checkpoints), `roadmap_import.py` (JSON et Markdown strict via `markdown_roadmap.py`), `roadmap_replan.py` (ajouts et modifications de missions READY, journal d'arbitrage, refus de reprise d'anciennes versions), `coverage.py` et `closure.py` (exigence → mission → SHA/proof et rapport de clôture).
- **Codé** : `git_worktree.py`, `git_journal.py`, `executor.py`, `code_worker.py`, `process_agent.py`, `engine.py`, `service.py`, `recovery.py`, `resource_guard.py` et `windows_job.py`. Un agent propose une correction, le superviseur valide ses chemins, écrit et commit sur la branche isolée.
- **Codé** : `qa_scoped.py`, `qa_dispatch.py`, `qa_repair.py`, `qa_inbox.py`, `proofs.py` : QA locale consultative, FAIL signé réparé sous budget et avec contrôle du lease, PASS final exclusivement GitHub signé. Seule une preuve HMAC sous clé séparée peut marquer DONE.
- **Contrôle disponible** : `python -m holding.daemon_cli --db <db> import|replan|coverage|closure|status|pause|resume|stop` ; `ControlPlane` offre les mêmes vues aux futurs écrans.
- **Tests ajoutés mais PAS encore exécutés dans l'environnement de cette conversation** : `test_markdown_roadmap.py`, `test_roadmap_replan.py`, `test_replan_cli.py`, `test_coverage.py`, `test_closure.py`, `test_local_agent_repair.py`, ainsi que la suite locale préexistante. Les changements ne valent aucune preuve de réussite.

**Bloquant avant READY** : essai du dépôt complet avec Python 3.13, vérification comportementale du système Windows réel (ACL et compte indépendant, reboot, GPU/VRAM, RAM process tree), génération des preuves de certification dans la #7, orchestration de rôles non-code par la #4 et réception du Control Center #8. L'import de prose libre et la conversion par CEO IA ne sont pas implémentés par le parseur Markdown strict.



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

## Gouvernance RAM pendant les sessions de codage IA

**Exigence utilisateur : éviter les pics importants pendant que les IA codent.** Budget mesuré pour **tout l'arbre des processus permanents et auxiliaires de Holding**, hors processus d'inférence des modèles IA (ex. Ollama) et hors exécution explicite d'audits lourds Doctor/Graphify/benchmarks, qui ont des budgets séparés.

- [ ] Cible au repos / supervision : **≤ 250 Mo** RAM ; régime normal pendant le codage : **250–350 Mo** ; **seuil de non-régression 400 Mo** pour Holding et ses auxiliaires ordinaires. **Aucun pic de 750 Mo** masqué comme transitoire normal.
- [ ] Le moteur garde un nombre borné de workers, buffers, historique chargé, processus et caches ; fermeture/libération après lot. SQLite et événements doivent être consommés en pagination / streaming plutôt qu'en mémoire totale.
- [ ] Lors de l'activité IA ordinaire, inclure dans le compteur les processus Holding, l'UI si ouverte, IPC, plugins légers, Git, superviseurs et workers non-IA. Ne pas exclure arbitrairement les sous-processus au prétexte qu'ils travaillent pour les agents.
- [ ] Doctor/Graphify **lourds** et leurs sous-processus peuvent dépasser ces seuils **uniquement dans une fenêtre d'audit identifiée**, avec type, horodatages, propriétaire, mémoire maximale, fin / nettoyage tracés ; un pic de codage ne peut pas être reclassé a posteriori comme audit.
- [ ] Pas de scans Graphify, de chargement HTML WebEngine ou d'indexation exhaustive en arrière-plan sans nécessité ; déclencher, borner et libérer explicitement.
- [ ] En cas de dépassement en régime ordinaire : capturer la cause par processus, éviter les boucles/fuites, libérer les caches et suspendre le lancement de nouveaux travaux si nécessaire, **sans tuer brutalement une écriture Git, une transaction SQLite ou la session du modèle**. Ne jamais masquer la mesure.

**Preuves attendues** : benchmark Windows en cours de vraies missions IA, UI ouverte et fermée, longue durée (plusieurs heures), contrôle du RSS/private bytes de l'arbre complet et de la croissance après retour au repos. Graphiques séparés : « Holding pendant codage » et « audits lourds ». Un chiffre sans mesure ne vaut pas PASS.

## Pilotage adaptatif qualité / temps (décision utilisateur du 20/10/2026 — À IMPLÉMENTER)

**Politique par défaut : `BALANCED_VERIFIED`** — meilleur compromis qualité/temps, jamais la vitesse au prix d'une régression, d'une sécurité affaiblie ou d'un faux PASS. Le CEO ajuste l'effort selon complexité, risque et progression mesurée, sans micro-validation utilisateur.

- [ ] Évaluer la complexité, le périmètre, le risque et les dépendances avant chaque lot ; ne déclencher que les rôles et outils utiles. Petits correctifs : tests ciblés ; opérations critiques/multi-modules : revue indépendante et validation renforcée.
- [ ] Fixer un **budget d'observation initial** par tâche (repères, **pas des délais d'arrêt aveugles**) : correctif simple 2–5 min ; modification classique 10–20 min ; fonctionnalité complexe 30–60 min ; grosse PR sur plusieurs lots aussi longtemps que des progrès vérifiables justifient l'effort. Calibrer sur benchmarks réels du PC et des modèles, pas sur intuition.
- [ ] Suivre séparément durée de codage, tests ciblés, attente externe et certification finale ; ne jamais écourter Doctor/CI obligatoire pour afficher artificiellement une tâche « rapide ».
- [ ] Watchdog de **stagnation**, basé sur signaux réels : pas d'évolution pertinente du diff/tests, erreurs identiques répétées, recherches circulaires, relecture sans résultat et absence de nouveau diagnostic. Une durée longue avec progrès démontrable reste acceptable.
- [ ] En cas de stagnation : arrêter la répétition identique, réutiliser le contexte utile, diagnostiquer la cause, changer de stratégie / modèle / rôle / outil (Graphify/Doctor ciblés si justifiés), sous budgets bornés ; mettre BLOCKED avec raison et poursuivre les autres lots si aucune stratégie sûre ne progresse.
- [ ] Historiser en SQLite les décisions de routage et escalade, contexte épinglé par SHA, temps réel, coût de calcul, résultats de tests et raisons d'arrêt ; ne pas charger tout l'historique en RAM.
- [ ] Tests déterministes : une tâche simple ne déclenche pas tous les agents ; progrès réel au-delà du budget indicatif n'est pas interrompu brutalement ; stagnation répétée déclenche escalade bornée ; arrêt différé à checkpoint sûr ; aucun bypass de grant, SHA, QA ou CI.

**Important :** les composants actuels ne démontrent pas encore ces capacités. Pas de statut PASS sans tests réels.
