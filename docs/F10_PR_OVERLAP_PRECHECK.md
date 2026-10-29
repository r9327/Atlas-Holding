# F10 — Croisement des PR ouvertes et des fichiers locaux

Ce lot consomme les trois relevés existants : les modules du commit local
inspectés par F10, les PR ouvertes lues via le courtier GitHub, et la liste
des chemins modifiés à une tête de PR vérifiée deux fois.

Pour préserver la rapidité, Holding ne consulte **qu'une PR au maximum
par mission** : celle dont le nom de branche recoupe lexicalement des mots
des critères ou des modules candidats. Les branches sans correspondance ne
donnent lieu à aucun nouvel appel GitHub. Cette sélection est heuristique :
un nom non pertinent peut malgré tout cacher un doublon.

L'analyse signale des collisions exactes de chemins, non une duplication
fonctionnelle prouvée. `EXACT_PATH_OVERLAP` invite le modèle à examiner les
modifications existantes avant de créer un module concurrent.
`NO_MATCH_IN_PARTIAL_PR_SCAN` et `NO_EXACT_PATH_MATCH` **ne** signifient
pas « aucune fonctionnalité équivalente ».

Le courtier privilégié garde le jeton GitHub. Le codeur ne reçoit que
les chemins autorisés et les SHA ; l'adaptateur Ollama valide l'identité
de la PR sélectionnée, les noms de fichiers, la cohérence des chemins
communs et le fait qu'aucun relevé n'autorise une omission de travail
ou un PASS automatique.

Sans courtier, ou sans PR lexicalement pertinente, l'observation reste
`NOT_CHECKED`. Si la lecture du SHA exact échoue, elle reste
`UNAVAILABLE`. Aucun réseau n'est requis pour les missions locales et
une API GitHub indisponible ne produit jamais un inventaire vide certifié.

Tests : sélection déterministe limitée, chemins identiques, absence de
correspondance sans preuve négative, changement de SHA refusé, contexte
Ollama non fiable et intégration dans le codeur réel.

Il reste à enrichir la comparaison par les diff et par la sémantique des
critères, à vérifier la livraison fonctionnelle et à étendre les
consommateurs multi-langages ; F10 demeure **partiellement implémenté**.
