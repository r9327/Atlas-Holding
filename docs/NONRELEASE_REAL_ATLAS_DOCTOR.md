# NONRELEASE — vrai Doctor Dof-Atlas sous Windows, lecture seule

Le bloc Ollama reel #14 est PASS sur trois missions Git jetables (SHA
`8188327ca255a498f3eea14dcd595e92984528ae`, run
[39793253915](https://github.com/r9327/Atlas-Holding/actions/runs/39793253915)).
Cela ne prouve ni Doctor ni certification du vrai Dofus Atlas.

## Contrat de cette experience complementaire

- Branche Holding independante, dependante de #14 ; la CI ne tourne qu'avec
  un label explicite `ci:real-atlas-doctor`.
- La CI utilise deux checkouts distincts : Holding au SHA exact de la PR et
  **Dof-Atlas public au SHA epingle**
  `54d45478c88cd733d2127789c1bef7caa599b54d`. Pas d'identifiants
  conserves apres checkout et permissions GitHub lecture seule.
- Sur Windows 2025, `AtlasDoctorRunner` examine l'identite du dossier
  canonique, le `git status`, le HEAD exact et le remote canonique, puis
  appelle `python -B -m tools.atlas_doctor --json agent plan
  app/constants.py --base-ref HEAD` sous supervision bornée.
- Le rapport est interprete par le **vrai consommateur de protocole**
  `consume_doctor_report`, qui exige SHA, action, scope, statut, digest
  d'arbre et codes retour concordants, sans croire le simple texte du Doctor.
- Avant et apres le Doctor, le HEAD et la proprete du checkout sont verifies.
  Une mutation, un rapport incoherent ou un refus de connexion echoue.
- Un statut `PLANNED_LOCAL` signifie uniquement « plan local
  protocolairement valide ». Si Doctor repond `REVIEW`, l'integration
  l'affiche explicitement comme `REVIEW_NOT_READY`, jamais comme READY.

## Ce que cela ne prouve PAS

Le mode `plan` **ne lance aucun test** du candidat et ne signe aucun
verdict. Pas de certification FULL, pas de signature privilegiee Doctor/CI,
pas de release, pas de correction reelle, pas de changement du depot public,
pas de Graphify structurel confirme. L'objectif est de verifier le premier
raccordement independant entre la Holding et le **vrai Doctor du vrai depot**.
Une prochaine etape devra creer une branche candidate totalement isolee,
executer `check`/`validate` avec un test cible, puis demander les preuves
privilegiees exact-SHA sans permettre au codeur de s'auto-certifier.
