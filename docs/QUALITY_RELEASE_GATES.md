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

## Gate RAM — aucun pic de 750 Mo en codage IA normal

- [ ] Vérifier sur **Windows réel**, avec une roadmap multi-lots exécutée par les agents, la mémoire de **tout l'arbre Holding** : daemon, UI, plugins, IPC, superviseurs, Git et utilitaires ordinaires. Ne pas compter les poids / processus d'inférence IA dans ce budget, mais ne pas cacher les autres sous-processus.
- [ ] **Objectif repos ≤ 250 Mo**, **régime normal 250–350 Mo**, **plafond d'acceptation 400 Mo en codage IA ordinaire**. Un pic de **750 Mo** pendant le travail IA normal = **FAIL**, même fugace, s'il vient de Holding / ses auxiliaires.
- [ ] Benchmarks avec UI ouverte / fermée, historique de nombreux lots, graphique Graphify ouvert puis refermé, reprise réseau et arrêt propre, enregistrement des pics par PID (mesure de l'arbre complet) et dérive mémoire sur plusieurs heures.
- [ ] Les **audits lourds Doctor / Graphify / certifications** sont explicitement **exemptés** de ce plafond ; fournir pour eux des courbes et pics séparés, identifiés à l'avance par type d'opération, avec retour au niveau normal après libération. Ils ne doivent pas rendre le travail normal anormalement gourmand à long terme.
- [ ] Pas de release « certifiée RAM » en l'absence de traces de mesure Windows, ni de rapport qui ne mesure que le PID de l'UI.

Une consommation modèle IA (Ollama et autres inférences) est reportée à part et n'est pas comprise dans ce budget de Holding.
