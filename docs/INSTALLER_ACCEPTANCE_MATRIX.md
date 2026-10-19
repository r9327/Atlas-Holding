# PR #2 — Matrice de détection, installation et premier lancement

**Spécification et critères de test**, non implémentés.

## Modes

`FIRST_INSTALL`, `ALREADY_READY`, `REPAIR`, `UPGRADE`, `OFFLINE_PREPROVISIONED`, `OFFLINE_MISSING_REQUIRED`.

Pour **chaque** dépendance : `NOT_FOUND` → installer ; `FOUND_VALID` → réutiliser ; `FOUND_INCOMPATIBLE` → proposer mise à niveau/réparation sans suppression destructrice ; `FOUND_BROKEN` → réparation contrôlée et healthcheck ; `AUTH_REQUIRED` / `NETWORK_REQUIRED` → affichage explicite, suite différée.

## Audit des composants

| Composant | Détection au-delà de « fichier existe » | Traitement |
|---|---|---|
| Windows x64 + matériel | OS/architecture, RAM, GPU/VRAM, CPU, espace libre et droits | Vérifier budget disque et mode modèle |
| Dossier Holding | Chemin réel, version, intégrité, config et données précédentes | Réutiliser ou mettre à jour atomiquement |
| Python | Version compatible et commande effective ou runtime embarqué dans l'EXE | Installer seulement si une dépendance l'exige |
| Git | `git --version`, accès aux worktrees et au dépôt canonique | Réutiliser/réparer installer |
| Ollama | Exécutable/version, API locale joignable, santé du serveur | Installer/démarrer/configurer sans exposition Internet |
| Modèle IA | Inventaire Ollama, tag/quantification/version, taille, disponibilité locale, test réponse | Ne pas retélécharger un modèle vérifié ; offre de téléchargement adaptée VRAM/RAM |
| OpenCode | Version, capacité d'appeler le serveur Ollama et de traiter une mission locale | Installer version compatible, config de tool-calling |
| Python deps GUI | `PySide6` et bibliothèques requises dans le bon environnement ; Qt plugins | Installation isolée, smoke fenêtre réelle |
| AI Context | Contrat et commande dans le clone Atlas, sortie lisible | Pas d'installation doublon, utiliser Atlas canonique |
| Graphify | Version épinglée, graphe valide pour SHA/worktree, génération explicite | Installer/réparer si requis par lot structurel |
| Doctor Atlas | Version depuis dépôt Atlas, commande et tests pertinents | Validation réelle plutôt que simple existence |
| MCP local | Liste réelle des serveurs, transport, healthcheck, permissions et versions | Installer clients/adaptateurs autorisés selon besoin |
| Context7/Firecrawl | Client MCP local, **service distant séparé**, cache/version, quotas/autorisation | Connexion différée ; ne pas exiger secrets pour premier lancement |
| GitHub | `gh`/API, remote `origin`, connexion, scopes, présence d'auth valide | Assistance connexion utilisateur sans écrire les tokens en Git |
| SQLite & réglages | Ouvrir base, schéma/migrations, transactions/sauvegarde | Migrer sans perte de missions existantes |

## Stratégie téléchargement / stockage

1. **Inventaire avant changement**. Afficher un écran « Déjà présent / À installer / À réparer / Facultatif », avec source, version, téléchargement approximatif et disque cible.
2. **Pas de doublons** : ne pas re-télécharger un modèle ni installer un deuxième Python si le runtime packagé suffit. Conserver les logiciels déjà valides.
3. **Téléchargements lourds explicites**, notamment modèle 24–30B quantifié : utilisateur autorise l'espace réseau mobile ; pause/reprise via API si possible, intégrité SHA avant adoption. Les outils petits peuvent être préparés dans une phase distincte.
4. **Aucun abonnement** ni carte de paiement obligatoire ; les fonctionnalités nécessitant un compte ou quota (GitHub/Firecrawl) sont déclarées facultatives tant qu'elles ne sont pas requises pour la tâche.
5. **Choix du disque** : séparer setup/binaire, modèles, caches Web/Docs, clones/worktrees et SQLite pour pouvoir déplacer les gros volumes sans casser les chemins.
6. **Source officielle + versions/hash épinglés** : bannir les installations `curl|sh` ou `iwr|iex` arbitraires depuis des pages récupérées par les IA.
7. **Reprise** : état transactionnel d'installation + checkpoints, checksum et rollback ; ne jamais corrompre un Ollama ou une DB existante.
8. **Healthchecks après chaque étape** : si échec, afficher cause/proposition de réparation et garder la fenêtre utilisable ; pas de faux message « tout est installé ».

## Tests Windows bloquants

- [ ] Machine vierge sans Python/Git/Ollama : premier lancement assistant avec choix disque, téléchargement contrôlé et lancement UI.
- [ ] Machine déjà prête : aucun téléchargement inutile, aucun outil réinstallé.
- [ ] Outils partiellement présents : versions compatibles conservées ; version cassée réparée sans écraser les réglages.
- [ ] Internet perdu pendant modèle : état persistant, reprise sans recommencer inutilement, pas de mode « prêt » avant healthcheck.
- [ ] Démarrage hors ligne avec préprovisionné : la Holding et ses MCP locaux fonctionnent sans accès externe.
- [ ] Démarrage hors ligne **sans** modèle : explication claire des éléments manquants et possibilité de réessayer après réseau.
- [ ] Espace insuffisant/VRAM 8 Go/RAM 32 Go : pas de choix de modèle irréaliste présenté comme « tient intégralement sur GPU ».
- [ ] Erreur permission administrative : demander UAC uniquement si requis ou choisir profil par utilisateur, sans contournement.
- [ ] Windows redémarré après installation : modèles, plugins, mémoire et interface retrouvés.
- [ ] Tests sur compte sans privilèges, chemins accents/espaces, et volumes distincts ; désinstallation respecte données utilisateur sauf accord.

## Critère de DONE

Le contrat de #2 n'est validé que lorsqu'un **vrai installateur** réalise ces contrôles sur Windows et que les scénarios ont des preuves. Les documents seuls ne sont jamais un test.
