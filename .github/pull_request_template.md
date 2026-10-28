## Périmètre
Objectif, exigences vérifiables, exclusions et impact sur les permissions, plugins, persistance, UI ou Windows.

## Candidat et vérifications
- Base SHA :
- HEAD SHA exact :
- Revue de chaque commit (risques, consommateurs, tests) :
- Revue globale du diff / verdict : REVIEW OK | IMPROVEMENTS ADVISED | CHANGES REQUIRED
- Tests locaux exécutés (commande, résultat, environnement) :
- GitHub checks et preuves liées au SHA exact :
- Tests NON EXÉCUTÉS / BLOQUÉS (notamment Windows, RAM, réseau) :
- État revendiqué : CODE_DONE | VALIDATED | CERTIFIED (seul le producteur indépendant peut attester CERTIFIED)

## Outils et plugins
Justifier les capacités sélectionnées, registre/version, réseau/cache, quotas et autorisations ; noter tout outil manquant ou non vérifié.

## Livrable
- [ ] Worktree isolé, aucune écriture directe sur main
- [ ] Sources de vérité et consommateurs inspectés
- [ ] Pas d'assouplissement des tests, droits ou certifications
- [ ] PR complète, sans résultat SKIPPED/NOT_RUN présenté comme PASS
- [ ] Aucun secret, donnée utilisateur ou artefact d'exécution commité

Voir `AI_ENTRYPOINT.md`, `docs/AI_CERTIFICATION.md` et `AGENTS.md`.
