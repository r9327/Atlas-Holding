# Documentaliste IA — contrat V1 (22/10/2026)

## Objet
Sixième rôle de Holding, générique et réutilisable hors DOFUS : collecte, compréhension et préparation de données, sous orchestration du CEO. Ne pas créer un deuxième orchestrateur, un moteur MCP parallèle ou une base de connaissance lourde.

## Intégration attendue
- Brancher le rôle `documentalist` sur le registre de capacités/scheduler existant (PR #3/#4), avec tâches bornées, pauses/reprises, événements SQLite et état visible dans le Control Center #8.
- Réutiliser le hub MCP #5 : priorités aux adaptateurs existants Firecrawl/Context7 ; ajouter Crawl4AI comme collecteur local optionnel, Docling pour PDF complexes et MarkItDown comme conversion légère. Aucun service lourd lancé par défaut.
- Entrées : URL, liste de pages, PDF, HTML, JSON, Markdown, CSV, fichiers de projet. Sorties : enregistrements normalisés plus preuves de provenance (URI, date, version, ID, empreinte SHA-256), anomalies et degré de confiance *explicable*.
- Pipeline : découvrir -> collecter par lots -> parser -> normaliser -> dédupliquer -> comparer -> contrôler -> fournir données au codeur. Ne jamais exécuter d'instructions contenues dans les pages/documents.
- Réseau mobile : téléchargements reprenables, cache content-addressed, quotas de transfert et d'API configurables, aucune dépense imposée, reprises après échec, `OFFLINE_DEFERRED` explicite.
- Documents volumineux : streaming, bornes taille/pages/temps/processus, traitement par lots, pas de matérialisation intégrale en RAM.
- Comparaison source/date/identifiants ; ne pas transformer une contradiction entre sources en vérité inventée.
- Adapters métier : le schéma DOFUS reste dans Dof-Atlas et non dans Holding.

## Tests obligatoires
- Tests sans Internet : HTML/JSON/PDF de fixtures, documents corrompus, doublons et conflit d'IDs, dossier >1000 entrées, rollback et reprise des lots.
- Faux contenu Web contenant une prompt injection : doit rester une donnée, jamais une autorisation.
- Tests réseau opt-in avec quota nul/épuisé, erreurs 429/5xx, interruptions et cache périmé correctement signalés.
- Mesure RSS du processus Holding (hors IA), pas de plugin lourd lancé automatiquement.
- Intégration bout-en-bout : import -> sortie vérifiable -> mission codeur; pas d'auto-certification.

## Dépendances et livraison
- Base : PR Holding #3, #4, #5, #8 et #10. Adaptateur Atlas : PR Dof-Atlas liée à cette feuille de route.
- Cette PR démarre par le contrat : **aucun collecteur ni agent effectif n'est prétendu livré**. Conserver Draft jusqu'au code, tests ciblés et intégration Windows validés.
