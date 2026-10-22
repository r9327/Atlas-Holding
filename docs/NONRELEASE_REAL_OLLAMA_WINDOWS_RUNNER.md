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
5. Refuse une preuve sans SHA exact, une affirmation de Doctor PASS,
   un push GitHub, une release ou un autre modele.
6. Emet uniquement des traces sans code genere ni donnees du modele :
   `NONRELEASE_REAL_OLLAMA_FROZEN_CODE_COMMIT_PASS` si le test reussit,
   digest du modele, SHA-256 du recu et statut Doctor `NOT_EXECUTED`.

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
