# PR 08 — Application Windows Control Center (SPECIFICATION, NON IMPLEMENTEE)

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

Le premier lancement affiche **« Atlas choisit la meilleure configuration IA pour votre PC »**, avec choix implicite `Qualité maximale compatible` et détails optionnels, plutôt qu'un sélecteur brut de noms de modèles. Le bouton **« Installer ma configuration recommandée »** doit afficher l'espace occupé, les gros téléchargements et leurs autorisations avant téléchargement. L'utilisateur peut reporter sans entrer dans la configuration avancée.

Après installation : cartes **« IA de développement : prête / en cours de test / indisponible »** et **« IA de vérification : prête / facultative »** ; les modèles peuvent être identiques par défaut. Afficher la justification réelle (tests passés, limites de RAM/VRAM, date, éventuellement lenteur), le volume sur le disque et un bouton **« Réévaluer automatiquement »**. Les modèles/tags/quantifications sont dans « Détails avancés ».

- [ ] Test UI : un débutant configure sans identifier ni nommer de modèle.
- [ ] Aucun faux statut « prêt » pendant téléchargement ou benchmark.
- [ ] Affichage correct : partage mobile, téléchargement reporté, modèle installé, modèle trop volumineux, fallback et CPU offload.
- [ ] Les décisions de modèle et leurs preuves sont récupérées de #4, pas simulées par l'UI.
