# PR #13 - Preuve Windows de mission locale

Candidat **nonrelease** issu du bootstrap #2 et du moteur/agent #11.
Aucun test ici ne vaut autorisation de pousser ou de modifier le vrai
depot r9327/Dof-Atlas.

## CI sur SHA exact

Le workflow windows_integrated_validation.yml valide :

1. Suite complete Python 3.13 sous Ubuntu et Windows, et analyse AST.
2. Construction des trois vrais EXE et test de leurs protocoles natifs.
3. Installation de ces EXE avec ACL Windows natives, choix de disque et
   configuration moteur schema v3, refus de faux clone Git et d'un clone sale.
4. Un vrai AtlasHoldingAgent.exe lance par LocalProcessAgent, qui appelle
   LocalCodeWorker et produit un commit Git avec bail, grants et SQLite.
   Dans la CI, la reponse de generation de code vient d'un **faux Ollama
   local deterministe**. La verification independante lit un AST Python :
   fonction answer sans parametre, retourne exactement entier 42, aucun
   autre fichier modifie. Main reste inchange et la mission reste RUNNING,
   sans simuler un PASS Doctor ni un push GitHub.
5. Emballage du setup avec SHA des trois EXE et execution du self-test.

Ce test prouve une chaine technique agent natif -> worker -> commit autorise ;
il ne prouve **pas** qu'un vrai LLM code correctement.

## Essai optionnel avec modele Ollama reel sur Windows

Le script tests/native_windows_code_mission_smoke.py accepte deux modes.
Par defaut, le modele est simule pour CI. Avec --model-json il utilise un
modele deja installe et epingle par digest selon /api/tags de Ollama local.

Exemple de JSON en chemin absolu : 
    {"schema":1,"model":"nom-exact","digest":"64-hex-du-tags","num_ctx":8192,"timeout":300}

Commande PowerShell (sur machine equipee, a partir du checkout Holding) :
    python -B -X utf8 tests/native_windows_code_mission_smoke.py --agent "C:\chemin\AtlasHoldingAgent.exe" --model-json "C:\chemin\model.json"

Le vrai modele travaille alors **exclusivement dans un nouveau depot Git
temporaire**. Aucune PR n'est ouverte, pas de push, aucune commande de tests
dans du code genere : uniquement controle AST non executable. Un modele qui
ne satisfait pas exactement le contrat rend un echec explicite.

## Verrous de release

Modele reel et qualite benchmarkee, tests UI Windows visibles, gestion de
redemarrage et upgrade, pression RAM/VRAM Ollama mesuree independamment,
Doctor et CI preuves exact-SHA, GitHub broker authentifie et PR brouillon.
Ne jamais confondre un commit local avec une mission autonome certifiee.
