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
