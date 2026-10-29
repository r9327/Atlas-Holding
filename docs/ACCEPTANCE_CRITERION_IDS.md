# Autonomie V2 — F09 : identifiants stables des critères d'acceptation

Ce lot utilise la couverture canonique existante
(`holding.coverage.RequirementCoverage`) au lieu d'un nouveau registre de
critères ou d'un second moteur de preuves.

Chaque critère de mission expose désormais un `criterion_id` déterministe,
dérivé de **l'identifiant de mission**, du SHA-256 du texte exact du critère et
du rang de répétition de ce même texte. Il est consultable par
`ControlPlane.requirement_page(...)`.

- Réordonner des critères différents conserve leurs identifiants.
- Modifier la formulation crée un nouvel identifiant : l'ancien contrat n'est
  pas réutilisé silencieusement.
- Deux critères textuellement identiques restent distinguables.
- Une consultation paginée ne crée ni nouvelle version de roadmap, ni preuve,
  ni autorisation.
- Le champ `proof_contract` reflète la règle actuelle du module de couverture :
  `SIGNED_EXACT_SHA_PASS`. Un contrôle ou une sortie de modèle ne vaut pas PASS.

L'état `PROVEN` reste calculé comme avant, uniquement à partir d'une mission
`DONE`, des SHA correspondants et d'une preuve signée indépendante
`PASS`. L'ajout d'un identifiant n'invalide pas ni ne certifie une mission
existante : la révision de la roadmap et les preuves restent sous leurs
contrôleurs canoniques.

**Périmètre partiel de F09** : l'interface de rédaction des critères et le
lien explicite critère → scénario de test exécutable restent à développer.
Aucun critère n'est annoncé terminé sur la base du seul identifiant.
