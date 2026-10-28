# Atlas Holding — certification des changements et des releases

## Preuves distinctes

- **IMPLEMENTED** : code écrit et diff relu ; aucune implication de test réussi.
- **TARGETED_VALIDATED** : tests locaux ciblés réellement réussis, avec SHA, environnement et commandes connus. Ne prouve ni FULL ni Windows réel.
- **HOLDING_CI_VALIDATED** : la PR complète, hors Draft, a obtenu le résultat GitHub **Holding Certification / Required = success** pour **le SHA exact**, avec la suite Linux/Windows réellement exécutée. Des jobs skipped, cancelled ou neutralisés ne comptent pas. Une nouvelle révision annule ce verdict pour l'ancien SHA.
- **RELEASE_CERTIFIED** : exige en plus les preuves d'acceptation natives Windows, sécurité, installation, reprise, RAM et distribution de `docs/QUALITY_RELEASE_GATES.md` et `docs/OPERATIONAL_READINESS.md`. Le nouveau workflow GitHub n'accorde **pas** ce statut à lui seul.

Les verdicts de revue `REVIEW OK`, `IMPROVEMENTS ADVISED`, `CHANGES REQUIRED` sont séparés des preuves machine. Le codeur, un texte de modèle et une sortie de test locale ne peuvent jamais signer une attestation GitHub de l'orchestrateur ; l'émetteur privilégié et ses contrats existants restent obligatoires.

## Cadence obligatoire

1. Pendant la construction : PR **Draft**, tests ciblés ; pas de FULL à chaque commit.
2. Une fois la PR stabilisée : audit de chaque commit et du diff intégré, tests locaux appropriés, puis passage **Ready for review**.
3. `.github/workflows/holding-certification.yml` s'exécute pour toutes les PR prêtes vers `main` et à chaque `synchronize` ultérieur. Les déclenchements sur `main` et manuels ne remplacent pas la preuve requise sur le HEAD d'une PR.
4. Il vérifie le SHA checkout, les contrats d'outils/MCP et les tests complets sur Ubuntu et Windows, ainsi que le contrôle architectural. Le job agrégateur **Holding Certification / Required** échoue si une des deux matrices échoue, est annulée ou est ignorée.
5. Ne fusionner qu'avec revue logique satisfaisante, vérification des exigences de la branche, résultat agrégateur réussi et SHA actuel identique au SHA certifié. Une correction déclenche une nouvelle certification.
6. En cas d'échec / environnement indisponible : bloquer la certification, documenter les preuves manquantes ; aucune exception silencieuse. Un test natif Windows non réalisé doit rester explicitement non validé.

## Administration GitHub

Le fichier de workflow **ne configure pas à lui seul** la protection de branche. Un propriétaire doit activer, dans les rulesets GitHub de `main`, l'interdiction de push direct, les revues requises, la résolution des discussions et le statut obligatoire **Holding Certification / Required** avec protection contre les statuts périmés. Sans cet acte d'administration, aucune protection effective ne doit être annoncée.

La certification de **Dof-Atlas** reste définie par le `PHASE_CERTIFICATION.md` de Dof-Atlas et par ses propres contrôles Doctor, sur son propre SHA.
