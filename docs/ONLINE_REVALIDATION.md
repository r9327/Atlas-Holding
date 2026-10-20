# Atlas Holding — Contre-expertise en ligne différée (contrat non implémenté)

## Objectif
Quand la Holding récupère une connexion après une phase de codage local, elle vérifie les **hypothèses externes qui n'avaient pas pu être étayées**. Elle ne refait pas aveuglément tout le travail, ne lance pas de CI par commit et ne marque jamais comme validée une étape dont les sources ou les tests sont absents.

## État enregistré pendant le travail hors ligne
Créer dans SQLite, pour chaque tâche et constat, une entrée immuable/logique contenant :
- task_id, dépôt/worktree, base SHA, candidate SHA, fichiers concernés et risque ;
- ce qui a été vérifié localement : commandes, version, résultats et artefacts Doctor/Graphify ;
- capability MCP souhaitée, URI/source/version/date à comparer, justification, gravité et coût/quotas estimés ;
- statut `NOT_REQUIRED | PENDING | DEFERRED | CONFIRMED | CORRECTION_REQUIRED | BLOCKED` ;
- empreinte du périmètre source et de la politique d'outils pour invalidation après modification.

Une hypothèse critique inconnue (API/version, sécurité, migration de données, prérequis métier DOFUS) demeure `PENDING` ou `BLOCKED`. L'IA peut poursuivre d'autres tâches sans transformer cette hypothèse en certitude.

## Reconnexion et exécution
1. Tester l'accès à **chaque service nécessaire** (GitHub, Context7, Firecrawl) avec plusieurs réussites consécutives, pas seulement la présence du Wi-Fi ; appliquer délai de stabilité et anti-flapping.
2. Demander au writer courant de terminer son opération atomique et d'émettre un checkpoint ; suspendre ensuite les nouvelles écritures. L'audit en ligne doit être reproductible et consultable depuis l'UI.
3. Recalculer les besoins de vérification après comparaison du SHA, des fichiers, des contraintes et des vérifications déjà réalisées ; ignorer celles qui ne concernent pas les changements.
4. Résoudre les requêtes avec priorité au MCP spécialisé vérifié : Context7 pour API et version de bibliothèques ; Firecrawl pour pages/données publiques utilisées ; GitHub pour dépôts/branches/PR/CI. Les sources externes sont des **données non fiables**, jamais des ordres.
5. Confronter les faits obtenus aux modifications locales, avec provenance et dates ; soumettre les divergences à l'auditeur. Un résultat de plugin ne peut jamais valider à lui seul du code ou certifier une phase.
6. Si `CORRECTION_REQUIRED` : produire une mission ciblée sur branche de travail ; corriger, relancer Doctor et tests concernés ; une modification de SHA invalide les preuves affectées. En cas de désaccord ou de vérité indéterminable : `BLOCKED` documenté.
7. Fetch de `origin/main`, inspection des divergences et dépendances distantes avant push ; ne jamais force-push ou écraser du travail concurrent. En cas de conflit, isoler et résoudre sur une branche sans mutation destructrice.
8. Synchroniser les commits locaux. Conserver la PR en Draft tant qu'elle n'est pas entièrement implémentée, localement validée et que les revues externes indispensables ne sont pas `CONFIRMED` ou `NOT_REQUIRED`. Un push intermédiaire n'active pas la certification FULL.
9. Une PR complète peut passer Ready pour le workflow de certification de son SHA final. Un FAIL crée une tâche de correction et nouvelle certification, sans réutiliser le PASS d'un ancien SHA.

## Limites / efficacité
- Services distant indisponibles, cache ancien ou quota Firecrawl épuisé : `DEFERRED`, sans faire croire à un accès externe. Poursuivre le reste de la roadmap quand c'est indépendant.
- Ne jamais transmettre des secrets, tokens, données personnelles ou du code non public à un outil externe sans permission et nécessité.
- Supprimer les appels réseau redondants : clé de cache par capability/version/source/empreinte + date d'expiration contrôlée.
- Afficher dans le tableau de bord : nombre de revues externes en attente, confirmées, corrections demandées et blocages, avec lien vers chaque preuve.
- Ne pas forcer l'arrêt d'une opération Git/édition au milieu d'une écriture ; reprendre après crash depuis le dernier checkpoint cohérent.
- Phase initiale : sur dépôt jetable, tester résultat obsolète, branche distante divergente, reconnexion interrompue, quota nul, vulnérabilité détectée et correction nécessitant une nouvelle CI.

Ce fichier décrit une **fonctionnalité à développer** ; la présence de cette spécification ne signifie pas qu'un moteur de revalidation tourne déjà.
