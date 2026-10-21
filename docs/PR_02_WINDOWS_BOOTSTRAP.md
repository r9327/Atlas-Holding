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
