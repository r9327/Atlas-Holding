# Atlas Holding — Profil utilisateur et autonomie complète (spécification, non implémentée)

## Décisions fermes du porteur du projet

- **Seul travail récurrent de l'utilisateur** : fournir / modifier la **ligne directrice d'une ou plusieurs roadmaps** (objectifs, priorités produit et éventuelles contraintes). **Pas** de tri manuel, choix de LLM, choix de plugin, micro-validations de PR, répétitions de « continue », ni supervision technique quotidienne.
- Après la **première installation et connexion de comptes nécessaire**, la Holding analyse les roadmaps, choisit les tâches, décompose les lots, construit les solutions, corrige, réexamine, valide localement et synchronise GitHub **dans les autorisations préalablement accordées**.
- Forfait mobile **illimité** : **aucune limite artificielle de Go/jour ou de téléchargement** pour ce profil. Afficher tailles, stockage et progression, permettre reprise en cas de coupure ; les limites de **crédits, rate-limits et dépenses de services externes** restent strictement actives, avec zéro paiement par défaut.
- L'utilisateur **n'exécutera pas volontairement la Holding pendant ses sessions de jeu** : pas de détection DOFUS/jeu ni de mode de pause gaming obligatoire. Garder protections générales contre surconsommation RAM/VRAM/CPU, OOM et corruption de données.
- Priorité absolue : **qualité du code et vérifications prouvées**, vitesse secondaire ; aucun choix de modèle par nom imposé.
- L'application autonome est démarrée/arrêtée **par l'utilisateur**. Ne pas imposer de tournées en arrière-plan non autorisées. L'interface montre les preuves sans demander à l'utilisateur de devenir gestionnaire.

## Installation initiale « un clic » puis liberté

La première fois, le setup détecte les logiciels déjà prêts, vérifie/installe les dépendances nécessaires et choisit les modèles **automatiquement**. Les téléchargements de poids IA, parfois énormes, peuvent démarrer sous l'autorisation globale donnée lors de l'assistant initial ; l'UI conserve taille, état et possibilité d'annuler. Aucun écran ne demande « Qwen ou Devstral ? ».

Seules les **autorisations initiales techniquement indispensables** ne peuvent pas être inventées : connexion GitHub / OAuth, éventuelle UAC Windows, acceptation des téléchargements et installation. Une fois ces permissions acquises, plus de validation de routine si elles couvrent l'action. Aucun abonnement payant, achat ou nouvelle permission système sensible accordé implicitement.

## Autonomie GitHub : autorisation durable bornée

L'assistant propose un consentement **une seule fois** pour `r9327/Dof-Atlas` et les branches de travail créées par la Holding :
1. Par défaut : lire, planifier, écrire sur worktrees dédiés, tests, commits locaux.
2. **Autorisation facultative unique « Autopilot complet »** : push sur branches dédiées, ouverture PR Draft, passage Ready **une fois la PR complète**, certification exacte SHA, analyse des erreurs, fusion seulement si toutes exigences du dépôt sont satisfaites.
3. Autorisation révocable depuis l'UI, enregistrée hors dépôt, liée à un compte, dépôt, opérations permises, durée / expiration et audit. Aucune nouvelle demande pour chaque PR couverte.
4. Toujours interdits : travailler directement sur `main`, forcer un merge, falsifier des preuves, supprimer les données personnelles, payer, changer les politiques, élargir les permissions, publier des secrets ou agir sur un dépôt non autorisé.

Si une opération n'est pas couverte par une permission ou qu'une décision métier est **véritablement indécidable** depuis les roadmaps, la Holding consigne `REQUIRES_DECISION`, avance sur les tâches indépendantes et affiche la décision en attente dans l'interface **sans harceler l'utilisateur**. Elle ne devine pas une permission manquante.

## CEO : source unique de direction

- **Entrée** : les roadmaps de l'utilisateur, documents liés, règles canonique du dépôt et état des preuves. L'utilisateur n'a jamais besoin d'établir manuellement le DAG ni de ranger les PR.
- L'IA CEO fait extraction exhaustive, audit de couverture, priorisation et dépendances ; l'architecte vérifie les contrats ; codeur/auditeur travaillent chacun leur tour ; plugins locaux et Doctor priment ; les externes recontrôlent après reconnexion.
- Les ambiguïtés mineures sont résolues à partir des guardrails et données observées, en enregistrant le raisonnement sous forme de **décision synthétique auditable**, et non d'une requête utilisateur.
- Toute décision conséquente non déductible de l'objectif ou toute opération interdite est isolée ; poursuivre les autres lots.
- Aucun `DONE` non prouvé ni merge de PR verte sur un ancien SHA. À la fin, le CEO fournit un rapport de la roadmap accomplie, preuves, liens et éventuelles limites.

## Tests indispensables
- [ ] Entrer une roadmap de 30 lots ; le CEO produit le graphe et travaille les lots prêts sans « go » successifs.
- [ ] Autorisation durable GitHub puis 3 PR successives : aucun clic d'approbation par PR, mais protections GitHub toujours vérifiées.
- [ ] Auteur non connecté sur GitHub : assistant initial explicite, aucune récupération frauduleuse de token.
- [ ] Connexion mobile avec données illimitées : aucun plafond de Go inventé, téléchargement résilient à la coupure, volumes visibles.
- [ ] Sans mode détection de jeu : contrôle de saturation RAM/VRAM et arrêt sûr démontré.
- [ ] Exigence indécidable : mission bloquée affichée une fois, autres lots autonomes.
- [ ] Aucun choix de LLM ou de plugin imposé ; tests indépendants de qualité.
- [ ] Aucun push/fusion sans délégation exacte, ni aucune publication de secret, aucune altération des checks Doctor.

**C'est un CONTRAT À IMPLÉMENTER, pas une fonctionnalité déjà présente.**
