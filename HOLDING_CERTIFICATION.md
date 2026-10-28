# Atlas Holding — contrat de certification

Inspiré de la discipline de Dof-Atlas, mais **adapté au runtime de Holding**. Le Doctor d'Atlas ne devient pas arbitrairement le Doctor de Holding. Les preuves sont émises par les commandes réelles et les workflows du dépôt concerné.

## États distincts

| État | Signification | Preuve minimale |
| --- | --- | --- |
| CODE_DONE | Modification codée et relue | diff et revue des commits |
| VALIDATED | Vérification ciblée PASS | commande, environnement, résultat et SHA |
| CERTIFIED | Ensemble des gates requis PASS sur le SHA exact | runs CI et preuves externes nécessaires |
| BLOCKED / NOT_RUN | Validation absente ou impossible | motif, preuve manquante, prochain contrôle |

Ne jamais inférer CERTIFIED de CODE_DONE, d'un lint, d'une PR verte partielle, d'une sortie IA, ou d'un test SKIPPED. Les validations enregistrent obligatoirement la portée, le SHA et les limites de preuve ; toute modification du SHA ou des règles pertinentes rend l'attestation ancienne périmée.

## Déroulement d'une PR

1. Draft pendant l'implémentation : petites modifications cohérentes, tests ciblés locaux, revue logique commit par commit puis globale.
2. Ready for review uniquement une fois le périmètre complet : workflow universel `Holding AI & Quality Certification` (politique, suite Linux et Windows sur SHA exact), plus les workflows de domaine applicables.
3. Échec : corriger, refaire les tests et certifier la nouvelle tête. Checks « skipped » / « not run » n'ont aucune valeur positive.
4. Fusion : uniquement après résultats requis et règles GitHub de protection. L'activation des **required status checks** et protections de branches se vérifie dans les paramètres du dépôt ; un YAML seul ne les active pas.
5. Aucun test Linux en `offscreen` ne remplace l'acceptation utilisateur **Windows réel / Ollama / installation / mise à jour / MCP**. Les contrats `docs/WINDOWS_LIVE_ACCEPTANCE.md`, `docs/QUALITY_RELEASE_GATES.md` et `docs/QUALITY_EFFECTIVENESS_ACCEPTANCE.md` restent des conditions indépendantes de livraison.

## Priorités de qualité

- Sécurité des secrets, permissions bornées, absence de fusion automatique sans preuves.
- Intégrité de l'état persisté et capacité à reprendre une exécution interrompue.
- Cohérence des outils et plugins autorisés ; mode offline réellement vérifié.
- Tests d'acceptation représentatifs, dont erreurs injectées et conditions Windows.
- Maîtrise de la RAM et des coûts réels (ne pas confondre inférence modèle et daemon).

`tools/governance_check.py` valide **uniquement** les invariants statiques du registre et l'existence des contrats. Son PASS ne représente jamais une certification produit ou native.