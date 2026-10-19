# PR 02 — Installation et réparation Windows (SPECIFICATION, NON IMPLEMENTEE)

**But utilisateur :** depuis une Release GitHub, télécharger un `AtlasHoldingSetup.exe`, double-cliquer et obtenir les dépendances et les modèles préparés avec un assistant visuel. Zéro achat imposé.

## Portée
- Détecter Windows x64, RAM, VRAM, CPU, espace disque, droits, versions Python/Git/Ollama/OpenCode, connexion et capacité de téléchargement.
- Installation reproductible et **épinglée** de Python compatible, Git, Ollama, OpenCode, dépendances GUI, outils MCP nécessaires et prérequis Atlas ; provenance et hash des paquets ; ne pas exécuter des scripts distants arbitraires.
- Choix du disque et du répertoire. Réparer une installation partielle, mettre à jour sans perdre la base SQLite ni les préférences. Aucune suppression implicite.
- Premier démarrage guidé : choisir le clone Atlas ou le cloner, lire ses règles, sélectionner un profil d'IA et configurer les permissions ; **aucun accès en écriture à main**.
- Sur partage mobile, afficher les tailles réelles avant tout téléchargement lourd (poids LLM), demander l'accord, permettre pause/reprise et vérification intégrale SHA. Après provisionnement, lancement offline.
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
- Ne pas retélécharger un modèle déjà présent et fonctionnel. Possibilité de **reporter** un téléchargement lourd sur partage mobile, sans bloquer l'interface ; travail autonome impossible si aucun modèle validé.
- Première configuration en un clic **après consentement pour les téléchargements lourds et les permissions** ; le nom/digest/quantification demeure dans la vue avancée seulement.
- Ne pas promettre qu'un 24–30B tient dans 8 Go de VRAM ; détecter CPU offload et tester stabilité / contexte réel.
- La calibration qualité et l'affectation alternée des modèles relèvent de #4 ; écran explicite et installation de #2 ; le contrat canonique est `docs/LLM_AUTO_SELECTION.md` dans #1.
- [ ] Sur machine neuve/partiellement équipée, aucun choix technique de modèle exigé.
- [ ] Sélection automatique vérifiée sur matériel 8 Go VRAM / 32 Go RAM ; fallback sûr si OOM.
- [ ] Reprise après téléchargement interrompu, consentement sur les Go avant téléchargement, préservation des modèles existants.
