# Atlas Holding — Sélection automatique intelligente des LLM (CONTRAT, NON IMPLEMENTE)

## Objectif utilisateur
**Aucun nom de modèle à choisir.** En première installation, proposer par défaut « Configuration automatique recommandée — priorité qualité ». Le modèle, la version, la quantification, les limites contextuelles, le nombre de modèles chargés et les agents associés sont choisis par des tests de capacité et une évaluation technique reproductible. Une vue avancée peut exposer les noms techniques ; elle n'est jamais un prérequis.

## Contraintes par défaut
- Matériel de référence : Ryzen 7 5800X, 8 cœurs / 16 threads ; 32 Go de RAM, RTX 3060 Ti avec **8 Go de VRAM** ; qualité du code prioritaire, temps secondaire.
- **0 € de coûts obligatoires** ; inférence locale Ollama et outils locaux, aucun modèle distant payé déclenché implicitement.
- Profil principal : `QUALITY_FIRST`. Les autres profils `BALANCED` et `LOW_RESOURCE` ne sont proposés qu'en réglages avancés, avec vocabulaire utilisateur.
- Un seul LLM lourd chargé en mémoire à un instant ; CPU offload autorisé si mesuré stable. Pas de promesse de vitesse ou de contexte fondée uniquement sur le nombre de paramètres.
- Installer uniquement ce qui manque. Poids déjà disponibles et compatibles = réutiliser après healthcheck et benchmark léger.
- Si Internet absent et aucun modèle installé : état `WAITING_FOR_MODEL_DOWNLOAD` et instructions claires, pas de prétendu fonctionnement autonome.

## Pipeline de choix déterministe, sans "classement marketing"
1. **Inventaire** : détecter CPU/RAM réellement utilisables, VRAM libre, espace disque sur volume choisi, charge actuelle, version du moteur Ollama/OpenCode et modèles présents (tag + digest + quantification + capacités tools + licence + contexte déclaré).
2. **Catalogue signé/épinglé et actualisable** : découvrir modèles *locaux de codage* compatibles avec les versions de runtime et le tool calling. Écarter les modèles sans licence adéquate, format utilisable ou source intègre. Lors d'une panne réseau, réutiliser un catalogue local horodaté en signalant sa fraîcheur.
3. **Préfiltrer sans gros téléchargement** : estimer poids réels, RAM + VRAM, KV cache au contexte cible, réserve pour Windows/Qt/Doctor/agents/processus, CPU offload et disque avec marge. Une estimation ne suffit pas : rejeter si manifestement impossible ; autres candidats à valider par essai réel.
4. **Réutiliser puis sonder** les modèles installés ; mesurer temps de chargement, mémoire maximale, pression mémoire Windows, stabilité, qualité du tool calling et capacité à garder le contexte requis. Un crash/oom invalide ce candidat.
5. **Proposer le téléchargement le plus utile**, sans imposer plusieurs variantes de 15–25 Go. Afficher AVANT accord : "Meilleure qualité détectée pour ce PC" ; volume à télécharger, espace total, taille du cache, ralentissement éventuel, mode offline après préparation. L'utilisateur possède un partage mobile illimité : l'autorisation globale d'installation suffit pour les modèles recommandés, sans plafond Go ni nouveau choix par nom de modèle ; afficher le poids réel et permettre une annulation.
6. **Benchmark local après installation** : mini-batterie versionnée de vrais problèmes *sur worktree jetable* ; compréhension du code, modification ciblée, utilisation de plugins, respect des guardrails, correctif avec test rouge→vert, revue d'un défaut injecté, bonne réponse en cas de manque de preuve ; aucun changement sur main ni données utilisateur.
7. **Score fondé sur preuves** : portes de sécurité obligatoires d'abord (aucun changement non autorisé, pas de faux PASS, pas de fuite de secret), puis conformité aux tests/acceptance, taux de tâches correctement finalisées, qualité des correctifs, précision des tool calls et stabilité. Vitesse utilisée uniquement pour départager des candidats de qualité comparable selon le profil.
8. **Affecter le meilleur modèle au code**, et un modèle différent à l'audit **uniquement si sa contre-expertise apporte un gain testé** et si son téléchargement est autorisé. Les rôles CEO/architecte/codeur/auditeur utilisent des profils d'instructions distincts même avec un seul modèle. Alternance sans deux modèles lourds simultanés.
9. **Sauvegarder le profil** (IDs/digests, quantifications, tokens de contexte vérifiés, paramètres, matériel, score par tâche, dates, preuve des essais) hors Git. Tous les noms techniques sont visibles sous « Détails avancés », pas dans la question initiale.
10. **Réévaluer automatiquement** après erreurs répétitives mesurées, manque de mémoire, dérive de tests, modification de hardware/runtime ou nouvelle version disponible. Ne jamais remplacer un modèle opérationnel par un candidat non testé ; bascule/rollback idempotents. Mise à jour des poids avec accord utilisateur si téléchargement significatif.

## Scoring et règles anti-auto-notation
- Score final n'est jamais produit uniquement par une IA. Exemple de composition possible : sécurité (gate éliminatoire), tests corrects (50 %), critères d'acceptation vérifiés (30 %), revue indépendante / erreurs non détectées (15 %), stabilité et tool calling (5 %). **Ces pondérations sont à calibrer** avec des lots Atlas et ne doivent pas servir à fabriquer une note sans preuve.
- Le test ne se limite pas à la syntaxe : la capacité de suivre `AGENTS.md`, Graphify et Doctor, de refuser les actions risquées et de corriger une régression prime.
- Aucun benchmark modifiant le dépôt utilisateur : branche/worktree sandbox avec fixtures versionnées et hash. Aucun accès GitHub/Firecrawl nécessaire pendant les exercices offline.
- Si aucun candidat ne passe les critères obligatoires, déclarer **« Aucun modèle compatible validé pour un fonctionnement autonome »** et proposer réparation/retéléchargement/choix manuel avancé, sans faire semblant.

## Interface de premier lancement

### Écran « Votre configuration IA »
- Titre : **« Atlas choisit les meilleures IA pour votre PC »**.
- Carte simple : « Priorité : qualité du code », « Fonctionne sans Internet après installation », « Aucune API payante nécessaire ».
- Résultat : « Configuration recommandée » avec statuts **Compatible / En cours de vérification / Téléchargement nécessaire / Indisponible**. Explication humaine : qualité, ralentissement possible, téléchargement en Go et espace requis ; aucun nom de modèle exigé.
- Parcours : **Installer Atlas Holding avec la configuration IA recommandée** (consentement unique de démarrage et de stockage) ; détails et annulation facultatifs. Pas de demande de validation répétée pour les poids couverts par cette autorisation ; seules de nouvelles permissions sensibles ou un service payant nécessitent un consentement spécifique.
- Après les tests : affichage simple **« Modèle de développement prêt »**, **« Contrôleur IA prêt »**, ou cause du blocage. Le second modèle ne doit pas être imposé si aucun gain de qualité n'est démontré.
- Bouton **« Réévaluer ma configuration »** dans l'onglet IA. Disponibilité d'un profil gratuit confirmé, date de test et avertissements visibles.

## Tests de réception (tous bloquants)
- [ ] Aucun choix de nom requis sur Windows avec plusieurs modèles compatibles disponibles.
- [ ] Réutilisation d'un Ollama/modèle déjà installé, sans téléchargement ni désinstallation.
- [ ] Sélection sûre avec RTX 3060 Ti 8 Go, 32 Go RAM ; CPU offload indiqué si nécessaire ; contexte réel mesuré sans OOM.
- [ ] Internet absent / aucun modèle / catalogue périmé : attente claire, pas de faux « prêt ».
- [ ] Connexion mobile illimitée : aucune limite quotidienne en Go inventée ; téléchargement automatique sous permission initiale, interruption/reprise vérifiées.
- [ ] Test qualité différencie deux modèles par résultats réels, même si le plus rapide est moins bon.
- [ ] Modèle incapable d'utiliser les tools ou violant les règles : refus même si ses réponses semblent bonnes.
- [ ] Un modèle crash/oom : arrêt sûr, rollback, nouveau candidat ou état bloqué explicite.
- [ ] Auditeur distinct facultatif, téléchargé seulement sur autorisation et chargé **en alternance**.
- [ ] Après mise à jour d'Ollama/modèle/changement de matériel : requalification, conservation du profil précédent tant qu'il reste valide.
- [ ] Les résultats, versions, tailles, licences et provenance des modèles restent auditables dans « Détails ».
- [ ] Toutes les expériences offline sur clone jetable, jamais sur `main` de Dofus Atlas.

## Responsabilité des PR
- #1 : ce contrat et la roadmap canonique.
- #2 : découverte matériel, installateur, UX de sélection, téléchargement et healthchecks.
- #4 : benchmark, scoring, affectation par rôle et requalification automatique.
- #8 : écrans premier lancement et surveillance dans Control Center.
- #9 : tests Windows bout-en-bout avant premier EXE Release.

**Ce document formalise un comportement à construire. Aucun sélectionneur intelligent, benchmark ni téléchargement LLM n'est livré par la documentation seule.**
