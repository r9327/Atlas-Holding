# PR 02 — Installation et réparation Windows (IMPLÉMENTATION PARTIELLE)

**But utilisateur :** depuis une Release GitHub, télécharger un `AtlasHoldingSetup.exe`, double-cliquer et obtenir les dépendances et les modèles préparés avec un assistant visuel. Zéro achat imposé.

## Portée
- Détecter Windows x64, RAM, VRAM, CPU, espace disque, droits, versions Python/Git/Ollama/OpenCode, connexion et capacité de téléchargement.
- Installation reproductible et **épinglée** de Python compatible, Git, Ollama, OpenCode, dépendances GUI, outils MCP nécessaires et prérequis Atlas ; provenance et hash des paquets ; ne pas exécuter des scripts distants arbitraires.
- Choix du disque et du répertoire. Réparer une installation partielle, mettre à jour sans perdre la base SQLite ni les préférences. Aucune suppression implicite.
- Premier démarrage guidé : choisir le clone Atlas ou le cloner, lire ses règles, sélectionner un profil d'IA et configurer les permissions ; **aucun accès en écriture à main**.
- Connexion mobile illimitée : afficher les tailles et progression, puis installer automatiquement les éléments recommandés dans le consentement global du premier démarrage ; pause/reprise et vérification SHA. Après provisionnement, lancement offline.
- Config et secrets hors dépôt ; plan d'installation/désinstallation et journal d'erreurs compréhensible ; rollback si installation incomplète.
- Outils lourds ou payants **jamais imposés**. Firecrawl auto-hébergé reste un profil optionnel.

## Sortie vérifiable
- [ ] Installation depuis Windows vierge validée, sous compte utilisateur standard lorsque possible.
- [ ] 2e installation idempotente, reprise après réseau coupé pendant téléchargement et réparation sans perte de configuration.
- [ ] Choix du volume, espace insuffisant, modèle trop gros, absence de GPU, manque de permissions et quotas affichés correctement.
- [ ] Ollama et exécuteur répondent à des healthchecks sans Internet après préparation.
- [ ] Test automatisé Windows + preuve d'un parcours interactif réel dans artefacts CI.

## Dépendances / interdictions
Dépend de #1 (règles). Fournit le bootstrap à #9. L'installateur lui-même et l'exécutable final ne sont pas encore livrés. Ne jamais déclarer installé un outil simplement parce que le downloader s'est lancé.

## Ajout obligatoire — Sélection automatique du LLM (sans jargon)

L'installateur ne doit **pas** demander « Quel modèle voulez-vous ? » ni imposer à l'utilisateur de comprendre quantification, paramètres ou backend. Par défaut il détecte matériel, modèles disponibles et cache, applique le profil `QUALITY_FIRST` puis propose « **Installer la meilleure configuration IA vérifiée pour ce PC** » avec taille de téléchargement et explication compréhensible.

- Le modèle est filtré par compatibilité RAM/VRAM/disque/contexte/outils ; le choix final est confirmé par essais contrôlés, jamais par le seul nombre de paramètres.
- Ne pas retélécharger un modèle déjà présent et fonctionnel. Les téléchargements nécessaires s'effectuent automatiquement sous la permission initiale, même sur connexion mobile illimitée ; sans modèle validé, le travail autonome est indisponible.
- Première configuration en un clic **après consentement pour les téléchargements lourds et les permissions** ; le nom/digest/quantification demeure dans la vue avancée seulement.
- Ne pas promettre qu'un 24–30B tient dans 8 Go de VRAM ; détecter CPU offload et tester stabilité / contexte réel.
- La calibration qualité et l'affectation alternée des modèles relèvent de #4 ; écran explicite et installation de #2 ; le contrat canonique est `docs/LLM_AUTO_SELECTION.md` dans #1.
- [ ] Sur machine neuve/partiellement équipée, aucun choix technique de modèle exigé.
- [ ] Sélection automatique vérifiée sur matériel 8 Go VRAM / 32 Go RAM ; fallback sûr si OOM.
- [ ] Reprise après téléchargement interrompu, autorisation globale unique au premier setup, volumes visibles, préservation des modèles existants et aucune micro-approbation par modèle.

## Passage sans intervention récurrente
L'utilisateur fournit ensuite **uniquement les roadmaps**. Les téléchargements et la sélection des modèles se font dans les autorisations initiales ; les permissions de GitHub sont également consenties une fois pour le dépôt exact. Suivre le contrat `docs/USER_AUTONOMY_PROFILE.md` de la PR #1, sans plafond mobile ni choix de modèles par nom. Cette exigence est à tester, pas déjà implémentée.

## Lot réel : assets ZIP versionnés et manifestes (21 octobre 2026)

- `holding/staged_install.py` : installation sous `components/<nom>/<version>-<SHA>` des **archives ZIP de runtime déjà approuvées**, après téléchargement/reprise SHA-256, vérification de la taille exacte et contrôle strict de chaque entrée ZIP (traversée, symlinks, fichiers spéciaux, doublons insensibles à la casse, noms Windows réservés, collisions fichier/dossier, limite de volume). Répertoire intermédiaire, publication finale par renommage, nettoyage en cas d'échec. Une version déjà installée est intégralement revérifiée avant réutilisation ; une version corrompue est refusée, **jamais écrasée ni réparée implicitement**.
- `holding/bootstrap_assets_cli.py` : prévisualisation **sans mutation** par défaut ; manifeste JSON de release limité, dont le SHA-256 doit venir d'une source indépendante fiable (installateur/répertoire de publication), URL HTTPS, budget des téléchargements, approbation explicite `--approve-install`. Le résumé `PAYLOADS_STAGED_NOT_VALIDATED` n'atteste pas l'état READY des exécutables.
- `tests/test_staged_install.py` et `tests/test_bootstrap_assets_cli.py` : cas de version réutilisée, archive malveillante, contenu modifié, autorisation, manif illisible/mal épinglé, échec partiel et annulation.
- **Frontière de sécurité :** le manifeste ne doit jamais provenir d'une roadmap ou d'une réponse de modèle sans pin de release validé. L'installation sous une identité Windows dédiée et ACL de répertoires sécurisées reste du ressort du vrai installateur.
- **Reste bloquant :** construire l'EXE, les intégrations natives Git/Ollama/OpenCode/Qt et leurs healthchecks après déploiement, la sélection des modèles par benchmarks #4, l'UI guidée/disque choisi et le scénario complet sur Windows propre. Les ZIP sont de simples données versionnées ; aucune commande téléchargée n'est exécutée par ce lot.

## Durcissement sondes locales (21 octobre 2026)

- `holding/probe_process.py` ne collecte plus les sorties avec `communicate()` sans borne : deux lecteurs simultanés plafonnent stdout et stderr à **32 KiB chacun** par défaut, détruisent le groupe de processus/Job Windows lors d'un dépassement ou timeout et refusent les descendants qui gardent des pipes ouverts.
- `tests/test_probe_process.py` couvre sortie normale, saturation stdout/stderr, timeout, descendant qui hérite des handles et paramètres invalides.
- Il s'agit du budget de la **sonde**, pas d'une preuve du budget RAM global du Control Center/holding, qui reste une exigence E2E de la PR #9.

## Inventaire des versions installées (21 octobre 2026)

- `inspect_versioned_zip()` lit le répertoire versionné sans créer de dossier, télécharger, lancer un exécutable ou réparer. Les retours sont `NOT_INSTALLED`, `VERIFIED_PAYLOAD` (hash de chaque fichier revérifié) et `BLOCKED_INVALID` (installation altérée, chemin lié ou état illisible).
- `bootstrap_assets_cli` renvoie ces observations dans le `PREVIEW_ONLY` avec un volume réseau prévu de **0 octet** si les fichiers correspondent déjà exactement au manifeste. Une installation corrompue produit `PREVIEW_BLOCKED` et un code de sortie d'échec ; aucune réparation silencieuse, ni suppression de données.
- La prévisualisation peut devenir périmée : avant tout réemploi, le téléchargement/installer recontrôle le paquet sous verrou. `VERIFIED_PAYLOAD` signifie uniquement **fichiers installés conformes**, jamais `READY` du programme tant que son healthcheck réel n'est pas passé.
- Testé par les nouvelles régressions de découverte sans mutation, état corrompu/symlink et indicateurs du CLI. **Dépendance restante** : brancher ces états à la GUI Windows et aux vraies vérifications Git/Ollama/OpenCode, avec des ACL natives.

## Exécution ciblée du plan de provisionnement (21 octobre 2026)

- `holding/provision_runtime.py` raccorde **l'inventaire mesuré** (`inventory.py`), les décisions `REUSE / INSTALL / REPAIR` (`provision_plan.py`) et les archives de release vérifiées (`staged_install.py`). Les composants `FOUND_VALID` sont **préservés** ; seuls les éléments INSTALL/REPAIR disposent d'un chemin de staging versionné.
- Avant toute mutation, le runner vérifie la compatibilité Windows/disque/réseau, les pins disponibles pour **tous** les composants requis et l'autorisation globale. Pas de pin ou de permission : `BLOCKED` / `AUTH_REQUIRED` sans installer un composant sur la base d'une simple supposition.
- Si un paquet de release correspondant est déjà conforme, il est réutilisé sans téléchargement. Une version corrompue bloque sans effacement, et un échec du 2e composant conserve un état `PARTIAL_OR_BLOCKED` plutôt que de déclarer un succès.
- Les tests `tests/test_provision_runtime.py` couvrent non-réinstallation, mandat, pins manquants, réutilisation, corruption, plusieurs lots et échec partiel.
- **Limites explicites :** fichiers préparés ≠ logiciels enregistrés/actifs, réparation PATH/services/native Windows à implémenter ; modèle IA non benchmarké ; premier EXE non construit. La couche d'installateur natif doit appliquer ACL, intégrité du manifeste signé et healthcheck réel avant d'annoncer la Holding opérationnelle.

## Point d'entrée intégré pour le futur setup (21 octobre 2026)

`holding/provision_cli.py` expose une commande d'installation sans choix manuel de modèle :

```powershell
python -B -m holding.provision_cli --manifest "C:\AtlasRelease\assets.json" --manifest-sha256 "<SHA256 signé/épinglé par la release>" --install-root "D:\Atlas Holding" --cache-root "D:\Atlas Cache" --online-confirmed
```

L'absence de `--approve-install` produit une prévisualisation `AUTH_REQUIRED` sans mutation ; le futur assistant graphique recueillera l'autorisation initiale puis rappellera **la même commande** avec l'approbation. `--online-confirmed` est un signal du programme de démarrage, pas une preuve réseau indépendante. Manifestes et versions sont des entrées **de confiance provenant de la release**. Aucune sortie « installé » ne vaut healthcheck natif ou benchmark IA.

Le module appelle réellement le scan du matériel, `make_plan()`, la prévalidation des pins et le staging sélectif. Les erreurs restent `BLOCKED` ou `PARTIAL_OR_BLOCKED`, sans exposer de secrets dans le JSON. Les tests `test_provision_cli.py` couvrent l'absence de consentement, un outil manquant, l'absence de modèle hors ligne, le manifeste incorrect et les chemins relatifs.

**Restant pour Windows final :** empaquetage sans Python préinstallé, formulaire de choix disque, catalogues authentifiés de versions réelles, acquisition Ollama des poids IA, ACL et comptes dédiés, healthchecks après installation, redémarrage/réparation et EXE E2E. Ne pas utiliser la CLI de développement comme substitut à `AtlasHoldingSetup.exe`.

## Assistant Windows et packaging contrôlé — 21 octobre 2026

- `holding/windows_setup.py` est un assistant tkinter dédié au **premier provisionnement** : choix du disque et des dossiers (application, cache), inventaire réel du matériel, distinction REUSE/INSTALL/REPAIR, estimation conservatrice des téléchargements, validation unique de l'utilisateur et progression en octets réels. Le travail disque/réseau est réalisé dans un thread dédié, avec annulation qui préserve les téléchargements partiels. La GUI n'exécute pas de code Python provenant d'une roadmap et ne considère jamais un simple paquet extrait comme un modèle prêt.
- `holding/provision_runtime.py` relaie l'annulation et les octets effectivement téléchargés au code de reprise SHA-256 existant, sans changer les étapes déjà validées. `tests/test_windows_setup.py` couvre le préflight sans écriture, paquet déjà installé, corruption, absence de pin, mode sans réseau ni modèle, consentement et relecture du système avant mutation.
- `holding/windows_build.py` permet, **sur un runner Windows uniquement**, de construire `AtlasHoldingSetup.exe` avec PyInstaller épinglé : manifeste JSON dont le SHA-256 est injecté dans l'exécutable, mode `--self-test` pour vérifier l'asset embarqué sans lancer Tk, contrôle du hash final et refus d'écraser un EXE existant. Aucun build n'est publié automatiquement.
- `.github/workflows/bootstrap_exe_smoke.yml` est un **test explicite d'empaquetage** (`ci:package-smoke`), non une release. Sa fixture `tests/fixtures/bootstrap_smoke_assets.json` contient une URL volontairement inutilisable et **ne peut pas installer Git/Ollama/OpenCode**. Un EXE produit par ce smoke ne doit jamais être distribué comme installateur utilisable.
- Les vrais manifestes de versions et checksums officielles, les binaires testés, la configuration des services, la gestion des modèles Ollama, les ACL Windows, les raccourcis, la reprise après redémarrage, le code signing et les tests E2E sur machine propre restent bloquants. La PR #9 porte la publication et la certification de l'EXE final ; ce lot ne revendique aucune release utilisateur.

## Sonde des exécutables installés — manifestes version 2

Le manifeste de release **v2** ajoute `executable` (chemin ZIP relatif se terminant par .exe) et `expected_version` à chaque composant. Le format historique v1 demeure accepté pour **staging uniquement**, sans revendiquer le healthcheck natif. Tout chemin `..\`, absolu, extension .bat/.cmd, champ manquant ou version invalide est rejeté.

`holding/native_health.py` confirme l'intégrité de chaque fichier du composant **avant et après** l'exécution, contrôle l'absence de lien pour l'EXE et exécute seulement `<exe> --version` dans le groupe/Job Object borné du superviseur ; toute version inattendue, timeout ou détérioration retourne un statut bloquant. Les sondes natales sont limitées aux composants Git, Ollama et OpenCode. Ce contrôle n'installe pas de service, ne configure pas PATH et ne prouve pas les appels API Ollama, l'appel d'outil OpenCode ou la qualité de l'IA.

**Validation finale de la PR #2 non satisfaite** avant installation réelle depuis machine Windows vierge, autorisation/ACL native et manifeste officiel épinglé, réparations/upgrade/reboot, vérification des vraies versions et intégration de l'EXE au processus de release #9.

## Acquisition des modèles Ollama (contrat de staging, non qualifié)

`holding/ollama_model_pull.py` permet à l'installateur autorisé de demander `POST http://127.0.0.1:11434/api/pull` avec le **nom exact et le digest SHA-256 épinglés** par le futur catalogue/benchmarks de #4. Ollama sait reprendre ses téléchargements interrompus ; la Holding consomme le flux NDJSON avec des limites strictes par message et par essai, progression octet réelle, annulation et timeout. Après le statut `success`, la Holding doit relire `/api/tags` et obtenir le **même digest** avant de déclarer `MODEL_VERIFIED_NOT_BENCHMARKED`.

Les modèles déjà présents au digest exact sont réutilisés. Une étiquette installée mais correspondant à un autre digest est bloquée sans remplacement. Hors ligne, sans consentement ou en cas de flux interrompu, aucun modèle n'est déclaré prêt et la commande n'efface rien.

**À raccorder** : catalogue vérifié #4, emplacement `OLLAMA_MODELS` piloté au lancement du service, outil d'installation natif Ollama, adaptation du wizard, calibration VRAM/contexte et benchmark de code et tool-calling indépendant. Le module seul ne choisit jamais un modèle sur ses paramètres et ne prétend pas que le téléchargement prouve sa qualité.
