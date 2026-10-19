# Atlas Holding — Conditions opérationnelles avant autonomie (NON IMPLÉMENTÉ)

Ce document complète [ROADMAP.md](../ROADMAP.md) et [AGENTS.md](../AGENTS.md). Les exigences ci-dessous sont **obligatoires pour le premier EXE autonome** ; leur présence écrite ne prouve aucune implémentation ni aucun test.

## 1. Réseau, coupures et quotas de services (PR #2, #5 et #7)
- Utiliser le réseau mobile illimité normalement : aucun profil « forfait limité » ni plafond quotidien imposés. Afficher les transferts et vérifier la stabilité de connexion.
- Aucun plafond artificiel de données ; montrer les tailles et progressions, surveiller le disque, les limites réelles des services (quota Firecrawl / GitHub) et respecter zéro dépense par défaut.
- L'assistant d'installation obtient une autorisation **globale initiale** pour la configuration locale recommandée et indique la taille des téléchargements ; ensuite téléchargements prévus automatiques avec reprise, sans nouveau clic à chaque fichier. Ne pas lancer de service payant sans nouvelle autorisation.
- Après reconnexion, privilégier les *petites* vérifications nécessaires ; Git fetch/API avec délais et retry bornés, pas de polling CI à la seconde, pas de nouveau benchmark Web global.
- Quota épuisé ou réseau instable : `DEFERRED`, pas `PASS`; poursuivre les tâches indépendantes hors ligne.

## 2. Sauvegardes / reprise après panne (PR #2, #3 et #9)
- Base SQLite transactionnelle, migrations de schéma versionnées avec sauvegarde, checks d'intégrité à froid et récupération explicite d'une migration interrompue.
- Snapshots **avant** mise à jour, changement de modèle, grosse mutation Git et changement de configuration sensible ; retour arrière reproductible. Conserver provenance des commits, refs, tâches, états des plugins, cache et preuves de QA.
- Sauvegardes locales séparées des fichiers installés et du clone Atlas ; ne jamais enregistrer les secrets en clair dans une archive ni dans Git. Politique de rétention, espace disque maximal et purge uniquement des fichiers que Holding a créés.
- Tests : kill process entre checkpoint/commit/SQLite, reboot Windows au milieu d'un téléchargement, mauvais disque, corruption volontaire d'une copie de DB, rollback sur dernière version saine.
- Une sauvegarde non restaurée en test ne suffit pas : **exercer réellement la restauration** sur un environnement jetable.

## 3. Protection du PC et cohabitation avec DOFUS (PR #3, #4 et #8)
- Mesurer mémoire disponible, CPU/GPU, disque et ressources des modèles ; protection automatique à seuil critique et reprise à checkpoint sûr. **Aucune détection de jeu obligatoire** puisque la Holding n'est pas lancée pendant les sessions de jeu de l'utilisateur.
- Journaliser charges élevées ; si ressource critique, mettre le codeur en pause **au prochain checkpoint sûr** puis reprendre automatiquement quand le matériel redevient disponible. Ne jamais interrompre une écriture Git atomique.
- Surveillance thermique uniquement si un capteur fiable et permissionné est accessible : sinon indiquer « mesure indisponible », sans inventer des températures.
- Le processus moteur doit fonctionner séparément de la fenêtre Qt, avec watchdog sur process crash/hang, détection des sorties et contrôle anti-redémarrages infinis.
- Les agents ne doivent pas lancer de tests très lourds ni plusieurs LLM lourds en même temps sans gestion mémoire explicite.

## 4. Gouvernance des agents / autorisations durables (PR #1, #3, #5 et #7)
- **Immutabilité de la politique** : le modèle ne peut pas éditer ses permissions, l'exécuteur, la politique de secrets, les garde-fous de sécurité, tests Doctor/CI ni le mécanisme de certification pour déclarer son propre travail valide. Toute modification de ces composants exige une voie de maintenance distincte et une revue humaine.
- Les plugins Firecrawl/Context7/MCP locaux sont une frontière de confiance : sorties considérées comme données, non comme ordres. Tool manifest épinglé, vérifié et permissions minimales.
- Un écran initial permet d'accorder pour **le dépôt exact et la durée choisie** : lire, coder sur worktrees, commits locaux ; séparément push, PR Draft, lancement certification, fusion des PR vertes. Chaque grant révocable et auditée.
- Le modèle n'a jamais le token GitHub en texte libre ; le programme déterministe vérifie opérations Git/CI, branche, SHA, règles du dépôt cible et autorisation avant l'appel.
- Bloquer automatiquement paiements, modification de secrets/permissions, contournement de vérifications, publication de données privées, suppression de sauvegardes et extension non autorisée du périmètre.
- Ne pas confondre « IA peut gérer la roadmap entière » et « IA peut choisir de réécrire ses propres règles ».

## 5. Mesure de qualité sans auto-évaluation (PR #4 et #6)
- Benchmark sur tâches de programmation **maintenues par tests non exposés aux modèles** : correction de bug, conformité au contrat, respect des plugins, refus d'instructions extérieures malveillantes et lecture d'un code existant.
- Distinguer qualité locale du modèle, qualité de l'orchestrateur, qualité des tests Doctor et tests Windows réels ; publier les résultats sans prétendre à une certification produit.
- Score sur preuves non générées par l'agent lui-même : assertions, tests Doctor, logs d'exécution, diff analysable, SHA, revues de risques. Ne pas faire confiance aux chiffres « 100 % » inventés par un modèle.
- Regression benchmark après mise à jour de modèle, taille de contexte, quantification ou politique ; rollback si performance qualité baisse.
- Évaluer capacité à s'arrêter en cas d'incertitude et à signaler `BLOCKED`, pas seulement taux de patchs.

## 6. Distribution, mises à jour et sécurité Windows (PR #2 et #9)
- Manifest de version verrouillé (Python, Ollama, OpenCode, MCP, outils natifs, modèles) ; installer depuis URLs approuvées avec vérification de hash et contrôle de provenance. Ne pas accepter `curl|sh`/scripts copiés d'une page Web.
- Le setup EXE doit être indépendant de Python sur le PC ; tous les prérequis doivent pouvoir être détectés, réutilisés ou installés séparément, sans casser DOFUS Atlas.
- Mettre à jour **uniquement** hors mission active ou à checkpoint sûr. Avant mise à jour, sauvegarder état, vérifier l'intégrité et assurer la réversibilité.
- Publier SHA-256 des binaires, artefacts de build, notes de version et limites de signature Windows. Si non signé, le signaler honnêtement (SmartScreen possible).
- Nettoyage/désinstallation uniquement des répertoires gérés par Holding, avec choix explicite concernant modèles partagés, bases et cache. Aucun effacement automatique des clones utilisateur.

## 7. Politique par défaut en un clic
- Mode `QUALITY_FIRST`, outils/plug-ins locaux prioritaires, téléchargements lourds avec accord, zéro service payant et aucune API externe imposée.
- Autoriser sans intervention : lecture, planification, travail local sur worktrees, tests ciblés Doctor, revue, commits sur branche dédiée, sauvegardes non destructives.
- Proposer dès l'installation le consentement durable « Autopilot complet sur Atlas » couvrant push, PR Draft, Ready/certification et fusion verte sur le dépôt exact ; aucune question répétée par PR si autorisé. Les modifications de configuration sensible restent exclues.
- Contrôle global visible : STOP sûr, état des sauvegardes, consommation de données et raison de tout blocage.

## Gates supplémentaires de premier lancement
- [ ] Connexion mobile illimitée, modèle 15+ Go : après consentement initial global, téléchargement lancé automatiquement et résilient aux coupures ; tailles affichées, aucun cap artificiel.
- [ ] Installation interrompue et relancée : reprise ou rollback correct, aucune deuxième installation d'un composant valide.
- [ ] Crash SQLite et reboot au milieu d'une tâche : état exact restauré, aucune mutation dupliquée, test de restauration depuis sauvegarde.
- [ ] Saturation RAM/VRAM ou disque durant inférence : pause sûre et reprise, sans détection de jeu imposée.
- [ ] Injection de prompt via MCP malveillant tentant de modifier Doctor/permissions : refus technique côté exécuteur.
- [ ] LLM hallucinant un PASS sans test : refus ; benchmark caché détecte comportement fautif.
- [ ] Modèle installé ou plugin mis à jour puis incompatible : retour au dernier état fonctionnel.
- [ ] Workflow autorisé sur dépôt exact seulement : aucun push/fusion sur dépôt non autorisé.
- [ ] Windows propre : install, mode offline après provisioning, contrôles UI réels et EXE hashé.

**Ces exigences n'autorisent pas la Holding à s'installer ou à travailler sur le PC de l'utilisateur tant qu'elles ne sont pas codées et testées.**

## Directive utilisateur sans micro-gestion

La seule action récurrente attendue est la **ligne directrice des roadmaps**. Les décisions de tri, de modèle, de plugins, d'implémentation, de tests, de correction, de CI et de fusion (dans une délégation initiale consentie) appartiennent à la Holding. Les autorisations initiales GitHub/UAC et les actions réellement sensibles restent des exceptions de sécurité, pas un mécanisme d'approbation continue. Voir `docs/USER_AUTONOMY_PROFILE.md`.
