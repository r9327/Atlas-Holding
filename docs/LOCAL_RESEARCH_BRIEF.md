# Premier livrable R&D local — propositions vérifiables

Cette étape complète la PR #25 : la Holding peut produire **à la demande** un
dossier de recherche et conception pour une mission de type \`plan\`, en
utilisant un modèle Ollama installé localement, identifié par son digest exact.
L'outil ne nécessite pas de service distant, n'accède pas au Web et ne modifie
aucun dépôt Git.

## Préparation

Importer une roadmap JSON via la commande opérateur existante. Son objectif
\`project_goal\` est obligatoire pour produire un dossier R&D (mais demeure
facultatif pour les autres tâches).

\`\`\`json
{
  "repo": "owner/projet",
  "project_goal": "Concevoir un système de tri automatisé pour un atelier.",
  "missions": [
    {
      "id": "etude-1",
      "operation": "plan",
      "acceptance": ["Proposer des hypothèses, expériences et étapes de prototype."]
    }
  ]
}
\`\`\`

Le modèle doit être déjà installé sur le poste avec une configuration
Ollama existante et épinglée au format \`schema=1\`, \`model\`, \`digest\`,
\`num_ctx\`, \`timeout\`. Le fichier de configuration et la base SQLite sont
des chemins locaux absolus choisis par l'opérateur.

\`\`\`shell
python -m holding.daemon_cli --db "/chemin/holding.sqlite" import --roadmap-file "/chemin/roadmap.json" --source "operator:rd"
python -m holding.daemon_cli --db "/chemin/holding.sqlite" research-brief --mission-id "etude-1" --model-config "/chemin/modele.json"
\`\`\`

La commande retourne un JSON comprenant \`status\` =
\`UNVERIFIED_RESEARCH_PROPOSAL\`, un concept, des hypothèses, des expériences
avec critères de mesure, des étapes de prototype, des inconnues et les
empreintes de la spécification, du modèle et du dossier.

## Limites et garanties

- Le modèle reçoit uniquement l'objectif et les critères de la mission
  enregistrés dans la base. Il n'obtient ni commande shell, ni mutation Git,
  ni accès à des secrets, à des outils externes ou aux permissions de l'hôte.
- Les résultats sont **des propositions non vérifiées**. Aucune expérience
  n'a été exécutée, aucune source extérieure n'a été consultée et aucune
  certification scientifique ou Doctor n'est générée.
- Un résultat valide est inscrit atomiquement en SQLite, lié aux empreintes
  de sa mission et du modèle. Un appel identique rend l'artefact enregistré
  sans réinterroger le modèle ; après révision du plan, un nouvel artefact
  reçoit une nouvelle identité. Les propositions historiques ne valent jamais
  preuve de réalisation.
- La commande n'accepte qu'une mission \`plan\` en état \`READY\`.
  Pause/STOP et toute modification de mission pendant la génération
  empêchent de persister le résultat.
- Il s'agit pour l'instant d'une **action explicite de l'opérateur**,
  non d'un travail R&D automatiquement dispatché par le service Windows.
  L'extension du scheduler à des agents d'étude/expérimentation, la
  validation de sources externes, les simulations et l'exécution de
  prototypes sont des étapes ultérieures distinctes.
