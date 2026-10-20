# PR 08 — Application Windows Control Center (IMPLEMENTATION PARTIELLE)

## État technique

`holding/control_center_state.py` fournit une projection d'affichage immutable,
bornée et liée aux preuves exact-SHA. Les snapshots ambigus (clés JSON
dupliquées), futurs ou vieux de plus de 30 secondes sont refusés afin qu'un état
ancien ne soit jamais présenté comme « live ».

La fenêtre PySide6, l'IPC authentifié, le tray et les scénarios UI/HDPI restent
à implémenter et à mesurer sur Windows réel ; aucune validation native n'est
revendiquée par ce contrat de données.

**But :** à partir du Bureau Windows, voir en direct **ce que fait la Holding**, sans consulter des logs ou relancer "go" ; fermer/minimiser la fenêtre sans interrompre le moteur.

## UI / état réel
- Application PySide6 dédiée, indépendante de Dofus Atlas ; processus moteur distinct et IPC local authentifié. Interface jamais source de vérité : SQLite / événements de l'orchestrateur.
- Tableau de bord : roadmap totale (DONE réellement attesté), prochain lot, tâches bloquées, PR/SHAs/CI, agents et mission actuelle, heures et ressources.
- Agents : CEO, architecte, codeur, auditeur, Doctor/intégrateur ; chaque agent indique statut, objectif, modèle chargé, décisions et événements sourcés.
- Plugins : liste capabilities, version, local/online/cache/indisponible, origine, permissions, quotas/usage, date du cache, healthcheck ; installations/réparations autorisées explicitement.
- Réseau : OFFLINE / ONLINE_STABLE / SYNCING / CI_PENDING / ERROR, nombre de vérifications externes en attente et état de reprise après connexion.
- Journaux filtrables par tâche, plugin, code, QA et CI ; ouvrir diff/rapport GitHub local sans exposer les secrets.
- Boutons fonctionnels Démarrer / Pause sûre / Reprendre / Stop gracieux, configuration de disque et de modèle ; zones de risques et décisions nécessaires clairement visibles.
- Tray Windows + reprise au redémarrage seulement avec activation utilisateur. Pas d'interaction Qt sur thread moteur, pas de fuite au changement d'écran.

## Tests de sortie
- [ ] Captures Qt réelles normal/HDPI/fenêtre minimale, pas de boutons vides ou débordements.
- [ ] Scénarios de 50+ lots, 1000 logs, historique, affichage offline et reconnexion sans freeze UI.
- [ ] Crash de l'interface ne tue pas les tâches, reconnexion au processus moteur avec état exact.
- [ ] Les faux pourcentages et "PASS" non prouvés sont impossibles.
- [ ] Installation/erreur plugin et clic Pause/Stop fonctionnent réellement.

## Dépendances
#1, API #3 et événements #4/#5/#7 ; la fenêtre peut démarrer avec données de test sans déclarer la Holding autonome.

## Écran IA compréhensible, sans choix technique obligatoire

Le premier lancement affiche **« Atlas choisit la meilleure configuration IA pour votre PC »**, avec profil **par défaut `Meilleur compromis qualité / temps`** et détails optionnels (profil « Qualité maximale » disponible uniquement en option), plutôt qu'un sélecteur brut de noms de modèles. Le bouton **« Installer ma configuration recommandée »** doit afficher l'espace occupé, les gros téléchargements et leurs autorisations avant téléchargement. L'utilisateur peut reporter sans entrer dans la configuration avancée.

Après installation : cartes **« IA de développement : prête / en cours de test / indisponible »** et **« IA de vérification : prête / facultative »** ; les modèles peuvent être identiques par défaut. Afficher la justification réelle (tests passés, limites de RAM/VRAM, date, éventuellement lenteur), le volume sur le disque et un bouton **« Réévaluer automatiquement »**. Les modèles/tags/quantifications sont dans « Détails avancés ».

- [ ] Test UI : un débutant configure sans identifier ni nommer de modèle.
- [ ] Aucun faux statut « prêt » pendant téléchargement ou benchmark.
- [ ] Affichage correct : partage mobile, téléchargement reporté, modèle installé, modèle trop volumineux, fallback et CPU offload.
- [ ] Les décisions de modèle et leurs preuves sont récupérées de #4, pas simulées par l'UI.

## Consommation RAM et interface passive pendant le travail IA

- [ ] **RAM légère pendant le codage autonome** : Holding + Control Center + plugins et utilitaires ordinaires (hors processus d'inférence IA et hors audits lourds identifiés) visent **≤ 250 Mo au repos**, **250–350 Mo en fonctionnement**, et ne doivent pas dépasser **400 Mo en fonctionnement normal**. Aucun pic de 750 Mo attribuable à l'UI / Holding n'est acceptable comme « normal ».
- [ ] Vue de consommation **réelle** de l'arbre de processus complet (pas seulement PID de la fenêtre), avec mémoire actuelle, sommet mesuré, tendance/fuites, UI affichée ou fermée et état du moteur.
- [ ] **Graphify interactif chargé à la demande uniquement quand son onglet est consulté** ; fermer / détruire le rendu QtWebEngine et purger les caches de la vue lorsqu'on quitte l'onglet, sans supprimer les données du graphe sur disque.
- [ ] Vue plugins limitée aux métadonnées et healthchecks peu coûteux ; pas de démarrage de tous les plugins ni d'import massif pour afficher un tableau. Logs et événements paginés ou virtualisés, throttling des mises à jour et absence de rafraîchissement destructif.
- [ ] Audits lourds Doctor / Graphify exemptés du **plafond de codage normal** uniquement pendant leur exécution explicite et identifiée. Afficher séparément leur pic, durée et retour à la RAM de repos ; ne jamais dissimuler une consommation anormale de Holding sous l'étiquette « audit ».
- [ ] Tests Windows réels avec longues sessions et simulations de flux (50+ lots / 1000 logs) ; vérifier le **pic de l'arbre de processus**, l'absence de fuite entre ouvertures Graphify, et que la fermeture de l'UI ne gonfle pas ni n'interrompt le moteur.

Ce sont des **objectifs et gates futurs**, pas des consommations de l'EXE déjà mesurées.

## Visibilité de l'efficacité et de la stagnation (décision utilisateur du 20/10/2026 — À IMPLÉMENTER)

- [ ] Profil par défaut affiché : **Meilleur compromis qualité/temps**, sans sélection de modèle obligatoire ; variante « Qualité maximale » optionnelle et jamais au détriment des tests/sécurités.
- [ ] Pour chaque tâche, afficher temps écoulé, classe de difficulté, étape en cours (codage/tests/certification/attente), dernier progrès **démontré**, modèle/rôle choisi, tentatives et raison d'escalade ou de blocage. Pas de pourcentage ni de qualité « 9/10 » inventés.
- [ ] Historique compact : durée médiane sur tâches comparables, taux de succès validé, corrections et régressions, consommation mesurée, économies calculées uniquement à partir de mesures répétables.
- [ ] Statut explicite « progresse », « réévalue sa stratégie », « attente externe », « bloqué » : ne pas confondre une longue certification avec une IA en boucle.
- [ ] Rafraîchissement économe, données issues de SQLite/événements et jamais de métriques simulées en production ; tests d'UI et d'absence de fuite RAM sur longues sessions.

**Ces vues sont des critères de sortie futurs et non des fonctions actuellement disponibles dans l'EXE.**
