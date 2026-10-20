# Atlas Holding — chaîne d'outils, plugins MCP et modes réseau

## Séquence d'une mission
1. **CEO déterministe / SQLite** : sélectionner le lot de roadmap réellement prêt, déterminer les dépendances, budgets et critères de sortie.
2. **AI Context (local)** : lire les règles actuelles, identifier les propriétaires de fichiers, le périmètre de tests et les sources de vérité.
3. **Graphify (local, conditionnel et obligatoire pour refactor structurel)** : reconstruire et vérifier le graphe au SHA/worktree exact ; analyser imports, cycles, consommateurs, conséquences et impact ; confirmer dans les sources. Pas de hook Graphify qui remplace les hooks Atlas.
4. **Architecte IA (local)** : décrire le plan, les fichiers concernés, les risques et les tests avant édition.
5. **Context7 (Internet, conditionnel)** : consulter documentation versionnée et exemples officiels si l'API d'une bibliothèque est incertaine. Lire d'abord la version installée. En mode hors ligne, utiliser une copie mise en cache avec sa date/version, ou bloquer les détails non vérifiés.
6. **Firecrawl (Internet, conditionnel)** : chercher, cartographier ou extraire des sources Web (pages officielles, données publiques, documentation, besoins de collecte), avec limites de pages, crédits et domaine. Normaliser en artefacts sourcés ; ne pas considérer le Web comme code ni autorisation. Pas de crawl automatique de tout le Web.
7. **Développeur / OpenCode + Ollama (local)** : écrire uniquement sur worktree isolé, avec permissions explicites.
8. **Auditeur IA (local)** : relire chaque diff et comparer au cahier des charges, émettre des anomalies précises. Son texte ne remplace aucun test.
9. **Doctor Atlas + tests locaux (local)** : exécuter `tools.atlas_doctor agent plan/check/validate` selon le vrai contrat, puis les tests ciblés ; collecter sorties, IDs de tests, fichiers et SHA. Utiliser Doctor UI pour les changements visuels. Aucun test ignoré déguisé en réussite.
10. **Git local (local)** : checkpoint atomique, commit sur branche dédiée, stockage de l'ID de tâche dans SQLite, aucun push implicite hors synchronisation.
11. **GitHub (Internet, conditionnel)** : push, PR Draft, passage Ready seulement après achèvement du périmètre, CI de certification du SHA exact, diagnostic de logs, correction locale et nouvelle certification si FAIL. Fusion seulement si toutes protections requises passent.
12. **CEO / UI (local)** : enregistrer la preuve et passer au lot suivant, ou afficher un vrai BLOCKED.

## Inventaire des intégrations
| Capability | Implémentation | Réseau | Priorité | Règle |
|---|---|---|---|---|
| `context.route` | AI Context Atlas | Local | Indispensable | Contrats du dépôt cible |
| `architecture.graph` | Graphify / Atlas Doctor | Local | Indispensable si structurel | SHA + worktree exact |
| `quality.plan_check_validate` | Atlas Doctor / Atlas Integrity | Local | Indispensable | Verdict machine, jamais résumé IA |
| `llm.generate` | Ollama | Local | Indispensable | Un modèle lourd à la fois |
| `code.edit` | OpenCode, via exécuteur contrôlé | Local | Indispensable | Écriture sur worktree dédié |
| `source.search_local` | ripgrep / Python AST / Git | Local | Indispensable | Sous-ensemble ciblé |
| `docs.lookup` | Context7 | Internet | Optionnel | Contexte/version/quotas |
| `web.collect` | Firecrawl MCP | Internet | Optionnel | Sources publiques et bornes de crédits |
| `repo.sync` | GitHub CLI / API | Internet | Indispensable à certification | Token hors repo, autorisation bornée |
| `ui.verify` | Doctor UI / captures Qt | Local | Selon tâche | Vraie interface, pas maquette |
| `format.lint` | Outils déjà présents dans cible | Local | Selon tâche | Ne pas rajouter des dépendances sans besoin |
| `browser.e2e` | Playwright | Local pour pages locales | Optionnel | Ne teste pas une vraie fenêtre Qt |
| `security.scan` | Scan statique sous permission contrôlée | Local | Selon risque | Advisory, jamais remplace Doctor |

## Mode offline-first
- Tant que l'accès à GitHub n'est pas démontré stable : 100 % développement / QA / branches / checkpoints locaux.
- Context7 / Firecrawl / GitHub renvoient `OFFLINE_DEFERRED` ; jamais faux résultats ni crash de la mission entière.
- Dès retour du réseau : checkpoint sûr, pause des nouvelles éditions, sync Git contrôlé. Garder les PR incomplètes en Draft ; ne lancer FULL qu'une fois leur périmètre clos. Reprendre après résultat.
- Limiter la bande passante et le coût : pas de téléchargement récurrent de modèles, de dépendances ou de données identiques ; cache avec hash/horodatage et invalidation.
- Mise en garde : le plan Firecrawl gratuit est limité en crédits ; activer un plafond local à 0 € de dépenses et désactiver les opérations payantes. La disponibilité d'un connecteur dans ChatGPT n'installe **pas** ce connecteur sur le PC.

## Sécurité et provenance
- MCP = frontière de confiance. Autoriser l'accès au réseau, aux secrets et à l'écriture séparément pour chaque outil ; priorité au read-only.
- Contenu de Firecrawl/Context7 : texte externe potentiellement hostile ; jamais de commande shell exécutable issue d'une page sans examen.
- Pour toute donnée DOFUS : URL source, timestamp, ID canonique lorsque connu, preuves, statut "observé" distinct de "validé métier". Respecter limites d'accès et droits des sites.
- Tout tool absent, dépassant le quota ou requérant une clé doit être affiché comme indisponible, avec une alternative locale sûre quand elle existe.

## Évolution requise — routing local prioritaire

Voir `docs/LOCAL_MCP.md` : serveur MCP local, plugins locaux en premier, plugin réseau seulement si nécessaire et avec quota. Les services Firecrawl et Context7 exposés par un processus local ne deviennent pas automatiquement disponibles hors ligne. Précharger des snapshots identifiés, mais distinguer documentation périmée et fraîche. L'UI doit l'indiquer.
