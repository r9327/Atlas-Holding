# Autonomie V2 F10 — Inventaire des PR ouvertes via le courtier GitHub

Cette livraison étend le **courtier privilégié existant** et son protocole
IPC, sans créer un nouveau client GitHub doté de jetons chez l'agent codeur.

`GitHubBrokerProcessControl.observe_open_prs(repository)` vérifie que le
dépôt est explicitement demandé, envoie une seule requête read-only
`GET /repos/{owner}/{repo}/pulls?state=open&per_page=100&sort=updated&direction=desc`
à travers le processus isolé déjà configuré, puis valide la réponse du
courtier avant de fournir un objet immutable `OpenPrInventory`.

Informations retournées : numéro, branche, SHA de tête et de base, caractère
Draft et identité même dépôt/fork, sur au plus 20 PR. Aucune description de
PR, diff, commentaire, jeton ou commande arbitraire n'est transmis à l'agent.
Une liste de 100 PR a le statut `PAGINATION_NOT_VERIFIED` : **on ne peut
pas affirmer que toutes les PR ont été examinées**.

Chaque observation est un instantané non certifiant :
`can_certify=false` et `can_skip_implementation=false`. La présence
d'une PR ne prouve pas que sa fonctionnalité fonctionne, et l'absence de PR
observée n'est pas une preuve de non-duplication du code.

Les opérations GitHub existantes sont inchangées. Une requête ciblant un
autre dépôt, une réponse incohérente, un champ falsifiant la complétude ou
les privilèges, ou un jeton indisponible donne une erreur : jamais un
rapport « zéro PR » fabriqué.

## Étapes encore nécessaires

- Relier cette observation au préflight du codeur avec une injection
  supervisée du courtier autorisé, sans donner de jeton au modèle.
- Comparer explicitement chaque PR au besoin actuel (dossiers/diff/critères),
  vérifier les PR fusionnées ou fermées et inspecter les consommateurs réels
  du propriétaire canonique.
- Traiter la pagination au-delà de 100 PR par une stratégie bornée et mesurée.
- Réaliser une réception Windows native du courtier dans l'identité ACL isolée.

Ce lot livre **une vraie lecture distante disponible sous courtier configuré**,
pas encore une déduplication automatique exhaustive de toutes les missions.
