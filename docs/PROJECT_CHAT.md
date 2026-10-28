# Petit chat R&D : objectif, 0 à 3 questions, démarrage automatique

L'application locale est **distincte du Control Center** : elle ne reçoit
aucune clé de pilotage du moteur. Elle exige un chemin SQLite local,
un dépôt exact sélectionné par l'opérateur, et la configuration d'un
modèle Ollama déjà installé et identifié par son digest réel.

Au premier message, le modèle évalue si des détails essentiels manquent :
- projet déjà assez précis : **aucune question**, démarrage immédiat ;
- ambiguïté bloquante : **au plus trois questions**, posées une à une ;
- après la dernière réponse, la Holding importe une mission de type
  `plan` et génère automatiquement un dossier R&D via la PR #26.

Le dossier, horodaté et identifié par SHA-256, comporte concept, hypothèses,
expériences proposées, critères de réussite, étapes de prototype et inconnues.
Il est **non vérifié** : aucune expérimentation n'est exécutée ni aucun
prototype fabriqué à cette étape. Le scheduler de code n'est pas lancé
automatiquement : les futures opérations sensibles restent conditionnées
aux autorisations et contrôles du moteur.

L'état du dialogue est conservé en SQLite : `ASSESSING`, `ASKING`,
`GENERATING`, `READY`. Après une interruption, ouvrir l'application
et utiliser **Reprendre** si une génération était en cours ; l'import
et le dossier sont idempotents. Un seul chat de projet par dépôt est
recommandé dans cette première version (les roadmaps de ce repo ont un
objectif global immuable).

La saisie est limitée en **octets UTF-8**, comme le service : 1 400 pour
l'objectif et 600 pour chaque réponse. Un texte trop long reste modifiable
et n'est pas envoyé au modèle. Après une erreur, le texte reste dans le champ
et le bouton **Reprendre** recharge la session persistée sans fermer la fenêtre.
Le champ n'est effacé qu'après constat de l'enregistrement de l'objectif ou
de la réponse ; une réponse déjà enregistrée n'est pas renvoyée au modèle.

La consultation et l'export complet des dossiers sont également accessibles
par la commande locale de [#26](LOCAL_RESEARCH_BRIEF.md#consulter-et-exporter-les-dossiers-enregistrés),
avec distinction entre le contrat courant et les contrats historiques.

Démarrage (PySide6 installé, Windows ou Linux) :

```shell
python -m holding.project_chat_ui --db "/chemin/holding.sqlite" --repo "owner/depot" --model-config "/chemin/modele.json"
```

Cette interface utilise un accès **opérateur local** à la même base que le
moteur, pas le canal privé et en lecture seule du Control Center.
Un service distant et une machine non fiable ne doivent pas recevoir
directement ce point d'entrée. Pause/STOP empêchent la génération ; aucune
permission Git, shell ou token n'est accordé par le texte du chat.

Une nouvelle conversation indépendante sur le même dépôt est refusée :
relancer l'outil avec un autre dépôt explicitement choisi. Le formulaire
est désactivé après création du dossier pour ne pas faire croire qu'une
deuxième mission autonome peut être lancée sous ce même objectif.

L'analyse de l'idée, la génération du dossier et le redémarrage sont
automatiques dans ce périmètre ; la R&D expérimentale, la conception CAO,
les simulations, le développement complet et l'exécution multi-agents
autonome constituent les prochaines étapes.
