# PR #4 — Preuve d'efficacité réelle des IA sur DOFUS Atlas

La Holding ne vaut rien si elle crée des PR sans résoudre les vrais besoins. **Le test d'acceptation est la qualité des résultats sur Atlas**, pas la qualité perçue du texte du modèle ou le volume de commits. Ce document est un contrat à implémenter ; les benchmarks ne sont **pas encore exécutés**.

## Règle pour l'utilisateur
Seule intervention récurrente : la ligne directrice des roadmaps. Aucun choix de modèle, de priorité technique, de correction, de plugin ou de PR demandé. Le CEO automatise et trace toutes les tâches relevant d'une délégation GitHub initiale bornée. Le modèle local n'est pas considéré suffisamment fiable pour coder seul **avant** qualification.

## Évaluation en deux phases
**A. Sandbox reproductible, sans risque.**
- Cloner un Atlas de test / worktree jetable au SHA connu, ne modifier ni main, ni sauvegardes utilisateurs, ni CI.
- Évaluer différents modèles / prompts / tailles de contexte sur tâches **réalistes du dépôt actuel** : correction régression Guide, logique de persistance, source de données, import entre modules, Graphify et détection des consommateurs, UI Qt, launcher Windows, packaging et erreurs d'intégration.
- Chaque tâche : résultat attendu dans une fiche d'acceptation indépendante, diff, tests « rouges avant / verts après », cas limites cachés aux agents, absence de détérioration d'autres modules, mémoire/latence si concernées.
- Inclure des tâches nécessitant : lecture des règles, utilisation correcte de Context7/Firecrawl quand elles apportent de la preuve, capacité à répondre « bloqué » quand le réseau ou une donnée nécessaire manque, et protection contre prompt injection.
- Deux rôles alternés : un codeur, puis un auditeur qui examine le diff et les cas oubliés ; Doctor tranche via commandes réelles. Aucun auto-score « j'ai fini ».

**B. Qualification en conditions réelles sous protections.**
- Lancer des lots indépendants représentatifs en mode « prévisualisation » (aucune fusion automatique) puis vérifier que le comportement se maintient après correction, crash et changement de roadmap.
- Seulement après essais concluants, activer le mode autonome (s'il a été délégué) sur des PR à risque limité. Étendre progressivement aux lots plus structurants, jamais en sautant Graphify/Doctor/CI.
- Les modèles incapables de faire converger une tâche vers le résultat attendu sont remplacés ou la tâche est bloquée : la Holding ne doit pas réessayer indéfiniment en masquant son échec.

## Indicateurs mesurés par logiciel et traces
- **Couverture** : toutes exigences de roadmap importées, associées à un lot, des tests et des preuves `exigence → lot → résultat → SHA`.
- **Succès fonctionnel** : pour chaque tâche, tests d'acceptation réels et cas limites, y compris tests non montrés à l'IA, tous passés avant DONE.
- **Régressions** : aucune régression critique ; toute preuve périmée invalidée si SHA/source/règle a changé.
- **Respect du périmètre** : aucun changement non sollicité de données utilisateur, sécurité, permissions, tests pour rendre vert, ou main.
- **Honnêteté de validation** : faux PASS / rapports inventés = échec disqualifiant même avec beaucoup de code.
- **Autonomie** : temps en BLOCKED, répétitions sans progrès, nombre de corrections avant verdict, nombre de décisions humaines nécessaires *hors mandat*. Les tâches indépendantes continuent.
- **Impact** : revue du code utile, suppression des changements non nécessaires, non-régression RAM/UI/launcher selon chaque lot.
- **Comparaison** : comparer architecture d'un seul rôle vs chaîne CEO→Architecte→Codeur→Auditeur→Doctor, mesurer si la multi-IA réduit vraiment les défauts.

## Conditions avant livraison autonome
- [ ] Un corpus versionné d'exercices Atlas existe, avec au moins un test critique par domaine cible, résultat attendu défini indépendamment de l'agent.
- [ ] Les agents peuvent accomplir plusieurs tâches non triviales du véritable dépôt, y compris correction multi-fichiers ; tests cachés et Doctor confirmés.
- [ ] Revue auditeur détecte un bug injecté et bloque un faux PASS ; aucun contournement de protections / escalade de permissions.
- [ ] Des échecs d'IA restent visibles et ne produisent pas de commits ou merges non vérifiés ; arrêt sans boucle répétitive.
- [ ] Aucune fonctionnalité de lot déclarée DONE sans critères de sortie vérifiés. Si la qualité réelle reste insuffisante, **NE PAS qualifier comme "développement autonome fiable"**.
- [ ] Résultats comparatifs et contexte/matériel de l'essai affichés, avec limites, au lieu d'un pourcentage inventé.

## Sécurité du protocole
Aucun test de modèle ne doit voir la solution de référence ou les assertions secrètes pendant qu'il code ; permettre aux validateurs déterministes de les exécuter. Le contrôle humain initial sert à **certifier la méthode une fois**, pas à demander à l'utilisateur de conduire chaque PR.
