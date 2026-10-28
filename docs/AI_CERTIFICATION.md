# Holding — discipline de certification IA et de livraison

Ce runbook **référence** les gates exécutables existants. Il ne signe aucune preuve et ne remplace ni `AGENTS.md`, ni `docs/QUALITY_RELEASE_GATES.md`, ni `docs/SCOPED_QA_RUNTIME.md`.

## États à déclarer, jamais à deviner

- `CODE_DONE` : implémentation terminée, diff et commits relus ; tests non nécessairement réussis.
- `VALIDATED` : tests requis *réellement exécutés* et réussis sur l'environnement et le périmètre indiqués. Préciser LOCAL, SCOPED ou WINDOWS ; ce n'est pas la certification de release.
- `CERTIFIED` : uniquement après preuve indépendante autorisée et vérifiée sur le **SHA candidat exact**, avec tous les gates obligatoires du périmètre (notamment Windows réel lorsque requis). Le signataire privilégié doit être séparé de l'agent qui écrit le code.

Un rapport local `qa_scoped`, une analyse statique, un texte d'agent, un run annulé/ignoré ou un succès à un ancien SHA ne donne pas l'état `CERTIFIED`. Réutiliser les émetteurs et contrôles de preuve existants ; ne pas ajouter un chemin permettant à l'agent de signer ses propres résultats.

## Méthode par PR

1. Établir la base et le SHA candidat, les exigences fixes et les risques (sécurité, persistance, Windows, UI, réseau, plugins, politique CI).
2. Exécuter les tests ciblés ; vérifier la cohérence du registre, la provenance des outils, les permissions et les quotas. Si le changement touche les règles elles-mêmes, passer le contrôle `tools/ai_contract_check.py` et ses tests sans les affaiblir.
3. Effectuer la revue **commit par commit** (logique, sécurité, effets secondaires et couverture), puis la revue du diff complet. Verdict de revue indépendant : `REVIEW OK`, `IMPROVEMENTS ADVISED` ou `CHANGES REQUIRED`.
4. Garder la PR Draft jusqu'à stabilisation. Sur Ready for review, les workflows applicables doivent s'exécuter au SHA exact ; si un test nécessaire n'est pas déclenché, la preuve reste manquante, pas réussie.
5. Vérifier l'origine GitHub, la conclusion, les étapes obligatoires, les artefacts et le SHA avant toute promotion. Les tests Windows réels, RAM, installateur, Ollama et exécution complète ne sont jamais présumés à partir des checks de gouvernance.

## Matrice minimale

| Surface modifiée | Preuve obligatoire avant approbation / promotion |
| --- | --- |
| Documentation et instructions IA seules | Contrat statique, revue logique, cohérence des références et du périmètre |
| Code Holding ou outils exécutables | Tests fonctionnels ciblés + contrôles existants du périmètre et de ses consommateurs |
| Runtime, ACL, secrets, plugins MCP, sync GitHub, certification | Tests de sécurité et d'intégration concernés ; preuve indépendante ; escalade si un bord est inconnu |
| Installeur, UI, exécutable Windows, RAM / autonomie complète | Essais réels Windows et critères `docs/QUALITY_RELEASE_GATES.md` ; pas de certification par simulation |

Le nouveau workflow `AI contract validation` vérifie **seulement** les fichiers de gouvernance et le registre ; il ne remplace aucune certification métier, sécurité ou Windows. Ne pas modifier artificiellement le statut des contrôles requis ni les protections GitHub.
