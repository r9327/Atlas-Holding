# PR #9 — Conditions de sortie : réseau extrêmement instable + vraie qualité

Ne pas publier d'EXE comme « autonome » uniquement parce que l'interface s'ouvre, qu'un modèle répond ou que des PR sont créées.

## Tests d'acceptation bloquants supplémentaires
- [ ] Le setup télécharge réellement ses dépendances et un gros modèle sur Windows, même si le réseau devient intermittent ; contrôle d'intégrité, reprise possible ou mode de réparation explicite quand le serveur ne supporte pas la reprise.
- [ ] Coupure + retour réseau + **redémarrage de l'EXE et du service Ollama** en milieu de téléchargement ; pas de modèle incomplet annoncé « prêt », aucun ancien modèle fonctionnel effacé.
- [ ] Les preuves d'une installation réelle Windows (logs sans secrets, mesures de taille et SHA, santé d'Ollama et OpenCode, démonstration hors ligne après provisionnement) sont attachées au SHA exact.
- [ ] Exercice complet **ROADMAP entière** sur dépôt de test, intégration de vrais outils AI Context/Graphify/Doctor, test de défaut injecté, correction et certification GitHub sans « continue » de l'utilisateur.
- [ ] Au moins des lots représentatifs de Dofus Atlas (dont un changement multi-fichiers et une régression UI/persistance), révisés et testés au lieu de simples programmes de démonstration.
- [ ] Après crash et 3 reprises, aucune tâche dupliquée, aucun état DONE inventé, aucun conflit Git écrasé.
- [ ] Publication de la Release refusée si des tests de sécurité ou de qualité sont absents. En cas de capacité modèle insuffisante, qualifier le produit d'**assistant contrôlé** et non de Holding autonome prête.

Sources d'acceptation : `docs/ACCEPTANCE_RELIABILITY.md` (PR #2), `docs/QUALITY_EFFECTIVENESS_ACCEPTANCE.md` (PR #4) et la roadmap canonique #1. Les tests doivent être **réellement exécutés sur Windows**, pas seulement écrits.
