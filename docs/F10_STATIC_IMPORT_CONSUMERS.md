# F10 — Consommateurs statiques avant génération d'un patch

Cette étape enrichit la découverte de l'existant déjà présente dans le codeur
de la Holding. Les candidats proviennent de `inspect_existing_code` à
l'empreinte Git SHA exacte. Avant d'envoyer les instructions au modèle local,
le superviseur cherche des **imports Python explicites** vers ces candidats,
puis confirme les correspondances par analyse AST de fichiers lus depuis le
même commit. Aucun module du projet n'est importé ou exécuté.

Le budget est volontairement borné : six candidats, vingt-quatre fichiers
Python lus au maximum, quarante-huit Kio par source, vingt-quatre relations
retournées. Les imports absolus et relatifs simples sont reconnus. Les simples
chaînes de caractères ressemblant à des imports ne sont pas considérées comme
des consommateurs. Les tests utilisent un vrai dépôt Git jetable et des SHAs
figés, notamment en présence de modifications locales non committées.

Les résultats sont des **indices partiels** : un import statique ne prouve pas
qu'un appel a lieu en production. Les imports dynamiques, chemins alternatifs,
langages autres que Python, fichiers trop volumineux et dépendances externes
échappent à l'analyse. En cas de recherche Git non concluante, l'état reste
`NOT_VERIFIED`. L'absence de relation n'est jamais interprétée comme
« module inutilisé ». Les champs `functionality_verified`,
`consumers_exhaustive` et `can_skip_implementation` restent tous à `false`.

Le codeur transmet ce relevé dans `existing_consumers` à l'adaptateur Ollama
déjà installé. Celui-ci revalide les chemins, les bornes, les SHA et les flags
avant de montrer les indices au modèle. Il reçoit l'instruction de préserver
les interfaces existantes, mais ne peut pas les déclarer certifiées.

Les analyses complètes Graphify/Serena, la vérification de l'usage effectif,
la comparaison des diff PR avec les critères et l'élimination automatique des
doublons demeurent à construire. Ce lot n'ajoute aucun processus privilégié,
aucune permission et ne modifie pas les règles GitHub ou les certificats CI.
