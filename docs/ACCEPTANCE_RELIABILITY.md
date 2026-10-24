# PR #2 — Épreuve réseau réel et modèles Ollama

Ce document définit **les conditions de réussite à atteindre avant Ready**. L'utilisateur a du réseau mobile illimité mais **très instable**, privilégie l'intégrité au temps de transfert. On ne doit jamais lui promettre qu'une reprise est garantie par tous les serveurs : la capacité de reprise dépend du protocole HTTP, du serveur et du downloader effectivement utilisé.

## Architecture à réaliser
- Installer / mettre à jour l'exécutable Holding via des artefacts HTTPS épinglés avec manifeste fiable **SHA-256 + taille + version** ; conserver `.part` sur toute erreur temporaire et ne publier le fichier final que vérifié.
- Pour un serveur Range : `206` et `Content-Range` valides, reprise à l'octet correct après coupure/redémarrage de Holding ; si réponse `200`, reprendre proprement depuis zéro **sans concaténer** ; s'il n'y a pas de support Range, l'UI doit le signaler.
- Retry avec backoff, timeout configurable et distinction échec temporaire / permanent (404, auth, checksum, espace disque), pas de boucle CPU serrée.
- Journal de transfert persistant : URL canonique autorisée, ID manifeste, taille, SHA, taille partielle, erreur, horodatage, retries et chemin cible. Compatible reprise Windows et changement du réseau.
- Mise en quarantaine et réparation des partiels corrompus (en laissant une preuve utile), verrou inter-processus pour éviter deux writers sur un même poids/fichier, protection contre manque de disque, restauration après crash.
- HTTP TLS vérifié, refus d'une redirection vers une URL non fiable / non TLS. Les URL, signatures et hashes doivent venir de l'installateur **de confiance**, jamais directement d'une page MCP ou d'un texte généré.
- Affichage précis : « téléchargé / total », débit moyen, phases téléchargement / vérification / installation, statut « réseau coupé, reprise prévue », jamais « prêt » avant test logiciel.

## Ollama : flux séparé obligatoire
- Le downloader Python de `holding/downloads.py` ne gère **pas** les blobs Ollama. Utiliser une version officielle et épinglée d'Ollama, piloter `ollama pull` via son API/CLI, vérifier `ollama show` / manifeste et une inférence locale réelle **après** téléchargement.
- Les mécanismes de reprise Ollama diffèrent selon version et chemin interne de téléchargement. Des mainteneurs rapportent que les fragments de certains pulls peuvent être purgés au redémarrage du serveur ; documenter et tester l'effet de la configuration `OLLAMA_NOPRUNE` dans le **processus serveur Ollama**, pas uniquement le CLI client. Ne jamais considérer cette variable comme une garantie sans essai.
- En cas de coupure longue : protéger la partie déjà présente quand Ollama le permet, réessayer après reconnectivité, récupérer les logs, et **ne jamais effacer le modèle fonctionnel précédent** avant validation du nouveau.
- Si reprise native réellement impossible pour un modèle/version/source, le déclarer explicitement, prévoir alternative fiable (artefact GGUF officiel + import vérifié si compatible et légal) plutôt que simuler un redémarrage réussi.

## Tests bloquants
- [ ] Serveur HTTP local coupé au hasard au milieu d'un téléchargement de plusieurs Go **simulés** ; les fichiers partiels restent exploitables.
- [ ] 25 interruptions successives, trois redémarrages du processus et deux redémarrages Windows, hash final identique et aucune corruption.
- [ ] Serveur Range, sans Range, `Content-Range` invalide, 404/429/503, hash faux, URL non HTTPS, expiration d'URL, manque de disque, antivirus verrouillant l'EXE et stockage partagé.
- [ ] Téléchargement réel d'un modèle choisi par le sélecteur : coupure Wi-Fi/mobile, retour réseau, **arrêt puis redémarrage du service Ollama**, reprise ou fallback clairement expliqué.
- [ ] Le modèle précédemment installé reste utilisable pendant échec de téléchargement du candidat.
- [ ] Windows propre et réseau faible : installeur réellement utilisable, et non succès uniquement sur serveur local rapide.

## Statut initial
- Composant réel `holding/downloads.py` + 8 tests ciblés locaux (sur machine de développement), mais **pas** encore de pilotage Ollama, d'installation Windows ou d'EXE.
- Il faut tester sous Windows et injecter des pannes avant certification. Ne pas lancer une FULL à chaque commit.

Sources à vérifier selon la version d'Ollama au moment du développement :
- https://github.com/ollama/ollama/issues/13034
- https://github.com/ollama/ollama/issues/15320
