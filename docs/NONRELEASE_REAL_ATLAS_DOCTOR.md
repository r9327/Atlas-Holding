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

## 22 octobre — difference de proprietaire entre deux chemins

La premiere CI du vrai Doctor est **PASS cote execution** sur
[39799116731](https://github.com/r9327/Atlas-Holding/actions/runs/39799116731),
mais son resultat metier est **REVIEW_NOT_READY** :
`NONRELEASE_DOCTOR_REVIEW_ACTION_1: Resolve scope ownership before editing:
app/constants.py`. C'est un refus legitime de la cartographie d'Atlas, pas
une erreur du modele Ollama.

Sur le meme SHA d'Atlas, la CI suivante compare desormais deux cibles
**explicitement allowlistees**, et non une entree libre du modele :

- `unowned` = `app/constants.py` : le Doctor DOIT retourner REVIEW.
  Toute autorisation inattendue bloque la CI.
- `owned_home` = `app/pages/home_page.py` : ce fichier est le
  `working_set` declare du scope `home` dans
  `.ai/scopes/home.yaml`. Doctor peut retourner READY ou REVIEW suivant
  ses **autres** regles, qui ne sont pas assouplies.

Chaque appel est relie au HEAD exact, au remote canonique et a un arbre
Git propre avant/apres. Le diagnostic REVIEW est un second appel plan-only
dont le rapport est relu a travers le meme protocole strict. Ne jamais
interpréter un proprietaire unique comme un passe-droit pour pousser,
modifier, tester ou fusionner.

## Reçus indépendants exploitables par la Holding (NONRELEASE)

Chaque comparaison émet maintenant un petit reçu canonique au format JSON dans
le **répertoire temporaire du runner GitHub**, jamais dans le checkout Atlas.
Il contient : dépôt et SHA Atlas exact, chemin de scope autorisé, statut
`PLANNED_LOCAL` ou `REVIEW`, empreinte du rapport Doctor et quatre verrous
explicites à `false` (tests exécutés, Doctor FULL, push GitHub, release).
La création utilise `O_EXCL` : un reçu préexistant ne peut pas être remplacé.
Avant la création, les deux commandes Git vérifient que HEAD et l'arbre Atlas
n'ont pas changé.

Le workflow compare indépendamment les deux reçus et vérifie que le fichier
sans propriétaire reste en `REVIEW`. Seulement ensuite, il produit un
artefact GitHub **NONRELEASE**, conservé trois jours.
Une empreinte SHA-256 à 64 caractères ne constitue **pas** une signature
d'autorité du Doctor ; ces reçus restent des diagnostics locaux.
Cette différence est essentielle pour le futur Control Center, qui ne doit pas
convertir un rapport consultatif en autorisation de fusion.
