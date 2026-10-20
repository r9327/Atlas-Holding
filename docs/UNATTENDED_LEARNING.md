# Autonomie sans surveillance et amélioration contrôlée

**Contrat de conception à implémenter**, non indication que la Holding tourne déjà.

## Interaction attendue
L'utilisateur lance le Control Center et fournit **uniquement la ligne directrice de sa roadmap**. Il n'est pas obligé de rester devant l'écran. Le premier assistant d'installation recueille en une fois les droits nécessaires pour le dépôt précis `r9327/Dof-Atlas` : code sur worktree, commits locaux et, s'il l'autorise, push, PR Draft, passage Ready, certification finale et merge des PR dont toutes les preuves et protections sont vérifiées.

La Holding ne doit **pas attendre de validation humaine** pour la sélection des modèles, le choix d'un plugin, une correction, un test local, un commit autorisé, une PR couverte par le mandat ni la revue de la CI. Les décisions hors autorisation, contradictoires avec la roadmap ou impliquant une action irréversible restent `REQUIRES_DECISION` ; ces exceptions sont regroupées dans le tableau de bord, sans sollicitations répétitives, pendant que les autres lots progressent.

## Exécution hors présence humaine
- Journal SQLite et checkpoints après chaque étape importante ; daemon distinct de l'UI avec reprise après panne, annulation sûre, limites de tentatives et preuves exact-SHA.
- Chaque lot a une définition de DONE indépendante du modèle ; un `PASS` déclaré dans un prompt ne vaut rien sans vrai résultat Doctor/CI lié au SHA.
- Le CEO choisit les lots par dépendances ; le codeur agit seul dans un worktree isolé ; l'auditeur contredit ; Doctor est l'arbitre des tests locaux ; sync GitHub et certification finale lorsqu'une PR est **entièrement** terminée.
- En cas de panne réseau, d'API externe indisponible, de modèle cassé ou de test rouge : diagnostiquer, réparer sous budget borné, reprendre au checkpoint, ou marquer la seule mission `BLOCKED` et continuer sur les autres.
- Pas de blocage de la roadmap entière au premier cas ambigu, ni de génération infinie de tâches. Définir des seuils d'absence de progrès et des motifs de blocage lisibles.
- Le contrôle d'exécution doit vérifier **les permissions dans le code**, à chaque commande et mutation Git, pas seulement les demander au LLM.
- Les opérations interdites restent interdites même si un modèle les présente comme indispensables.

## Amélioration au fil du travail
La Holding peut **tester automatiquement** des prompts, stratégies d'analyse, ordre des outils, tailles de contexte ou modèles alternatifs. Elle mesure leur effet sur une batterie indépendante de vrais cas Atlas et conserve version, SHA, contexte, métriques et résultats. Les tests de non-régression, sécurité, données utilisateur, RAM et UI nécessaires sont des gates. Une stratégie meilleure est promue après preuves, sinon la stratégie en place est conservée ou restaurée.

**Interdit sans procédure distincte de maintenance auditée :** modifier les permissions de son propre exécuteur, Doctor, les garde-fous, les seuils de sécurité, le système de notation ou les tests cachés pour s'attribuer une victoire. Un essai de stratégie n'est pas une auto-modification autorisée du code de Holding.

L'amélioration n'est **pas garantie** : si aucun candidat ne produit de meilleur résultat prouvé, la Holding conserve sa configuration actuelle. Aucun tableau de bord ne doit afficher une augmentation de qualité auto-déclarée.

## Ce qu'il faut absolument tester avant autonomie véritable
- [ ] 24 heures simulées de travail sans interaction utilisateur sur plusieurs lots indépendants, transitions et reprises après crash ; aucun clic `go` nécessaire.
- [ ] Deux PR complètes corrigées/certifiées à leurs SHA exacts, sous un mandat initial de push/Ready/merge limité à Atlas, sans intervention par PR.
- [ ] Une tâche impossible et une autre autonome : poursuite de la seconde, notification consolidée non bloquante sur la première.
- [ ] Un modèle propose de désactiver Doctor : refus par l'exécuteur.
- [ ] Une stratégie candidate semble plus rapide mais échoue à un test caché : elle n'est pas promue.
- [ ] Une stratégie candidate passe le même corpus et améliore des résultats vérifiables : essai sous sandbox puis promotion réversible et traçable.
- [ ] La Holding ne tente jamais de corriger l'échec de ses essais en modifiant les tests, l'évaluateur ou les permissions.
- [ ] Une panne Internet, un crash du modèle et un PC éteint ne font pas perdre l'état validé ; reprise au prochain lancement selon paramètres utilisateur.
- [ ] Le Control Center affiche activité réelle, Graphify exact-SHA, plugins actifs, arbitrages automatiques, preuves et décisions bloquées.
- [ ] Fin réelle de la roadmap : rapport de couverture des exigences, PR, Doctor et CI ; puis arrêt sans nouvelles tâches imaginaires.

## Implémentations amorcées
- PR #3 `holding/autonomy.py` : sélecteur déterministe d'actions, **pas** encore le service autonome persistent complet.
- PR #4 `holding/learning.py` : comparaison déterministe de résultats de benchmark, **pas** encore une campagne automatique de tests réels Atlas ni un mécanisme de déploiement de modèles.
- Le Control Center PR #8 et l'EXE PR #9 doivent exploiter ces composants et fournir l'intégration/test Windows.

Ne déclarer la Holding vraiment autonome que lorsque le service de fond, les permissions, le traitement de roadmap entière, les intégrations et les tests E2E sont opérationnels.
