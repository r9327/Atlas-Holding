# NONRELEASE — vraie inference Ollama sur Windows jetable

**Ce test ne publie aucun installateur et ne touche jamais au vrai depot
r9327/Dof-Atlas.** Il est independant de la certification #13 (Windows et
Linux PASS sur SHA c7407b5828d75a6a97101aa06e952d1316739c8a).

## Ce que la CI fait

Le workflow `.github/workflows/windows_real_ollama_inference.yml` ne s'active
que sur la PR experimentale `experiment/native-real-ollama-20261022`,
par le label explicite `ci:live-ollama`. Il tourne avec un token GitHub
**lecture seule**, checkout sans credentials, Windows 2025 jetable,
Python 3.13 et PyInstaller epingle.

1. Installe Ollama WinGet **0.32.8** avec volume temporaire. Un daemon deja
   present provoque un refus, jamais une prise de controle.
2. Telecharge uniquement `qwen2.5-coder:1.5b` (environ 986 Mo) sur le
   runner GitHub ; aucun telechargement sur le PC utilisateur.
3. Confirme la version native et le digest complet dans `/api/tags`.
4. Construit deux vrais EXE, `AtlasHoldingAgent.exe` et
   `AtlasHoldingAcceptance.exe`. Ce dernier execute une **vraie requete
   /api/chat** (pas un faux service) pour proposer un seul fichier dans un
   depot Git **temporaire**, valider un AST strict et effectuer un commit local.
   **Trois missions indépendantes** sont exécutées successivement. Avant
   chaque mission le digest Ollama est contrôlé de nouveau ; chaque essai
   doit avoir son propre SHA de commit, sa durée et son reçu canonicalisé.
5. Refuse une preuve sans SHA exact, une affirmation de Doctor PASS,
   un push GitHub, une release ou un autre modele.
6. Chaque essai local ayant réussi reçoit un marqueur
   `NONRELEASE_REAL_OLLAMA_INDIVIDUAL_TRIAL_PASS=N/3`. **Après les trois
   succès distincts seulement**, le script crée sans écrasement un JSON
   de session liant les trois empreintes SHA-256 de reçus, les trois commits
   Git, les durées et le digest du modèle. Le marqueur global est
   `NONRELEASE_REAL_OLLAMA_FROZEN_THREE_TRIALS_PASS`, accompagné du
   digest, du SHA-256 de session et de `Doctor: NOT_EXECUTED`.
   Tout échec ou changement de modèle interdit ce bilan global. Les preuves
   restent sur le runner éphémère (pas de diffusion des sorties du modèle).

Si le modele faible echoue meme cette petite tache, le job reste rouge.
Un PASS atteste seulement la chaine native et cette tache jetable, **pas**
la qualite pour Dofus Atlas, pas l'auto-selection, pas la RAM/VRAM du PC,
pas la securite d'execution d'un code adversarial ni une certification Doctor.

## Garde-fous

Le workflow requiert un label d'opt-in, borne l'installation et le modele
par des deadlines, refuse les sorties non authentiques et n'execute jamais
le code genere. Les jobs normaux de #13 restent invariables et ne
telechargent aucun modele. Ce scenario existe pour montrer ce qu'une
API Ollama **reelle** change par rapport au stub deterministe.

## Preuve initiale indépendante du nouveau lot

Le premier essai simple est **PASS** sur le SHA GitHub
`872e423ffc009dbf7ab7fe182d854d94c0cff39a` :
[GitHub Actions #39789768043](https://github.com/r9327/Atlas-Holding/actions/runs/39789768043).
Digest du modèle exécuté :
`d7372fd828518a4d38b1eb196c673c31a85f2ed302b3d1e406c4c2d1b64a0668`.
Preuve de commit Git jetable acceptée, Doctor non exécuté.

Ce succès n'est **pas** extrapolé à 3/3 essais ni à des tâches Atlas.
Le nouveau lot de répétabilité doit obtenir son propre verdict CI
sur le HEAD exact, avec ses trois reçus et sans faux aggregate PASS.
