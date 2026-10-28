# Mesures expérimentales vs simulation — circuit RC

Cette PR ajoute la **comparaison**, pas une acquisition automatique ni une
certification des capteurs. Une personne ou un instrument exporte un CSV de
mesures de tension, puis la Holding compare ces valeurs à la trace ngspice
déjà effectivement calculée et enregistrée par la PR #30.

## Format accepté

CSV **UTF-8**, virgule, en-tête et unités explicites :

```csv
time_seconds,voltage_volts
0,0.05
0.25,2.12
0.5,3.94
1,4.98
```

Deux à 512 mesures par fichier, 64 Kio maximum, temps strictement croissants
entre 0 et 10 secondes, tensions finies dans ±1000 V. Les points doivent se
trouver **à l'intérieur de la plage réellement simulée**. Aucune extrapolation
ni interprétation automatique des unités, de l'horloge du capteur ou de la
qualité de ses données.

Le système interpole linéairement la tension simulée aux timestamps du CSV
et calcule : erreur absolue moyenne (MAE), racine de l'erreur quadratique
moyenne (RMSE), erreur maximale et biais. Les écarts et les points alignés
sont enregistrés dans SQLite.

**Deux voies d'import :**

- Dans le petit chat Qt, lorsque la simulation ngspice existe : choisir
  le circuit, puis cliquer **Importer mesures CSV**. Le chat affiche le
  RMSE et le maximum des écarts, toujours avec la mention « mesures non
  certifiées ».
- Sans l'interface, depuis le terminal opérateur :

```shell
python -m holding.measurement_cli --db "/chemin/holding.sqlite" --repo "owner/depot" --session-id "ID_SESSION" --task-index 0 --csv-file "/chemin/mesures.csv"
```

Le numéro de tâche commence à **0** dans la CLI.

## Provenance, reprise et limites

Les empreintes du fichier source, des valeurs normalisées, de la simulation
ngspice et de la comparaison sont enregistrées ; un même CSV sur une même
simulation renvoie le même résultat, sans double événement. Chaque nouvelle
série de mesures est conservée séparément (maximum 16 par tâche), sans écraser
l'historique. Une discordance de provenance ou de calcul est signalée.

Le CSV doit être un fichier local explicitement choisi, et n'est jamais une
commande fournie par le modèle LLM. Aucune permission supplémentaire n'est
accordée ; aucune mission n'est marquée `DONE` ou validée Doctor. Pause/STOP
bloque l'import.

**Limite fondamentale :** l'authenticité des mesures, l'étalonnage, les
conditions expérimentales, l'incertitude des capteurs et les valeurs physiques
des composants ne sont pas attestés par le CSV. Un petit RMSE peut suggérer un
bon accord, mais n'est **pas une preuve suffisante** qu'un prototype fonctionne.

L'interface ne connecte pas encore de sonde, d'oscilloscope ou de robot :
un futur adaptateur matériel devra vérifier ces droits et les étalonnages
avant de considérer une acquisition comme instrumentée.
