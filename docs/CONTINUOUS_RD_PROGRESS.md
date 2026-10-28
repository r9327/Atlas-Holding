# Parcours R&D et reprise des travaux

Le chat local conserve le projet et ses réponses. Il affiche le dossier complet
(concept, hypothèses, expériences proposées, critères de réussite, étapes et
inconnues), les calculs disponibles et l'état de chaque tâche.

## Lancement local

Avec Python et PySide6 de la version épinglée dans la CI déjà disponibles :

```text
python -m holding.project_chat_ui --db /chemin/absolu/holding.sqlite --repo proprietaire/depot --model-config /chemin/absolu/model.json
```

Le modèle Ollama local doit être installé et sa configuration valide et épinglée.
Le dépôt est choisi par la configuration opérateur. Pour les circuits, ajouter
`--circuit-config /chemin/absolu/ngspice.json` avec l'exécutable et son SHA-256
vérifiés. Les chemins Windows sont également absolus, par exemple
`C:\Atlas\holding.sqlite`.

Ce lancement développeur ne constitue pas une intégration au setup Windows #9.

## Saisie et reprise

- Objectif : 1 400 octets UTF-8 au maximum ; réponse à une précision : 600 octets.
  Un caractère accentué peut occuper plusieurs octets.
- Le texte trop long reste modifiable et n'est pas envoyé au modèle.
- La saisie reste visible après un échec. **Reprendre** recharge la session
  persistée : une réponse déjà enregistrée n'est pas envoyée une seconde fois.
  Le champ n'est effacé qu'après constat de son acceptation.
- Le dossier et les calculs déjà enregistrés ne sont pas régénérés à la reprise.

## Dépendances et progression

Les tâches du plan forment un graphe sans cycle de huit tâches au maximum.
Le coordinateur réutilise les moteurs numérique et RC existants jusqu'à ce
qu'aucun calcul supplémentaire ne puisse avancer. Une chaîne
`numérique → RC → numérique → RC` peut se terminer dans le même passage.
Chaque tâche conserve ses paramètres : ce mécanisme ordonne les calculs ; il
ne substitue pas automatiquement la sortie d'un calcul aux paramètres du suivant.

Une tâche de code peut être transmise après des calculs effectivement enregistrés
et vérifiés, ou avec des dépendances vers d'autres tâches de code transmises.
Les empreintes des calculs directement utilisés sont attachées à son contrat.
Le moteur conserve ses propres conditions de dépendances, permissions et tests.
Transmise ne signifie donc ni exécutée ni terminée.

Si le simulateur RC manque ou échoue, ses descendants restent différés ; les
travaux indépendants admissibles continuent. Une tâche native en erreur est
tentée une seule fois par passage ; son motif est conservé dans le journal sans
événements identiques répétés. Une reprise permet un nouvel essai après réparation
du simulateur. La corruption d'un résultat enregistré bloque la reprise ; elle
n'est pas assimilée à une simple indisponibilité. Pause/STOP reste prioritaire.

Le transfert au moteur est rejouable : une interruption après l'import des tâches
et avant le checkpoint ne crée ni nouvelle tâche ni nouvelle version inutile
lors de la reprise. Aucun grant ni statut DONE n'est émis par le chat.

## Limites

Les calculs reposent sur des hypothèses ; ils ne certifient pas une expérience.
Les mesures CSV restent fournies par l'opérateur et non authentifiées. Les
travaux matériels, externes et de recherche sans adaptateur restent différés.
Un nouveau projet indépendant exige encore un autre dépôt.

La modification d'un objectif existant, l'export du dossier, la correction générale
des tâches de code échouées, les variantes et la sélection automatique du modèle
restent suivis dans les PR de la feuille de route. Cette livraison ne les annonce
pas implémentés et ne certifie pas l'autonomie complète du produit.

## Vérification ciblée

Les tests des modules `test_project_chat`, `test_project_chat_ui`,
`test_research_workplan`, `test_research_simulation`, `test_ngspice_adapter` et
`test_research_measurement` couvrent les frontières UTF-8, la conservation de
saisie, le dossier complet, les chaînes mixtes, l'indisponibilité RC, l'absence de
grants, la corruption et le crash entre import et checkpoint. Le vrai ngspice est
testé séparément lorsque `ATLAS_TEST_REAL_NGSPICE` désigne un binaire disponible.

Les réponses de modèle injectées dans ces tests ne constituent pas un essai du
parcours complet avec un vrai modèle Ollama.
