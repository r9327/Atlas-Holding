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

## Staging natif de la paire EXE Holding (21 octobre 2026)

- `holding/native_runtime_stage.py` prend les **deux fichiers déjà construits** (Control Center et moteur autonome) ainsi que les SHA-256/tailles épinglés par la release. Les deux EXE sont vérifiés intégralement avant toute mutation ; copie vers un répertoire temporaire commun sur le volume de destination, seconde vérification des octets copiés, reçu `STAGED_UNREGISTERED` puis publication du dossier de version par renommage.
- Source corrompue, second transfert interrompu, changement de taille, symlink/jonction, reçu modifié ou version déjà présente mais altérée : refus explicite sans exécution des binaires, écrasement ni résultat « installé » mensonger. Une version strictement identique est réutilisée sans copie.
- Le mode **sans autorisation** renvoie `APPROVAL_REQUIRED` et ne crée pas de destination. Les pins sont une entrée *trusted installer* issue d'une release contrôlée, jamais du texte de l'IA. `tests/test_native_runtime_stage.py` couvre l'atomicité, l'idempotence, l'absence d'écrasement, les chemins liés et la panne du second fichier.
- **Non encore accompli :** ACL réelles Windows, lancement enregistré, choix du dossier dans le setup pour cette paire, récupération après redémarrage, signature/SmartScreen, intégration de ces fichiers dans `AtlasHoldingSetup.exe` et test utilisateur complet sur Windows vierge. Cette primitive prépare uniquement les octets vérifiés.

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

## Windows sans archive ZIP portable : winget épinglé

`holding/windows_package_manager.py` ajoute une voie Windows **autorisée et versionnée**, via le gestionnaire `winget` avec des identifiants strictement limités à `Git.Git`, `Ollama.Ollama` et `SST.opencode`. La commande réelle utilise `--id`, `--exact`, `--version`, `--source winget`, `--scope user`, `--silent`, les autorisations de contrat requises et le refus d'interaction, **sans shell ni élévation automatique** ; timeout/sortie/arbre de processus sont bornés. Le gestionnaire winget valide ses propres manifestes/hashes : ce mécanisme n'est pas une archive ZIP dont le SHA est épinglé par Atlas, et le statut reste `INSTALLED_NOT_HEALTHCHECKED` jusqu'à inventaire et validation native.

Le manifeste de confiance **v3** accepte des entrées typées :
- `{"kind":"winget","component":"git","package_id":"Git.Git","version":"<version exacte du catalogue>"}`
- `{"kind":"zip","component":"ollama","version":"<version>","url":"https://...","sha256":"<64 hex>","download_bytes":123,"unpacked_bytes":456,"executable":"ollama.exe","expected_version":"<version>"}`

La version et les hashes réels doivent provenir de la release approuvée avant le build. La CLI et le Wizard acceptent ces deux catégories, ne réinstallent jamais un composant jugé sain par l'inventaire, et signalent les **tailles de téléchargement winget comme inconnues** plutôt que les afficher à zéro. Aucune entrée v3 n'autorise une URL, un package ou une commande inventés par le LLM.

**Reste bloquant :** intégrer une release officielle avec versions/hashes contrôlés, recontrôler les exécutables après installation winget, configurer et valider le démarrage Ollama, l'emplacement des modèles, le sandbox OS/ACL, les redémarrages Windows, le benchmark Atlas et le build E2E final.

## Correctifs de certification du 21 octobre — exécutable et contrôle post-install

- L'entrypoint généré par `holding/windows_build.py` a été corrigé : il contient des vrais sauts de ligne, est analysé par `ast.parse` en test unitaire et propose `--self-test` sans ouverture de fenêtre, qui vérifie **le SHA du manifeste embarqué et son schéma**. Le test antérieur n'analysait que des fragments texte, ce qui pouvait masquer un EXE invalide.
- La CI `bootstrap_validation.yml` construit désormais un **EXE Windows réel, uniquement depuis une fixture non installable**, avec PyInstaller 6.22.3, puis exécute son auto-test. La fixture utilise une URL `example.invalid` : le binaire issu de la CI est une preuve de packaging, **pas une release utilisable**. Aucun artefact n'est publié et le répertoire de build est nettoyé.
- `windows_setup.execute_setup` refait un inventaire **après** l'installation et `assess_post_install` exige Git/Ollama/OpenCode/Python en état `FOUND_VALID` avec une **version réellement épinglée pour chaque composant ajouté**. Les états `POST_INSTALL_RECHECK_REQUIRED` et `TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING` ne peuvent être confondus avec un LLM certifié et fonctionnel.
- Un exécutable Ollama installé mais dont l'API est arrêtée ne déclenche plus une seconde installation par défaut : le plan retourne `INSPECT` et demande le diagnostic du service. La sélection de disque dans l'interface est attachée au préflight : tout changement après présentation invalide l'accord jusqu'à une nouvelle analyse.
- Bloquant pour la PR complète : confirmer cette nouvelle CI, intégrer un manifeste de distribution **officiel**, valider les divers scopes installateurs winget, activer le vrai service Ollama et les modèles, reprendre au reboot et exécuter un E2E Windows propre. Tous les tests automatisés restent distincts de la certification utilisateur.

## Résolution native des outils gérés, sans modification du PATH — lot complémentaire

`holding/managed_toolchain.py` résout Git/OpenCode/Ollama depuis les dossiers ZIP versionnés de la Holding : chemin **absolu** uniquement si le manifeste épinglé, tous les octets du paquet, l'exécutable et la sortie `--version` passent les vérifications, avec une seconde lecture d'intégrité après la sonde. Le binding reste une preuve **instantanée** : un futur lanceur doit revérifier avant chaque exécution.

`holding/windows_setup.py` prend maintenant en compte ces binaires contrôlés lors du **post-install de Git et OpenCode**. Ils peuvent ainsi fonctionner sans second installateur ni modification de `PATH` ; la seule extraction d'un ZIP ou une sonde de version Ollama **ne suffit jamais** à confirmer le service Ollama, qui doit répondre correctement sur son API loopback.

`holding/version_evidence.py` centralise l'identification **exacte** de la version attendue entre sonde native et inventaire post-install : une demande de `2.55.0` n'accepte pas `2.55.01`, `12.55.0`, `2.55.0-beta` ni `2.55.0.1` ; seul le suffixe officiel `.windows.N` est toléré pour Git.

Enfin, `holding/provision_cli.py` réalise lui aussi l'inventaire post-install, et ne retourne le code 0 pour une installation validée que si **l'outillage réel est sain** (statut `TOOLCHAIN_HEALTHY_MODEL_BENCHMARK_PENDING`). Un service encore arrêté ou un exécutable non découvert bloque proprement. Le benchmark modèle et l'E2E Windows restent obligatoires ; aucun statut `READY` n'est émis.

## Démarrage local Ollama / dossier de modèles distinct — lot complémentaire

L'assistant de premier lancement accepte maintenant un **troisième chemin distinct** : `--models-root` ou le sélecteur « Modèles IA (disque) ». Après consentement et installation des dépendances, il démarre `ollama serve` **uniquement en local** avec `OLLAMA_HOST=127.0.0.1:11434` et `OLLAMA_MODELS=<chemin absolu choisi>`. Il attend une vraie réponse du service via `/api/version`, puis inventorie de nouveau les programmes et les modèles. En cas d'erreur de lancement, délai dépassé, manque de disque, chemin symlink/jonction ou annulation, seul **son propre** processus peut être arrêté. Aucun processus Ollama extérieur n'est supprimé. L'installation ne lance aucun service sans autorisation.

Le plan détecte `START_SERVICE` si le **binaire Ollama existe mais son API locale est arrêtée**. Il ne réinstalle donc pas Ollama : hors ligne, les modèles sont déclarés « inventaire à refaire après démarrage », non « absents ». Le provisionnement laisse l'action au processus d'installation Windows après consentement. Les healthchecks natifs et l'inventaire réel restent bloquants pour la suite.

Si une autre instance Ollama répond déjà sur le port local, la Holding la réutilise sans la modifier, **mais n'atteste pas qu'elle utilise le nouveau disque demandé**. Ceci est nécessaire sur Windows, où des configurations de l'application desktop peuvent diverger de l'environnement de commande. Voir FAQ officielle : `https://github.com/ollama/ollama/blob/main/docs/faq.mdx` et incident public `https://github.com/ollama/ollama/issues/17374`.

`holding/ollama_storage.py` sauvegarde le choix **uniquement après le démarrage réussi d'une instance possédée par la Holding**, dans un fichier de préférences atomique sous le répertoire d'installation. Au lancement suivant, ce choix remplit le sélecteur ; un disque manquant, des métadonnées incompatibles ou un chemin à lien sont signalés. Il s'agit d'une **préférence**, pas d'une preuve de configuration de tout autre serveur Ollama. Aucune écriture dans les variables Windows globales, aucun autostart au redémarrage et aucune migration/suppression de modèles existants. Ces fonctionnalités doivent être traitées séparément et testées sur Windows réel avant certification.

## Reprise Ollama après connexion Windows — lot de certification

- `holding/ollama_resume.py` fournit la **reprise sans interface** à partir du manifeste de release livré dans l'EXE et du dossier de modèles explicitement sauvegardé. Elle refuse les configurations manquantes/falsifiées et les disques déconnectés, ne télécharge rien, ne remplace aucun modèle et refait le contrôle `/api/version` après le démarrage.
- `holding/windows_autostart.py` copie, **sans écraser une ancienne version différente**, l'exécutable PyInstaller lancé dans `<disque choisi>/AtlasHoldingAgent.exe` et en vérifie le SHA-256. Il n'accepte pas le Python source comme agent Windows. Après consentement distinct dans le Wizard, il crée uniquement `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run : AtlasHoldingOllama` ; la commande est courte (limite Windows 260 caractères), entièrement citée, pointe vers l'exécutable installé et passe `--resume-ollama <installation>`. Une entrée préexistante différente est préservée. La désactivation retire **uniquement** l'entrée exactement connue. Pas de `HKLM`, de droits administrateur, de modification globale du PATH ni des variables d'environnement.
- `holding/windows_build.py` reconnaît maintenant `--resume-ollama <install-root>` en plus du `--self-test` ; le mode headless valide le manifeste embarqué et se termine sans afficher l'assistant. Les autres arguments sont bloqués. La CI Windows inclut un smoke sans démarrage de serveur, prouvant qu'un ordinateur sans inscription préalable retourne un code d'erreur propre.
- L'activation à la connexion **n'est pas implicite** dans l'installation initiale : une deuxième autorisation apparaît uniquement après démarrage réussi d'une instance Ollama réellement gérée par Atlas et sauvegarde du chemin des modèles. L'EXE de test avec `example.invalid` ne constitue en aucun cas une release.

**Limites explicites :** lancement après **connexion utilisateur**, pas un véritable service Windows avant connexion ; l'ordre d'exécution de plusieurs programmes au démarrage Windows n'est pas garanti, et l'Ollama officiel peut déjà être inscrit au démarrage. Si un serveur externe s'est lancé avant nous, il n'est pas arrêté et son emplacement de stockage reste non attesté. Aucune migration forcée, aucun remplacement d'un binaire agent différent. L'autostart après un redémarrage réel ainsi que la coexistence avec l'application Ollama officielle restent à certifier sur PC Windows propre avant sortie de Draft.

Sources de contrat : Microsoft Learn « Run and RunOnce Registry Keys » (`https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys`) et FAQ officielle Ollama (`https://github.com/ollama/ollama/blob/main/docs/faq.mdx`).

## Candidats réels WinGet et vérification du disque modèles

Le fichier `release-candidates/winget-windows-x64-v3.json` contient une **sélection explicite à examiner**, sans installation déclenchée par son seul ajout :
- Git for Windows CLI `Git.Git`, version `2.55.0`.
- Ollama CLI/runtime `Ollama.Ollama`, version `0.32.8`.
- OpenCode **CLI** `SST.opencode`, version `1.18.29`. **Ce n'est pas** `SST.OpenCodeDesktop`.

Les IDs, versions et modes d'installation sont vérifiés à partir des index WinGet et des manifests disponibles publiquement, mais **pas encore depuis un Windows propre réel**. Les versions de ce candidat ne doivent pas être interprétées comme automatiquement les dernières ou les plus sûres. Le catalogue de production exigera un examen de provenance, sécurité, disponibilité des versions, portée utilisateur et tests E2E. Les téléchargements restent vérifiés par le mécanisme natif de WinGet, qui possède ses propres manifestes et SHA ; ces entrées de type `winget` ne sont **pas** des ZIP dont Atlas déclare lui-même connaître le SHA.

`holding/model_volume.py` exécute désormais un contrôle **strictement en lecture seule** du disque choisi pour les modèles : chemin absolu, pas de symlink/jonction, dossier valide, volume disponible et au moins 2 Gio libres. Un lecteur débranché/inaccessible ou insuffisant bloque la phase **avant** les mutations du runtime. Le Wizard affiche son espace libre à côté de celui de l'application. Cette borne de 2 Gio est un premier verrou et **ne vaut pas estimation du futur modèle** : sa taille réelle sera contrôlée depuis le catalogue/benchmark #4 avant téléchargement.

`holding/winget_binary.py` recherche après installation les binaires d'utilisateur aux chemins connus de Git, Ollama et OpenCode CLI portable (`%LOCALAPPDATA%\\Microsoft\\WinGet\\Packages`). Il ne parcourt pas librement le disque : chemins autorisés, budget de fichiers, refus des liens, contrôle `--version` par processus borné, version complète strictement égale à la version WinGet du candidat. Le Wizard reconnait Git et OpenCode ainsi installés même si le `PATH` hérité n'a pas été rafraîchi ; `ollama_service.py` les utilise pour lancer Ollama. `holding/post_ollama_inventory.py` vérifie **à la fois** cet exécutable et l'API Ollama réelle lors du contrôle après installation et lors de la reprise Windows : le succès du paquet ou de l'API seule ne suffit pas.

**Contrat final** : aucune déclaration `READY` tant qu'une vraie installation WinGet/ZIP avec versions officielles, authentification manifeste, modèle local reconnu, benchmark de qualité et test de redémarrage Windows sur machine propre n'a pas réussi.

Sources de vérification documentaire :
- `https://github.com/microsoft/winget-pkgs/blob/master/manifests/g/Git/Git/2.55.0/Git.Git.yaml`
- `https://winget.tulio.org/pkg/Ollama.Ollama/`
- `https://wingetly.io/apps/sst/opencode`
- `https://learn.microsoft.com/windows/package-manager/winget/settings`

## Relance de la Holding et validation des manifestes WinGet — lot supplémentaire

`holding/pinned_inventory.py` reconstruit l'inventaire **à chaque lancement** après le scan classique : Git et OpenCode ne sont considérés disponibles qu'après vérification native de la version exacte du ZIP Atlas ou du binaire utilisateur WinGet. Pour Ollama, un exécutable exact n'est pas une API saine ; si le service est arrêté, son état `FOUND_BROKEN` conserve son chemin et conduit à `START_SERVICE`, sans inventer les modèles. Les processus Git, Ollama ou OpenCode valides déjà présents dans le PATH sont réutilisés, sans remplacement. Le Wizard et le CLI utilisent ce même inventaire, ce qui évite de réinstaller ces composants à chaque exécution sur une machine dont le PATH n'a pas été modifié.

`install_pinned_winget` exécute désormais `winget show --id <id> --exact --version <version> --source winget --scope user --architecture x64` **avant** `winget install`, avec une échéance et une sortie bornées. Un paquet absent, une version retirée ou une portée utilisateur non prise en charge bloque l'installation du paquet concerné ; les libellés localisés de WinGet ne sont pas interprétés arbitrairement par des regex fragiles. Comme WinGet reste maître du manifeste qu'il consomme, le post-install exige toujours sa propre preuve native de version, et Ollama exige également une API fonctionnelle.

La source authentique `microsoft/winget-pkgs` a été consultée pour les versions candidates ; les URL upstream et empreintes SHA256 Windows x64 figurent dans `release-candidates/winget-windows-x64-installer-evidence.json`. Le manifeste Git 2.55.0 précise à la fois `Scope: user` et `ElevationRequirement: elevatesSelf` : la possibilité d'une élévation Windows demeure **un bloqueur d'autonomie** à lever au test réel (ou via un ZIP portable équivalent vérifié). Le manifeste Ollama 0.32.8 fournit un programme Inno Setup par utilisateur. OpenCode 1.18.29 fournit un ZIP portable x64. Ces empreintes documentent la revue des versions ; la distribution WinGet conserve sa vérification d'installateur, elles ne prouvent pas qu'une vraie installation de ce lot a été exercée sur un PC Windows vierge.

**Non certifié :** installation WinGet native bout à bout sur un runner Windows vierge, garantie sans UAC de Git, démarrage réel Ollama Desktop versus Atlas, reboot effectif, signatures et manifestes du Setup de production, benchmark qualité modèle #4. PR restant Draft.

## Solution officielle Git portable, sans UAC — candidat MinGit 2.56.0.2

Le paquet initial Git 2.55.0 via WinGet annonçait `ElevationRequirement: elevatesSelf` et sa première publication présentait une régression NTLM. Ne pas l'imposer comme solution d'installation autonome sans intervention.

Le chemin retenu pour lever cette dépendance à Inno Setup est **MinGit**, le ZIP portable natif officiel de `git-for-windows/git` version `v2.56.0.windows.2` (5 octobre 2026), avec Git `2.56.0`. Le chemin stable du binaire est `cmd/git.exe` : l'éditeur déconseille de dépendre de `mingw64/bin/git.exe` depuis le passage à UCRT64. Un ZIP correctement vérifié peut être installé sous le disque Holding choisi sans appeler d'installateur MSI/Inno Setup, modifier le registre, ouvrir d'UAC ou modifier le PATH global.

- **Fichier de provenance :** `release-candidates/mingit-2.56.0.2-x64-evidence.json`, SHA-256 éditeur `da35e72aa21c005a5a0d298cfbae110bc1609a815730ea0dde84b01a1b3cd3be`. Source immuable : `https://github.com/git-for-windows/git/releases/tag/v2.56.0.windows.2`.
- **Candidat préparé à partir des octets réels :** `python -m holding.mingit_candidate --archive <absolute MinGit ZIP> --output <absolute candidate manifest.json>`. Cette commande **ne télécharge, n'exécute ni n'installe quoi que ce soit**. Elle compare d'abord toute l'archive avec le SHA public de l'éditeur, mesure sa taille compressée et la somme exacte des fichiers décompressés depuis le répertoire central, rejette les membres ZIP dangereux, exige `cmd/git.exe` et produit un **manifeste v3 hybride** : Git portable + Ollama WinGet + OpenCode CLI WinGet. Le SHA global de ce manifeste est renvoyé pour son futur épinglage indépendant dans l'EXE. Les tailles non vérifiées ne sont jamais inventées.
- **Certification Win32 native ciblée :** le job Windows `ci:validate` télécharge temporairement l'archive Git *directement depuis le dépôt officiel*, vérifie son SHA éditeur, construit le manifeste de test, installe le ZIP dans un dossier temporaire via le runtime Atlas existant, refait l'authentification de tous les fichiers et la vérification `git --version`, et réalise un vrai `git init`, `git add`, `git commit`, `git rev-parse HEAD` et `git status --porcelain`. Aucun exécutable n'est enregistré globalement, aucune modification de PATH ni installation système, et le dossier est détruit par le runner. C'est une validation native du *parcours portable Git*, **pas** une installation complète de la Holding, ni un test de redémarrage utilisateur.

**Bloqueurs restant à lever pour déclarer la PR #2 terminée :** validation WinGet Ollama/OpenCode, conflit possible Ollama Desktop préinstallé, véritable démarrage après reboot Windows, signature/code signing de l'EXE final, et certification croisée avec la sélection qualité des IA #4. Le fait de disposer d'un Git portable ne suffit pas à déclarer tout le Setup autonome. La PR reste Draft.

## OpenCode portable réel, manifeste ZIP double et précontrôle WinGet natif

`holding/portable_runtime.py` ajoute la construction reproductible de la sélection **Git portable + OpenCode CLI portable + Ollama WinGet utilisateur**. Son entrée : les deux ZIP officiels préalablement récupérés sur GitHub. Aucune taille n'est fabriquée : avant de produire `manifest.json`, les archives passent leurs SHA-256 d'éditeur, le contrôle de structure ZIP (traversal, collisions de noms, symlinks, volume maximal) et la présence du binaire `cmd/git.exe` ou `opencode.exe`. Les tailles compressées et décompressées sont mesurées à partir des octets vérifiés. Le manifeste hybride v3, y compris son empreinte globale, devient un candidat pour le vrai `AtlasHoldingSetup.exe`, **pas** une release ni une autorisation d'installation automatique.

Pour OpenCode, référence `SST.opencode@1.18.29` manifestée par `microsoft/winget-pkgs` : ZIP `https://github.com/anomalyco/opencode/releases/download/v1.18.29/opencode-windows-x64.zip`, SHA-256 fournisseur `b32618aa3d1415f6e4f473aec248edef25759203fb707d7d968359d86d4a35ee`. `holding/opencode_smoke.py` installe le ZIP dans un dossier temporaire sur Windows avec le même runtime Atlas : contenu et version native vérifiés, puis vraie commande `opencode --help` sans provider, compte, clé API ni appel de LLM. Cela teste la **CLI locale**, pas la qualité du modèle ni les appels d'outils Codex/agent.

Le workflow de certification charge aussi les métadonnées `Ollama.Ollama@0.32.8` avec la commande **native et en lecture seule** `winget show --scope user --architecture x64`. L'installateur Ollama réel et le redémarrage d'un vrai daemon ne sont **pas** lancés par ce contrôle. La mise en place WinGet Ollama sur PC Windows propre, le stockage des modèles et le redémarrage avec Ollama Desktop restent des portes de sortie explicites.

**Correctif de version importante :** `assess_post_install` utilise désormais `PinnedZip.expected_version` pour les versions natives ZIP, au lieu de comparer systématiquement `PinnedZip.version`. Sans cela, l'archive officielle `MinGit 2.56.0.2`, dont `git --version` rapporte `2.56.0.windows.2`, était injustement refusée après installation malgré un ZIP entièrement valide et un outil sain. Un test de régression couvre ce cas.

## Campagne réelle Ollama sur un Windows éphémère (distincte de la CI ordinaire)

`holding/ollama_native_smoke.py` propose une **véritable preuve native ciblée** : autorisée uniquement quand `GITHUB_ACTIONS=true`, `platform.system() == Windows` et un `RUNNER_TEMP` absolu sont présents ; elle refuse un Ollama ou une API préexistante ; installe `Ollama.Ollama@0.32.8` via le même `install_pinned_winget` utilisé par le Setup ; découvre le binaire réel dans l'installation utilisateur ; démarre sa propre instance `ollama serve` sur la boucle locale avec `OLLAMA_MODELS` sur un répertoire de test dédié ; vérifie les routes `/api/version` et `/api/tags` avec version exacte ; puis arrête **uniquement** son processus possédé. Pas de téléchargement de modèle, pas de secrets, aucun processus utilisateur tiers terminé. Les installations ont lieu seulement dans la VM GitHub éphémère, qui est détruite après l'exécution.

La nouvelle workflow `.github/workflows/ollama_native_validation.yml` est **indépendante, déclenchée explicitement** uniquement par le label `ci:native-ollama` ; elle ne s'exécute ni à chaque commit ni dans la CI standard `ci:validate`. Les tests unitaires ajoutés garantissent le blocage sur une machine non CI, le refus de prise de contrôle d'une installation existante, la détection d'une erreur d'installation et l'arrêt du daemon de test même après une erreur de version.

**Interprétation des preuves** : `winget show` réussie = installateur exact disponible ; `ollama_native_smoke.py` réussie = WinGet utilisateur, binaire exact et API locale testés nativement sur un Windows de runner. Ce n'est toujours **pas** un test avec un vrai modèle chargé, un GPU utilisateur, une connexion Windows après reboot, ni une certification de la coexistence Ollama Desktop, qui nécessitent le poste cible.

## Constat CI réel : WinGet Ollama lance lui-même le serveur

Premier essai ciblé `ci:native-ollama` sur `windows-2025` au SHA `9a559681` : le paquet utilisateur Ollama 0.32.8 est installé et son binaire exact découvert, mais l'API sur `127.0.0.1:11434` est déjà active quand le moteur Atlas tente son `ollama serve`. Ce n'est **pas** une preuve du chemin de modèles demandé par Atlas et la première version du test a **FAIL**, sans forcer la fermeture d'un daemon extérieur (Actions #39596772600).

Correctif : `ollama_native_smoke.py` accepte désormais, seulement dans cette VM jetable et **après** constat initial qu'aucune installation/daemon n'existait, de vérifier la vraie version serveur et `/api/tags` si WinGet a auto-démarré Ollama. La sortie doit alors indiquer `OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED` avec `model_volume: NOT_ATTESTED`, **jamais** `OLLAMA_WINDOWS_NATIVE_API_PASS` (réservé à un processus démarré et contrôlé par Atlas). Aucun daemon extérieur n'est arrêté. Les tests de régression distinguent les deux chemins et les courses d'apparition sur le port.

Ce constat révèle un **verrou produit à part entière** : l'installeur Ollama peut prendre le port et le stockage par défaut avant l'agent Atlas. La certification de l'emplacement des modèles, du redémarrage Windows et de la coexistence Ollama Desktop ne peut pas être inférée de la réussite WinGet/API ; il faudra une stratégie de configuration consentie et vérifiable avant la certification finale.

## Essai natif Ollama : temporisation lors de l'auto-démarrage utilisateur

Deuxième campagne `ci:native-ollama`, [Actions #39597630994](https://github.com/r9327/Atlas-Holding/actions/runs/39597630994) : l'installation WinGet utilisateur a abouti et le binaire exact a été détecté ; lors du démarrage explicite du service, son processus s'est terminé avec `PROCESS_EXITED`. Le serveur lancé automatiquement par l'installateur peut entrer en concurrence sur le port 11434 pendant quelques secondes après le retour de WinGet. **Le test est FAIL**, car il n'a pas pu vérifier l'API avant de conclure ; la présence du paquet ne vaut donc pas succès de la phase serveur.

Le test natif dispose maintenant d'une attente **courte et bornée** uniquement sur ce cas `PROCESS_EXITED` : il n'essaie pas de tuer le processus extérieur, ne relance pas en boucle `ollama serve`, et n'accepte la reprise qu'après succès complet sur `/api/version` (version exacte) **et** `/api/tags` (liste structurée). Si l'API reste indisponible, le test reste FAIL. Toute reprise d'un daemon extérieur reste marquée `OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED` — le chemin réellement utilisé pour les modèles n'est pas attesté. Les tests incluent le délai d'apparition et l'échec final.

## Stockage sur disque choisi : configuration utilisateur avant Ollama Desktop

La documentation officielle `https://docs.ollama.com/windows` précise que **l'application Windows Ollama démarre en arrière-plan** après installation, que l'installation utilisateur n'exige pas d'administrateur, et que le stockage des modèles dépend de la variable **utilisateur** `OLLAMA_MODELS` (Settings → Variables d'environnement de ce compte). Le simple fait de passer `OLLAMA_MODELS` à une instance `ollama serve` démarrée après l'installation était donc insuffisant si Ollama Desktop avait déjà pris le port.

`holding/windows_ollama_environment.py` fournit une configuration **HKCU\\Environment uniquement** après autorisation explicite : disque et chemin validés, création si la valeur n'existe pas, acceptation idempotente si la valeur existante est strictement identique, refus explicite si la valeur appartient déjà à un autre chemin/format. Aucune mutation de `HKLM`, aucun accès administrateur, aucune suppression/modification de la valeur extérieure. Un rollback est possible **uniquement** si la valeur créée correspond encore au chemin attendu ; le rollback est employé lors d'un échec d'installation avant tout succès Ollama confirmé.

Le Wizard `windows_setup.execute_setup` installe cette préférence **avant** de déclencher le WinGet utilisateur lorsqu'Ollama est initialement absent. Le consentement affiché à l'utilisateur explique la création possible de `OLLAMA_MODELS`. `stage_required_dependencies` passe le répertoire choisi au seul paquet WinGet Ollama et `install_pinned_winget` place `OLLAMA_MODELS` dans l'environnement explicite du processus installateur pour couvrir le démarrage immédiat, sans modifier le PATH système.

**Limites honnêtes :** la présence de la valeur HKCU et l'environnement de l'installateur sont deux preuves de configuration, **pas** une preuve d'où un processus déjà en cours lit ses modèles. Si l'API est déjà lancée, Atlas ne peut ni l'arrêter arbitrairement ni prétendre qu'elle utilise le nouveau disque. Le stockage reste `NOT_ATTESTED` tant qu'une preuve de runtime n'existe pas ; les modèles ne sont jamais déplacés de force. La coexistence avec le Desktop et la reprise au prochain sign-in doivent être vérifiées sur PC Windows ciblé. Le choix d'installation des **binaires Ollama eux-mêmes** vers un autre disque nécessite encore un installateur natif spécifique (`OllamaSetup.exe /DIR=...`), non couvert par WinGet user scope.

## Réconciliation Ollama Desktop et livraison candidat avec pins officiels

À la reprise du SHA `c50da6df`, la CI générale avait certifié **285/285 tests sur Windows et Linux** ([Actions #39599948416](https://github.com/r9327/Atlas-Holding/actions/runs/39599948416)) et l'installation native Ollama sur le même commit avait **PASS** ([Actions #39600264788](https://github.com/r9327/Atlas-Holding/actions/runs/39600264788)). Verdict natif : `OLLAMA_WINDOWS_AUTOSTART_API_PASS_STORAGE_UNVERIFIED` et `api_version=0.32.8`. Ce résultat n'est pas une preuve de stockage effectif des modèles et ne nécessite pas d'arrêter Ollama Desktop.

**Correctif de continuité** : `inspect_user_models` vérifie en lecture seule la valeur HKCU `OLLAMA_MODELS`, son type `REG_SZ` et la disponibilité du volume. Si le vrai serveur Ollama est déjà actif et la variable utilisateur exactement conforme, `ensure_selected_model_volume` enregistre le chemin du disque **avec provenance distincte** `consented_user_ollama_environment` dans le fichier de paramètres Holding. Il retourne explicitement `USER_ENV_CONFIGURED_API_STORAGE_UNATTESTED`. Aucune fonction ne prétend que l'instance déjà active utilise ce dossier. Un chemin différent déjà enregistré n'est plus écrasé ; le nouveau login refuse de redémarrer avec une préférence Windows modifiée depuis l'acceptation précédente. Les tests couvrent collisions, disques retirés, version conservée et provenance.

**Certification améliorée** : la CI Windows télécharge les vrais ZIP Git et OpenCode, vérifie leurs SHA-256 éditeurs, les exécute réellement dans le smoke VCS/CLI, puis **dérive le manifeste v3 hybride exact depuis les octets officiels**, avec tailles mesurées plutôt que fabriquées. Elle construit ensuite `AtlasHoldingSetup.exe` de test **avec ce manifeste candidat réel**, et valide son `--self-test` et son refus de relance non configurée. Cette EXE reste jetable et non publiée : aucun benchmark modèle, redémarrage matériel réel, code signing ou validation de release n'est implicite.

Le test natif Ollama doit également prouver la relecture réelle du chemin choisi dans **HKCU Environment** et dans le fichier Holding, tout en maintenant le résultat API/stockage séparé. L'installation via OllamaSetup conserve son automatisme utilisateur ; pas de prise de contrôle forcée du Desktop.

## Bundle GUI + moteur dans l'installateur Windows (candidat, non-release)

Le constructeur `holding/windows_build.py` accepte désormais les deux
exécutables compilés de la PR #11, chacun avec son empreinte SHA-256 exacte.
Le build `AtlasHoldingSetup.exe` embarque leurs octets dans sa ressource
`runtime/`, à l'intérieur du **même EXE** ; aucun deuxième téléchargement
n'est nécessaire pour ces deux fichiers quand cette option est activée.
Les originaux et leurs copies de staging sont recontrôlés avant empaquetage,
puis les binaires extraits sont recontrôlés au démarrage de l'EXE et dans
`--self-test`. Les liens, tailles impossibles, fichiers absents et empreintes
divergentes sont refusés avant de construire un installateur.

Le runner de confiance peut fournir :
`--runtime-directory <dossier> --viewer-sha256 <SHA256> --engine-sha256 <SHA256>`.
Les versions anciennes du constructeur restent compatibles sans cette option ;
**ce mode ancien ne contient pas de pair GUI/moteur et ne peut donc pas
satisfaire le gate de publication de la PR #9**.

**Limite restante** : intégrer l'extraction atomique vers le disque choisi,
les ACL Windows de l'identité installée, puis le lancement natif du moteur et
du Control Center depuis ce chemin. Le fait d'embarquer le binaire ne garantit
pas sa mise en service : ce build demeure non-release et la PR reste Draft.


### Staging après consentement — nouveaux exécutables embarqués

L'assistant Windows reçoit les empreintes fixes et les fichiers extraits du
setup autonome. Après validation des composants préexistants et autorisation
explicite de l'utilisateur, il copie la paire dans le dossier choisi
(`runtime/<version-des-SHA>/`) et recontrôle leurs SHA-256. Une paire saine
est réutilisée au prochain passage ; une paire endommagée exige une réparation
explicite et n'est jamais écrasée silencieusement.

L'opération retourne `RUNTIME_STAGED_ENGINE_CONFIG_PENDING`, **pas READY**.
Même si Git/Ollama sont déjà présents et sains, l'UI conserve le bouton
d'autorisation nécessaire pour installer la paire de binaires. Le provisioning
des identités/ACL Windows, la génération de la configuration fiable v3 et le
lancement de l'EXE moteur/GUI n'ont pas encore passé de test d'installation
complet et restent des blocages de certification.

## Vérification native du setup avec deux ressources embarquées (21 octobre 2026)

Le workflow Windows de la PR #2 dispose d'un **test distinct du smoke historique** : `holding/native_embedded_setup_smoke.py` fait construire un véritable `AtlasHoldingSetup.exe` avec ses deux ressources `runtime/` épinglées. L'EXE produit doit démarrer en `--self-test`, extraire ses ressources PyInstaller et valider leurs SHA-256. Le test vérifie ensuite que le staging sur un dossier de disque choisi contenant espaces et accents ne fonctionne qu'après autorisation, réutilise une paire intègre et refuse d'écraser un moteur altéré.

Les deux fichiers EXE de cette épreuve sont **des fixtures synthétiques, pas des applications fonctionnelles**. Ce smoke certifie uniquement le *câblage d'emballage/extraction/staging*, sans revendiquer le lancement du moteur depuis le setup. La PR #11 teste séparément le véritable moteur et le vrai Control Center compilés. Les deux chaînes devront être réunies sur un seul SHA intégré avant une release. Les ACL Windows et l'E2E de machine vierge restent bloquants.

## ACL natives des binaires installés (21 octobre 2026)

L'installation consentie de la paire `AtlasHoldingEngine.exe` /
`AtlasHoldingControlCenter.exe` applique sous Windows un **DACL protégé
sans héritage d'autorisations de tiers** au dossier `runtime`, au répertoire
versionné, à la zone de copie temporaire et à chaque exécutable avant
publication. Le vérificateur Win32 `holding/windows_runtime_acl.py` lit le
SID de l'utilisateur du processus, l'owner réel et chaque ACE du descripteur
système ; les seules identités autorisées sont cet utilisateur, SYSTEM et les
Administrateurs, en accès complet. Lorsque le propriétaire Windows est le
groupe Administrateurs, cette situation n'est acceptée que si le jeton du
processus démontre l'appartenance administrateur active via
`CheckTokenMembership` (pas seulement une appartenance UAC désactivée). Une ACL héritée, ouverte à Everyone/Users,
mal formée, non inspectable ou située sur un disque incapable de représenter
ce descripteur bloque la préparation. Une paire déjà installée n'est jamais
réutilisée sans recontrôle des ACL **et** SHA-256.

Ces garde-fous sont exercés par `tests/test_windows_runtime_acl.py` sur
l'OS Windows natif ; sous Linux ils refusent de feindre un verdict ACL
Windows. Le résultat reste volontairement `SECURITY_PENDING` :
l'intégrité globale du chemin d'installation et la séparation des droits
du lanceur par rapport aux autres processus de même utilisateur doivent
encore être certifiées, ainsi que le service réellement démarré.


## Lancement contrôlé du moteur installé (candidat, sans déclaration READY)

`holding/windows_runtime_launch.py` ajoute une frontière distincte après la
copie des binaires. `verify_installed_engine(spec)` refuse toute exécution
hors Windows natif, sans chemins canoniques situés sous le volume sélectionné,
sans une paire de binaires SHA-256 exacte, sans DACL native vérifiée pour les
binaires et leur répertoire, ou sans configuration moteur JSON v3 protégée,
épinglée au même Control Center installé. Un fichier de configuration en dehors
du dossier `config/`, un autre exécutable GUI, des clés JSON dupliquées et
une modification des octets après l'inscription sont refusés.

`launch_installed_engine(spec, approved=True)` lance uniquement le binaire
moteur vérifié avec `--config <fichier vérifié>`, sans interpréteur de
commandes, secret CLI, héritage des sorties ni variables d'environnement
arbitraires. La fenêtre Qt doit être ouverte ensuite par le moteur via le
canal IPC privé de la PR #11, pas par un second lancement non authentifié.
La réponse `ENGINE_SPAWNED_HEALTH_PENDING` n'affirme **jamais** que le moteur
ou les agents sont prêts : il faudra une preuve du service vivant et du cycle
roadmap/CI/Doctor complet.

**Important :** ce module est une brique contrôlée en attente du provisioning
effectif de la configuration moteur et de la sélection du compte Windows.
Le wizard ne produit pas encore cette configuration ni ne l'active
automatiquement, et la version définitive de l'installateur reste bloquée
par la PR #9. Tests : `tests/test_windows_runtime_launch.py`.

### Refus explicite des sessions Windows non interactives

Le lancement contrôlé ci-dessus vérifie maintenant l'appartenance du processus
à une session utilisateur Windows interactive via `ProcessIdToSessionId`.
Les services Windows en session 0 et les contextes dont la session ne peut
pas être déterminée ne peuvent pas démarrer un moteur censé posséder une
fenêtre Qt. Le refus intervient **avant** toute création de processus et
avant le chargement d'une configuration active. Les tests de régression
couvrent cette règle séparément des ACL et des signatures SHA-256.


## Blocage du lancement hors bureau Windows interactif

Le lancement de l'exécutable moteur vérifie maintenant par les API Win32
`ProcessIdToSessionId`, `GetProcessWindowStation` et
`GetUserObjectInformationW(UOI_FLAGS)` que la Holding s'exécute **dans une
session utilisateur non nulle disposant d'une window station visible**.
Les services en Session 0, les processus sans station visible et les appels
natifs impossibles sont refusés avant `Popen` : un processus démarré
silencieusement ne doit pas être confondu avec une interface utilisable.
Cela s'ajoute aux contrôles SHA-256 et ACL des exécutables et de la
configuration. Les tests du module simulent explicitement ces cas d'échec.

Ce contrôle ne vaut pas encore installation terminée : le compte Windows,
le schéma de configuration moteur, le premier cycle roadmap et les preuves
d'E2E restent des gates séparés.


### Inscription déterministe de la configuration moteur v3

Le nouveau module `holding/windows_engine_enrollment.py` impose un
consentement initial, des répertoires `data/` et `config/` sur le disque
choisi avec ACL Windows protégées, les exécutables Control Center/moteur
déjà vérifiés, un exécutable d'agent épinglé, les arguments de code épinglés
et un dépôt canonique existant. Il produit une **configuration JSON v3
entièrement déterministe** compatible avec le chargeur moteur de la PR #11,
sans autorisation GitHub implicite et sans secret dans le JSON.

La publication du fichier se fait via un temporaire protégé, écriture
synchronisée puis renommage. La version identique se réutilise ; une
configuration différente ou abîmée exige une réparation explicite et n'est
jamais écrasée. Le résultat reste `CONFIGURED_ENGINE_HEALTH_PENDING` :
l'inscription ne prouve pas que l'agent, son modèle IA, les plugins ou
l'interface sont réellement opérationnels.

Les tests `tests/test_windows_engine_enrollment.py` vérifient le contenu,
les SHA, les échecs d'ACL, les chemins, la réutilisation et la compatibilité
réelle avec `verify_installed_engine()` (ACL simulées uniquement dans les
tests transplateformes). **Reste indispensable** : brancher cet onboarding
sur les vrais adaptateurs d'agents validés et réaliser l'E2E interactif sur
Windows vierge avant toute fusion ou release.

La validation complémentaire `test_real_windows_acl_enrollment_and_cross_module_launch_preflight` s'exécute **uniquement sur le runner Windows natif** : elle crée les DACL protégées des deux fichiers EXE, du dossier `data/` et de `config/`, inscrit réellement `engine.json`, revérifie son ACL puis lui fait passer la vérification intégrale du lanceur. Une configuration et des fichiers réellement protégés sont ainsi testés sans prétendre qu'un cycle IA complet est achevé.

### Passage contrôlé de l'inscription au lanceur natif

`prepare_installed_engine_launch(request, enrollment)` relie maintenant le reçu
`engine.json` de l'inscription à `InstalledEngineSpec` du véritable lanceur.
Il revérifie le dépôt/agent approuvé, les deux EXE, les octets exacts du JSON,
le SHA du reçu et les ACL natives, puis appelle `verify_installed_engine`.
Un reçu ancien, substitué ou un agent remplacé bloquent **avant** la création
du processus. Cette fonction **ne lance rien** : `launch_installed_engine` exige
un consentement distinct et retourne encore `ENGINE_SPAWNED_HEALTH_PENDING`.
Les scénarios d'acceptation sont ajoutés à `test_windows_engine_enrollment.py`.
L'exposition dans le wizard, la configuration automatique d'un agent réellement
fonctionnel, l'observation de santé du moteur et l'E2E Windows restent à réaliser.

### Protéger aussi le dossier d'installation parent

Le contrôleur refuse désormais l'inscription et le lancement si le **dossier
d'installation sélectionné lui-même** ne passe pas le contrôle ACL Windows.
Sécuriser seulement `runtime/` et `config/` ne suffisait pas si un autre compte
pouvait renommer leurs parents. Le premier passage exige que le provisioner
protège explicitement le dossier racine, sans corriger en silence les ACL
d'un dossier utilisateur existant. Les tests natifs sécurisent la racine et
recontrôlent 7 cibles ACL à chaque préflight de lancement.

`launch_enrolled_engine` impose un consentement supplémentaire, revalide le reçu
v3 et les EXE puis appelle le vrai lanceur. Le résultat ne revendique que
`ENGINE_SPAWNED_HEALTH_PENDING` : ni un agent sain ni une release certifiée.

### Parcours wizard → adaptateur embarqué → moteur natif (22 octobre 2026)

Le wizard demande le clone Git existant de `r9327/Dof-Atlas` et n'autorise
« Configurer / lancer le moteur » que si les deux EXE et
`AtlasHoldingAgent.exe` ont été installés avec des SHA-256 correspondant
aux ressources du setup. Les étapes restent explicitement séparées :

1. Le packager peut embarquer `AtlasHoldingAgent.exe` avec son SHA-256, mais
   **aucun binaire d'agent fonctionnel n'est fourni ni certifié par ce lot**.
2. Après consentement, le setup copie l'adaptateur sous `agents/`, le
   protège par ACL native et refuse de remplacer un binaire endommagé.
3. Le premier lancement peut protéger le dossier d'application dédié, mais
   refuse les racines de disque, profils et répertoires système. Les ACL d'un
   autre dossier de données ne sont jamais corrigées automatiquement.
4. Le wizard inscrit `engine.json` puis demande séparément l'autorisation
   de lancer le processus moteur. Le choix « Configurer uniquement » permet
   de revenir plus tard lancer sans perdre l'inscription.
5. Les reçus portent `CONFIGURED_ENGINE_HEALTH_PENDING` ou
   `ENGINE_SPAWNED_HEALTH_PENDING`, jamais `READY` sans preuve d'exécution.

Le setup de test sans adaptateur embarqué garde l'activation **désactivée**.
OpenCode/Ollama ne sont pas arbitrairement substitués à l'adaptateur JSON v1.
Reste : produire l'adaptateur réellement compatible, intégrer la paire
GUI+moteur depuis la PR #11 sur un SHA commun, valider le protocole de santé,
les permissions Windows réelles, l'E2E machine propre et la certification
avant release.

### Enrôlement du modèle Ollama local réellement installé

Le wizard sélectionne maintenant un nom de modèle issu exclusivement
de `GET http://127.0.0.1:11434/api/tags` ; les réponses sont bornées,
sans proxy et les noms/digests sont vérifiés. Un modèle absent, ambigu
ou dont l'empreinte est modifiée bloque l'activation.

`holding/windows_model_enrollment.py` écrit uniquement après
consentement un `agents/AtlasHoldingModel.json` protégé par ACL Windows
et épinglé SHA-256, avec les cinq champs attendus par le protocole Ollama
de la PR #11 : `schema=1`, `model`, `digest`, `num_ctx`, `timeout`.
La configuration de l'agent dans `engine.json` contient désormais
**l'argument absolu du fichier** et son empreinte. Sans choix d'un modèle
vérifié, le moteur n'est pas inscrit. Les réutilisations sont idempotentes,
un fichier différent exige réparation explicite et le modèle ne reçoit
aucune autorisation GitHub.

Limites : le choix d'un modèle installé ne vaut ni benchmark qualité,
ni modèle chargé en VRAM, ni preuve d'agent sain. Le couple de vrais
binaires GUI/moteur/agent doit encore provenir du même SHA intégré
(PR #2 + PR #11), puis passer les tests Windows et une mission réelle
avant toute publication.
