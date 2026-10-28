# Simulation spécialisée de circuits RC avec ngspice local

Cette PR ajoute un premier **simulateur externe réellement raccordé** à la Holding.
Le circuit représenté est un réseau RC idéal : source de tension constante,
résistance et condensateur. Il est calculé par **ngspice**, et non par l'IA.

## Configuration opérateur (facultative)

Installer ngspice localement depuis une distribution de confiance, puis
créer un fichier JSON épinglant **le chemin absolu de son exécutable et son
SHA-256**, par exemple :

```json
{
  "schema": 1,
  "executable": "/usr/bin/ngspice",
  "sha256": "<SHA-256 réel de cet exécutable, 64 caractères hexadécimaux>",
  "timeout_seconds": 15
}
```

L'empreinte n'est pas déduite du nom du programme. Sur Windows, utiliser le
chemin absolu de `ngspice.exe` sur son ordinateur, sans le mettre dans le chat.

Lancer le petit chat en ajoutant `--circuit-config` :

```shell
python -m holding.project_chat_ui --db "/chemin/holding.sqlite" --repo "owner/depot" --model-config "/chemin/modele.json" --circuit-config "/chemin/ngspice.json"
```

Sans cette option, les tâches nécessitant ngspice **restent différées**, même
si le dossier R&D et les simulations numériques internes fonctionnent.

## Format de tâche

Une mission du plan peut avoir `kind="simulation"` et un objet `circuit` :

```json
{
  "kind": "simulation",
  "title": "Analyser la réponse RC idéale",
  "acceptance": ["Enregistrer la tension en fonction du temps"],
  "depends_on": [],
  "circuit": {
    "resistance_ohms": 1000,
    "capacitance_microfarads": 100,
    "supply_millivolts": 5000,
    "initial_millivolts": 0,
    "duration_ms": 1000,
    "samples": 32
  }
}
```

Les entiers sont bornés ; le programme ne reçoit **jamais un netlist arbitraire**,
du code Python généré, un chemin choisi par le modèle ou un shell. L'adaptateur
écrit des directives SPICE fixes, lance le binaire épinglé en batch avec `-n`
(sans `.spiceinit` utilisateur), vérifie le retour et la trace, puis conserve
les séries et empreintes dans SQLite. L'appel est limité à 30 secondes.

Un circuit qui dépend d'un essai matériel, d'un travail de code non validé
ou d'une expérience extérieure reste différé. Un calcul sans résultats
valides **n'est jamais marqué réussi**.

## Statut réel

Une trace `COMPUTED_ASSUMPTIONS_UNVALIDATED` est une **simulation
effectivement calculée**, et non la preuve que les valeurs de composants
correspondent au monde réel. Cet adaptateur ne commande aucun laboratoire,
n'achète rien et ne certifie aucun prototype. Il exécute toutefois un
programme natif explicitement installé par l'opérateur : il ne constitue
pas une sandbox système. Réserver cette connexion à un simulateur local
de confiance et isoler l'hôte selon les exigences de votre environnement.

Cette intégration est la première d'une série d'adaptateurs spécialisés ;
d'autres topologies de circuits, la mécanique, la CAO, les tests matériels
et le retour d'expérience exigent leurs propres modèles et vérifications.
