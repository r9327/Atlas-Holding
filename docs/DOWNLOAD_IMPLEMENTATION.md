# Premiers composants réels — téléchargeur d'artefacts épinglés

Le module `holding/downloads.py` est un **composant implémenté**, distinct des cahiers des charges :
- téléchargement HTTP(S) à adresse HTTPS (HTTP seulement en boucle locale pour tests) ;
- reprise par `Range` HTTP après coupure / reprise du programme si le serveur accepte les plages ;
- fichiers partiels `.part` conservés sur échec ; réutilisation d'un téléchargement déjà vérifié ;
- vérification de la taille **et du SHA-256 de manifeste fiable** avant publication par `os.replace` ;
- refus de `Content-Range` incorrect, redirection HTTPS→HTTP, fichiers finaux corrompus et liens symboliques ;
- backoff sur erreurs réseau temporaires, avec limite par invocation ; la reprise lors d'une autre session est possible.

## Tests
`python -m unittest discover -s tests -v` depuis la racine : huit cas avec serveur HTTP local : téléchargement normal, panne et reprise, serveur sans Range, partiel complet hors ligne, intégrité, plages incohérentes, symlink, URL et hash invalides.

## Limites à ne PAS masquer
1. Composant **non branché à un assistant d'installation**, pas un EXE et pas encore testé sur Windows.
2. Un serveur HTTP sans support des plages reprend depuis zéro ; jamais prétendre le contraire.
3. Un hash attendu doit provenir d'un manifeste de confiance, pas du contenu téléchargé lui-même.
4. Les poids Ollama se téléchargent avec l'API/CLI Ollama, **pas** ce module. La reprise native des blobs doit être testée **en coupant et redémarrant réellement le serveur Ollama sur Windows**. La conservation des fragments peut dépendre de la version/chemin et de OLLAMA_NOPRUNE.
5. Restent à faire : téléchargement d'un vrai binaire épinglé, mode Windows, UI/progress bar, annulation, boucle de reprise intersession, tests avec coupures répétées, contrôle disque, verrouillage multi-processus, antivirus et intégration au setup #2.

Le lot #2 reste Draft jusqu'à installation réelle, tests Windows et vérification des cas de panne.