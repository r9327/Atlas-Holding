# Premier exécuteur R&D non logiciel : simulations numériques locales

Après le chat et le plan R&D, la Holding peut calculer automatiquement
certaines expériences **numériques purement hypothétiques**.

Un lot `simulation` peut inclure un bloc `simulation` avec :
`model` (`linear_discrete` ou `decay_discrete`), `initial`,
`parameter`, `steps`. Tous les nombres sont des entiers strictement
bornés. `linear_discrete` calcule `x[n+1]=x[n]+parameter`.
`decay_discrete` calcule `x[n+1]=floor(x[n]*parameter/10000)`.

Seules les simulations indépendantes ou dépendantes de simulations
déjà calculées du même plan peuvent être exécutées. Les dépendances
sur du code non validé, sur du matériel ou sur des expériences externes
restent différées.

Les séries effectivement calculées sont persistées en SQLite, liées
aux SHA-256 de leur plan et paramètres. Leur statut est
`COMPUTED_ASSUMPTIONS_UNVALIDATED` : aucune donnée du monde réel,
aucune mesure expérimentale, aucune validation scientifique, aucune
fabrication matérielle ne sont prétendues. Pause/STOP bloque l'exécution.

Les agents IA ne fournissent que des **paramètres**, jamais du code
Python exécutable. Aucun accès réseau, shell, robot, coût ou permission
n'est créé. Un résultat existant est vérifié et réutilisé lors de la reprise.
