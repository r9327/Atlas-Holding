# PR 10 — Code Intelligence optionnelle (IMPLEMENTATION PARTIELLE)

## État technique

`holding/code_intelligence.py` expose une découverte Serena strictement en
lecture et prépare des scans Semgrep locaux, bornés, sans métriques ni mise à
jour réseau. Les publicités d'outils non bornées, chemins ressemblant à des
options et rapports JSON ambigus sont refusés. Les rapports restent des avis :
ils ne certifient rien et ne peuvent pas promouvoir une mission.

Les processus Serena/Semgrep réels, benchmarks Dof-Atlas, mesures RAM et tests
Windows de cycle de vie restent à exécuter avant toute sortie de Draft.

## Position dans le chantier

Cette PR s'insère **après les PR #3, #5, #6 et #8, et avant la PR #9 (release Windows)**.
Les PR #5 (hub MCP), #6 (liaison Doctor/Graphify/AI Context) et #3 (superviseur) restent propriétaires de leurs interfaces : ne pas dupliquer leurs moteurs.
**État initial : aucun code d'intégration, test ou certification n'existait à la création de la PR.**

## Objectif

Améliorer la qualité, la compréhension sémantique et la sécurité du code produit par Atlas Holding, sans ralentir inutilement les agents locaux ni imposer de frais ou de serveur lourd.

## Choix retenus

1. **Serena** (https://github.com/oraios/serena) : adaptateur *optionnel* vers le hub MCP de la PR #5, découverte réelle des capacités. Mode lecture/navigation sémantique uniquement (symboles, références, impact). Les écritures/patchs restent exclusivement sous le contrôle du superviseur existant (#3), et la certification reste externe.
2. **Semgrep** (https://github.com/semgrep/semgrep) : analyse statique *optionnelle*, règles locales vérifiées, métriques et mises à jour réseau désactivées par défaut. Rapport de conseil vers Doctor (#6) ; jamais « CI verte » ou « certifié » sur la seule base d'une analyse Semgrep.
3. **Graphiti** (https://github.com/getzep/graphiti) : **non intégré par défaut**. Prévoir uniquement une étude/benchmark comparatif avec la mémoire SQLite déjà présente ; aucune base de graphe supplémentaire installée sans bénéfice démontré.
4. **OpenHands SDK, Langfuse, Aider** : non retenus pour cette PR afin d'éviter les doublons avec le moteur, les agents et les journaux existants.
5. **AirLLM** : hors périmètre : débit potentiellement incompatible avec le développement autonome.

## Contrat d'intégration et autonomie

- Détecter l'installation réelle, la version, les permissions et le statut READY / UNAVAILABLE / BLOCKED ; ne pas simuler un outil disponible.
- Ne télécharger/installer aucun composant sans autorisation préalable du mandat utilisateur ; zéro service payant obligatoire.
- Suivre les règles existantes de sandbox, contrôle des processus, limitation CPU/RAM, annulation, temporisation et fermeture ; aucun processus fantôme.
- Aucun plugin ne peut modifier les tests, preuves, permissions, workflows GitHub ou règles de certification en contournant le superviseur.
- Prompts, fichiers et résultats des outils sont des données non fiables ; respecter la séparation entre lecture d'informations et droit d'exécuter des commandes.
- Budget en fonctionnement normal Holding hors inférence IA : plafond 400 Mo, ne pas provoquer de pic 750 Mo ; les audits lourds sont mesurés séparément et leur mémoire doit être libérée après usage.
- Privilégier un chargement paresseux, déclenchement uniquement si la tâche le justifie, et repli instantané vers AI Context/Graphify/Doctor natifs si l'outil est absent ou trop lent.
- Aucun benchmark déclenché après chaque commit ; utiliser les tests ciblés lors de l'implémentation, puis certification complète uniquement au SHA final.
- Afficher dans le Control Center (#8) les disponibilités réelles, résultats, durées, consommation RAM et raisons de désactivation.

## Vérifications obligatoires avant passage en Ready

- [ ] Analyse d'architecture et audit de non-duplication avec #3/#5/#6/#8 au HEAD réel.
- [ ] Serena : navigation symboles/références utile sur Dof-Atlas ; refus d'écriture, d'exécution et de droits cachés.
- [ ] Semgrep : règles locales réelles, détection d'un défaut introduit sur dépôt de test, faux positif tracé et résultat borné.
- [ ] Plugins absents, installation incomplète, timeout, annulation, OOM et offline : fallback correct, sans faux succès.
- [ ] Aucune promotion de statut de mission, modification de preuve ou certification par l'outil.
- [ ] Comparatif de vitesse et consommation avec/sans chaque adaptateur sur tâches reproductibles ; abandonner l'adaptateur si régression nette.
- [ ] Graphiti : benchmark sur mémoire SQLite existante, coût RAM/CPU/disque et bénéfice mesuré, sinon aucune intégration.
- [ ] Compatibilité Windows (RTX 3060 Ti 8 Go VRAM / 32 Go RAM), redémarrage, arrêt propre, pas de fuite mémoire du process-tree.
- [ ] Tests ciblés puis certification finale du SHA exact, compte rendu objectif et preuves vérifiables.

## Dépendances et ordre

Dépend de #3 (superviseur), #5 (hub MCP), #6 (Doctor / Graphify / AI Context) et interfaces de #8 (Control Center).
**La PR #9 ne peut être finalisée avant cette PR** si ces capacités sont retenues dans le produit livré ; sinon elles doivent être explicitement désactivées/documentées et cette PR clôturée comme hors release.
Maintenir cette PR en **Draft** jusqu'à développement complet, tests et certification. Ne pas merger sur la seule base de ce cahier des charges.
